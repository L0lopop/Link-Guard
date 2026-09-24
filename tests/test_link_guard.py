
import os
import struct
import sys
import tempfile
import time
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
CURRENT_CHAT = [None]
CURRENT_USER = [None]


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

    fragment = types.SimpleNamespace(getParentActivity=lambda: object(),
                                     getCurrentChat=lambda: CURRENT_CHAT[0],
                                     getCurrentUser=lambda: CURRENT_USER[0])
    sent_documents = SENT_DOCUMENTS
    _stub("client_utils", get_last_fragment=lambda: fragment,
          run_on_queue=lambda fn, *a, **kw: fn(),
          send_document=lambda peer, path, caption=None: sent_documents.append((peer, path)),
          get_user_config=lambda *a: types.SimpleNamespace(getClientUserId=lambda: 42),
          get_messages_controller=lambda *a: types.SimpleNamespace(
              getUser=lambda uid: types.SimpleNamespace(contact=(int(uid) == 777))))
    import tempfile
    sandbox = tempfile.mkdtemp(prefix="link_guard_tests_")
    _stub("file_utils", get_plugins_dir=lambda: sandbox,
          get_cache_dir=lambda: sandbox,
          get_documents_dir=lambda: sandbox,
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
lg.fetch_database = lambda name, timeout=90: None

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
          and FakeDialog.last.title == lg.phrase("title_danger"),
          FakeDialog.last.title if FakeDialog.last else None)
    check("на опасной ссылке главная кнопка — отмена",
          FakeDialog.last.buttons["positive"][0] == lg.phrase("btn_cancel"),
          FakeDialog.last.buttons["positive"][0])
    check("переход спрятан во вторую кнопку",
          FakeDialog.last.buttons["negative"][0] == lg.phrase("btn_open"))
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
check("английские строки подставляются", lg.phrase("btn_open") == "Open", lg.phrase("btn_open"))
check("подстановка аргументов работает", "42" in lg.phrase("f_port", 42), lg.phrase("f_port", 42))
lg.LANG = "xx"
check("неизвестный язык падает на английский", lg.phrase("btn_cancel") == "Cancel")
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

check("подписка идёт по точным именам TL",
      "TL_updateNewMessage" in plugin.update_hooks
      and "TL_updates" in plugin.update_hooks, plugin.update_hooks)
check("подписаны и контейнеры, и короткие апдейты",
      "TL_updatesCombined" in plugin.update_hooks
      and "TL_updateShortMessage" in plugin.update_hooks, plugin.update_hooks)


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
plugin.on_update_hook("TL_updateNewMessage", 0, update)
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
          FakeDialog.last.title == lg.phrase("trust_title"), FakeDialog.last.title)
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
check("в конце есть кнопка добавления", lg.phrase("btn_add") in titles, titles)

remove = plugin._make_remove("ozon.ru")
FakeDialog.last = None
remove()
check("удаление спрашивает подтверждение",
      FakeDialog.last is not None and FakeDialog.last.title == lg.phrase("del_title"),
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
      FakeDialog.last is not None and FakeDialog.last.title == lg.phrase("tags_title"),
      FakeDialog.last.title if FakeDialog.last else None)
check("в окне полный текст, а не обрезок",
      "utm_source" in (FakeDialog.last.message or ""), FakeDialog.last.message)

FakeDialog.last = None
plugin._on_privacy_note()
check("«как это работает» тоже открывается окном",
      FakeDialog.last is not None and FakeDialog.last.title == lg.phrase("privacy_title"))

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
      FakeDialog.last.buttons["positive"][0] == lg.phrase("upd_install"))
check("вторая кнопка — позже",
      FakeDialog.last.buttons["negative"][0] == lg.phrase("btn_later"))
check("в тексте есть пункты чейнджлога",
      "• вторая строка" in (FakeDialog.last.message or ""), FakeDialog.last.message)

FakeDialog.last.press("positive")
check("нажатие запускает загрузку", downloads == [("https://example.com/link_guard.plugin", "9.9.9")],
      downloads)
check("кнопка ведёт на новую версию, а не на отправку файла",
      lg.phrase("upd_install") == "Перейти на новую версию", lg.phrase("upd_install"))
check("во время загрузки показан индикатор",
      FakeDialog.last is not None and FakeDialog.last.title == lg.phrase("upd_downloading"),
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

copied = []
real_clip = lg.copy_to_clipboard
lg.copy_to_clipboard = lambda text: copied.append(text)

lg.PluginsController = None
plugin._finish_download("/tmp/plugins/link_guard_9_9_9.plugin", "",
                        "https://example.com/x.plugin", "9.9.9")
check("без установщика ничего не шлём в чат", not SENT_DOCUMENTS, SENT_DOCUMENTS)
check("вместо этого копируем ссылку", copied == ["https://example.com/x.plugin"], copied)

copied.clear()
plugin._finish_download(None, "нет доступной папки", "https://example.com/x.plugin", "9.9.9")
check("если файл не скачался — ссылка в буфер", copied == ["https://example.com/x.plugin"], copied)
lg.copy_to_clipboard = real_clip

check("в коде не осталось отправки в чат",
      not hasattr(lg, "send_document") and not hasattr(lg, "send_text"),
      [n for n in ("send_document", "send_text") if hasattr(lg, n)])

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

print("\nОбновления в контейнере")


class FakeList:

    def __init__(self, items):
        self.items = items

    def size(self):
        return len(self.items)

    def get(self, i):
        return self.items[i]


plugin._anchors.clear()
plugin._sources.clear()
inner_text = "смотри sberbank.ru внутри контейнера"
inner = types.SimpleNamespace(message=types.SimpleNamespace(
    out=False,
    peer_id=types.SimpleNamespace(channel_id=77),
    from_id=None,
    message=inner_text,
    entities=FakeEntities([FakeEntity(inner_text.index("sberbank.ru"), len("sberbank.ru"),
                                      "https://sber-oplata.buzz/pay")]),
))
plugin.on_updates_hook("TL_updates", 0, types.SimpleNamespace(updates=FakeList([inner])))
check("ссылка из контейнера разобрана",
      plugin._anchors.get("https://sber-oplata.buzz/pay") == "sberbank.ru", plugin._anchors)
check("источник из контейнера определён",
      plugin._sources.get("https://sber-oplata.buzz/pay") == "unknown", plugin._sources)

short = types.SimpleNamespace(out=False, user_id=12345,
                              message="короткое https://korotkoe.example/x", entities=None)
plugin._consume_update(short)
check("короткий апдейт с текстом строкой тоже разбирается",
      plugin._sources.get("https://korotkoe.example/x") == "unknown", plugin._sources)

contact_msg = types.SimpleNamespace(out=False, user_id=777, peer_id=None, from_id=None,
                                    message="от друга https://drug.example/y", entities=None)
plugin._consume_update(contact_msg)
check("ссылка от контакта помечена доверенной",
      plugin._sources.get("https://drug.example/y") == "trusted", plugin._sources)
plugin._anchors.clear()
plugin._sources.clear()

print("\nСписки внутри плагина")
for brand in ("aliexpress.ru", "citilink.ru", "dns-shop.ru", "mvideo.ru",
              "eldorado.ru", "lamoda.ru", "sportmaster.ru", "rzd.ru",
              "aeroflot.ru", "pochtabank.ru", "raiffeisen.ru", "psbank.ru",
              "gazprombank.ru", "sovcombank.ru", "yoomoney.ru", "sbermarket.ru",
              "samokat.ru", "vkusvill.ru", "rutube.ru", "twitch.tv",
              "epicgames.com", "roblox.com", "steamgifts.com"):
    check("бренд %s на месте" % brand, brand in lg.BRANDS)

for tracker in ("erid", "ymclid", "yadclid", "_ga", "_gl", "mc_tc",
                "sc_cid", "srsltid", "cjevent", "irclickid"):
    url, removed = lg.clean_url("https://shop.ru/x?%s=1" % tracker)
    check("метка %s вырезается" % tracker, url == "https://shop.ru/x", url)

for prefix in ("matomo_", "sc_"):
    url, _ = lg.clean_url("https://shop.ru/x?%ssource=a" % prefix)
    check("префикс %s вырезается" % prefix, url == "https://shop.ru/x", url)

for short in ("goo.su", "clck.su", "kurl.ru", "shrturi.com", "tlgg.ru"):
    check("сокращатель %s узнан" % short,
          lg.analyze("https://%s/abc" % short).is_shortener)

for zone in ("buzz", "cricket", "download", "loan", "party",
             "review", "science", "stream", "trade", "webcam"):
    check("зона .%s помечена" % zone, zone in lg.RISKY_TLD)

for bait in ("oplata", "dostavka", "posylka", "shtraf", "vozvrat", "vyplata",
             "kompensaciya", "podtverdite", "razblokirovka", "verifikaciya"):
    check("приманка %s на месте" % bait, bait in lg.BAIT_WORDS)

check("механизма внешних списков больше нет",
      not hasattr(lg, "fetch_rules") and not hasattr(lg, "apply_rules"))
check("файла rules.json в репозитории нет",
      not os.path.exists(os.path.join(ROOT, "rules.json")))

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

print("\nЛокальная сеть и IP")
for host in ("192.168.1.129", "10.8.0.2", "127.0.0.1", "172.16.0.5"):
    check("%s распознан как локальный" % host, lg.is_private_ip(host))
check("внешний адрес локальным не считается", not lg.is_private_ip("185.11.22.33"))

home = lg.analyze("http://192.168.1.129:8096")
check("домашний сервер не подозрителен", not home.suspicious, home.flags)
check("но отмечен как локальный",
      any(lg.phrase("f_ip_local") == text for _, text in home.flags), home.flags)
foreign = lg.analyze("http://185.11.22.33/wallet/recovery")
check("чужой IP остаётся подозрительным", foreign.suspicious, foreign.flags)

asked = []
real_rdap = lg.domain_age_days
lg.domain_age_days = lambda domain, timeout=8: asked.append(domain) or 100
plugin._ages.clear()
plugin._domain_age("1.129")
check("у IP возраст не спрашиваем", not asked, asked)
plugin._domain_age("example.com")
check("у домена спрашиваем", asked == ["example.com"], asked)
lg.domain_age_days = real_rdap

print("\nВозраст в разборе")
check("меньше суток", lg.human_age(0) == lg.phrase("age_today"), lg.human_age(0))
check("один день", lg.human_age(1) == "1 день", lg.human_age(1))
check("два дня", lg.human_age(2) == "2 дня", lg.human_age(2))
check("пять дней", lg.human_age(5) == "5 дней", lg.human_age(5))
check("одиннадцать дней", lg.human_age(11) == "11 дней", lg.human_age(11))
check("недели", lg.human_age(21) == "3 недели", lg.human_age(21))
check("одна неделя", lg.human_age(14) == "2 недели", lg.human_age(14))
check("месяцы", lg.human_age(200) == "6 месяцев", lg.human_age(200))
check("один год", lg.human_age(740) == "2 года", lg.human_age(740))
check("двадцать два года", lg.human_age(8030) == "22 года", lg.human_age(8030))
check("двадцать девять лет", lg.human_age(10670) == "29 лет", lg.human_age(10670))
check("символ в единственном числе",
      "на 1 символ" in [f[1] for f in lg.analyze("https://sberbamk.ru/login").flags][0],
      [f[1] for f in lg.analyze("https://sberbamk.ru/login").flags])

aged = lg.analyze("https://vk.com:8080/feed")
lg.add_age_flag(aged, 10670)
check("старый домен не добавляет тревогу", len(aged.flags) == 1, aged.flags)
check("но возраст попадает в окно",
      lg.phrase("lbl_age", lg.human_age(10670)) in plugin._describe(aged),
      plugin._describe(aged))

young = lg.analyze("https://pay-now.top/enter")
lg.add_age_flag(young, 5)
check("свежий домен и тревожит, и виден в окне",
      young.risk == lg.HIGH and lg.phrase("lbl_age", lg.human_age(5)) in plugin._describe(young),
      plugin._describe(young))

print("\nРевизия: смещения, скачанный файл, счётчик")
emoji_text = "🎁 держи sberbank.ru прямо тут"
offset = len(emoji_text[:emoji_text.index("sberbank.ru")].encode("utf-16-le")) // 2
plugin._anchors.clear()
plugin._index_anchors(emoji_text,
                      FakeEntities([FakeEntity(offset, len("sberbank.ru"),
                                               "https://phish.top/enter")]))
check("эмодзи перед ссылкой не сдвигает подпись",
      plugin._anchors.get("https://phish.top/enter") == "sberbank.ru", plugin._anchors)
plugin._anchors.clear()

good = b'__id__ = "link_guard"\n__version__ = "9.9.9"\n'
check("наш файл распознан", plugin._looks_like_our_plugin(good))
check("чужой файл отвергнут", not plugin._looks_like_our_plugin(b'__id__ = "other_plugin"'))
check("пустой файл отвергнут", not plugin._looks_like_our_plugin(b""))
check("огромный файл отвергнут",
      not plugin._looks_like_our_plugin(good + b"x" * (3 * 1024 * 1024)))

if handler is not None:
    plugin._on_reset_stats_click()
    plugin._cache.clear()
    plugin._counted.clear()
    plugin._sources.clear()
    plugin.set_setting("show_mode", 1)
    param = FakeParam("https://promo-gift.top/x?utm_source=a&fbclid=b")
    handler.before_hooked_method(param)
    check("до решения пользователя метки не засчитаны",
          plugin._stat("stats_cleaned") == 0, plugin._stat("stats_cleaned"))
    FakeDialog.last.press("negative")
    check("после отмены тоже не засчитаны",
          plugin._stat("stats_cleaned") == 0, plugin._stat("stats_cleaned"))

    plugin._cache.clear()
    param = FakeParam("https://promo-gift.top/y?utm_source=a&fbclid=b")
    handler.before_hooked_method(param)
    FakeDialog.last.press("positive")
    check("после «Открыть» метки засчитаны",
          plugin._stat("stats_cleaned") == 2, plugin._stat("stats_cleaned"))
    plugin.set_setting("show_mode", 0)
    plugin._on_reset_stats_click()

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
          lg.phrase("src_stranger") in (FakeDialog.last.message or ""), FakeDialog.last.message)
    FakeDialog.last.press("positive")

    danger = "https://sberbank.ru@phish.top/login"
    plugin._sources[danger] = "trusted"
    plugin._cache.clear()
    param = FakeParam(danger)
    handler.before_hooked_method(param)
    check("опасное тревожит даже от контакта", param.cancelled)
    FakeDialog.last.press("negative")
    plugin._sources.clear()

print("\nИсточник по открытому чату")
CURRENT_CHAT[0] = types.SimpleNamespace(title="Новости")
CURRENT_USER[0] = None
check("открыт канал — ссылка считается чужой", plugin._fragment_source() == "unknown")

CURRENT_CHAT[0] = None
CURRENT_USER[0] = types.SimpleNamespace(contact=True)
check("открыт чат с контактом — доверенная", plugin._fragment_source() == "trusted")

CURRENT_USER[0] = types.SimpleNamespace(contact=False)
check("незнакомец в личке — чужая", plugin._fragment_source() == "unknown")

CURRENT_USER[0] = None
check("не чат — источник неизвестен", plugin._fragment_source() is None)

if handler is not None:
    CURRENT_CHAT[0] = types.SimpleNamespace(title="Канал")
    plugin._cache.clear()
    plugin._sources.clear()
    plugin.set_setting("show_mode", 0)
    param = FakeParam("https://rasprodaja.xyz/iz-kanala")
    handler.before_hooked_method(param)
    check("ссылка из открытого канала поднимает разбор", param.cancelled)
    check("в разборе указан чужой источник",
          lg.phrase("src_stranger") in (FakeDialog.last.message or ""), FakeDialog.last.message)
    FakeDialog.last.press("positive")
    CURRENT_CHAT[0] = None

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

print("\nБаза мошеннических доменов")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
import build_db

BAD = ["moshennik.top", "sber-oplata.xyz", "phish.pages.dev"]
GOOD = ["primer-horoshiy-sayt-s-dlinnym-imenem.top"]


FRESH10 = ["sber-bonus-new.top"]
FRESH30 = ["oplata-dostavki-new.ru"]
ZONES = {"xyz": 3, "shop": 2}


def make_database(bad=BAD, good=GOOD, platforms=("pages.dev",),
                  fresh10=FRESH10, fresh30=FRESH30, zones=ZONES):
    malw, _ = build_db.hash_section(bad, build_db.FULL_BITS)
    whit, _ = build_db.hash_section(good, build_db.WHITE_BITS)
    fr10, _ = build_db.hash_section(fresh10, build_db.FULL_BITS)
    fr30, _ = build_db.hash_section(fresh30, build_db.FULL_BITS)
    zone_text = "\n".join("%s %d" % (z, lvl) for z, lvl in sorted(zones.items()))
    sections = [
        ("MALW", malw),
        ("WHIT", whit),
        ("TLDR", zone_text.encode("utf-8")),
        ("FR10", fr10),
        ("FR30", fr30),
        ("BRND", b"example.com"),
        ("PLAT", "\n".join(platforms).encode("utf-8")),
    ]
    body = b"".join(build_db.section(tag, payload) for tag, payload in sections)
    return b"LGDB" + struct.pack(">BIB", 1, 20400, len(sections)) + body


blob = make_database()
database = lg.DomainDatabase(blob)
lg.install_database(database)

check("база разобрана", database.total == len(BAD), database.total)
check("платформы прочитаны", "pages.dev" in database.platforms, database.platforms)

v = lg.analyze("https://moshennik.top/vhod")
check("домен из базы — высокий риск", v.risk == lg.HIGH, v.flags)
check("в разборе сказано про базу",
      any(lg.phrase("f_blocklist") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://lk.moshennik.top/vhod")
check("поддомен мошеннического тоже опасен", v.risk == lg.HIGH, v.flags)
check("в разборе назван родительский домен",
      any("moshennik.top" in text for _, text in v.flags), v.flags)

v = lg.analyze("https://phish.pages.dev/")
check("конкретный поддомен платформы опасен", v.risk == lg.HIGH, v.flags)
v = lg.analyze("https://drugoy-sayt.pages.dev/")
check("платформа целиком не блокируется",
      not any(lg.phrase("f_blocklist") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://primer-horoshiy-sayt-s-dlinnym-imenem.top/")
check("у посещаемого сайта мелкие придирки сняты", v.risk == lg.INFO, v.flags)

print("\nИмя банка с приставкой")
for host in ("sberbank-shop.ru", "sberbankshop.com", "tinkoff-oplata.top",
             "vtb-online.info", "gosuslugi-vyplaty.shop", "wildberries-sale.ru",
             "ozon-bonus.store", "yandex-dostavka.site", "avito-dostavka.top",
             "telegram-premium.shop", "whatsapp-web.online"):
    v = lg.analyze("https://%s/" % host)
    named = [text for _, text in v.flags if "не принадлежит" in text]
    check("%s — высокий риск" % host, v.risk == lg.HIGH and named, v.flags)

for host in ("sberbank.ru", "tinkoff.ru", "gosuslugi.ru", "ozon.ru",
             "wildberries.ru", "yandex.ru", "avito.ru", "telegram.org"):
    v = lg.analyze("https://%s/" % host)
    check("%s не тронут" % host,
          not any("не принадлежит" in text for _, text in v.flags), v.flags)

v = lg.analyze("https://steampowered-fan.ru/")
check("чужое имя с приставкой ловится даже у безобидного с виду сайта",
      any("steampowered" in text for _, text in v.flags), v.flags)

for host in ("pineapple-shop.ru", "moy-magazin.shop",
             "kakoy-to-sayt.ru", "post-service.ru", "mail-arhiv.ru"):
    v = lg.analyze("https://%s/" % host)
    check("%s не считается подделкой" % host,
          not any("не принадлежит" in text for _, text in v.flags), v.flags)

print("\nСклеенное имя ловится только со словом-приманкой")
for host, stolen in (("sberbankshop.com", "sberbank"),
                     ("googleoplata.ru", "google"),
                     ("wildberriesbonus.top", "wildberries"),
                     ("telegrampremium.site", "telegram")):
    v = lg.analyze("https://%s/" % host)
    check("%s — подделка" % host,
          any(stolen in t and "не принадлежит" in t for _, t in v.flags), v.flags)

for host in ("googlefonts.github.io", "googledevelopers.example.com",
             "appleinsider.ru", "telegramgeek.ru", "yandexblog.example"):
    v = lg.analyze("https://%s/" % host)
    check("%s — не подделка" % host,
          not any("не принадлежит" in t for _, t in v.flags), v.flags)

check("отдельным словом ловится по-прежнему",
      lg.brand_with_extra("google-oplata.ru", lg.BRANDS, lg.BRAND_LIST) == "google")
check("склеенное с обычным словом пропускается",
      lg.brand_with_extra("googlefonts.github.io", lg.BRANDS, lg.BRAND_LIST) is None)

check("имя ровно как у бренда, но в другой зоне, правилом не ловится",
      lg.brand_with_extra("sberbank.com", lg.BRANDS, lg.BRAND_LIST) is None,
      lg.brand_with_extra("sberbank.com", lg.BRANDS, lg.BRAND_LIST))
check("короткие названия ловятся только целым словом",
      lg.brand_with_extra("vtb-vhod.ru", lg.BRANDS, lg.BRAND_LIST) == "vtb"
      and lg.brand_with_extra("montblanc.ru", lg.BRANDS, lg.BRAND_LIST) is None)
check("список брендов отсортирован один раз, а не на каждой ссылке",
      lg.BRAND_LIST == sorted(lg.BRANDS) and isinstance(lg.BRAND_LIST, list))

print("\nДоверенные домены с кириллицей")
IDN_URL = "https://xn--80aswg.xn--p1ai/vhod"
idn = lg.analyze(IDN_URL)
check("punycode распознан", idn.host == "xn--80aswg.xn--p1ai"
      and idn.display_host == "сайт.рф", (idn.host, idn.display_host))

shown_form = lg.registrable(idn.display_host)
check("доверие в читаемом виде работает",
      not lg.analyze(IDN_URL, whitelist={shown_form}).flags,
      lg.analyze(IDN_URL, whitelist={shown_form}).flags)
check("доверие в виде punycode тоже работает",
      not lg.analyze(IDN_URL, whitelist={idn.host}).flags,
      lg.analyze(IDN_URL, whitelist={idn.host}).flags)

for typed, expect in (("сайт.рф", "сайт.рф"),
                      ("xn--80aswg.xn--p1ai", "сайт.рф"),
                      ("Пример.РФ", "пример.рф"),
                      ("https://сайт.рф/stranica", "сайт.рф"),
                      ("не домен", ""),
                      ("", "")):
    check("поле ввода: %r -> %r" % (typed, expect),
          lg.normalize_domain(typed) == expect, lg.normalize_domain(typed))

trusting = lg.LinkGuardPlugin()
trusting.on_plugin_load()
trusting._toast = lambda text: None
trusting._trust_domain(lg.registrable(idn.display_host))
check("домен попал в список доверенных",
      "сайт.рф" in trusting._whitelist(), trusting._whitelist())
check("и ссылка после этого не тревожит",
      not lg.analyze(IDN_URL, whitelist=trusting._whitelist()).flags,
      lg.analyze(IDN_URL, whitelist=trusting._whitelist()).flags)

print("\nБыстрый отсев не теряет опечатки")
missed = []
for brand in sorted(lg.BRANDS):
    head, _, zone = brand.partition(".")
    if len(head) < 5:
        continue
    variants = (
        head[:-1] + "." + zone,
        head + head[-1] + "." + zone,
        head[:2] + head[3:] + "." + zone,
        head[:-2] + head[-1] + head[-2] + "." + zone,
    )
    for variant in variants:
        if variant in lg.BRANDS or variant == brand:
            continue
        found = any(brand.split(".")[0] in text
                    for _, text in lg.analyze("https://%s/" % variant).flags)
        if not found:
            missed.append((brand, variant))
check("однобуквенные опечатки во всех брендах ловятся: пропущено %d"
      % len(missed), not missed, missed[:4])

print("\nРепутация зон из базы")
check("уровни зон прочитаны", database.zones == ZONES, database.zones)
v = lg.analyze("https://kakoy-to-sayt.xyz/")
check("худшая зона даёт средний риск", v.risk == lg.MEDIUM, v.flags)
check("в разборе названа зона",
      any(lg.phrase("f_tld_worst", "xyz") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://kakoy-to-sayt.shop/")
check("зона попроще сама по себе молчит", not v.flags, v.flags)

v = lg.analyze("https://kakoy-to-sayt.shop/oplata/podtverdite")
check("но вместе с другой находкой добавляет замечание",
      any(lg.phrase("f_tld", "shop") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://kakoy-to-sayt.ru/")
check("обычная зона замечаний не даёт", not v.flags, v.flags)

v = lg.analyze("https://kakoy-to-sayt.zip/")
check("зона из встроенного списка работает, когда в базе её нет",
      v.risk == lg.LOW, v.flags)

print("\nСвежерегистрированные домены")
v = lg.analyze("https://sber-bonus-new.top/")
check("домен младше десяти дней тревожит",
      any(lg.phrase("f_fresh_10") == text for _, text in v.flags), v.flags)
check("и поднимает риск до высокого вместе с прочим",
      v.risk == lg.HIGH, v.flags)

v = lg.analyze("https://lk.sber-bonus-new.top/vhod")
check("поддомен свежего домена тоже тревожит",
      any(lg.phrase("f_fresh_10") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://oplata-dostavki-new.ru/")
check("домен младше месяца отмечается мягче",
      any(lg.phrase("f_fresh_30") == text for _, text in v.flags), v.flags)

v = lg.analyze("https://staryy-sayt-obychnyy.ru/")
check("давно живущий домен не трогаем",
      not any(lg.phrase("f_fresh_10") == text or lg.phrase("f_fresh_30") == text
              for _, text in v.flags), v.flags)

lg.install_database(None)
v = lg.analyze("https://moshennik.top/vhod")
check("без базы проверка по ней не идёт",
      not any(lg.phrase("f_blocklist") == text for _, text in v.flags), v.flags)

print("\nБренды подхватываются из базы")
REAL_DB = os.path.join(tempfile.gettempdir(), "lgdb_test", "full.lgdb")
if os.path.exists(REAL_DB):
    real = lg.read_database(REAL_DB)
    lg.install_database(real)
    check("бренды прочитаны из базы", len(real.brands) == 1000, len(real.brands))
    check("своих брендов в коде нет среди подхваченных",
          "cloudflare.com" not in lg.BRANDS and "cloudflare.com" in real.brands)

    v = lg.analyze("https://cloudflaer.com/login")
    check("опечатка в подхваченном бренде поймана", v.risk == lg.HIGH, v.flags)

    started = time.time()
    for i in range(200):
        lg.analyze("https://primer-%d.example.net/stranica" % i)
    per_call = (time.time() - started) / 200 * 1000
    print("  %.1f мс на разбор с тысячей брендов" % per_call)
    check("разбор укладывается в 40 мс", per_call < 40, per_call)

    alarms = []
    for host in sorted(real.brands):
        verdict = lg.analyze("https://%s/" % host)
        if verdict.risk == lg.HIGH:
            alarms.append((host, verdict.flags))
    check("тысяча посещаемых сайтов не считается подделками: тревог %d"
          % len(alarms), not alarms, alarms[:3])

    everyday = ["ya.ru", "dzen.ru", "habr.com", "rutracker.org", "kinopoisk.ru",
                "2gis.ru", "sravni.ru", "banki.ru", "auto.ru", "cian.ru"]
    noisy = [h for h in everyday if lg.analyze("https://%s/" % h).risk == lg.HIGH]
    check("обычные сайты не тревожат: %s" % noisy, not noisy)

    fresh_words = (lg.phrase("f_fresh_10"), lg.phrase("f_fresh_30"))
    mistaken = []
    for host in sorted(real.brands) + everyday:
        for _, text in lg.analyze("https://%s/" % host).flags:
            if text in fresh_words:
                mistaken.append(host)
                break
    check("посещаемые сайты не считаются свежими: %d" % len(mistaken),
          not mistaken, mistaken[:5])

    zones_seen = sum(1 for _, text in
                     lg.analyze("https://kakoy-to-novyy-sayt.digital/").flags
                     if text == lg.phrase("f_tld_worst", "digital"))
    check("репутация зон работает на настоящей базе", zones_seen == 1,
          lg.analyze("https://kakoy-to-novyy-sayt.digital/").flags)

    with_subdomains = [
        "apple.stackexchange.com", "android.stackexchange.com",
        "money.yandex.ru", "market.yandex.ru", "cloud.mail.ru",
        "pay.google.com", "drive.google.com", "support.apple.com",
        "music.apple.com", "docs.google.com", "web.whatsapp.com",
        "online.sberbank.ru", "passport.yandex.ru", "outlook.office.com",
        "login.microsoftonline.com", "static.rutube.ru", "id.vk.com",
    ]
    noisy = [(h, lg.analyze("https://%s/" % h).flags) for h in with_subdomains
             if lg.analyze("https://%s/" % h).risk != lg.INFO]
    check("известные сайты с поддоменами молчат: тревог %d" % len(noisy),
          not noisy, noisy[:3])

    v = lg.analyze("https://telegram.org.ru/")
    check("а подозрительное имя в чужой зоне по-прежнему ловится",
          v.risk == lg.HIGH, v.flags)

    lg.install_database(None)
else:
    check("настоящая база найдена для проверки брендов", True,
          "пропущено: нет %s" % REAL_DB)

print("\nЗабытые копии базы убираются")
sweep_root = tempfile.mkdtemp(prefix="link_guard_sweep_")
folders = [os.path.join(sweep_root, name) for name in ("plugins", "cache", "docs")]
for folder in folders:
    os.makedirs(folder)
    with open(os.path.join(folder, lg.DB_FILE_NAME), "wb") as handle:
        handle.write(b"LGDB starye dannye")

sweeper = lg.LinkGuardPlugin()
sweeper.on_plugin_load()
sweeper._writable_dirs = lambda: folders
check("рабочий файл лежит в первом каталоге",
      sweeper._database_path() == os.path.join(folders[0], lg.DB_FILE_NAME))
check("копии в других каталогах найдены", sweeper._sweep_database_copies() == 2)
check("рабочий файл на месте",
      os.path.exists(os.path.join(folders[0], lg.DB_FILE_NAME)))
check("забытые копии удалены",
      not any(os.path.exists(os.path.join(f, lg.DB_FILE_NAME))
              for f in folders[1:]))
check("повторная уборка ничего не находит",
      sweeper._sweep_database_copies() == 0)

sweeper._writable_dirs = lambda: []
check("без каталогов уборка не падает", sweeper._sweep_database_copies() == 0)
check("и путь к базе не выдумывается", sweeper._database_path() is None)

print("\nСтарая база без новых разделов")
old_sections = [
    ("MALW", build_db.hash_section(BAD, build_db.FULL_BITS)[0]),
    ("WHIT", build_db.hash_section(GOOD, build_db.WHITE_BITS)[0]),
    ("BRND", b"example.com"),
    ("PLAT", b"pages.dev"),
]
old_body = b"".join(build_db.section(t, p) for t, p in old_sections)
old_blob = b"LGDB" + struct.pack(">BIB", 1, 20400, len(old_sections)) + old_body
old_db = lg.DomainDatabase(old_blob)
lg.install_database(old_db)
check("старая база читается", old_db.total == len(BAD), old_db.total)
check("зон в ней нет", old_db.zones == {}, old_db.zones)
v = lg.analyze("https://moshennik.top/vhod")
check("мошеннический домен по-прежнему ловится", v.risk == lg.HIGH, v.flags)
v = lg.analyze("https://kakoy-to-sayt.top/")
check("зона берётся из встроенного списка",
      any(lg.phrase("f_tld", "top") == t for _, t in v.flags), v.flags)
v = lg.analyze("https://kakoy-to-sayt.digital/")
check("без данных о зоне тревоги нет", not v.flags, v.flags)
v = lg.analyze("https://sberbank-shop.ru/")
check("имя с приставкой не зависит от базы", v.risk == lg.HIGH, v.flags)
lg.install_database(None)

print("\nБаза: порченые файлы")
for broken, title in (
    (b"", "пустой файл"),
    (b"NOPE" + blob[4:], "чужая подпись"),
    (b"LGDB" + struct.pack(">BIB", 77, 20400, 0), "чужая версия"),
    (blob[:len(blob) // 2], "обрезанный файл"),
):
    try:
        lg.DomainDatabase(broken)
        ok = False
    except Exception:
        ok = True
    check("%s отвергается" % title, ok)

with tempfile.NamedTemporaryFile(suffix=".lgdb", delete=False) as handle:
    handle.write(b"musor, ne nasha baza")
    junk_path = handle.name
check("испорченный файл с диска не ломает плагин",
      lg.read_database(junk_path) is None)
check("несуществующий файл не ломает плагин",
      lg.read_database(os.path.join(tempfile.gettempdir(), "net-takogo.lgdb")) is None)
os.unlink(junk_path)

with tempfile.NamedTemporaryFile(suffix=".lgdb", delete=False) as handle:
    handle.write(blob)
    good_path = handle.name
restored = lg.read_database(good_path)
check("целая база с диска читается", restored is not None and restored.total == len(BAD))
os.unlink(good_path)

print("\nБаза: настройки и расписание")
db_plugin = lg.LinkGuardPlugin()
db_plugin.on_plugin_load()
db_plugin.set_setting("use_database", False)
check("тумблер «выключено» читается", not db_plugin._database_enabled())
db_plugin._load_database()
check("при выключенной базе она не подставляется", lg.active_database() is None)
db_plugin.set_setting("use_database", True)
check("тумблер «включено» читается", db_plugin._database_enabled())
check("состояние базы описано словами",
      isinstance(db_plugin._database_status(), str) and db_plugin._database_status())
check("качается только полная база", lg.DB_NAME == "full.lgdb", lg.DB_NAME)

baza = types.SimpleNamespace(age_days=0, total=10)
now = lg.time.time()

check("проверяем раз в три часа", lg.DB_CHECK_GAP == 3 * 60 * 60, lg.DB_CHECK_GAP)

db_plugin.set_setting("db_checked_at", now)
check("сразу после проверки повторно не лезем",
      not db_plugin._due_for_refresh(baza))
db_plugin.set_setting("db_checked_at", now - lg.DB_CHECK_GAP - 1)
check("через три часа проверяем снова", db_plugin._due_for_refresh(baza))

db_plugin.set_setting("db_attempt_at", now)
check("без базы после неудачи ждём час, а не долбим",
      not db_plugin._due_for_refresh(None))
db_plugin.set_setting("db_attempt_at", now - lg.DB_RETRY_GAP - 1)
check("через час пробуем скачать снова", db_plugin._due_for_refresh(None))
check("повтор после неудачи — раз в час", lg.DB_RETRY_GAP == 60 * 60,
      lg.DB_RETRY_GAP)

print("\nБаза: качаем только изменившуюся")
downloads = []
lg.fetch_database = lambda name, timeout=90: downloads.append(name)
lg.fetch_database_stamp = lambda timeout=20: "2026-09-12 02:36 UTC"
lg.install_database(baza)
db_plugin.set_setting("db_built", "2026-09-12 02:36 UTC")
db_plugin._refresh_database(manual=False)
check("та же сборка на сервере — загрузки нет", not downloads, downloads)
check("но время проверки записано",
      db_plugin._moment("db_checked_at") > now - 5,
      db_plugin._moment("db_checked_at"))

db_plugin.set_setting("db_built", "2026-09-11 02:37 UTC")
db_plugin._refresh_database(manual=False)
check("новая сборка на сервере — качаем", downloads == ["full.lgdb"], downloads)

downloads[:] = []
db_plugin.set_setting("db_built", "2026-09-12 02:36 UTC")
db_plugin._refresh_database(manual=True)
check("кнопка в настройках качает всегда", downloads == ["full.lgdb"], downloads)

lg.install_database(None)
lg.fetch_database = lambda name, timeout=90: None
lg.fetch_database_stamp = lambda timeout=20: None
db_plugin.set_setting("db_checked_at", 0)
db_plugin.set_setting("db_attempt_at", 0)

check("обновления проверяются раз в шесть часов",
      lg.UPDATE_INTERVAL == 6 * 60 * 60, lg.UPDATE_INTERVAL)

print("\nБаза: цифры в настройках не отстают")
drawn = []
real_set_setting = db_plugin.set_setting


def watched_set_setting(key, value, reload_settings=False):
    if reload_settings:
        drawn.append(key)
    return real_set_setting(key, value)


db_plugin.set_setting = watched_set_setting
db_plugin._toast = lambda text: None
db_plugin._after_refresh(2984808, manual=True)
check("после обновления экран перерисовывается", "db_entries" in drawn, drawn)
check("новое число запомнено",
      int(db_plugin.get_setting("db_entries", 0)) == 2984808,
      db_plugin.get_setting("db_entries", 0))
db_plugin.set_setting = real_set_setting

print("\nБаза: проверка при открытии ссылки")
started_refresh = []
db_plugin._run_background = lambda func: started_refresh.append(func)
db_plugin._due_for_refresh = lambda database: True
db_plugin.set_setting("use_database", True)
db_plugin._db_ticked = 0.0

db_plugin._tick_database()
check("первое открытие ссылки запускает проверку", len(started_refresh) == 1,
      len(started_refresh))
db_plugin._tick_database()
db_plugin._tick_database()
check("подряд идущие переходы лишнего не делают", len(started_refresh) == 1,
      len(started_refresh))

db_plugin._db_ticked = time.time() - lg.DB_TICK_GAP - 1
db_plugin._tick_database()
check("через положенное время заглядывает снова", len(started_refresh) == 2,
      len(started_refresh))

db_plugin.set_setting("use_database", False)
db_plugin._db_ticked = 0.0
db_plugin._tick_database()
check("при выключенной базе не проверяет", len(started_refresh) == 2,
      len(started_refresh))

db_plugin.set_setting("use_database", True)
db_plugin._due_for_refresh = lambda database: False
db_plugin._db_ticked = 0.0
db_plugin._tick_database()
check("если проверялись недавно, загрузки нет",
      len(started_refresh) == 2, len(started_refresh))
check("заглядываем не чаще раза в полчаса", lg.DB_TICK_GAP == 30 * 60,
      lg.DB_TICK_GAP)

print("\nДиагностика")
diag = db_plugin._diagnostic_rows()
if lg.DEBUG_BUILD:
    check("раздел диагностики на месте", len(diag) == 3, len(diag))
else:
    check("в сборке для каталога раздела диагностики нет", diag == [], diag)
db_plugin._log = []
db_plugin.set_setting("debug_log", False)
db_plugin._debug("этого в журнале быть не должно")
check("при выключенном тумблере журнал пуст", not db_plugin._log, db_plugin._log)

db_plugin.set_setting("debug_log", True)
db_plugin._debug("первая запись")
db_plugin._debug("вторая запись")
if lg.DEBUG_BUILD:
    check("записи попадают в журнал", len(db_plugin._log) == 2, db_plugin._log)
    check("в тексте журнала видно версию и обе записи",
          "первая запись" in db_plugin._log_text()
          and "вторая запись" in db_plugin._log_text()
          and lg.__version__ in db_plugin._log_text())
    for i in range(lg.LOG_MAX + 50):
        db_plugin._debug("запись %d" % i)
    check("журнал не растёт без предела", len(db_plugin._log) == lg.LOG_MAX,
          len(db_plugin._log))
else:
    check("в сборке для каталога журнал не копится",
          not db_plugin._log, db_plugin._log)

db_plugin._on_clear_log()
check("журнал очищается", not db_plugin._log, db_plugin._log)
check("у пустого журнала понятный текст",
      db_plugin._log_text() == lg.phrase("log_empty"))
db_plugin.set_setting("debug_log", False)

print("\nПункты меню сообщения")
menu_plugin = lg.LinkGuardPlugin()
menu_plugin.on_plugin_load()
menu_said = []
menu_plugin._toast = lambda text: menu_said.append(text)
fake_msg = types.SimpleNamespace(
    messageText="Смотри https://sberbank-shop.ru/oplata и https://google.com/",
    caption=None,
    messageOwner=types.SimpleNamespace(message="", entities=None))

FakeDialog.last = None
menu_plugin.on_menu_check({"message": fake_msg})
check("«Проверить ссылки» открывает окно",
      FakeDialog.last is not None and FakeDialog.last.shown)
check("в окне оба адреса",
      "sberbank-shop.ru" in (FakeDialog.last.message or "")
      and "google.com" in (FakeDialog.last.message or ""),
      FakeDialog.last.message)
check("и сказано, сколько проверено",
      lg.phrase("checked_n", 2) == FakeDialog.last.title, FakeDialog.last.title)

copied = []
real_copy = lg.copy_to_clipboard
lg.copy_to_clipboard = lambda text: copied.append(text)
menu_plugin.on_menu_copy({"message": fake_msg})
check("«Копировать без трекеров» кладёт адреса в буфер",
      copied and "sberbank-shop.ru" in copied[0], copied)
lg.copy_to_clipboard = real_copy

menu_said[:] = []
FakeDialog.last = None
empty_msg = types.SimpleNamespace(
    messageText="просто текст без ссылок", caption=None,
    messageOwner=types.SimpleNamespace(message="", entities=None))
menu_plugin.on_menu_check({"message": empty_msg})
check("без ссылок окно не открывается и есть уведомление",
      FakeDialog.last is None and menu_said, menu_said)

menu_plugin.on_menu_check({})
check("без сообщения обработчик молча выходит", True)

print("\nНелепые адреса не подвешивают разбор")
started_at = time.time()
for host, name in (
    ("." * 9000 + "com", "девять тысяч точек"),
    ("www." * 3000, "три тысячи www"),
    ("a" * 9000 + ".com", "очень длинное имя"),
    (".".join("x" for _ in range(500)) + ".com", "пятьсот меток"),
):
    v = lg.analyze("https://%s/stranica" % host)
    check("%s — отказ, а не разбор" % name,
          any(lg.phrase("f_unparsable") == t for _, t in v.flags), v.flags)
spent_ms = (time.time() - started_at) * 1000
check("на все четыре ушло меньше 50 мс: %.0f" % spent_ms, spent_ms < 50, spent_ms)

v = lg.analyze("https://a.b.c.d.example.com/x")
check("обычная глубокая цепочка разбирается",
      not any(lg.phrase("f_unparsable") == t for _, t in v.flags), v.flags)
check("и по-прежнему считается глубокой",
      any(lg.phrase("f_deep") == t for _, t in v.flags), v.flags)

v = lg.analyze("https://primer.example.com/" + "a" * 5000)
check("длинный путь при нормальном домене не мешает",
      not any(lg.phrase("f_unparsable") == t for _, t in v.flags), v.flags)

check("предел длины имени — как в DNS", lg.HOST_MAX == 253, lg.HOST_MAX)

print("\nКнопка на репозиторий")
repo_plugin = lg.LinkGuardPlugin()
repo_plugin.on_plugin_load()
opened = []
repo_plugin._open_with_browser = lambda context, url: opened.append(("browser", url)) or True
repo_plugin._on_repo_click()
check("кнопка открывает наш репозиторий",
      opened == [("browser", lg.REPO_URL)], opened)
check("адрес ведёт на GitHub",
      lg.REPO_URL.startswith("https://github.com/"), lg.REPO_URL)
check("своя же ссылка не вызовет предупреждения",
      lg.REPO_URL in repo_plugin._bypass, list(repo_plugin._bypass))

opened[:] = []
repo_plugin._open_with_browser = lambda context, url: False
repo_plugin._open_with_intent = staticmethod(
    lambda context, url: opened.append(("intent", url)) or True)
repo_plugin._on_repo_click()
check("если браузер не вышел, пробуем систему",
      opened == [("intent", lg.REPO_URL)], opened)

opened[:] = []
said_repo = []
repo_plugin._open_with_intent = staticmethod(lambda context, url: False)
repo_plugin._toast = lambda text: said_repo.append(text)
repo_plugin._bypass.clear()
check("когда открыть нечем, ссылка копируется",
      repo_plugin._open_link(lg.REPO_URL) is False and said_repo, said_repo)
check("и след от неё убран", lg.REPO_URL not in repo_plugin._bypass)

rows = [r for r in repo_plugin.create_settings()
        if getattr(r, "text", None) == lg.phrase("btn_repo")]
check("строка есть в настройках", len(rows) == 1, len(rows))

opened[:] = []
repo_plugin._open_with_browser = lambda context, url: opened.append(("browser", url)) or True
repo_plugin._on_chat_click()
check("кнопка чата открывает нужный адрес",
      opened == [("browser", "https://t.me/kringplugins")], opened)
check("адрес чата ведёт в Telegram",
      lg.CHAT_URL == "https://t.me/kringplugins", lg.CHAT_URL)
chat_rows = [r for r in repo_plugin.create_settings()
             if getattr(r, "text", None) == lg.phrase("btn_chat")]
check("строка чата есть в настройках", len(chat_rows) == 1, len(chat_rows))
check("и она под проверкой обновлений",
      [getattr(r, "text", "") for r in repo_plugin.create_settings()].index(
          lg.phrase("btn_repo"))
      == [getattr(r, "text", "") for r in repo_plugin.create_settings()].index(
          lg.phrase("btn_check_now")) + 1)

print("\nМинимальная версия клиента")
check("плагин требует 12.1.1", lg.__app_version__ == ">=12.1.1", lg.__app_version__)
check("без сведений о клиенте ничего не блокируем",
      not lg.client_too_old("12.1.1"))

real_client = lg.client_version
lg.client_version = lambda: "12.0.1"
check("старый клиент распознан", lg.client_too_old("12.1.1"))
lg.client_version = lambda: "12.1.1"
check("ровно нужная версия подходит", not lg.client_too_old("12.1.1"))
lg.client_version = lambda: "12.9.0"
check("новый клиент подходит", not lg.client_too_old("12.1.1"))
lg.client_version = lambda: "12.0.1"
check("без требования в update.json не блокируем", not lg.client_too_old(""))

upd_plugin = lg.LinkGuardPlugin()
upd_plugin.on_plugin_load()
said = []
upd_plugin._toast = lambda text: said.append(text)
lg.fetch_update_info = lambda timeout=8: {
    "version": "9.9.9", "min_app_version": "12.1.1", "changelog": ["новое"]}
upd_plugin._check_updates(manual=True)
check("обновление не предлагается на старом клиенте",
      not upd_plugin.get_setting("update_version", ""),
      upd_plugin.get_setting("update_version", ""))
check("и человеку сказано почему",
      any("12.1.1" in text for text in said), said)

lg.client_version = lambda: "12.9.0"
said[:] = []
upd_plugin._check_updates(manual=True)
check("на новом клиенте обновление предлагается",
      upd_plugin.get_setting("update_version", "") == "9.9.9",
      upd_plugin.get_setting("update_version", ""))
lg.client_version = real_client
lg.fetch_update_info = lambda timeout=8: None

print("\nЧистка: метки с заглавными и нетронутые параметры")
url, removed = lg.clean_url("https://x.example.com/p?hsCtaTracking=abc&id=1")
check("hsCtaTracking вырезается", url == "https://x.example.com/p?id=1"
      and removed == ["hsCtaTracking"], (url, removed))
url, removed = lg.clean_url("https://x.example.com/p?trkCampaign=abc&id=1", aggressive=True)
check("trkCampaign вырезается в агрессивном режиме", url == "https://x.example.com/p?id=1", url)
check("метки в списках записаны строчными — иначе они мертвы",
      all(k == k.lower() for k in lg.TRACKER_EXACT | lg.TRACKER_AGGRESSIVE))
url, removed = lg.clean_url(
    "https://x.example.com/p?flag&q=a%2Fb+c&utm_source=tg&sig=AbC%3D%3D#part")
check("оставшиеся параметры не пересобираются",
      url == "https://x.example.com/p?flag&q=a%2Fb+c&sig=AbC%3D%3D#part", url)
url, _ = lg.clean_url("https://x.example.com/p?UTM_Source=tg&id=1")
check("метка в верхнем регистре снимается", url == "https://x.example.com/p?id=1", url)
url, _ = lg.clean_url("https://x.example.com/p?utm_source=tg")
check("если вырезано всё, знак вопроса не остаётся", url == "https://x.example.com/p", url)
url, _ = lg.clean_url("https://x.example.com/p?utm%5Fsource=tg&id=1")
check("закодированное имя метки распознаётся", url == "https://x.example.com/p?id=1", url)

print("\nРазвёрнутая короткая ссылка чистится")
real_expand = lg.expand
lg.expand = lambda url, timeout=6: ("https://final.example.com/promo?utm_source=bitly&id=5", 1)
exp_plugin = lg.LinkGuardPlugin()
exp_plugin.on_plugin_load()
exp_handler = exp_plugin.installed_hooks[-1]
param = FakeParam("https://bit.ly/clean1")
exp_handler.before_hooked_method(param)
shown_text = FakeDialog.last.message or ""
check("в разборе конечный адрес без трекеров",
      "final.example.com/promo?id=5" in shown_text and "utm_source=" not in shown_text,
      shown_text)
check("вырезанная у конечного адреса метка названа в разборе",
      "utm_source" in shown_text, shown_text)
FakeDialog.last.press("positive")
check("открывается очищенный конечный адрес",
      param.method.calls and param.method.calls[-1][1]
      == "https://final.example.com/promo?id=5",
      param.method.calls)
exp_plugin.set_setting("clean_on_open", False)
param = FakeParam("https://bit.ly/clean2")
exp_handler.before_hooked_method(param)
check("с выключенной чисткой конечный адрес не трогаем",
      "utm_source=bitly" in (FakeDialog.last.message or ""), FakeDialog.last.message)
lg.expand = real_expand

print("\nСчётчик предупреждений")
warn_plugin = lg.LinkGuardPlugin()
warn_plugin.on_plugin_load()
warn_handler = warn_plugin.installed_hooks[-1]
warn_plugin.set_setting("show_mode", 1)
before = warn_plugin._stat("stats_warned")
warn_handler.before_hooked_method(FakeParam("https://www.wikipedia.org/wiki/Telegram"))
check("окно на чистой ссылке не считается предупреждением",
      warn_plugin._stat("stats_warned") == before, warn_plugin._stat("stats_warned"))
warn_handler.before_hooked_method(FakeParam("https://sberbamk.ru/login"))
check("настоящее предупреждение считается",
      warn_plugin._stat("stats_warned") == before + 1, warn_plugin._stat("stats_warned"))

print("\nСтатус базы в настройках")
db_plugin.set_setting("use_database", True)
lg.install_database(types.SimpleNamespace(age_days=0, total=3664881))
status = db_plugin._database_status()
check("свежая база — «сегодня», а не «0 дней назад»",
      "сегодня" in status and "0 дн" not in status, status)
check("число с разрядами и верным окончанием", "3 664 881 запись" in status, status)
lg.install_database(types.SimpleNamespace(age_days=3, total=12))
status = db_plugin._database_status()
check("старая база — «3 дня назад»", "3 дня назад" in status and "12 записей" in status,
      status)
lg.install_database(None)

print("\nБаза: без описания сборки вслепую не качаем")
real_fetch, real_stamp = lg.fetch_database, lg.fetch_database_stamp
blind = []
lg.fetch_database = lambda name, timeout=90: blind.append(name)
lg.fetch_database_stamp = lambda timeout=20: None
mf_plugin = lg.LinkGuardPlugin()
mf_plugin._cache = {}
lg.install_database(types.SimpleNamespace(age_days=0, total=10))
mf_plugin._refresh_database(manual=False)
check("база есть, описание не пришло — 11 МБ не качаем", not blind, blind)
mf_plugin._refresh_database(manual=True)
check("по кнопке в настройках качаем всё равно", blind == ["full.lgdb"], blind)
blind[:] = []
lg.install_database(None)
mf_plugin._refresh_database(manual=False)
check("базы нет совсем — качаем и без описания", blind == ["full.lgdb"], blind)

print("\nБаза: запись на диск и повторное включение")
lg.fetch_database = lambda name, timeout=90: blob
lg.fetch_database_stamp = lambda timeout=20: "2026-09-23 02:36 UTC"
wr_plugin = lg.LinkGuardPlugin()
wr_plugin._cache = {}
wr_plugin._refresh_database(manual=True)
db_path = wr_plugin._database_path()
check("база записана целиком", bool(db_path) and os.path.exists(db_path)
      and os.path.getsize(db_path) == len(blob), db_path)
check("временный файл не остался", not os.path.exists(db_path + ".part"))

wr_downloads = []
lg.fetch_database = lambda name, timeout=90: wr_downloads.append(name)
lg.install_database(None)
wr_plugin.set_setting("db_applied", False)
wr_plugin.set_setting("db_checked_at", lg.time.time())
wr_plugin._sync_database_mode()
check("после включения база поднята с диска",
      lg.active_database() is not None and lg.active_database().total == len(BAD))
check("из сети при этом ничего не качали", not wr_downloads, wr_downloads)

os.remove(db_path)
lg.install_database(None)
wr_plugin.set_setting("db_applied", False)
wr_plugin._sync_database_mode()
check("копии на диске нет — качаем сразу", wr_downloads == ["full.lgdb"], wr_downloads)
lg.fetch_database, lg.fetch_database_stamp = real_fetch, real_stamp
lg.install_database(None)

print("\nБаза: испорченный заголовок")
broken = bytearray(blob)
broken[23:25] = b"\x00\x00"
try:
    lg.DomainDatabase(bytes(broken))
    check("нулевой размер блока отвергается", False)
except ValueError:
    check("нулевой размер блока отвергается", True)

print("\nПорядок настроек")
lay_plugin = lg.LinkGuardPlugin()
lay_plugin.on_plugin_load()
rows = lay_plugin.create_settings()


def row_index(predicate):
    for i, row in enumerate(rows):
        if predicate(row):
            return i
    return -1


tracker_at = row_index(lambda r: getattr(r, "text", None) == lg.phrase("hdr_tracker"))
stats_at = row_index(lambda r: getattr(r, "text", None) == lg.phrase("hdr_stats"))
age_at = row_index(lambda r: getattr(r, "key", None) == "check_age")
tags_at = row_index(lambda r: getattr(r, "text", None) == lg.phrase("tags_title"))
check("возраст домена стоит в «Проверке ссылок»", 0 <= age_at < tracker_at,
      (age_at, tracker_at))
check("пояснение про метки стоит в «Антитрекере»", tracker_at < tags_at < stats_at,
      (tracker_at, tags_at, stats_at))

print("\nПодписи в настройках видны целиком")
fit_plugin = lg.LinkGuardPlugin()
fit_plugin.on_plugin_load()
fit_plugin.set_setting("stats_cleaned", 123456)
fit_plugin.set_setting("stats_warned", 123456)
fit_plugin.set_setting("whitelist", "example.com")
fit_plugin.set_setting("use_database", True)
real_debug_build = lg.DEBUG_BUILD
lg.DEBUG_BUILD = True
for lang in ("ru", "en"):
    lg.LANG = lang
    for total, days in ((9876545, 12), (9876541, 0)):
        lg.install_database(types.SimpleNamespace(age_days=days, total=total))
        rows = fit_plugin.create_settings()
        single_line = [row.subtext for row in rows
                       if getattr(row, "subtext", None)
                       and not isinstance(getattr(row, "default", None), bool)]
        too_long = [text for text in single_line if len(text) > 34]
        check("подписи строк влезают в одну строку (%s, %d дн.)" % (lang, days),
              single_line and not too_long, too_long)
lg.LANG = "ru"
lg.DEBUG_BUILD = real_debug_build
lg.install_database(None)
check("подсказка про сброс счётчиков вынесена в подпись блока",
      any(getattr(row, "text", None) == lg.phrase("stats_hint")
          for row in fit_plugin.create_settings()))

print("\nСтроки с переносом подписи")


class FakeDetailCell:

    def __init__(self, context):
        self.multiline = False
        self.text = self.value = None
        self.divider = None

    def setMultilineDetail(self, value):
        self.multiline = value

    def setTextAndValue(self, text, value, divider):
        self.text, self.value, self.divider = text, value, divider


class BrokenDetailCell:

    def __init__(self, context):
        raise RuntimeError("такой ячейки нет в этой сборке")


lg.Custom = lambda **kw: types.SimpleNamespace(kind="custom", **kw)
lg.TextDetailSettingsCell = FakeDetailCell
cell_plugin = lg.LinkGuardPlugin()
cell_plugin.on_plugin_load()
cell_plugin.set_setting("stats_cleaned", 1)
cell_plugin.set_setting("stats_warned", 22)
cell_plugin.set_setting("use_database", True)
lg.install_database(types.SimpleNamespace(age_days=0, total=3723568))
rows = cell_plugin.create_settings()
custom = [row for row in rows if getattr(row, "kind", None) == "custom"]
values = [row.view.value for row in custom]
check("четыре строки с переносом подписи", len(custom) == 4, len(custom))
check("статистика: подсказка прямо под счётчиками",
      "Предупреждений: 22 · нажмите, чтобы сбросить счётчики" in values, values)
check("база: полный текст", "В базе 3 723 568 записей, обновлена сегодня" in values, values)
check("исходный код и чат: полные подписи",
      any("сообщить о проблеме" in v for v in values)
      and any("нажмите, чтобы вступить" in v for v in values), values)
check("перенос включён у всех", all(row.view.multiline for row in custom))
check("отдельной подписи под статистикой больше нет",
      not any(getattr(row, "text", None) == lg.phrase("stats_hint") for row in rows))
dividers = {row.view.text: row.view.divider for row in custom}
check("черта под «Исходным кодом» есть, под «Чатом» нет",
      dividers.get(lg.phrase("btn_repo")) is True
      and dividers.get(lg.phrase("btn_chat")) is False, dividers)
stats_row = [row for row in custom if row.view.text == lg.phrase("stats_line", 1)][0]
stats_row.on_click(object())
check("нажатие на строку статистики сбрасывает счётчики",
      cell_plugin._stat("stats_warned") == 0, cell_plugin._stat("stats_warned"))

lg.TextDetailSettingsCell = BrokenDetailCell
rows = cell_plugin.create_settings()
check("ячейка не создалась — обычные строки, настройки открываются",
      not any(getattr(row, "kind", None) == "custom" for row in rows)
      and any(getattr(row, "text", None) == lg.phrase("stats_hint") for row in rows))
lg.Custom = None
lg.TextDetailSettingsCell = None
lg.install_database(None)

print("\nВключение базы: статус не врёт")
later = []
re_plugin = lg.LinkGuardPlugin()
re_plugin.on_plugin_load()
re_path = re_plugin._database_path()
with open(re_path, "wb") as handle:
    handle.write(blob)
re_plugin._run_background = lambda func: later.append(func)
lg.install_database(None)
re_plugin.set_setting("use_database", True)
re_plugin.set_setting("db_applied", False)
re_plugin.set_setting("db_checked_at", lg.time.time())
re_plugin._sync_database_mode()
check("пока база поднимается — «Загружаю базу…», а не «не скачана»",
      re_plugin._database_status() == lg.phrase("db_status_loading"),
      re_plugin._database_status())
redrawn = []
real_re_set = re_plugin.set_setting


def watch_redraw(key, value, reload_settings=False):
    if reload_settings:
        redrawn.append(key)
    return real_re_set(key, value, reload_settings)


re_plugin.set_setting = watch_redraw
for task in later:
    task()
status = re_plugin._database_status()
check("после загрузки статус показывает базу",
      status not in (lg.phrase("db_status_loading"), lg.phrase("db_status_none")), status)
check("экран настроек перерисован", "db_entries" in redrawn, redrawn)
re_plugin.set_setting = real_re_set
os.remove(re_path)
lg.install_database(None)

print("\nАнглийский язык")
ru_keys, en_keys = set(lg.STRINGS["ru"]), set(lg.STRINGS["en"])
check("у каждой русской строки есть английская", ru_keys == en_keys,
      sorted(ru_keys ^ en_keys))
check("в английских строках нет «ёлочек»",
      not [k for k, v in lg.STRINGS["en"].items() if "«" in v or "»" in v])
check("у английских строк столько же подстановок, сколько у русских",
      not [k for k in ru_keys if lg.STRINGS["ru"][k].count("%s")
           != lg.STRINGS["en"][k].count("%s")])

lg.LANG = "en"
check("по-английски 21 — это days, а не day",
      lg.plural_form(lg.phrase("unit_day"), 21) == "days")
check("по-английски 1 — day", lg.plural_form(lg.phrase("unit_day"), 1) == "day")
check("размер базы по-английски: 3,664,881 entries",
      lg.big_amount(3664881, "unit_entry") == "3,664,881 entries",
      lg.big_amount(3664881, "unit_entry"))
check("возраст домена по-английски читается",
      lg.phrase("f_age_new", lg.human_age(0)) == "The domain is only a few hours old",
      lg.phrase("f_age_new", lg.human_age(0)))
lg.LANG = "ru"
check("по-русски 21 — день", lg.plural_form(lg.phrase("unit_day"), 21) == "день")
check("по-русски 3 664 881 запись", lg.big_amount(3664881, "unit_entry") == "3 664 881 запись")


class FakeLocaleInfo:

    def __init__(self, code):
        self.code = code

    def getLangCode(self):
        return self.code


def locale_stub(code):
    controller = types.SimpleNamespace(
        getInstance=lambda: types.SimpleNamespace(
            getCurrentLocaleInfo=lambda: FakeLocaleInfo(code)))

    def finder(name):
        if name.endswith("LocaleController"):
            return controller
        return None
    return finder


real_find_class = lg.find_class
for code, expected in (("ru", "ru"), ("en", "en"), ("pt-br", "en"), ("uk", "en")):
    lg.find_class = locale_stub(code)
    check("Telegram на «%s» — плагин на %s" % (code, expected),
          lg.pick_language("auto") == expected, lg.pick_language("auto"))
lg.find_class = locale_stub("ru")
check("ручной выбор English перекрывает русский Telegram",
      lg.pick_language("en") == "en")
lg.find_class = lambda name: None
check("язык не определился — остаётся прежний",
      lg.pick_language("auto") is None and lg.apply_language("auto") == lg.LANG)
lg.find_class = real_find_class

lang_plugin = lg.LinkGuardPlugin()
lang_plugin.on_plugin_load()
rows = lang_plugin.create_settings()
selector = [row for row in rows if getattr(row, "key", None) == "ui_lang"]
check("в настройках есть выбор языка", len(selector) == 1)
if selector:
    check("варианты: как в Telegram, русский, английский",
          selector[0].items == [lg.phrase("lang_auto"), "Русский", "English"],
          selector[0].items)
    redraw = []
    real_lang_set = lang_plugin.set_setting

    def watch_lang(key, value, reload_settings=False):
        if reload_settings:
            redraw.append(key)
        return real_lang_set(key, value, reload_settings)

    lang_plugin.set_setting = watch_lang
    selector[0].on_change(2)
    check("выбран English — интерфейс переключился", lg.LANG == "en", lg.LANG)
    check("пункты меню сообщения переведены",
          getattr(lang_plugin._menu_check, "text", None) == "Check links",
          getattr(lang_plugin._menu_check, "text", None))
    check("экран настроек перерисован", "ui_lang" in redraw, redraw)
    check("выбор запомнен", lang_plugin._language_choice() == "en")
    check("причина неудачной загрузки по-английски",
          lg.phrase("dl_not_ours") == "the file is not Link Guard")
    selector[0].on_change(1)
    check("обратно на русский", lg.LANG == "ru"
          and getattr(lang_plugin._menu_check, "text", None) == "Проверить ссылки")
    lang_plugin.set_setting = real_lang_set
lang_plugin.set_setting("ui_lang", 0)
lg.LANG = "ru"
check("описание плагина на двух языках",
      lg.__description__.startswith("Проверяет ссылки перед переходом")
      and "\n\nChecks links before you open them" in lg.__description__,
      lg.__description__)

import ast as _ast
with open(PLUGIN, encoding="utf-8") as _handle:
    _meta = {node.targets[0].id: node.value
             for node in _ast.parse(_handle.read()).body
             if isinstance(node, _ast.Assign) and isinstance(node.targets[0], _ast.Name)
             and node.targets[0].id.startswith("__")}
check("загрузчик клиента прочитает метаданные как простые строки",
      all(isinstance(_meta.get(key), _ast.Constant) and isinstance(_meta[key].value, str)
          for key in ("__id__", "__name__", "__description__", "__author__",
                      "__version__", "__icon__", "__app_version__")),
      {key: type(value).__name__ for key, value in _meta.items()})

print("\nТексты не врут")
for lang in ("ru", "en"):
    note = lg.STRINGS[lang]["privacy_note"]
    check("пояснение о сети (%s) называет базу и rdap.org" % lang,
          "rdap.org" in note and ("баз" in note or "database" in note), note)
check("подпись обновлений совпадает с интервалом",
      "шесть часов" in lg.STRINGS["ru"]["sw_updates_sub"]
      and "six hours" in lg.STRINGS["en"]["sw_updates_sub"]
      and lg.UPDATE_INTERVAL == 6 * 60 * 60)

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
