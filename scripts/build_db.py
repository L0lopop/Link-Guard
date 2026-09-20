
import csv
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
    ("phishunt", "https://phishunt.io/feed.txt"),
    ("phishdb-links", "https://raw.githubusercontent.com/Phishing-Database/Phishing.Database/master/phishing-links-ACTIVE.txt"),
]

KEYED_FEEDS = [
    ("urlhaus-full", "https://urlhaus.abuse.ch/downloads/text/",
     "ABUSE_CH_KEY", "Auth-Key", None),
    ("threatfox", "https://threatfox.abuse.ch/export/csv/full/",
     "ABUSE_CH_KEY", "Auth-Key", 2),
    ("phishtank", "http://data.phishtank.com/data/%s/online-valid.csv",
     "PHISHTANK_KEY", None, 1),
]

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"

FRESH_FEEDS = (
    ("FR10", "https://raw.githubusercontent.com/cenk/nrd/main/nrd-last-10-days.txt"),
    ("FR30", "https://raw.githubusercontent.com/cenk/nrd/main/nrd-last-30-days.txt"),
)

ZONE_MIN_BAD = 200
ZONE_BAD = 25
ZONE_WORST = 50
ZONE_REPORT = 40

BRAND_MARKERS = (
    "sber", "tinkoff", "tbank", "vtb", "alfabank", "alfa-bank", "gosuslug",
    "wildberries", "ozon", "yandex", "mailru", "mail-ru", "vkontakte", "vk-com",
    "telegram", "tgram", "whatsapp", "binance", "bybit", "metamask", "paypal",
    "apple", "icloud", "google", "microsoft", "outlook", "netflix", "steam",
    "roblox", "epicgames", "amazon", "aliexpress", "dns-shop", "mvideo",
    "eldorado", "rzd", "aeroflot", "pochta", "avito", "drom", "coinbase",
    "trustwallet", "ledger", "tronlink", "instagram", "facebook", "tiktok",
    "discord", "twitch", "raiffeisen", "psbank", "gazprombank", "sovcombank",
    "yoomoney", "sbermarket", "samokat", "vkusvill", "rutube", "citilink",
    "lamoda", "sportmaster", "megafon", "beeline", "rostelecom",
)

BAIT_MARKERS = (
    "oplat", "dostavk", "posylk", "shtraf", "vozvrat", "viplat", "vyplat",
    "kompensac", "podtverd", "razblok", "verifik", "bonus", "prize", "podarok",
    "winner", "login", "signin", "sign-in", "secure", "account", "wallet",
    "airdrop", "claim", "support", "update", "confirm", "recovery", "unlock",
    "refund", "invoice", "payment", "banking", "verify", "restore",
)

WHITELIST_TOP = 200000
PROTECT_TOP = 50000
BRAND_TOP = 1000

FULL_BITS = 40
WHITE_BITS = 36

MIN_FEEDS = 3
MIN_TOTAL = 500000

PREVIOUS_MANIFEST_URL = ("https://github.com/L0lopop/Link-Guard/releases/"
                         "download/feeds/manifest.json")
STALE_DAYS = 5
SHRINK_LIMIT = 0.5

POPULAR_SUBDOMAIN_LIMIT = 20

SERVICE_LABELS = frozenset((
    "raw", "gist", "media", "storage", "cdn", "static", "files", "file",
    "assets", "asset", "objects", "object", "content", "usercontent",
    "download", "downloads", "img", "images", "image", "upload", "uploads",
    "attachment", "attachments", "release", "releases", "blob", "blobs",
    "s3", "public", "cache", "edge", "stream", "video", "photo", "photos",
))

BLOCK_SIZE = 256
INDEX_ENTRY = 12

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-_]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-_]*[a-z0-9])?)+$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
STRIP_PREFIXES = ("0.0.0.0 ", "127.0.0.1 ", "0.0.0.0\t", "127.0.0.1\t", "||", "address=/")


def log(msg):
    print(msg, flush=True)


def warn(msg):
    log("ВНИМАНИЕ: %s" % msg)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::warning::%s" % msg, flush=True)


def fetch(url, binary=False, headers=None):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            sending = {"User-Agent": UA}
            if headers:
                sending.update(headers)
            req = urllib.request.Request(url, headers=sending)
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
    text, _ = fetch(PREVIOUS_MANIFEST_URL)
    if text is None:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def fingerprint(hosts):
    digest = hashlib.sha256()
    for host in sorted(hosts):
        digest.update(host.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def feed_status(previous, mark, today):
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


def unpack(payload):
    if payload is None:
        return None
    if payload[:2] != b"PK":
        return payload.decode("utf-8", "ignore")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if not names:
                return None
            return archive.read(names[0]).decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return None


def hosts_from(text, column=None):
    found = set()
    if column is None:
        for line in text.splitlines():
            host = normalize(line)
            if host:
                found.add(host)
        return found
    rows = csv.reader(
        [line for line in text.splitlines()
         if line.strip() and not line.startswith("#")],
        skipinitialspace=True)
    for row in rows:
        if len(row) > column:
            host = normalize(row[column])
            if host:
                found.add(host)
    return found


def keyed_sources():
    sources = []
    for name, url, variable, header, column in KEYED_FEEDS:
        key = os.environ.get(variable, "").strip()
        if not key:
            log("  %-17s пропущен: нет ключа %s" % (name, variable))
            continue
        if header:
            sources.append((name, url, {header: key}, column))
        else:
            sources.append((name, url % key, None, column))
    return sources


def collect_feeds(report, previous):
    membership = defaultdict(set)
    healthy = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old_feeds = previous.get("feeds") or {}

    plain = [(name, url, None, None) for name, url in FEEDS]
    for name, url, headers, column in plain + keyed_sources():
        payload, meta = fetch(url, binary=True, headers=headers)
        text = unpack(payload)
        if text is None:
            report["feeds"][name] = {"ok": False}
            warn("источник %s не скачался" % name)
            continue

        hosts = hosts_from(text, column)
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


def zone_levels(bad, good):
    levels = {}
    for zone, count in bad.items():
        if count < ZONE_MIN_BAD:
            continue
        ratio = count / (good.get(zone, 0) + 1)
        if ratio >= ZONE_WORST:
            levels[zone] = 3
        elif ratio >= ZONE_BAD:
            levels[zone] = 2
    return levels


def fresh_hosts(url, skip):
    text, _ = fetch(url)
    if text is None:
        return None
    picked = set()
    for line in text.splitlines():
        host = normalize(line)
        if not host or host in skip:
            continue
        if (any(m in host for m in BRAND_MARKERS)
                or any(m in host for m in BAIT_MARKERS)):
            picked.add(host)
    return picked


def find_platforms(hosts, rules, wildcards):
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
    parts = host.split(".")
    for i in range(1, len(parts)):
        candidate = ".".join(parts[i:])
        parent = ".".join(parts[i + 1:])
        if candidate in rules or (parent and parent in wildcards):
            return ".".join(parts[i - 1:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def popular_ancestor(host, protected):
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in protected:
            return candidate
    return None


def service_labels_only(prefix):
    pieces = []
    for label in prefix.split("."):
        pieces.extend(part for part in label.split("-") if part)
    return bool(pieces) and all(part in SERVICE_LABELS for part in pieces)


def clear_service_hosts(membership, protected):
    dropped = []
    for host in membership:
        parent = popular_ancestor(host, protected)
        if not parent:
            continue
        if service_labels_only(host[:len(host) - len(parent) - 1]):
            dropped.append(host)
    for host in dropped:
        del membership[host]
    return dropped


def clear_popular_subdomains(membership, protected, rules, wildcards):
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
    protected = set(popular[:PROTECT_TOP])
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

    service = clear_service_hosts(membership, protected)
    log("  снято служебных адресов известных сайтов: %d (например: %s)" % (
        len(service), ", ".join(sorted(service)[:5])))

    malicious = list(membership)
    log("== итог: %d уникальных вредоносных хостов ==" % len(malicious))

    if len(malicious) < MIN_TOTAL:
        log("ОШИБКА: база подозрительно маленькая, публиковать не будем")
        return 1

    bad_zones = Counter(h.rsplit(".", 1)[-1] for h in malicious)
    good_zones = Counter(h.rsplit(".", 1)[-1] for h in popular)
    levels = zone_levels(bad_zones, good_zones)
    tld_lines = ["%s %d" % (zone, level) for zone, level in sorted(levels.items())]
    worst = sorted(((bad_zones[z] / (good_zones.get(z, 0) + 1)), z)
                   for z in levels if levels[z] == 3)
    log("  зон с дурной репутацией: %d, из них худших: %d" % (
        len(levels), len(worst)))
    log("    %s" % ", ".join("%s (%.0f к 1)" % (z, r)
                             for r, z in sorted(worst, reverse=True)[:8]))

    platforms = find_platforms(malicious, rules, wildcards) | busy
    log("  платформ общего хостинга: %d" % len(platforms))

    log("== свежерегистрированные домены ==")
    skip = set(malicious) | protected
    fresh = {}
    for tag, url in FRESH_FEEDS:
        picked = fresh_hosts(url, skip)
        if picked is None:
            warn("список свежих доменов %s не скачался" % tag)
            fresh[tag] = set()
            continue
        fresh[tag] = picked
        log("  %s: %d с брендом или приманкой в имени" % (tag, len(picked)))
    fresh["FR30"] -= fresh["FR10"]

    brands = popular[:BRAND_TOP]
    built_day = int(time.time() // 86400)

    full_blob, full_count = hash_section(malicious, FULL_BITS)
    white_blob, white_count = hash_section(whitelist, WHITE_BITS)
    fresh10_blob, fresh10_count = hash_section(fresh["FR10"], FULL_BITS)
    fresh30_blob, fresh30_count = hash_section(fresh["FR30"], FULL_BITS)

    suffixes = sorted(r for r in rules if r.count(".") == 1)
    log("  зон второго уровня: %d" % len(suffixes))

    full_size = write_db(os.path.join(OUT_DIR, "full.lgdb"), built_day, [
        ("MALW", full_blob),
        ("WHIT", white_blob),
        ("BRND", "\n".join(brands).encode("utf-8")),
        ("TLDR", "\n".join(tld_lines).encode("utf-8")),
        ("PLAT", "\n".join(sorted(platforms)).encode("utf-8")),
        ("SUFX", "\n".join(suffixes).encode("utf-8")),
        ("FR10", fresh10_blob),
        ("FR30", fresh30_blob),
    ])

    zone_report = []
    for zone, level in sorted(levels.items(),
                              key=lambda kv: -bad_zones[kv[0]] / (good_zones.get(kv[0], 0) + 1)):
        zone_report.append("%s level=%d плохих=%d известных=%d" % (
            zone, level, bad_zones[zone], good_zones.get(zone, 0)))
        if len(zone_report) >= ZONE_REPORT:
            break

    report.update({
        "full": {"entries": full_count, "bytes": full_size, "hash_bits": FULL_BITS},
        "whitelist": {"entries": white_count, "hash_bits": WHITE_BITS},
        "fresh_10_days": fresh10_count,
        "fresh_30_days": fresh30_count,
        "brands": len(brands),
        "platforms": len(platforms),
        "suffixes": len(suffixes),
        "zones": len(levels),
        "worst_zones": zone_report,
        "removed_by_whitelist": len(removed),
        "removed_popular_subdomains": len(dropped),
        "removed_service_hosts": len(service),
    })
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

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
