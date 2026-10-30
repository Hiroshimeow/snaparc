import ctypes
import sys
import threading
import types
import unittest
from unittest.mock import patch


_ORIGINAL_WINDLL = getattr(ctypes, "windll", None)
_MODULE_OVERRIDES = {
    "src.gui": sys.modules.get("src.gui"),
    "src.gui.FloatingWindow": sys.modules.get("src.gui.FloatingWindow"),
    "src.handlers.InputHandler": sys.modules.get("src.handlers.InputHandler"),
    "src.utils.icon_animator": sys.modules.get("src.utils.icon_animator"),
    "src.utils.HotkeyManager": sys.modules.get("src.utils.HotkeyManager"),
    "src.utils.FollowManager": sys.modules.get("src.utils.FollowManager"),
    "keyboard": sys.modules.get("keyboard"),
    "soundcard": sys.modules.get("soundcard"),
    "soundfile": sys.modules.get("soundfile"),
}

floating_window_module = types.ModuleType("src.gui.FloatingWindow")
floating_window_module.show_floating_window = lambda *_args, **_kwargs: None
floating_window_module.FLOAT_WINDOW_LIST = []
sys.modules.setdefault("src.gui.FloatingWindow", floating_window_module)

gui_package = types.ModuleType("src.gui")
gui_package.FloatingWindow = floating_window_module
sys.modules.setdefault("src.gui", gui_package)

sys.modules.setdefault(
    "src.handlers.InputHandler",
    types.SimpleNamespace(InputHandler=type("InputHandler", (), {})),
)
sys.modules.setdefault(
    "src.utils.icon_animator",
    types.SimpleNamespace(IconAnimator=type("IconAnimator", (), {})),
)
sys.modules.setdefault(
    "src.utils.HotkeyManager",
    types.SimpleNamespace(HotkeyManager=type("HotkeyManager", (), {})),
)
sys.modules.setdefault(
    "src.utils.FollowManager",
    types.SimpleNamespace(FollowManager=type("FollowManager", (), {})),
)
sys.modules.setdefault("keyboard", types.SimpleNamespace())
sys.modules.setdefault("soundcard", types.SimpleNamespace())
sys.modules.setdefault(
    "soundfile",
    types.SimpleNamespace(
        write=lambda filepath, _audio, _sr: open(filepath, "wb").write(b"audio")
    ),
)



class _DummyUser32:
    def SetProcessDpiAwarenessContext(self, *_args):
        return 0

    def SetProcessDPIAware(self):
        return 1


class _DummyShcore:
    def SetProcessDpiAwareness(self, *_args):
        return 1


class _DummyKernel32:
    class _DummyCFunc:
        def __call__(self, *_args, **_kwargs):
            return 1

    def __getattr__(self, _name):
        return self._DummyCFunc()


_IMPORT_WINDLL = types.SimpleNamespace(
    user32=_DummyUser32(),
    shcore=_DummyShcore(),
    kernel32=_DummyKernel32(),
)
with patch.object(ctypes, "windll", _IMPORT_WINDLL):
    import main  # noqa: E402


def tearDownModule():
    for module_name, original in _MODULE_OVERRIDES.items():
        if original is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original




class _FakeWin:
    def __init__(self, exists=True):
        self._exists = exists

    def winfo_exists(self):
        return self._exists


class _FakeFloatingWindow:
    def __init__(self, exists=True):
        self.win = _FakeWin(exists=exists)


class _ImmediateRoot:
    def after(self, _delay_ms, callback):
        callback()


class _NeverRunsRoot:
    def after(self, _delay_ms, _callback):
        return None


class _RaisingRoot:
    def after(self, _delay_ms, _callback):
        raise RuntimeError("after failed")


class MainFloatAudioOutputBridgeTests(unittest.TestCase):
    def test_get_latest_live_float_window_skips_closed_windows(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        stale = _FakeFloatingWindow(exists=False)
        fresh = _FakeFloatingWindow(exists=True)

        with patch.object(main, "FLOAT_WINDOW_LIST", [stale, fresh]):
            result = app._get_latest_live_float_window()

        self.assertIs(result, fresh)

    def test_create_float_stt_window_forces_new_packet_and_returns_latest(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        latest = _FakeFloatingWindow(exists=True)
        captured = {}
        app.root = object()
        app.model_client = types.SimpleNamespace(model="cliproxy/gpt-5.4")
        app.recorder = types.SimpleNamespace(get_duration=lambda: 0)
        app.toggle_audio_recording = lambda *args, **kwargs: None
        app.capture_transcribe_snapshot = lambda *args, **kwargs: None
        app.select_audio_files_for_stt = lambda *args, **kwargs: None
        app.toggle_pause_recording = lambda *args, **kwargs: None
        app.toggle_follow_mode = lambda *args, **kwargs: None
        app._get_follow_state = lambda *args, **kwargs: {}
        app._get_audio_stt_state = lambda *args, **kwargs: {"running": False, "job_count": 0}
        app._get_latest_live_float_window = lambda: latest

        def fake_show_floating_window(_root, packet):
            captured["packet"] = packet

        with patch.object(main, "show_floating_window", side_effect=fake_show_floating_window):
            result = app._create_float_stt_window()

        packet = captured["packet"]
        self.assertIs(result, latest)
        self.assertTrue(packet.create_new)
        self.assertTrue(packet.skip_history)
        self.assertEqual(packet.prompt_name, "STT")
        self.assertEqual(packet.model_name, "cliproxy/gpt-5.4")
        self.assertIs(packet.select_audio_files_callback, app.select_audio_files_for_stt)
        self.assertIs(packet.get_audio_stt_state_callback, app._get_audio_stt_state)

    def test_capture_transcribe_snapshot_delegates_to_action_service(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        calls = []
        app.model_client = types.SimpleNamespace(model="cliproxy/gpt-5.4")
        app.float_audio_action_service = types.SimpleNamespace(
            transcribe_latest=lambda selected_model=None, window_instance=None: calls.append(
                (selected_model, window_instance)
            )
        )
        window = object()

        result = app.capture_transcribe_snapshot(window_instance=window)

        self.assertFalse(result)
        self.assertEqual(calls, [("cliproxy/gpt-5.4", window)])

    def test_select_audio_files_for_stt_delegates_to_action_service(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        calls = []
        root = object()
        app.root = root
        app.model_client = types.SimpleNamespace(model="cliproxy/gpt-5.4")
        app.float_audio_action_service = types.SimpleNamespace(
            transcribe_selected_files=lambda parent=None, selected_model=None: (
                calls.append((parent, selected_model)),
                "started",
            )[1]
        )

        result = app.select_audio_files_for_stt()

        self.assertEqual(result, "started")
        self.assertEqual(calls, [(root, "cliproxy/gpt-5.4")])

    def test_handle_model_result_passes_stt_callbacks_into_packet_factory(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = object()
        app.model_client = types.SimpleNamespace(model="cliproxy/gpt-5.4")
        app.animator = types.SimpleNamespace(stop_animation=lambda: None)
        app.PROMPT_DISPLAY_NAMES = {"prompt_fix_grammar": "Fix prompt"}
        app.screenshots_dir = "screens"
        app.recorder = types.SimpleNamespace(get_duration=lambda: 0)
        app.history_manager = types.SimpleNamespace(write_to_html=lambda **kwargs: None)
        app.rerun_model_processing = lambda *args, **kwargs: None
        app.toggle_audio_recording = lambda *args, **kwargs: None
        app.capture_transcribe_snapshot = lambda *args, **kwargs: None
        app.select_audio_files_for_stt = lambda *args, **kwargs: None
        app.toggle_pause_recording = lambda *args, **kwargs: None
        app.toggle_follow_mode = lambda *args, **kwargs: None
        app._get_follow_state = lambda *args, **kwargs: {}
        app._get_audio_stt_state = lambda *args, **kwargs: {"running": False, "job_count": 0}

        captured = {}

        def fake_from_model_result(**kwargs):
            captured["kwargs"] = kwargs
            return types.SimpleNamespace(ans="ok")

        with patch.object(main.DataPacket, "from_model_result", side_effect=fake_from_model_result), patch.object(
            main, "show_floating_window", side_effect=lambda _root, _packet: None
        ):
            app._handle_model_result(
                result={"text": "hello"},
                timestamp_str="20260409_010101",
                is_comparison_rerun=False,
                force_new_window=False,
                custom_prompt_type="prompt_fix_grammar",
            )

        kwargs = captured["kwargs"]
        self.assertIs(kwargs["transcribe_callback_provider"], app.capture_transcribe_snapshot)
        self.assertIs(kwargs["select_audio_files_callback_provider"], app.select_audio_files_for_stt)
        self.assertIs(kwargs["get_audio_stt_state_callback_provider"], app._get_audio_stt_state)

    def test_run_on_ui_thread_returns_result_from_worker_thread(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = _ImmediateRoot()
        output = {}

        def worker():
            output["value"] = app._run_on_ui_thread(lambda: "done")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(output["value"], "done")

    def test_run_on_ui_thread_executes_inline_on_main_thread(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = _ImmediateRoot()

        result = app._run_on_ui_thread(lambda: "inline")

        self.assertEqual(result, "inline")

    def test_run_on_ui_thread_returns_false_without_root_after(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = object()

        output = {}

        def worker():
            output["value"] = app._run_on_ui_thread(lambda: "done")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertFalse(output["value"])

    def test_run_on_ui_thread_returns_false_when_after_raises(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = _RaisingRoot()
        output = {}

        def worker():
            output["value"] = app._run_on_ui_thread(lambda: "done")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertFalse(output["value"])

    def test_run_on_ui_thread_times_out_when_ui_never_executes_callback(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = _NeverRunsRoot()
        output = {}

        def worker():
            output["value"] = app._run_on_ui_thread(lambda: "done")

        with patch.object(main, "UI_DISPATCH_TIMEOUT_SECONDS", 0.01):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()

        self.assertFalse(output["value"])

    def test_run_on_ui_thread_propagates_callback_error(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.root = _ImmediateRoot()
        output = {}

        def worker():
            try:
                app._run_on_ui_thread(lambda: (_ for _ in ()).throw(ValueError("boom")))
            except Exception as exc:
                output["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertIsInstance(output["error"], ValueError)
        self.assertEqual(str(output["error"]), "boom")

    def test_model_menu_uses_normalized_model_registry(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.model_client = types.SimpleNamespace(model="a5k/qwen256a", provider="a5k")
        app.config_manager = types.SimpleNamespace(
            get_all_models=lambda: {
                "a5k/qwen256a": {},
                "a5k/other-model": {},
                "cliproxy/gemini-3-flash": {},
            },
            get_provider_config=lambda provider_name: {
                "a5k": {"displayName": "A5K"},
                "cliproxy": {"displayName": "CliProxy"},
            }.get(provider_name, {}),
            get=lambda key, default=None: default,
        )

        def fake_menu(*items):
            return {"items": list(items)}

        def fake_menu_item(*args, **kwargs):
            text = kwargs.pop("text", args[0] if args else "")
            action = kwargs.pop("action", args[1] if len(args) > 1 else None)
            return {"text": text, "action": action, **kwargs}

        with patch.object(main, "Menu", side_effect=fake_menu), patch.object(
            main, "MenuItem", side_effect=fake_menu_item
        ):
            menus = app._build_provider_menus()

        self.assertEqual([item["text"] for item in menus], ["A5K", "CliProxy"])
        a5k_models = menus[0]["action"]["items"]
        self.assertEqual([item["text"] for item in a5k_models], ["other-model", "qwen256a"])
        selected_item = next(item for item in a5k_models if item["text"] == "qwen256a")
        self.assertTrue(selected_item["checked"](None))
        self.assertTrue(selected_item["radio"])

    def test_model_menu_stays_visible_when_no_models_are_found(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.model_client = types.SimpleNamespace(model="", provider="")
        app.config_manager = types.SimpleNamespace(
            get_all_models=lambda: {},
            get_provider_config=lambda provider_name: {},
            get=lambda key, default=None: default,
        )

        def fake_menu_item(*args, **kwargs):
            text = kwargs.pop("text", args[0] if args else "")
            action = kwargs.pop("action", args[1] if len(args) > 1 else None)
            return {"text": text, "action": action, **kwargs}

        with patch.object(main, "MenuItem", side_effect=fake_menu_item):
            menus = app._build_provider_menus()

        self.assertEqual(len(menus), 1)
        self.assertEqual(menus[0]["text"], "(no models found in config.yaml)")
        self.assertFalse(menus[0]["enabled"])

    def test_model_menu_title_does_not_show_unregistered_fallback_model(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.model_client = types.SimpleNamespace(model="ghost/model", provider="ghost")
        app.config_manager = types.SimpleNamespace(
            get_all_models=lambda: {},
            get=lambda key, default=None: "ghost/model"
            if key == "agents.defaults.model.primary"
            else default,
        )

        self.assertEqual(app._model_menu_title(), "Model: not configured")

    def test_model_menu_title_shows_registered_model(self):
        app = main.ScreenshotApp.__new__(main.ScreenshotApp)
        app.model_client = types.SimpleNamespace(model="a5k/qwen256a", provider="a5k")
        app.config_manager = types.SimpleNamespace(
            get_all_models=lambda: {"a5k/qwen256a": {}},
            get=lambda key, default=None: default,
        )

        self.assertEqual(app._model_menu_title(), "Model: a5k/qwen256a")


if __name__ == "__main__":
    unittest.main()
