"""Проверка логики Link Guard без Android-рантайма.

Android-модули плагина подменяются заглушками, после чего файл .plugin
загружается как обычный питоновский модуль и тестируется чистая логика:
разбор ссылки и вычистка трекеров.

Запуск: python tests/test_link_guard.py
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "src", "link_guard.plugin")


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class FakeDialog:

    last = None
    ALERT_TYPE_MESSAGE = 0
    ALERT_TYPE_LOADING = 1
    ALERT_TYPE_SPINNER = 2

    def __init__(self, activity, progress_style=None):
        FakeDialog.last = self
        self.title = self.message = None
        self.buttons = {}
        self.shown = False

    def set_title(self, text):
        self.title = text

    def set_message(self, text):
        self.message = text

    def set_positive_button(self, text, callback):
        self.buttons["positive"] = (text, callback)

    def set_negative_button(self, text, callback):
        self.buttons["negative"] = (text, callback)

    def set_neutral_button(self, text, callback):
        self.buttons["neutral"] = (text, callback)

    def set_cancelable(self, value):
        self.cancelable = value

    def show(self):
        self.shown = True

    def dismiss(self):
        self.shown = False

    def press(self, button="positive"):
        self.buttons[button][1](self, 0)


class FakeMethod:

    def __init__(self):
        self.calls = []

    def invoke(self, obj, *args):
        self.calls.append(args)


class FakeUri:

    def __init__(self, value):
        self.value = str(value)

    def getScheme(self):
        return self.value.split(":", 1)[0]

    def __str__(self):
        return self.value


class FakeContext:
    started = []

    def getPackageName(self):
        return "org.telegram.messenger"

    def startActivity(self, intent):
        FakeContext.started.append(intent)


class FakeParam:

    def __init__(self, url):
        self.args = [FakeContext(), url]
        self.method = FakeMethod()
        self.cancelled = False

    def setResult(self, value):
        self.cancelled = True


SENT_DOCUMENTS = []


def install_stubs():
    hooks_installed = []

    update_hooks = []

    class BasePlugin:
        def __init__(self):
            self._settings = {}
            self.installed_hooks = hooks_installed
            self.update_hooks = update_hooks
            self.reloaded = False

        def add_hook(self, name, match_substring=False, priority=0):
            update_hooks.append(name)

        def get_setting(self, key, default=None):
            return self._settings.get(key, default)

        def set_setting(self, key, value, reload_settings=False):
            self._settings[key] = value
            if reload_settings:
                self.reloaded = True

        def add_menu_item(self, data):
            return data

        def remove_menu_item(self, item):
            pass

        def add_on_send_message_hook(self, priority=0):
            pass

        def hook_all_methods(self, cls, name, handler):
            hooks_installed.append(handler)
            return ["hook-handle"]

        def unhook_method(self, handle):
            pass

    class MethodHook:
        pass

    class HookResult:
        def __init__(self, strategy=None, params=None):
            self.strategy, self.params = strategy, params

    class HookStrategy:
        DEFAULT = "default"
        CANCEL = "cancel"
        MODIFY = "modify"
        MODIFY_FINAL = "modify_final"

    class MenuItemData:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class MenuItemType:
        MESSAGE_CONTEXT_MENU = "message"

    _stub("base_plugin", BasePlugin=BasePlugin, MethodHook=MethodHook,
          HookResult=HookResult, HookStrategy=HookStrategy,
          MenuItemData=MenuItemData, MenuItemType=MenuItemType)

    row = lambda **kw: types.SimpleNamespace(**kw)
    _stub("ui", )
    _stub("ui.settings", Header=row, Switch=row, Selector=row,
          Input=row, Divider=row, Text=row)
    _stub("ui.alert", AlertDialogBuilder=FakeDialog)

    fragment = types.SimpleNamespace(getParentActivity=lambda: object())
    sent_documents = SENT_DOCUMENTS
    _stub("client_utils", get_last_fragment=lambda: fragment,
          run_on_queue=lambda fn, *a, **kw: fn(),
          send_document=lambda peer, path, caption=None: sent_documents.append((peer, path)),
          get_user_config=lambda *a: types.SimpleNamespace(getClientUserId=lambda: 42))
    _stub("file_utils", get_plugins_dir=lambda: "/tmp/plugins",
          get_cache_dir=lambda: "/tmp/cache",
          get_documents_dir=lambda: "/tmp/docs",
          ensure_dir_exists=lambda path: None,
          write_file_bytes=lambda path, data: None)
    _stub("android_utils", log=lambda *a: None, run_on_ui_thread=lambda f, d=0: f(),
          copy_to_clipboard=lambda t: None)
    def fake_find_class(name):
        if name.endswith("Uri"):
            return types.SimpleNamespace(name=name, parse=FakeUri)
        return types.SimpleNamespace(name=name)

    _stub("hook_utils", find_class=fake_find_class)


def install_minimal_stubs():
    for name in ("base_plugin", "ui", "ui.settings", "ui.alert",
                 "client_utils", "android_utils", "hook_utils"):
        sys.modules.pop(name, None)

    class BasePlugin:
        def get_setting(self, key, default=None):
            return default

    class HookResult:
        def __init__(self, strategy=None, params=None):
            self.strategy, self.params = strategy, params

    class HookStrategy:
        DEFAULT = "default"
        MODIFY = "modify"

    _stub("base_plugin", BasePlugin=BasePlugin, HookResult=HookResult,
          HookStrategy=HookStrategy)


def load_plugin(minimal=False):
    install_minimal_stubs() if minimal else install_stubs()
    import importlib.util
    spec = importlib.util.spec_from_loader("link_guard", loader=None)
    module = importlib.util.module_from_spec(spec)
    with open(PLUGIN, encoding="utf-8") as fh:
        code = fh.read()
    exec(compile(code, PLUGIN, "exec"), module.__dict__)
    return module


lg = load_plugin()

failures = []


def check(name, condition, extra=""):
    if condition:
        print("  ok   %s" % name)
    else:
        failures.append(name)
        print("  FAIL %s %s" % (name, extra))


print("\nЧистка трекеров")
url, removed = lg.clean_url(
    "https://shop.example.com/item?id=42&utm_source=tg&utm_campaign=x&fbclid=abc")
check("параметры сняты, товар остался", url == "https://shop.example.com/item?id=42", url)
check("посчитаны все три метки", len(removed) == 3, removed)

url, removed = lg.clean_url("https://example.com/page?id=1")
check("чистая ссылка не тронута", url == "https://example.com/page?id=1" and not removed)

url, _ = lg.clean_url("https://youtu.be/abc?si=track", aggressive=False)
check("si сохраняется в обычном режиме", "si=track" in url, url)
url, _ = lg.clean_url("https://youtu.be/abc?si=track", aggressive=True)
check("si снимается в агрессивном", "si=" not in url, url)

print("\nОпасные ссылки")
v = lg.analyze("https://sberbank.ru@evil-host.top/login")
check("подмена через @ — высокий риск", v.risk == lg.HIGH, v.flags)

v = lg.analyze("https://sberbamk.ru/login")
check("опечатка в бренде поймана", v.risk == lg.HIGH, v.flags)

v = lg.analyze("https://sberbank.ru.pay-secure.xyz/enter")
check("бренд в поддомене пойман", v.risk == lg.HIGH, v.flags)

v = lg.analyze("https://sbеrbank.ru/")
check("кириллическая подмена поймана", v.risk == lg.HIGH, v.flags)

v = lg.analyze("javascript:alert(1)")
check("javascript: заблокирован", v.risk == lg.HIGH, v.flags)

v = lg.analyze("https://files.example.top/telegram_premium.apk")
check("apk помечен", v.risk == lg.HIGH, v.flags)

print("\nСредний и низкий риск")
v = lg.analyze("http://185.12.34.56/login")
check("IP вместо домена", v.risk in (lg.MEDIUM, lg.HIGH), v.flags)

v = lg.analyze("https://bit.ly/abc")
check("сокращатель распознан", v.is_shortener, v.flags)

print("\nЧистые ссылки")
for good in ("https://t.me/durov",
             "https://github.com/L0lopop/Link-Guard",
             "https://www.wikipedia.org/wiki/Telegram",
             "https://ozon.ru/product/12345"):
    v = lg.analyze(good)
    check("не ругается на %s" % good, not v.suspicious, v.flags)

v = lg.analyze("https://gosuslugi.ru/login", whitelist={"gosuslugi.ru"})
check("белый список работает", not v.flags, v.flags)

v = lg.analyze("https://mail.google.com/mail/u/0/")
check("поддомен своего же бренда не ругается", not v.suspicious, v.flags)
v = lg.analyze("https://online.sberbank.ru/CSAFront/index.do")
check("настоящий банк не ругается", not v.suspicious, v.flags)

print("\nПоиск ссылок в тексте")
found = lg.URL_RE.findall("глянь https://example.com/a?utm_source=x и www.test.ru потом")
check("найдены обе ссылки", len(found) == 2, found)

print("\nЗагрузка плагина и перехват перехода")
plugin = lg.LinkGuardPlugin()
try:
    plugin.on_plugin_load()
    check("on_plugin_load отрабатывает без исключений", True)
except Exception as exc:
    check("on_plugin_load отрабатывает без исключений", False, repr(exc))

handler = plugin.installed_hooks[0] if plugin.installed_hooks else None
check("хук на openUrl установлен", handler is not None)

if handler is not None:
    param = FakeParam("https://shop.example.com/x?id=7&utm_source=tg&fbclid=zz")
    handler.before_hooked_method(param)
    check("чистая ссылка открывается без диалога", not param.cancelled)
    check("трекеры сняты прямо в аргументах",
          param.args[1] == "https://shop.example.com/x?id=7", param.args[1])

    param = FakeParam("https://sberbamk.ru/login")
    handler.before_hooked_method(param)
    check("опасный переход остановлен", param.cancelled)
    check("показан диалог с предупреждением",
          FakeDialog.last is not None and FakeDialog.last.shown
          and FakeDialog.last.title == lg.t("title_danger"),
          FakeDialog.last.title if FakeDialog.last else None)
    check("на опасной ссылке главная кнопка — отмена",
          FakeDialog.last.buttons["positive"][0] == lg.t("btn_cancel"),
          FakeDialog.last.buttons["positive"][0])
    check("переход спрятан во вторую кнопку",
          FakeDialog.last.buttons["negative"][0] == lg.t("btn_open"))
    check("доверять опасному домену одним тапом нельзя",
          "neutral" not in FakeDialog.last.buttons)

if handler is not None:
    print("\nПовторный вызов после одобрения")
    param = FakeParam("https://gosuslugi.ru.verify.cyou/enter")
    handler.before_hooked_method(param)
    FakeDialog.last.press("negative")
    check("после «Открыть» ссылка переоткрыта", len(param.method.calls) == 1,
          param.method.calls)

    nested = FakeParam("https://gosuslugi.ru.verify.cyou/enter")
    handler.before_hooked_method(nested)
    check("вложенная перегрузка не поднимает второй диалог", not nested.cancelled)

    plugin.APPROVAL_WINDOW = -1
    again = FakeParam("https://gosuslugi.ru.verify.cyou/enter")
    handler.before_hooked_method(again)
    check("по истечении окна проверка снова работает", again.cancelled)
    plugin.APPROVAL_WINDOW = 10.0

    print("\nКороткие ссылки")
    real_expand = lg.expand
    lg.expand = lambda url, timeout=6: ("https://final.example.com/promo", 2)
    param = FakeParam("https://bit.ly/xyz")
    handler.before_hooked_method(param)
    check("переход по сокращённой остановлен", param.cancelled)
    check("в разборе показан конечный адрес",
          "final.example.com" in (FakeDialog.last.message or ""),
          FakeDialog.last.message)
    lg.expand = real_expand

    print("\nСсылка без схемы")
    param = FakeParam("www.example.com?utm_source=tg")
    handler.before_hooked_method(param)
    check("схема не дописана в аргумент", param.args[1] == "www.example.com",
          param.args[1])

print("\nЛокализация")
missing_en = [k for k in lg.STRINGS["ru"] if k not in lg.STRINGS["en"]]
missing_ru = [k for k in lg.STRINGS["en"] if k not in lg.STRINGS["ru"]]
check("наборы строк совпадают по ключам", not missing_en and not missing_ru,
      missing_en + missing_ru)
saved_lang = lg.LANG
lg.LANG = "en"
check("английские строки подставляются", lg.t("btn_open") == "Open", lg.t("btn_open"))
check("подстановка аргументов работает", "42" in lg.t("f_port", 42), lg.t("f_port", 42))
lg.LANG = "xx"
check("неизвестный язык падает на английский", lg.t("btn_cancel") == "Cancel")
lg.LANG = saved_lang

print("\nПодпись ссылки не совпадает с адресом")
check("домен из подписи", lg.anchor_domain("sberbank.ru") == "sberbank.ru")
check("домен из подписи со схемой", lg.anchor_domain("https://vk.com/feed") == "vk.com")
check("обычный текст подписью не считается", lg.anchor_domain("нажми сюда") == "")
check("многоточие не домен", lg.anchor_domain("...") == "")

v = lg.analyze("https://pay-now.top/enter", anchor="sberbank.ru")
check("подмена подписи поймана", v.risk == lg.HIGH, v.flags)
v = lg.analyze("https://sberbank.ru/enter", anchor="sberbank.ru")
check("совпадающая подпись не тревожит", not v.suspicious, v.flags)
v = lg.analyze("https://online.sberbank.ru/enter", anchor="sberbank.ru")
check("поддомен того же сайта не тревожит", not v.suspicious, v.flags)
v = lg.analyze("https://example.com/a", anchor="нажми сюда")
check("текстовая подпись не тревожит", not v.suspicious, v.flags)

check("подписка на сообщения запрошена",
      any("NewMessage" in n for n in plugin.update_hooks), plugin.update_hooks)


class FakeEntity:
    def __init__(self, offset, length, url):
        self.offset, self.length, self.url = offset, length, url


class FakeEntities:
    def __init__(self, items):
        self.items = items

    def size(self):
        return len(self.items)

    def get(self, i):
        return self.items[i]


text = "Проверьте баланс на sberbank.ru прямо сейчас"
update = types.SimpleNamespace(message=types.SimpleNamespace(
    message=text,
    entities=FakeEntities([FakeEntity(text.index("sberbank.ru"), len("sberbank.ru"),
                                      "https://pay-now.top/enter")]),
))
plugin.on_update_hook("updateNewMessage", 0, update)
check("подпись из сообщения запомнена",
      plugin._anchors.get("https://pay-now.top/enter") == "sberbank.ru", plugin._anchors)

if handler is not None:
    param = FakeParam("https://pay-now.top/enter")
    handler.before_hooked_method(param)
    check("переход по подменённой подписи остановлен", param.cancelled)
    check("в разборе назван настоящий домен",
          "pay-now.top" in (FakeDialog.last.message or ""), FakeDialog.last.message)

print("\nДоверенные домены")
if handler is not None:
    param = FakeParam("https://promo.example.top/gift?bonus=1")
    handler.before_hooked_method(param)
    check("подозрительная ссылка остановлена", param.cancelled)
    FakeDialog.last.press("neutral")
    check("доверие спрашивает подтверждение",
          FakeDialog.last.title == lg.t("trust_title"), FakeDialog.last.title)
    check("до подтверждения список пуст", not plugin.get_setting("whitelist", ""),
          plugin.get_setting("whitelist", ""))

    FakeDialog.last.press("positive")
    check("отказ от подтверждения ничего не меняет",
          not plugin.get_setting("whitelist", ""), plugin.get_setting("whitelist", ""))

    handler.before_hooked_method(FakeParam("https://promo.example.top/gift?bonus=1"))
    FakeDialog.last.press("neutral")
    FakeDialog.last.press("negative")
    check("домен попал в белый список после подтверждения",
          "example.top" in plugin.get_setting("whitelist", ""),
          plugin.get_setting("whitelist", ""))

    again = FakeParam("https://promo.example.top/gift?bonus=2")
    handler.before_hooked_method(again)
    check("доверенный домен больше не тревожит", not again.cancelled)
    plugin.set_setting("whitelist", "")
    plugin._cache.clear()

print("\nСписок исключений")
for probe, expected in (("https://Example.COM/path?x=1", "example.com"),
                        ("*.example.com", "example.com"),
                        ("vk.com/feed", "vk.com"),
                        ("почта.рф", "почта.рф"),
                        ("example", ""),
                        ("не домен", "")):
    check("нормализация %r" % probe, lg.normalize_domain(probe) == expected,
          lg.normalize_domain(probe))

plugin.set_setting("whitelist", "")
plugin.set_setting("whitelist_add", "https://Shop.Example.com/catalog")
plugin._on_add_domain()
check("домен добавлен кнопкой", plugin._whitelist_list() == ["shop.example.com"],
      plugin._whitelist_list())
check("поле ввода очищено", not plugin.get_setting("whitelist_add", ""),
      plugin.get_setting("whitelist_add", ""))

plugin.set_setting("whitelist_add", "мусор")
plugin._on_add_domain()
check("мусор в список не попадает", plugin._whitelist_list() == ["shop.example.com"],
      plugin._whitelist_list())

plugin.set_setting("whitelist", "shop.example.com, ozon.ru")
rows = plugin._exception_rows()
titles = [getattr(r, "text", None) for r in rows]
check("каждый домен отдельной строкой",
      "shop.example.com" in titles and "ozon.ru" in titles, titles)
check("в конце есть кнопка добавления", lg.t("btn_add") in titles, titles)

remove = plugin._make_remove("ozon.ru")
FakeDialog.last = None
remove()
check("удаление спрашивает подтверждение",
      FakeDialog.last is not None and FakeDialog.last.title == lg.t("del_title"),
      FakeDialog.last.title if FakeDialog.last else None)
FakeDialog.last.press("positive")
check("отказ оставляет домен", "ozon.ru" in plugin._whitelist_list(), plugin._whitelist_list())
remove()
FakeDialog.last.press("negative")
check("после подтверждения домен удалён", plugin._whitelist_list() == ["shop.example.com"],
      plugin._whitelist_list())

plugin.set_setting("whitelist", "")
plugin._cache.clear()

plugin.set_setting("whitelist", "")
plugin.set_setting("whitelist_add", "ozon.ru")
plugin._on_add_domain()
add_row = [r for r in plugin._exception_rows() if getattr(r, "key", "") == "whitelist_add"][0]
check("поле ввода очищается и в отрисовке", add_row.default == "", add_row.default)
plugin.set_setting("whitelist", "")

print("\nДвойной вызов хука не задваивает счётчик")
if handler is not None:
    plugin._on_reset_stats_click()
    plugin._counted.clear()
    dirty = "https://shop.example.com/dup?utm_source=a&fbclid=b"
    handler.before_hooked_method(FakeParam(dirty))
    first = plugin._stat("stats_cleaned")
    handler.before_hooked_method(FakeParam(dirty))
    check("вторая перегрузка не считается заново",
          plugin._stat("stats_cleaned") == first == 2, (first, plugin._stat("stats_cleaned")))

    plugin._counted.clear()
    handler.before_hooked_method(FakeParam("https://shop.example.com/other?utm_source=a"))
    check("другая ссылка считается", plugin._stat("stats_cleaned") == 3,
          plugin._stat("stats_cleaned"))

print("\nРежимы показа разбора")
if handler is not None:
    plugin._cache.clear()
    plugin._counted.clear()
    weak = "https://novosti.xyz/article"
    check("слабое замечание есть, но подозрительной не считается",
          lg.analyze(weak).flags and not lg.analyze(weak).suspicious, lg.analyze(weak).flags)

    plugin.set_setting("show_mode", 0)
    param = FakeParam(weak)
    handler.before_hooked_method(param)
    check("слабое замечание поднимает разбор", param.cancelled)
    FakeDialog.last.press("positive")

    param = FakeParam("https://ozon.ru/product/1")
    handler.before_hooked_method(param)
    check("чистая ссылка открывается молча", not param.cancelled)

    plugin.set_setting("show_mode", 1)
    param = FakeParam("https://ozon.ru/product/2")
    handler.before_hooked_method(param)
    check("режим «всегда» показывает и чистую", param.cancelled)
    FakeDialog.last.press("positive")
    plugin.set_setting("show_mode", 0)

print("\nПояснения и лог")
FakeDialog.last = None
plugin._on_tags_note()
check("пояснение про метки открывается окном",
      FakeDialog.last is not None and FakeDialog.last.title == lg.t("tags_title"),
      FakeDialog.last.title if FakeDialog.last else None)
check("в окне полный текст, а не обрезок",
      "utm_source" in (FakeDialog.last.message or ""), FakeDialog.last.message)

FakeDialog.last = None
plugin._on_privacy_note()
check("«как это работает» тоже открывается окном",
      FakeDialog.last is not None and FakeDialog.last.title == lg.t("privacy_title"))

print("\nСброс счётчиков")
plugin.set_setting("stats_cleaned", 7)
plugin.set_setting("stats_warned", 3)
plugin._on_reset_stats_click()
check("оба счётчика обнулены",
      plugin._stat("stats_cleaned") == 0 and plugin._stat("stats_warned") == 0,
      (plugin._stat("stats_cleaned"), plugin._stat("stats_warned")))
check("экран настроек перерисован", plugin.reloaded, plugin.reloaded)

print("\nСчётчики и кэш")
plugin._on_reset_stats_click()
if handler is not None:
    handler.before_hooked_method(FakeParam("https://shop.example.com/x?utm_source=a&fbclid=b"))
    check("вырезанные метки посчитаны", plugin._stat("stats_cleaned") == 2,
          plugin._stat("stats_cleaned"))
    handler.before_hooked_method(FakeParam("https://sberbamk.ru/login"))
    check("предупреждения посчитаны", plugin._stat("stats_warned") == 1,
          plugin._stat("stats_warned"))

first = plugin._cached_analyze("https://example.com/cached")
first.add(lg.HIGH, "пометка только для этой копии")
second = plugin._cached_analyze("https://example.com/cached")
check("кэш отдаёт независимую копию", not second.suspicious, second.flags)
plugin.set_setting("whitelist", "example.com")
third = plugin._cached_analyze("https://example.com/cached")
check("смена настроек кэш не переиспользует", not third.flags, third.flags)
plugin.set_setting("whitelist", "")

print("\nОткрытие ссылки")
if handler is not None:
    opened = []
    plugin._browser_cls = types.SimpleNamespace(
        openUrl=lambda ctx, url: opened.append(url))
    param = FakeParam("https://promo-gift.top/bonus")
    handler.before_hooked_method(param)
    FakeDialog.last.press("positive")
    check("открытие идёт напрямую через Browser.openUrl", opened == ["https://promo-gift.top/bonus"],
          opened)
    check("повторный вызов перехваченного метода не понадобился",
          not param.method.calls, param.method.calls)

    plugin._browser_cls = types.SimpleNamespace()
    param = FakeParam("https://promo-gift.top/bonus2")
    handler.before_hooked_method(param)
    FakeDialog.last.press("positive")
    check("без Browser.openUrl работает запасной путь", len(param.method.calls) == 1,
          param.method.calls)
    plugin._browser_cls = None

print("\nОкно обновления")
info = {"version": "9.9.9", "url": "https://example.com/link_guard.plugin",
        "changelog": ["первая строка", "вторая строка"]}
check("чейнджлог списком разворачивается в пункты",
      "• первая строка" in lg.LinkGuardPlugin._format_changelog(info),
      lg.LinkGuardPlugin._format_changelog(info))
check("чейнджлог строкой тоже работает",
      "• одна строка" in lg.LinkGuardPlugin._format_changelog({"changelog": "одна строка"}))

downloads = []
plugin._download_update = lambda link, remote: downloads.append((link, remote))
FakeDialog.last = None
plugin._show_update(info, "9.9.9")
check("окно обновления показано", FakeDialog.last is not None and FakeDialog.last.shown)
check("главная кнопка — установить",
      FakeDialog.last.buttons["positive"][0] == lg.t("upd_install"))
check("вторая кнопка — позже",
      FakeDialog.last.buttons["negative"][0] == lg.t("btn_later"))
check("в тексте есть пункты чейнджлога",
      "• вторая строка" in (FakeDialog.last.message or ""), FakeDialog.last.message)

FakeDialog.last.press("positive")
check("нажатие запускает загрузку", downloads == [("https://example.com/link_guard.plugin", "9.9.9")],
      downloads)
check("кнопка ведёт на новую версию, а не на отправку файла",
      lg.t("upd_install") == "Перейти на новую версию", lg.t("upd_install"))
check("во время загрузки показан индикатор",
      FakeDialog.last is not None and FakeDialog.last.title == lg.t("upd_downloading"),
      FakeDialog.last.title if FakeDialog.last else None)
plugin._hide_progress()

print("\nУстановка обновления")
installs = []
lg.PluginsController = types.SimpleNamespace(
    getPluginEngine=lambda f: object(),
    getInstance=lambda: types.SimpleNamespace(
        showInstallDialog=lambda fragment, path, enable: installs.append((path, enable))),
)
lg.JavaFile = None
SENT_DOCUMENTS.clear()
plugin._finish_download("/tmp/plugins/link_guard_9_9_9.plugin", "",
                        "https://example.com/x.plugin", "9.9.9")
check("открывается штатный диалог установки клиента",
      installs == [("/tmp/plugins/link_guard_9_9_9.plugin", True)], installs)
check("файл в «Избранное» при этом не шлём", not SENT_DOCUMENTS, SENT_DOCUMENTS)

lg.PluginsController = None
plugin._finish_download("/tmp/plugins/link_guard_9_9_9.plugin", "",
                        "https://example.com/x.plugin", "9.9.9")
check("без установщика остаётся отправка файла", len(SENT_DOCUMENTS) == 1, SENT_DOCUMENTS)

real_send = lg.send_document
lg.send_document = None
copied = []
real_clip = lg.copy_to_clipboard
lg.copy_to_clipboard = lambda text: copied.append(text)
plugin._finish_download(None, "нет доступной папки", "https://example.com/x.plugin", "9.9.9")
check("если файл не скачался — ссылка в буфер", copied == ["https://example.com/x.plugin"], copied)
lg.send_document, lg.copy_to_clipboard = real_send, real_clip

print("\nЧистка исходящих")
plugin.set_setting("clean_outgoing", True)
params = types.SimpleNamespace(message="смотри https://example.com/a?utm_source=tg тут")
result = plugin.on_send_message_hook(0, params)
check("метка снята в исходящем", params.message == "смотри https://example.com/a тут",
      params.message)
check("возвращена стратегия MODIFY", result.strategy == lg.HookStrategy.MODIFY)

formatted = types.SimpleNamespace(
    message="жирный текст и https://example.com/a?utm_source=tg",
    entities=types.SimpleNamespace(size=lambda: 1),
)
plugin.on_send_message_hook(0, formatted)
check("сообщение с форматированием не трогаем",
      formatted.message.endswith("?utm_source=tg"), formatted.message)

print("\nИнтернационализированные домены")
puny = "почта.рф".encode("idna").decode("ascii")
v = lg.analyze("https://%s/tracking" % puny)
check("кириллический домен не считается угрозой", not v.suspicious, v.flags)
check("в разборе показано читаемое имя", "почта.рф" in v.display_host, v.display_host)

print("\nОбновления")
check("1.10.0 новее 1.9.9", lg.parse_version("1.10.0") > lg.parse_version("1.9.9"))
check("мусор в номере не ломает разбор", lg.parse_version("v2.0-beta") == (2, 0, 0))

real_fetch = lg.fetch_update_info
lg.fetch_update_info = lambda timeout=8: {
    "version": "9.9.9", "changelog": "тестовый выпуск",
    "url": "https://example.com/link_guard.plugin",
}
FakeDialog.last = None
plugin._check_updates(manual=True)
check("новая версия предложена",
      FakeDialog.last is not None and "9.9.9" in (FakeDialog.last.title or ""),
      FakeDialog.last.title if FakeDialog.last else None)

FakeDialog.last = None
plugin._check_updates(manual=False)
check("версия запомнена для показа при следующем запуске",
      plugin.get_setting("update_version", "") == "9.9.9",
      plugin.get_setting("update_version", ""))
FakeDialog.last = None
plugin._maybe_check_updates()
check("при запуске окно показывается снова, пока не обновились",
      FakeDialog.last is not None and "9.9.9" in (FakeDialog.last.title or ""),
      FakeDialog.last.title if FakeDialog.last else None)
plugin.set_setting("update_version", "")

lg.fetch_update_info = lambda timeout=8: {"version": "0.0.1"}
FakeDialog.last = None
plugin._check_updates(manual=True)
check("старая версия в репозитории игнорируется", FakeDialog.last is None)

lg.fetch_update_info = lambda timeout=8: None
plugin._check_updates(manual=True)
check("недоступный репозиторий не роняет плагин", True)
lg.fetch_update_info = real_fetch

print("\nПравила из репозитория")
check("мусор в списки не попадает",
      lg.sanitize_rules_list(["ok.example", 42, "", "с пробелом", "x" * 200]) == ["ok.example"],
      lg.sanitize_rules_list(["ok.example", 42, "", "с пробелом", "x" * 200]))
check("не список — пустой результат", lg.sanitize_rules_list("строка") == [])

before_brands = len(lg.BRANDS)
added = lg.apply_rules({"version": 7, "brands": ["novyibank.ru"],
                        "trackers": ["newclid"], "bait": ["razblokirovka"],
                        "tracker_prefixes": ["zz_"], "risky_tld": ["bogus"]})
check("правила добавили записи", added >= 5, added)
check("версия правил запомнена", lg.RULES_VERSION == 7, lg.RULES_VERSION)
check("встроенные бренды не потерялись", len(lg.BRANDS) == before_brands + 1)
check("новый бренд участвует в проверке",
      lg.analyze("https://novyibank.ru.pay.top/enter").risk == lg.HIGH,
      lg.analyze("https://novyibank.ru.pay.top/enter").flags)
check("новый трекер вырезается",
      lg.clean_url("https://shop.ru/x?newclid=1")[0] == "https://shop.ru/x",
      lg.clean_url("https://shop.ru/x?newclid=1"))
check("новый префикс вырезается",
      lg.clean_url("https://shop.ru/x?zz_source=a")[0] == "https://shop.ru/x")

check("битые правила ничего не ломают", lg.apply_rules("не словарь") == 0)
check("после мусора списки целы", "novyibank.ru" in lg.BRANDS)

import json as _json
with open(os.path.join(ROOT, "rules.json"), encoding="utf-8") as fh:
    shipped = _json.load(fh)
check("rules.json в репозитории разбирается",
      isinstance(shipped, dict) and shipped["version"] >= 1)
check("в нём только списки и служебные поля",
      all(isinstance(v, (list, int, str)) for v in shipped.values()), list(shipped))

print("\nВозраст домена")
v = lg.analyze("https://pay-now.top/enter")
lg.add_age_flag(v, 3)
check("свежий домен поднимает риск", v.risk == lg.HIGH, v.flags)
check("возраст сохранён в вердикте", v.age_days == 3)
v2 = lg.analyze("https://ozon.ru/product/1")
lg.add_age_flag(v2, 60)
check("домен постарше — лишь замечание", v2.risk == lg.LOW, v2.flags)
v3 = lg.analyze("https://ozon.ru/product/2")
lg.add_age_flag(v3, 4000)
check("старый домен не тревожит", not v3.flags, v3.flags)
v4 = lg.analyze("https://ozon.ru/product/3")
lg.add_age_flag(v4, None)
check("неизвестный возраст ничего не добавляет", not v4.flags)

print("\nИсточник ссылки")
if handler is not None:
    plugin._cache.clear()
    plugin.set_setting("show_mode", 0)
    weak = "https://rasprodaja.xyz/sale"
    check("у ссылки есть слабое замечание",
          lg.analyze(weak).flags and not lg.analyze(weak).suspicious, lg.analyze(weak).flags)

    plugin._sources[weak] = "trusted"
    param = FakeParam(weak)
    handler.before_hooked_method(param)
    check("от контакта слабое замечание не тревожит", not param.cancelled)

    plugin._sources[weak] = "unknown"
    plugin._cache.clear()
    param = FakeParam(weak)
    handler.before_hooked_method(param)
    check("из чужого чата — показываем разбор", param.cancelled)
    check("в разборе сказано, откуда ссылка",
          lg.t("src_stranger") in (FakeDialog.last.message or ""), FakeDialog.last.message)
    FakeDialog.last.press("positive")

    danger = "https://sberbank.ru@phish.top/login"
    plugin._sources[danger] = "trusted"
    plugin._cache.clear()
    param = FakeParam(danger)
    handler.before_hooked_method(param)
    check("опасное тревожит даже от контакта", param.cancelled)
    FakeDialog.last.press("negative")
    plugin._sources.clear()

fake_message = types.SimpleNamespace(
    out=False,
    peer_id=types.SimpleNamespace(channel_id=555),
    from_id=None,
    message="держи https://kanal.example/promo",
    entities=None,
)
check("сообщение из канала считается чужим",
      lg.LinkGuardPlugin._message_source(fake_message) == "unknown")
check("своё сообщение считается доверенным",
      lg.LinkGuardPlugin._message_source(types.SimpleNamespace(out=True)) == "trusted")

plugin._sources.clear()
plugin._index_source(fake_message, fake_message.message)
check("источник запомнен для ссылки из сообщения",
      plugin._sources.get("https://kanal.example/promo") == "unknown", plugin._sources)

print("\nСовместимость со старым SDK")
try:
    lite = load_plugin(minimal=True)
    check("плагин загружается без ui/меню/хуков", True)
    check("настройки деградируют в пустой список",
          lite.LinkGuardPlugin().create_settings() == [])
    check("анализ работает и там", lite.analyze("https://sberbamk.ru/").risk == lite.HIGH)
except Exception as exc:
    check("плагин загружается без ui/меню/хуков", False, repr(exc))

print()
if failures:
    print("Провалено проверок: %d -> %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("Все проверки пройдены.")
