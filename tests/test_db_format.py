"""Проверка формата базы доменов.

Читалка здесь повторяет ту, что уйдёт в плагин: если тест проходит,
значит на устройстве файл прочитается так же.

Запуск: python tests/test_db_format.py [путь к каталогу с базой]
"""

import hashlib
import os
import struct
import sys
import time

MAGIC = b"LGDB"
FORMAT_VERSION = 1
HEADER = len(MAGIC) + 6
INDEX_ENTRY = 12

checks = 0
failures = []


def check(condition, title):
    global checks
    checks += 1
    if not condition:
        failures.append(title)
        print("  ПРОВАЛ  %s" % title)


class Database(object):
    """Читает файл базы и отвечает, есть ли в ней имя хоста."""

    def __init__(self, blob):
        if not blob.startswith(MAGIC):
            raise ValueError("не наш файл")
        version, self.built_day, count = struct.unpack(">BIB", blob[4:HEADER])
        if version != FORMAT_VERSION:
            raise ValueError("версия формата %d не поддерживается" % version)
        self.sections = {}
        offset = HEADER
        for _ in range(count):
            tag = blob[offset:offset + 4].decode("ascii")
            length = struct.unpack(">I", blob[offset + 4:offset + 8])[0]
            self.sections[tag] = blob[offset + 8:offset + 8 + length]
            offset += 8 + length
        self.platforms = self._lines("PLAT")
        self.brands = self._lines("BRND")

    def _lines(self, tag):
        raw = self.sections.get(tag)
        if not raw:
            return set()
        return set(raw.decode("utf-8").split("\n"))

    def tld_ranks(self):
        ranks = {}
        for line in self.sections.get("TLDR", b"").decode("utf-8").split("\n"):
            if " " in line:
                tld, count = line.rsplit(" ", 1)
                ranks[tld] = int(count)
        return ranks

    def has(self, tag, host):
        raw = self.sections.get(tag)
        if not raw:
            return False
        bits, count, block, index_count = struct.unpack(">BIHI", raw[:11])
        if not count:
            return False
        digest = hashlib.sha256(host.encode("utf-8")).digest()[:8]
        key = int.from_bytes(digest, "big") >> (64 - bits)

        index_at = 11
        data_at = index_at + index_count * INDEX_ENTRY

        # Находим последний блок, первое значение которого не больше искомого.
        low, high = 0, index_count
        while low < high:
            middle = (low + high) // 2
            spot = index_at + middle * INDEX_ENTRY
            if struct.unpack(">Q", raw[spot:spot + 8])[0] <= key:
                low = middle + 1
            else:
                high = middle
        if low == 0:
            return False
        spot = index_at + (low - 1) * INDEX_ENTRY
        value, start = struct.unpack(">QI", raw[spot:spot + INDEX_ENTRY])
        if value == key:
            return True

        position = data_at + start
        limit = len(raw)
        seen = 0
        # Первая запись блока записана нулевой разницей, её уже проверили.
        while seen < block and position < limit:
            shift = 0
            delta = 0
            while True:
                byte = raw[position]
                position += 1
                delta |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            if seen:
                value += delta
                if value == key:
                    return True
                if value > key:
                    return False
            seen += 1
        return False

    def is_malicious(self, host):
        return self.has("MALW", host)

    def is_popular(self, host):
        return self.has("WHIT", host)

    def chain(self, host):
        """Имя хоста и его родители, но не выше платформы общего хостинга."""
        parts = host.split(".")
        result = []
        for i in range(len(parts) - 1):
            candidate = ".".join(parts[i:])
            result.append(candidate)
            if candidate in self.platforms:
                break
        return result

    def verdict(self, host):
        host = host.lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        if self.is_popular(host):
            return "popular"
        for candidate in self.chain(host):
            if self.is_malicious(candidate):
                return "malicious"
        return "unknown"


def main():
    where = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("TEMP", "/tmp"), "lgdb_test")
    core_path = os.path.join(where, "core.lgdb")
    full_path = os.path.join(where, "full.lgdb")
    if not os.path.exists(core_path):
        print("нет файла %s — сначала python scripts/build_db.py" % core_path)
        return 1

    print("== чтение файлов ==")
    core = Database(open(core_path, "rb").read())
    full = Database(open(full_path, "rb").read())
    check(set(core.sections) == {"MALW", "WHIT", "BRND", "TLDR", "PLAT"},
          "в ядре все пять разделов")
    check(core.built_day > 20000, "дата сборки записана")
    check(len(core.brands) == 1000, "тысяча брендов на месте")
    check(len(core.platforms) > 1000, "платформы общего хостинга собраны")
    print("  разделы: %s" % ", ".join(sorted(core.sections)))
    print("  платформ: %d, брендов: %d" % (len(core.platforms), len(core.brands)))

    print("== мусор не ломает читалку ==")
    for broken, title in (
        (b"", "пустой файл"),
        (b"XXXX", "чужая подпись"),
        (MAGIC + struct.pack(">BIB", 99, 20000, 0), "чужая версия формата"),
    ):
        try:
            Database(broken)
            check(False, title + " отвергается")
        except (ValueError, struct.error):
            check(True, title + " отвергается")

    print("== популярные сайты не считаются опасными ==")
    for host in ("google.com", "github.com", "dropbox.com", "vk.com",
                 "telegram.org", "youtube.com", "wikipedia.org", "sberbank.ru"):
        verdict = full.verdict(host)
        check(verdict == "popular", "%s -> popular (получили %s)" % (host, verdict))

    print("== платформы общего хостинга целиком не блокируются ==")
    for host in ("vercel.app", "pages.dev", "github.io", "weebly.com",
                 "000webhostapp.com", "duckdns.org"):
        check(not full.is_malicious(host), "%s не помечен целиком" % host)

    print("== известные вредоносные находятся ==")
    sample = [
        "0-ilxrc-w285.p9bckp.sbs",
        "0016846261.com",
        "00000.uno",
    ]
    found = sum(1 for host in sample if full.verdict(host) == "malicious")
    check(found >= 2, "нашлось %d из %d образцов" % (found, len(sample)))

    print("== родительский домен ловится ==")
    # Если в базе есть evil.tld, то и его поддомен должен считаться опасным.
    parent = None
    for host in sample:
        if full.is_malicious(host) and host.count(".") == 1:
            parent = host
            break
    if parent:
        child = "login." + parent
        check(full.verdict(child) == "malicious", "%s -> malicious" % child)
    else:
        check(True, "образца для проверки родителя не нашлось")

    print("== выдуманные домены не срабатывают ==")
    misses = sum(1 for i in range(20000)
                 if full.is_malicious("proverka-%d-net.example" % i))
    check(misses == 0, "ложных срабатываний на 20000 выдуманных: %d" % misses)

    print("== выборка посещаемых сайтов не тревожит ==")
    # Список брендов в базе — это первая тысяча по посещаемости.
    # Ни один из них не должен считаться мошенническим.
    alarms = [host for host in full.brands if full.verdict(host) != "popular"]
    check(not alarms, "тревог на тысяче посещаемых сайтов: %d %s" % (
        len(alarms), alarms[:5]))

    # То же для типичных адресов, какие присылают в переписке.
    everyday = [
        "youtube.com/watch", "t.me/durov", "github.com/torvalds/linux",
        "ru.wikipedia.org/wiki/Кот", "market.yandex.ru", "avito.ru/moskva",
        "ozon.ru/product/123", "wildberries.ru/catalog", "vk.com/feed",
        "mail.google.com", "docs.google.com/document", "web.telegram.org",
        "habr.com/ru/articles", "stackoverflow.com/questions", "dzen.ru",
    ]
    noisy = [u for u in everyday if full.verdict(u.split("/")[0]) == "malicious"]
    check(not noisy, "тревог на обычных адресах: %s" % noisy)

    print("== скорость ==")
    probes = ["proverka-%d.example.com" % i for i in range(3000)]
    started = time.time()
    for host in probes:
        full.verdict(host)
    per_call = (time.time() - started) / len(probes) * 1e6
    print("  %.0f мкс на проверку по полной базе" % per_call)
    check(per_call < 2000, "проверка укладывается в 2 мс")

    ranks = full.tld_ranks()
    print("  худшие зоны: %s" % ", ".join(
        "%s(%d)" % (t, c) for t, c in sorted(
            ranks.items(), key=lambda kv: -kv[1])[:5]))
    check(ranks.get("xyz", 0) > 10000, "рейтинг зон посчитан")

    print()
    if failures:
        print("ПРОВАЛЕНО %d из %d" % (len(failures), checks))
        for title in failures:
            print("  - %s" % title)
        return 1
    print("Все %d проверок пройдены" % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
