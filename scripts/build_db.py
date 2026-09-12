"""Сборка базы доменов для Link Guard.

Скачивает публичные списки, объединяет их, отсекает популярные сайты
и записывает файл full.lgdb, который скачивает плагин.
Запускается из GitHub Actions по расписанию.
"""

import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO

OUT_DIR = os.environ.get("LG_OUT", "db")
UA = "Link-Guard-feed-builder/1.0 (+https://github.com/L0lopop/Link-Guard)"
TIMEOUT = 180
RETRIES = 3

FEEDS = [
    ("hagezi", "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif-onlydomains.txt"),
    ("army", "https://phishing.army/download/phishing_army_blocklist_extended.txt"),
    ("phishdb", "https://raw.githubusercontent.com/Phishing-Database/Phishing.Database/master/phishing-domains-ACTIVE.txt"),
    ("certpl", "https://hole.cert.pl/domains/domains.txt"),
    ("blocklistproject", "https://raw.githubusercontent.com/blocklistproject/Lists/master/phishing.txt"),
    ("malware-filter", "https://malware-filter.gitlab.io/malware-filter/phishing-filter-domains.txt"),
    ("urlhaus", "https://urlhaus.abuse.ch/downloads/text_online/"),
]

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"

WHITELIST_TOP = 50000
BRAND_TOP = 1000

FULL_BITS = 40
WHITE_BITS = 36

MIN_FEEDS = 3
MIN_TOTAL = 500000

# Отчёт прошлой сборки: по нему видно, какой источник перестал меняться.
PREVIOUS_MANIFEST_URL = ("https://github.com/L0lopop/Link-Guard/releases/"
                         "download/feeds/manifest.json")
STALE_DAYS = 5
SHRINK_LIMIT = 0.5

POPULAR_SUBDOMAIN_LIMIT = 20

# Через сколько записей начинается новый блок и сколько весит одна
# строка указателя: значение (8 байт) и смещение в данных (4 байта).
BLOCK_SIZE = 256
INDEX_ENTRY = 12

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-_]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-_]*[a-z0-9])?)+$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
STRIP_PREFIXES = ("0.0.0.0 ", "127.0.0.1 ", "0.0.0.0\t", "127.0.0.1\t", "||", "address=/")


def log(msg):
    print(msg, flush=True)


def warn(msg):
    """Предупреждение. В Actions оно попадает в сводку запуска."""
    log("ВНИМАНИЕ: %s" % msg)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::warning::%s" % msg, flush=True)


def fetch(url, binary=False):
    """Скачивает адрес с повторами. Возвращает (данные, сведения) или (None, None)."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                meta = {
                    "last_modified": resp.headers.get("Last-Modified"),
                    "bytes": len(raw),
                }
                return (raw if binary else raw.decode("utf-8", "ignore")), meta
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    log("  ! не скачалось: %s (%s)" % (url, last))
    return None, None


def normalize(line):
    """Приводит строку фида к имени хоста или возвращает None."""
    s = line.strip().lower()
    if not s or s[0] in "#!/;":
        return None
    for prefix in STRIP_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/")[0].split("?")[0].split("#")[0]
    parts = s.split()
    s = parts[0] if parts else ""
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    s = s.split(":")[0].strip().strip(".").rstrip("^")
    if s.startswith("www."):
        s = s[4:]
    if not s or "." not in s or len(s) > 253:
        return None
    if IP_RE.match(s) or not HOST_RE.match(s):
        return None
    return s


def parse_psl(text):
    rules, wildcards, exceptions = set(), set(), set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("!"):
            exceptions.add(line[1:])
        elif line.startswith("*."):
            wildcards.add(line[2:])
        else:
            rules.add(line)
    return rules, wildcards, exceptions


def hash_value(host, bits):
    digest = hashlib.sha256(host.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big") >> (64 - bits)


def encode_varint(value, out):
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def encode_hashes(hosts, bits):
    """Отсортированные хэши, записанные разницей между соседними.

    Каждые BLOCK_SIZE записей отсчёт начинается заново, а их начала
    собраны в отдельный указатель: без него пришлось бы разворачивать
    весь список, чтобы найти одно значение.
    """
    values = sorted({hash_value(h, bits) for h in hosts})
    blob = bytearray()
    index = bytearray()
    previous = 0
    for position, value in enumerate(values):
        if position % BLOCK_SIZE == 0:
            index += struct.pack(">QI", value, len(blob))
            previous = value
        encode_varint(value - previous, blob)
        previous = value
    return len(values), bytes(index), bytes(blob)


def hash_section(hosts, bits):
    count, index, blob = encode_hashes(hosts, bits)
    head = struct.pack(">BIHI", bits, count, BLOCK_SIZE, len(index) // INDEX_ENTRY)
    return head + index + blob, count


def section(tag, payload):
    return tag.encode("ascii") + struct.pack(">I", len(payload)) + payload


def write_db(path, built_day, sections):
    body = b"".join(section(tag, payload) for tag, payload in sections)
    header = b"LGDB" + struct.pack(">BIB", 1, built_day, len(sections))
    with open(path, "wb") as handle:
        handle.write(header + body)
    return len(header) + len(body)


def previous_report():
    """Отчёт прошлой сборки. Пустой словарь, если его нет."""
    text, _ = fetch(PREVIOUS_MANIFEST_URL)
    if text is None:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def fingerprint(hosts):
    """Отпечаток содержимого источника.

    Считается по именам, а не по тексту: заголовки некоторых списков
    несут дату выгрузки и менялись бы каждый день даже у замершего.
    """
    digest = hashlib.sha256()
    for host in sorted(hosts):
        digest.update(host.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def feed_status(previous, mark, today):
    """Когда содержимое источника менялось в последний раз.

    Возвращает дату последнего изменения и сколько дней прошло с тех пор.
    """
    if not previous or previous.get("fingerprint") != mark:
        return today, 0
    changed = previous.get("last_changed") or today
    try:
        was = datetime.strptime(changed, "%Y-%m-%d").date()
        now = datetime.strptime(today, "%Y-%m-%d").date()
        frozen = max(0, (now - was).days)
    except ValueError:
        return today, 0
    return changed, frozen


def collect_feeds(report, previous):
    membership = defaultdict(set)
    healthy = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old_feeds = previous.get("feeds") or {}

    for name, url in FEEDS:
        text, meta = fetch(url)
        if text is None:
            report["feeds"][name] = {"ok": False}
            warn("источник %s не скачался" % name)
            continue

        hosts = set()
        for line in text.splitlines():
            host = normalize(line)
            if host:
                hosts.add(host)
        mark = fingerprint(hosts)
        changed, frozen = feed_status(old_feeds.get(name), mark, today)

        for host in hosts:
            membership[host].add(name)
        healthy += 1

        report["feeds"][name] = {
            "ok": True,
            "accepted": len(hosts),
            "last_modified": meta.get("last_modified"),
            "fingerprint": mark,
            "last_changed": changed,
            "frozen_days": frozen,
        }
        note = "" if frozen < 1 else "  не менялся %d дн." % frozen
        log("  %-17s %8d записей%s" % (name, len(hosts), note))
        if frozen >= STALE_DAYS:
            warn("источник %s не менялся %d дней подряд — похоже, он замер"
                 % (name, frozen))

        # Источник может не замереть, а обвалиться: отдать остаток вместо списка.
        before = (old_feeds.get(name) or {}).get("accepted") or 0
        if before and len(hosts) < before * SHRINK_LIMIT:
            warn("источник %s отдал %d записей вместо %d — падение на %d%%"
                 % (name, len(hosts), before,
                    round((1 - len(hosts) / before) * 100)))
        del hosts
    return membership, healthy


def load_popular():
    payload, _ = fetch(TRANCO_URL, binary=True)
    if payload is None:
        return None
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        rows = archive.read(archive.namelist()[0]).decode("utf-8", "ignore").splitlines()
    popular = []
    for row in rows:
        parts = row.split(",")
        if len(parts) >= 2:
            host = normalize(parts[1])
            if host:
                popular.append(host)
    return popular


def find_platforms(hosts, rules, wildcards):
    """Суффиксы общего хостинга, встречающиеся в списках."""
    platforms = set()
    for host in hosts:
        parts = host.split(".")
        for i in range(1, len(parts)):
            candidate = ".".join(parts[i:])
            parent = ".".join(parts[i + 1:])
            if candidate in rules or (parent and parent in wildcards):
                platforms.add(candidate)
                break
    return platforms


def registrable(host, rules, wildcards):
    """Домен, который кто-то зарегистрировал, с оглядкой на co.uk и vercel.app."""
    parts = host.split(".")
    for i in range(1, len(parts)):
        candidate = ".".join(parts[i:])
        parent = ".".join(parts[i + 1:])
        if candidate in rules or (parent and parent in wildcards):
            return ".".join(parts[i - 1:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def clear_popular_subdomains(membership, protected, rules, wildcards):
    """Разбирает поддомены известных сайтов на ошибки списков и площадки."""
    groups = defaultdict(list)
    for host in membership:
        groups[registrable(host, rules, wildcards)].append(host)

    dropped, platforms = [], set()
    for parent, members in groups.items():
        if parent not in protected:
            continue
        if len(members) <= POPULAR_SUBDOMAIN_LIMIT:
            dropped.extend(members)
        else:
            platforms.add(parent)
    for host in dropped:
        del membership[host]
    return dropped, platforms


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    started = datetime.now(timezone.utc)
    report = {"built": started.strftime("%Y-%m-%d %H:%M UTC"), "feeds": {}}

    log("== источники вредоносных доменов ==")
    previous = previous_report()
    membership, healthy = collect_feeds(report, previous)
    if healthy < MIN_FEEDS:
        log("ОШИБКА: доступно источников %d, нужно минимум %d" % (healthy, MIN_FEEDS))
        return 1

    log("== список настоящих доменов ==")
    popular = load_popular()
    if popular is None:
        log("ОШИБКА: Tranco недоступен, без белого списка публиковать нельзя")
        return 1
    log("  Tranco: %d доменов" % len(popular))

    psl_text, _ = fetch(PSL_URL)
    if psl_text is None:
        log("ОШИБКА: Public Suffix List недоступен")
        return 1
    rules, wildcards, _ = parse_psl(psl_text)
    log("  PSL: %d правил" % len(rules))

    whitelist = popular[:WHITELIST_TOP]
    protected = set(whitelist)
    removed = sorted(h for h in membership if h in protected)
    for host in removed:
        del membership[host]
    log("  снято по белому списку: %d (например: %s)" % (
        len(removed), ", ".join(removed[:5])))

    dropped, busy = clear_popular_subdomains(membership, protected, rules, wildcards)
    log("  снято поддоменов известных сайтов: %d (например: %s)" % (
        len(dropped), ", ".join(sorted(dropped)[:5])))
    log("  площадок с самообслуживанием: %d (например: %s)" % (
        len(busy), ", ".join(sorted(busy)[:5])))

    malicious = list(membership)
    log("== итог: %d уникальных вредоносных хостов ==" % len(malicious))

    if len(malicious) < MIN_TOTAL:
        log("ОШИБКА: база подозрительно маленькая, публиковать не будем")
        return 1

    tld_counts = Counter(h.rsplit(".", 1)[-1] for h in malicious)
    tld_lines = ["%s %d" % (tld, count) for tld, count in tld_counts.most_common(400)]

    platforms = find_platforms(malicious, rules, wildcards) | busy
    log("  платформ общего хостинга: %d" % len(platforms))

    brands = popular[:BRAND_TOP]
    built_day = int(time.time() // 86400)

    full_blob, full_count = hash_section(malicious, FULL_BITS)
    white_blob, white_count = hash_section(whitelist, WHITE_BITS)

    # Зоны второго уровня вроде com.hk: без них google.com.hk выглядит как
    # сайт в зоне com.hk, притворяющийся Google.
    suffixes = sorted(r for r in rules if r.count(".") == 1)
    log("  зон второго уровня: %d" % len(suffixes))

    full_size = write_db(os.path.join(OUT_DIR, "full.lgdb"), built_day, [
        ("MALW", full_blob),
        ("WHIT", white_blob),
        ("BRND", "\n".join(brands).encode("utf-8")),
        ("TLDR", "\n".join(tld_lines).encode("utf-8")),
        ("PLAT", "\n".join(sorted(platforms)).encode("utf-8")),
        ("SUFX", "\n".join(suffixes).encode("utf-8")),
    ])

    report.update({
        "full": {"entries": full_count, "bytes": full_size, "hash_bits": FULL_BITS},
        "whitelist": {"entries": white_count, "hash_bits": WHITE_BITS},
        "brands": len(brands),
        "platforms": len(platforms),
        "suffixes": len(suffixes),
        "removed_by_whitelist": len(removed),
        "removed_popular_subdomains": len(dropped),
    })
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    # Заметное падение числа адресов — признак, что источник отдал обрезок.
    was = (previous.get("full") or {}).get("entries") or 0
    if was and full_count < was * 0.8:
        warn("адресов стало %d против %d в прошлый раз — падение на %d%%"
             % (full_count, was, round((1 - full_count / was) * 100)))

    frozen = sorted((info.get("frozen_days", 0), name)
                    for name, info in report["feeds"].items()
                    if info.get("frozen_days", 0) >= STALE_DAYS)
    if frozen:
        log("== замершие источники ==")
        for days, name in frozen:
            log("  %-17s не менялся %d дн." % (name, days))

    log("== файлы ==")
    log("  full.lgdb  %8.2f МБ  %d записей" % (full_size / 1048576, full_count))
    log("готово за %d с" % (datetime.now(timezone.utc) - started).seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
