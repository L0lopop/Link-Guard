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
    """Повторяет AlertDialogBuilder ровно настолько, чтобы поймать ошибки вызова."""

    last = None

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

    def show(self):
        self.shown = True

    def dismiss(self):
        self.shown = False

    def press(self, button="positive"):
        self.buttons[button][1](self, 0)


class FakeMethod:
    """java.lang.reflect.Method: считаем повторные вызовы openUrl."""

    def __init__(self):
        self.calls = []

    def invoke(self, obj, *args):
        self.calls.append(args)


class FakeParam:
    """Аналог param из Xposed-хука."""

    def __init__(self, url):
        self.args = [object(), url]
        self.method = FakeMethod()
        self.cancelled = False

    def setResult(self, value):
        self.cancelled = True


def install_stubs():
    hooks_installed = []

    class BasePlugin:
        def __init__(self):
            self._settings = {}
            self.installed_hooks = hooks_installed

        def get_setting(self, key, default=None):
            return self._settings.get(key, default)

        def set_setting(self, key, value, reload_settings=False):
            self._settings[key] = value

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
        # Настоящий MethodHook — обычный класс без конструктора: любой аргумент
        # при создании даёт TypeError. Заглушка обязана вести себя так же.
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
    _stub("client_utils", get_last_fragment=lambda: fragment,
          run_on_queue=lambda fn, *a, **kw: fn())
    _stub("android_utils", log=lambda *a: None, run_on_ui_thread=lambda f, d=0: f(),
          copy_to_clipboard=lambda t: None)
    _stub("hook_utils", find_class=lambda name: types.SimpleNamespace(name=name))


def install_minimal_stubs():
    """SDK старого клиента: есть только базовый набор, без меню, хуков и UI."""
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

v = lg.analyze("https://sbеrbank.ru/")  # кириллическая 'е'
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

# Регрессии на ложные срабатывания.
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
          and "Опасная" in (FakeDialog.last.title or ""),
          FakeDialog.last.title if FakeDialog.last else None)
    check("в диалоге есть кнопка отмены", "negative" in FakeDialog.last.buttons)

if handler is not None:
    print("\nПовторный вызов после одобрения")
    param = FakeParam("https://gosuslugi.ru.verify.cyou/enter")
    handler.before_hooked_method(param)
    FakeDialog.last.press("positive")
    check("после «Открыть» ссылка переоткрыта", len(param.method.calls) == 1,
          param.method.calls)

    nested = FakeParam("https://gosuslugi.ru.verify.cyou/enter")
    handler.before_hooked_method(nested)
    check("вложенная перегрузка не поднимает второй диалог", not nested.cancelled)

    plugin.APPROVAL_WINDOW = -1  # одобрение просрочено
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
check("о той же версии второй раз не напоминает", FakeDialog.last is None)

lg.fetch_update_info = lambda timeout=8: {"version": "0.0.1"}
FakeDialog.last = None
plugin._check_updates(manual=True)
check("старая версия в репозитории игнорируется", FakeDialog.last is None)

lg.fetch_update_info = lambda timeout=8: None
plugin._check_updates(manual=True)
check("недоступный репозиторий не роняет плагин", True)
lg.fetch_update_info = real_fetch

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
