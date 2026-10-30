# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pystray",
#     "pillow",
#     "pyperclip",
# #     "pynput",
#     "keyboard",
#     "python-dotenv",
#     "requests",
#     "customtkinter",
#     "pyautogui",
#     "openai",
#     "pywebview",
#     "numpy",
#     "soundcard",
#     "soundfile",
# ]
# ///

# main.py — Screens-Trans-Chatbot v2.4
import ctypes
from datetime import datetime
from functools import partial
import os
import queue
import sys
import threading
import time
import tkinter as tk

from PIL import Image, ImageGrab
from pystray import Icon, Menu, MenuItem

from src.core.event_bus import AppEventBus
from src.core.memory_guard import MemoryGuard
from src.core.model_state import ModelStateStore
from src.gui.FloatingWindow import FLOAT_WINDOW_LIST, show_floating_window
try:
    from src.gui.custom_prompt_dialog import show_custom_prompt_dialog
except ImportError:
    show_custom_prompt_dialog = None
from src.gui.quick_prompt_popup import QuickPromptPopup, QuickPromptSubmission
from src.gui.radial_wheel import RadialWheelOverlay
from src.handlers.InputHandler import InputHandler
from src.models.Gemini import Gemini
from src.services.float_audio_actions import FloatAudioActionService
from src.services.float_audio_service import FloatAudioOutputSink, FloatAudioSourceResolver
from src.services.radial_plugin_service import RadialPluginResult, RadialPluginService, RadialPluginSpec
from src.services.snap_modifier_actions import (
    ACTION_CUSTOM_PROMPT,
    ACTION_CUSTOM_PROMPT_NEW_SESSION,
    ACTION_RADIAL_WHEEL,
    ACTION_TEXT_FILE_PROMPT,
    SnapActionRouter,
)
from src.utils.AudioRecorder import AudioRecorder
from src.utils.ConfigManager import ConfigManager
from src.utils.DataPacket import DataPacket
from src.utils.FollowManager import FollowManager
from src.utils.HotkeyManager import HotkeyManager
from src.utils.custom_prompt_history import PromptHistoryStore
from src.utils.icon_animator import IconAnimator

# Define Base Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
UI_DISPATCH_TIMEOUT_SECONDS = 5.0


def enable_per_monitor_dpi_awareness():
    if sys.platform != "win32":
        return

    user32 = ctypes.windll.user32
    shcore = getattr(ctypes.windll, "shcore", None)

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass

    if shcore is not None:
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_virtual_screen_rect():
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, width, height


def capture_screen():
    return ImageGrab.grab(all_screens=True)


enable_per_monitor_dpi_awareness()


class ScreenshotApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

        # --- Config Manager & Core v1.1 State ---
        self.config_manager = ConfigManager(os.path.join(BASE_DIR, "config.yaml"))
        self.event_bus = AppEventBus()
        self.model_state_store = ModelStateStore(self.config_manager, self.event_bus)
        self.memory_guard = MemoryGuard(max_float_windows=6, max_screenshot_files=80)

        # --- Runtime state ---
        self.local_screen_window = None
        self.result_queue = queue.Queue()
        self._capture_queue = queue.Queue()
        self._capture_stop = threading.Event()
        self._capture_worker = None
        self._clipboard_transaction_lock = threading.Lock()
        self.held_images = []
        self.screenshot_chat_module = None
        self.follow_manager = FollowManager(os.path.join(BASE_DIR, "config.yaml"))
        self.gemini_start_time = None
        self.run_flag = 0  # 0=running, 1=paused
        self.current_icon = None
        self.restart_requested = False

        # UI variables for Shift + Hold logic
        self.hold_count_label = None
        self.hold_count_window = None
        self._hold_count_hide_timer = None
        self._hold_count_follow_timer = None

        self.PROMPT_DISPLAY_NAMES = {
            "prompt_fix_grammar": "FixPrompt",
            "prompt_summarize": "Sum",
            "prompt_translate": "Trans",
            "prompt_extract_text": "Original",
            "prompt_expert": "Expert",
            "custom_prompt": "Custom",
            "text_file_prompt": "FileQ&A",
        }

        # --- Init Gemini ---
        self.gemini = Gemini()

        # --- Load Config & Model List ---
        try:
            self.config, _ = self.gemini._load_config()
            print("--- Config Loaded (v1.1) ---")
            print(f"Initial Prompt Type: {self.gemini.prompt_type}")
            print(f"Initial Model: {self.gemini.model}")
            print(f"Initial Provider: {self.gemini.provider}")

            ui_config = self.config_manager.get("ui") or {}
            animation_speed = float(ui_config.get("animation_speed", 0.1))
            ani_folder = ui_config.get("ani_folder", "src/animate")

            self.all_models = self.config_manager.get_all_models()
            print(f"Models found in config.yaml: {list(self.all_models.keys())}")
            print("----------------------------")
        except Exception as e:
            print(f"Error loading config/animation: {e}")
            self.config = {}
            self.all_models = {}
            animation_speed = 0.1
            ani_folder = "src/animate"

        self.snap_action_router = SnapActionRouter.from_config(
            self.config_manager.get("snap_modifier_actions", [])
        )
        custom_prompt_history_limit = self.config_manager.get(
            "custom_prompt_history.limit", 20
        )
        self.custom_prompt_history = PromptHistoryStore(
            os.path.join(BASE_DIR, "data", "custom_prompt_history.json"),
            limit=int(custom_prompt_history_limit or 20),
        )

        # --- Radial Plugin Wheel Service & Overlay (Ctrl + Alt + Hold 0.2s) ---
        self.radial_plugin_service = RadialPluginService(repo_root=BASE_DIR)
        self.radial_plugin_service.register_builtin_action(
            "open_pi_chat", self._radial_action_open_pi_chat
        )
        self.radial_plugin_service.register_builtin_action(
            "open_quick_popup", self._radial_action_open_quick_popup
        )
        self.radial_plugin_service.register_builtin_action(
            "run_memory_cleanup", self._radial_action_memory_cleanup
        )
        self.radial_wheel = RadialWheelOverlay(
            self.root,
            self.radial_plugin_service,
            on_select=self._on_radial_plugin_selected,
        )

        # --- Load Icons ---
        try:
            self.image = Image.open(os.path.join(BASE_DIR, "src/icons/icon.png"))
            self.image_pause = Image.open(
                os.path.join(BASE_DIR, "src/icons/icon_pause")
            )
            self.icon_spi = Image.open(os.path.join(BASE_DIR, "src/icons/spi.png"))
            self.icon_fix = Image.open(os.path.join(BASE_DIR, "src/icons/icon_fix.png"))
            self.icon_trans = Image.open(os.path.join(BASE_DIR, "src/icons/trans.png"))
            self.icon_sum = Image.open(os.path.join(BASE_DIR, "src/icons/sum.png"))
            self.icon_voice_on = Image.open(
                os.path.join(BASE_DIR, "src/icons/voice_on.png")
            )
            self.icon_voice_off = Image.open(
                os.path.join(BASE_DIR, "src/icons/voice_off.png")
            )
            self.icon_left_on = self.icon_voice_on
            self.icon_left_off = self.icon_voice_off
        except Exception as e:
            print(f"Loi icon: {e}. Dung mau thay the.")
            self.image = Image.new("RGB", (64, 64), color=(70, 130, 180))
            self.image_pause = Image.new("RGB", (64, 64), color=(255, 165, 0))
            self.icon_spi = Image.new("RGB", (64, 64), color=(255, 0, 0))
            self.icon_fix = Image.new("RGB", (64, 64), color=(128, 0, 128))
            self.icon_trans = Image.new("RGB", (64, 64), color=(0, 255, 0))
            self.icon_sum = Image.new("RGB", (64, 64), color=(255, 255, 0))
            self.icon_left_on = Image.new("RGB", (64, 64), color=(0, 0, 255))
            self.icon_left_off = Image.new("RGB", (64, 64), color=(100, 100, 255))

        prompt_icons = {
            "prompt_fix_grammar": self.icon_fix,
            "prompt_summarize": self.icon_sum,
            "prompt_translate": self.icon_trans,
            "prompt_extract_text": self.icon_spi,
        }
        self.current_icon = prompt_icons.get(self.gemini.prompt_type, self.image)

        # --- Setup Menu (Dynamic callable title fixes stuck model label bug!) ---
        provider_menus = self._build_provider_menus()

        self._ensure_tray_state_attrs()
        menu = Menu(
            MenuItem(
                "Pause/Resume",
                self._tray_toggle_pause,
                checked=lambda item: self.run_flag == 1,
                default=lambda item: self._tray_left_click_action() == "toggle_pause",
            ),
            MenuItem(lambda item: self._model_menu_title(), Menu(*provider_menus)),
            MenuItem("--- Prompts ---", None, enabled=False),
            MenuItem(
                "Fix prompt",
                partial(
                    self.set_prompt_type,
                    "prompt_fix_grammar",
                    self.icon_fix,
                    fix_prompt_key=1,
                ),
                checked=lambda item: self.gemini.prompt_type == "prompt_fix_grammar",
                radio=True,
            ),
            MenuItem(
                "Sum",
                partial(self.set_prompt_type, "prompt_summarize", self.icon_sum),
                checked=lambda item: self.gemini.prompt_type == "prompt_summarize",
                radio=True,
            ),
            MenuItem(
                "Trans",
                partial(self.set_prompt_type, "prompt_translate", self.icon_trans),
                checked=lambda item: self.gemini.prompt_type == "prompt_translate",
                radio=True,
            ),
            MenuItem(
                "Original",
                partial(self.set_prompt_type, "prompt_extract_text", self.icon_spi),
                checked=lambda item: self.gemini.prompt_type == "prompt_extract_text",
                radio=True,
            ),
            MenuItem("--- Actions ---", None, enabled=False),
            MenuItem(
                "Open Chat Screen (Ctrl+Shift+Alt+X)",
                lambda icon=None, item=None: self._open_screenshot_chat({"mode": "show"}),
            ),
            MenuItem(
                "Quick File/Text Popup (Alt+Snap)",
                lambda icon=None, item=None: self.root.after(0, self._show_text_file_quick_popup, [], ""),
            ),
            MenuItem(
                "Record Audio",
                self._tray_toggle_recording,
                checked=lambda item: getattr(self.recorder, "is_recording", False),
            ),
            MenuItem(
                "Left Mouse Mode",
                self.toggle_left_click_mode,
                checked=lambda item: self.input_handler.left_click_mode_enabled,
            ),
            MenuItem(
                "Edit Prompts",
                lambda icon=None, item=None: self.open_config_file("config.yaml"),
            ),
            MenuItem(
                "Edit Radial Plugins (plugins.yaml)",
                lambda icon=None, item=None: self.open_config_file("plugins.yaml"),
            ),
            MenuItem(
                "Edit Settings & Hotkeys",
                lambda icon=None, item=None: self.open_config_file("config.yaml"),
            ),
            MenuItem("Restart App", self.reset_app),
            MenuItem("Exit", self.request_exit),
        )
        self.icon = Icon("SS", self.current_icon, "Tool", menu)

        # --- Components ---
        self.input_handler = InputHandler(
            on_capture=self._capture_and_process,
            on_radial_open=self._on_radial_open,
            on_radial_move=self._on_radial_move,
            on_radial_release=self._on_radial_release,
            on_alt_quick_popup=self._on_alt_quick_popup,
            radial_hold_seconds=self.radial_plugin_service.style.hold_seconds,
        )
        self.animator = IconAnimator(self.icon, animation_speed, ani_folder, self.root)
        self.animator.current_icon = self.current_icon
        self.recorder = AudioRecorder()
        self.float_audio_source_resolver = FloatAudioSourceResolver(self.recorder)
        self.float_audio_output_sink = FloatAudioOutputSink(
            latest_window_getter=self._get_latest_live_float_window,
            create_window_fn=self._create_float_stt_window,
            dispatch_fn=self._run_on_ui_thread,
        )
        self.float_audio_action_service = FloatAudioActionService(
            source_resolver=self.float_audio_source_resolver,
            output_sink=self.float_audio_output_sink,
            model_resolver=self.gemini.audio_model_resolver,
            transcribe_audio_fn=self.gemini.llm_service.process_audio,
            config_manager=self.config_manager,
            postprocess_text_fn=self.gemini.llm_service.send_text_message,
        )
        self.input_handler.start_mouse_listener()

        # --- Hotkey Manager ---
        self.hotkey_manager = HotkeyManager(
            callback_map={
                "process_clipboard": self._process_clipboard,
                "open_screenshot_chat": self._open_screenshot_chat,
            }
        )
        self.hotkey_manager.start(on_error=self._on_hotkey_error)
        self.root.after(250, self._prewarm_screenshot_chat)

        # --- Directories ---
        self.screenshots_dir = os.path.join(BASE_DIR, "src/screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)
        self._temp_files_to_cleanup = []
        self._cleanup_lock = threading.Lock()

        self._capture_worker = threading.Thread(
            target=self._capture_worker_loop,
            name="capture-worker",
            daemon=True,
        )
        self._capture_worker.start()

    # ------------------------------------------------------------------
    # Radial Plugin Wheel (`Ctrl + Alt + Hold Mouse 0.2s`)
    # ------------------------------------------------------------------
    def _on_radial_open(self, x: int, y: int):
        if self.run_flag == 1:
            return
        try:
            from src.utils.file_context_extractor import resolve_active_desktop_file_context
            active_files = resolve_active_desktop_file_context(
                extra_sources=list(self.held_images),
                cursor_pos=(int(x), int(y)),
            )
        except Exception:
            active_files = list(self.held_images)
        self.root.after(0, lambda: self.radial_wheel.show_at(int(x), int(y), active_files=active_files))

    def _on_radial_move(self, x: int, y: int):
        if self.run_flag == 1:
            return
        self.root.after(0, self.radial_wheel.update_cursor, int(x), int(y))

    def _on_radial_release(self, x: int, y: int):
        if self.run_flag == 1:
            return
        self.root.after(0, self.radial_wheel.complete_at, int(x), int(y))

    def _on_radial_plugin_selected(
        self,
        spec: RadialPluginSpec,
        origin: tuple[int, int],
        active_files: list[str] | None = None,
    ):
        """Route selected radial plugin: if it has parameters (or needs a file), show compact popup; otherwise run immediately."""
        try:
            from src.utils.file_context_extractor import resolve_active_desktop_file_context
            files = (
                list(active_files)
                if active_files
                else resolve_active_desktop_file_context(
                    extra_sources=list(self.held_images),
                    cursor_pos=(int(origin[0]), int(origin[1])),
                )
            )
        except Exception:
            files = list(active_files or self.held_images)

        fresh_spec = self.radial_plugin_service.get_plugin(spec.id) or spec
        if fresh_spec.has_params or (fresh_spec.requires_file_input and not files):
            self.root.after(
                0,
                lambda: self.radial_wheel.open_param_popup(
                    spec=fresh_spec,
                    active_files=files,
                    origin=origin,
                    on_run=lambda param_values, final_files: self._execute_radial_plugin_with_params(
                        fresh_spec,
                        origin,
                        final_files,
                        param_values,
                    ),
                ),
            )
            return

        self._execute_radial_plugin_with_params(fresh_spec, origin, files, {})

    def _execute_radial_plugin_with_params(
        self,
        spec: RadialPluginSpec,
        origin: tuple[int, int],
        files: list[str],
        param_values: dict[str, str] | None = None,
    ):
        """Execute selected radial plugin in a background worker thread with optional parameter overrides."""
        clip_payload = self._snapshot_clipboard_payload()
        clip_text = str(clip_payload[1] or "") if (clip_payload and clip_payload[0] == "text") else ""

        context = {
            "cursor": [origin[0], origin[1]],
            "clipboard_text": clip_text,
            "files": list(files or []),
            "model": self.gemini.model,
            "provider": self.gemini.provider,
            "params": dict(param_values or {}),
        }

        def _worker():
            res = self.radial_plugin_service.execute_plugin(
                spec.id,
                context=context,
                param_overrides=dict(param_values or {}),
            )
            self.root.after(0, self._handle_radial_plugin_result, res)

        threading.Thread(target=_worker, name=f"radial-plugin-{spec.id}", daemon=True).start()

    def _handle_radial_plugin_result(self, res: RadialPluginResult):
        if res.copy_to_clipboard:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(res.copy_to_clipboard)
            except Exception:
                pass

        if res.output_mode in ("chat_screen", "popup"):
            return

        # If plugin created files (e.g. PDF -> PNG generated pages), attach images to held_images
        gen_files = res.generated_files or []
        for gf in gen_files:
            if gf and os.path.isfile(gf) and gf.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                if gf not in self.held_images:
                    self.held_images.append(gf)

        packet = DataPacket(
            ans=res.markdown,
            model_name=self.gemini.model,
            prompt_name=res.title or res.plugin_name,
            create_new=True,
            image_path=res.generated_files[0] if res.generated_files else "",
            original_timestamp_str=time.strftime("%Y%m%d_%H%M%S"),
            record_audio_callback=self.toggle_audio_recording,
            transcribe_callback=self.capture_transcribe_snapshot,
            select_audio_files_callback=self.select_audio_files_for_stt,
            get_audio_duration_callback=self.recorder.get_duration,
            pause_audio_callback=self.toggle_pause_recording,
            gemini_instance=self.gemini,
            toggle_follow_callback=self.toggle_follow_mode,
            get_follow_state_callback=self._get_follow_state,
            get_audio_stt_state_callback=self._get_audio_stt_state,
        )
        packet.open_chat_screen_callback = self._escalate_packet_to_chat_screen
        show_floating_window(self.root, packet)

    def _radial_action_open_pi_chat(self, ctx: dict, spec: RadialPluginSpec) -> RadialPluginResult:
        submission = QuickPromptSubmission(
            prompt_text="",
            mode="text_file",
            target="pi_agent",
            context_text=str(ctx.get("clipboard_text") or ""),
        )
        self.root.after(0, self._escalate_popup_to_pi_chat, submission)
        return RadialPluginResult(
            plugin_id=spec.id,
            plugin_name=spec.name,
            ok=True,
            title=spec.name,
            markdown="🤖 Opened Pi Sidecar Chat Screen.",
            output_mode="chat_screen",
            action=spec.action,
        )

    def _radial_action_open_quick_popup(self, ctx: dict, spec: RadialPluginSpec) -> RadialPluginResult:
        files = list(ctx.get("files") or [])
        clip_text = str(ctx.get("clipboard_text") or "")
        self.root.after(0, self._show_text_file_quick_popup, files, clip_text)
        return RadialPluginResult(
            plugin_id=spec.id,
            plugin_name=spec.name,
            ok=True,
            title=spec.name,
            markdown="🗂️ Opened Unified Text/File Quick Popup.",
            output_mode="popup",
            action=spec.action,
        )

    def _radial_action_memory_cleanup(self, _ctx: dict, spec: RadialPluginSpec) -> RadialPluginResult:
        stats = self.memory_guard.enforce_window_cap(FLOAT_WINDOW_LIST)
        pruned_files = self.memory_guard.prune_screenshots_dir(self.screenshots_dir)
        md = (
            "### ⚡ Runtime Memory & Cache Optimized\n\n"
            f"- **Active Floating Windows**: `{stats['active_windows']}` (`{stats['closed_windows']}` stale closed)\n"
            f"- **Old Temp Screenshots Pruned**: `{pruned_files}`"
        )
        return RadialPluginResult(
            plugin_id=spec.id,
            plugin_name=spec.name,
            ok=True,
            title="Boost RAM",
            markdown=md,
            output_mode="floating",
            action=spec.action,
        )

    # ------------------------------------------------------------------
    # Unified Quick Prompt Popup (`Ctrl + Snap` Image / `Alt + Snap` Text & Files)
    # ------------------------------------------------------------------
    def _on_alt_quick_popup(self, _x: int, _y: int, _modifiers=None):
        if self.run_flag == 1:
            return
        clip_payload = self._snapshot_clipboard_payload()
        clip_text = clip_payload[1] if (clip_payload and clip_payload[0] == "text") else ""
        held = list(self.held_images)
        self.held_images = []
        self._hide_hold_count_tooltip()
        self.root.after(0, self._show_text_file_quick_popup, held, clip_text)

    def _show_text_file_quick_popup(self, file_paths=None, initial_text="", timestamp_str=None):
        ts = timestamp_str or time.strftime("%Y%m%d_%H%M%S")
        paths = list(file_paths or [])
        if not initial_text:
            clip_payload = self._snapshot_clipboard_payload()
            if clip_payload and clip_payload[0] == "text":
                initial_text = str(clip_payload[1] or "")

        def _on_submit(combined_prompt: str, submission: QuickPromptSubmission | None = None):
            img_paths = [
                att.path
                for att in (submission.attachments if submission else [])
                if att.kind == "image" and os.path.exists(att.path)
            ]
            if img_paths:
                self._start_image_processing(
                    img_paths,
                    ts,
                    True,
                    custom_prompt_type=ACTION_TEXT_FILE_PROMPT,
                    custom_prompt_text=combined_prompt,
                    source_label="Alt File/Image Popup",
                )
            else:
                self._start_text_processing(
                    combined_prompt,
                    ts,
                    prompt_type=ACTION_TEXT_FILE_PROMPT,
                    source_label="Alt Text/File Popup",
                )

        QuickPromptPopup(
            root=self.root,
            history_store=self.custom_prompt_history,
            default_prompt="",
            on_submit=_on_submit,
            mode="text_file",
            initial_text=initial_text,
            initial_files=paths,
            active_model_label=self.gemini.model,
            on_escalate_pi=self._escalate_popup_to_pi_chat,
        ).show()

    def _start_text_processing(self, text_prompt: str, timestamp_str: str, prompt_type: str, source_label: str):
        self.animator.start_animation()
        request_model_name = self.gemini.model
        request_prompt_name = self.PROMPT_DISPLAY_NAMES.get(prompt_type, "FileQ&A")
        follow_user_message = self._build_follow_text_request(prompt_type, text_prompt, source_label)

        def _cb(result):
            self._remember_follow_exchange(follow_user_message, result)
            self.root.after(
                0,
                self._handle_gemini_result,
                result,
                timestamp_str,
                False,
                True,
                prompt_type,
                request_model_name,
                request_prompt_name,
            )

        self.gemini.send_text_message(
            text_prompt=text_prompt,
            callback=_cb,
            history=self.follow_manager.build_history(),
            model_name=request_model_name,
        )

    def _escalate_popup_to_pi_chat(self, submission: QuickPromptSubmission):
        module = self._ensure_screenshot_chat_module()
        module.controller.escalate_from_quick_popup(submission)

    def _escalate_packet_to_chat_screen(self, packet: DataPacket):
        submission = QuickPromptSubmission(
            prompt_text="",
            mode="text_file",
            target="pi_agent",
            context_text=str(getattr(packet, "ans", "") or ""),
        )
        img_path = getattr(packet, "image_path", "")
        if img_path and os.path.exists(img_path):
            from src.gui.quick_prompt_popup import inspect_and_extract_file
            submission.attachments.append(inspect_and_extract_file(img_path))
        self._escalate_popup_to_pi_chat(submission)

    # ------------------------------------------------------------------
    # Follow Mode & Model Sync
    # ------------------------------------------------------------------
    def _get_follow_state(self):
        return self.follow_manager.get_state()

    def _refresh_follow_indicators(self):
        for floating_window in list(FLOAT_WINDOW_LIST):
            try:
                if floating_window and floating_window.win.winfo_exists():
                    floating_window.refresh_follow_state()
            except Exception:
                pass

    def _refresh_float_window_models(self, model_full_name):
        for floating_window in list(FLOAT_WINDOW_LIST):
            try:
                if floating_window and floating_window.win.winfo_exists():
                    floating_window.set_current_model(model_full_name)
            except Exception:
                pass

    def toggle_follow_mode(self, window_instance=None):
        state = self.follow_manager.toggle()
        print(
            f"Follow mode {'enabled' if state['enabled'] else 'disabled'} "
            f"(history={state['history_count']})"
        )
        self.root.after(0, self._refresh_follow_indicators)
        return state

    def _format_follow_prompt_name(self, prompt_type):
        return self.PROMPT_DISPLAY_NAMES.get(prompt_type, prompt_type or "Unknown")

    def _build_follow_image_request(self, prompt_type, image_path, source_label):
        prompt_name = self._format_follow_prompt_name(prompt_type)
        image_name = os.path.basename(image_path) if image_path else "unknown"
        return (
            f"[{prompt_name}] {source_label}\n"
            f"Image: {image_name}"
        )

    def _build_follow_text_request(self, prompt_type, text, source_label):
        prompt_name = self._format_follow_prompt_name(prompt_type)
        body = (text or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"
        return (
            f"[{prompt_name}] {source_label}\n"
            f"Content:\n{body}"
        )

    def _remember_follow_exchange(self, user_message, result):
        answer_text = ""
        if isinstance(result, dict):
            answer_text = str(result.get("text", "") or "")
        elif result is not None:
            answer_text = str(result)

        self.follow_manager.add_exchange(user_message, answer_text)

    def _open_screenshot_chat(self, hotkey_config=None):
        self.config_manager.reload_if_changed()
        chat_config = self.config_manager.get_screenshot_chat_config()
        if not chat_config.get("enabled", True):
            print("ScreenshotChat: Hotkey ignored because screenshot_chat.enabled is false.")
            return
        mode = self._screenshot_chat_hotkey_mode(hotkey_config)
        self.root.after(0, self._open_screenshot_chat_on_main_thread, mode)

    def _screenshot_chat_hotkey_mode(self, hotkey_config=None):
        hotkey_id = hotkey_config.get("id") if isinstance(hotkey_config, dict) else None
        fresh = None
        if hotkey_id:
            for entry in self.config_manager.get_hotkey_configs():
                if isinstance(entry, dict) and entry.get("id") == hotkey_id:
                    fresh = entry
                    break
        source = fresh if fresh is not None else hotkey_config
        mode = source.get("mode") if isinstance(source, dict) else None
        return str(mode or "toggle")

    def _open_screenshot_chat_on_main_thread(self, mode="toggle"):
        self._ensure_screenshot_chat_module().run_hotkey_action(mode)

    def _prewarm_screenshot_chat(self):
        try:
            chat_config = self.config_manager.get_screenshot_chat_config()
            if not chat_config.get("enabled", True):
                return
            self._ensure_screenshot_chat_module().prewarm()
        except Exception as exc:
            print(f"ScreenshotChat: prewarm failed: {exc}")

    def _ensure_screenshot_chat_module(self):
        if self.screenshot_chat_module is None:
            from src.gui.screenshot_chat.module import ChatbotModule

            self.screenshot_chat_module = ChatbotModule(
                self.root,
                config_manager=self.config_manager,
                project_root=BASE_DIR,
                held_images_getter=self._get_held_images_snapshot,
                held_images_consumer=self._consume_held_images,
                llm_service=self.gemini.llm_service,
                gemini_factory=lambda: self.gemini,
                model_state_store=getattr(self, "model_state_store", None),
            )
        return self.screenshot_chat_module

    def _get_held_images_snapshot(self):
        return list(self.held_images)

    def _consume_held_images(self, imported_paths):
        imported_set = set(imported_paths or [])
        if not imported_set:
            return
        self.held_images = [path for path in self.held_images if path not in imported_set]
        if not self.held_images:
            self._hide_hold_count_tooltip()
        else:
            self._show_hold_count_tooltip(len(self.held_images))

    def _capture_worker_loop(self):
        while not self._capture_stop.is_set():
            try:
                item = self._capture_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                self._capture_queue.task_done()
                break

            try:
                self._process_capture_job(*item)
            except Exception as e:
                print(f"Error in capture worker: {e}")
                try:
                    self.root.after(0, self.animator.stop_animation)
                except Exception:
                    pass
            finally:
                self._capture_queue.task_done()

    def _show_hold_count_tooltip(self, count):
        if count <= 1:
            self._hide_hold_count_tooltip()
            return

        def _update_ui():
            if not self.hold_count_window:
                self.hold_count_window = tk.Toplevel(self.root)
                self.hold_count_window.overrideredirect(True)
                self.hold_count_window.attributes("-topmost", True)
                self.hold_count_window.attributes("-alpha", 0.8)
                self.hold_count_window.configure(bg="black")
                self.hold_count_window.withdraw()
                self.hold_count_label = tk.Label(
                    self.hold_count_window,
                    text="",
                    fg="white",
                    bg="black",
                    font=("Segoe UI", 12, "bold"),
                    padx=8,
                    pady=4,
                )
                self.hold_count_label.pack()

            self.hold_count_label.config(text=str(count))
            self.hold_count_window.deiconify()
            if not self._hold_count_follow_timer:
                self._update_tooltip_position()

            if self._hold_count_hide_timer:
                self.root.after_cancel(self._hold_count_hide_timer)
            self._hold_count_hide_timer = self.root.after(3000, self._hide_hold_count_tooltip)

        self.root.after(0, _update_ui)

    def _update_tooltip_position(self):
        if self.hold_count_window:
            try:
                x, y = self.root.winfo_pointerxy()
                self.hold_count_window.geometry(f"+{x + 15}+{y + 15}")
                self._hold_count_follow_timer = self.root.after(16, self._update_tooltip_position)
            except Exception:
                self._hold_count_follow_timer = None
        else:
            self._hold_count_follow_timer = None

    def _hide_hold_count_tooltip(self):
        def _hide():
            if self._hold_count_hide_timer:
                self.root.after_cancel(self._hold_count_hide_timer)
                self._hold_count_hide_timer = None
            if self._hold_count_follow_timer:
                self.root.after_cancel(self._hold_count_follow_timer)
                self._hold_count_follow_timer = None
            if self.hold_count_window:
                self.hold_count_window.destroy()
                self.hold_count_window = None
                self.hold_count_label = None

        self.root.after(0, _hide)

    def _process_capture_job(
        self,
        left,
        top,
        right,
        bottom,
        timestamp_str,
        force_new_window,
        action_name=None,
        modifiers=None,
    ):
        filename = f"screenshot_{timestamp_str}.png"
        save_path = os.path.join(self.screenshots_dir, filename)
        selected_bbox = (left, top, right, bottom)

        try:
            with ImageGrab.grab(bbox=selected_bbox, all_screens=True) as cropped:
                cropped.save(save_path)
        except Exception as bbox_error:
            print(f"BBox capture failed, falling back to full-screen crop: {bbox_error}")
            screenshot = capture_screen()
            try:
                screen_w, screen_h = screenshot.size
                virtual_left, virtual_top, _, _ = get_virtual_screen_rect()

                crop_left = left - virtual_left
                crop_top = top - virtual_top
                crop_right = right - virtual_left
                crop_bottom = bottom - virtual_top

                crop_left = max(0, min(crop_left, screen_w - 1))
                crop_top = max(0, min(crop_top, screen_h - 1))
                crop_right = max(crop_left + 1, min(crop_right, screen_w))
                crop_bottom = max(crop_top + 1, min(crop_bottom, screen_h))

                with screenshot.crop((crop_left, crop_top, crop_right, crop_bottom)) as cropped:
                    cropped.save(save_path)
            finally:
                screenshot.close()

        is_plain_shift_stack = bool((modifiers or {}).get("shift")) and action_name is None
        if is_plain_shift_stack:
            self.held_images.append(save_path)
            self._show_hold_count_tooltip(len(self.held_images))
            print(f"Shift stack: {len(self.held_images)} image(s) held")
            return

        all_images = self.held_images + [save_path]
        self.held_images = []
        self._hide_hold_count_tooltip()

        if action_name == ACTION_CUSTOM_PROMPT:
            self._show_custom_prompt_for_capture(all_images, timestamp_str, force_new_window)
            return
        elif action_name == ACTION_TEXT_FILE_PROMPT:
            self.root.after(0, self._show_text_file_quick_popup, all_images, "", timestamp_str)
            return
        elif action_name == ACTION_CUSTOM_PROMPT_NEW_SESSION:
            self._route_snap_to_chat_screen(all_images, timestamp_str)
            return

        self._start_image_processing(
            all_images,
            timestamp_str,
            force_new_window,
            source_label="Mouse snap",
        )

    def _route_snap_to_chat_screen(self, save_paths, timestamp_str):
        """Route Ctrl+Shift+Snap directly into the reworked Chat Screen (Ctrl+Shift+Alt+X)."""
        paths = save_paths if isinstance(save_paths, list) else [save_paths]

        def _route():
            try:
                module = self._ensure_screenshot_chat_module()
                controller = module.controller
                controller.open_or_focus()
                controller.create_session()
                controller.import_paths(paths, source="ctrl_shift_snap")
            except Exception as exc:
                print(f"Chat Screen snap routing failed: {exc}")

        self.root.after(0, _route)

    def _start_image_processing(
        self,
        save_paths,
        timestamp_str,
        force_new_window,
        custom_prompt_type=None,
        custom_prompt_text=None,
        source_label="Mouse snap",
    ):
        self.gemini_start_time = time.time()
        self.root.after(0, self.animator.start_animation)
        follow_prompt_type = custom_prompt_type or self.gemini.prompt_type
        follow_history = self.follow_manager.build_history()
        request_model_name = self.gemini.model
        request_prompt_name = self._format_follow_prompt_name(follow_prompt_type)
        request_prompt_text = (
            custom_prompt_text
            if isinstance(custom_prompt_text, str) and custom_prompt_text.strip()
            else self.gemini.get_prompt(follow_prompt_type)
        )

        main_path = save_paths[0] if isinstance(save_paths, list) and save_paths else save_paths
        follow_user_message = self._build_follow_image_request(
            follow_prompt_type, main_path, source_label
        )
        threading.Thread(
            target=self._process_image_async,
            args=(
                save_paths,
                timestamp_str,
                False,
                force_new_window,
                follow_prompt_type,
                follow_history,
                follow_user_message,
                request_prompt_text,
                request_model_name,
                request_prompt_name,
            ),
            daemon=True,
        ).start()

    def _show_custom_prompt_for_capture(
        self, save_paths, timestamp_str, force_new_window, override_prompt=""
    ):
        default_prompt = override_prompt or self.gemini.get_prompt(self.gemini.prompt_type)
        paths = save_paths if isinstance(save_paths, list) else [save_paths]

        def on_submit(prompt_text, submission=None):
            all_img_paths = list(paths)
            if submission is not None:
                for att in getattr(submission, "attachments", []):
                    if getattr(att, "kind", "") == "image" and att.path not in all_img_paths:
                        all_img_paths.append(att.path)
            self._start_image_processing(
                all_img_paths,
                timestamp_str,
                force_new_window,
                custom_prompt_type=ACTION_CUSTOM_PROMPT,
                custom_prompt_text=prompt_text,
                source_label="Ctrl snap",
            )

        if show_custom_prompt_dialog is None:
            self._route_snap_to_chat_screen(paths, timestamp_str)
            return

        show_custom_prompt_dialog(
            self.root,
            self.custom_prompt_history,
            default_prompt,
            on_submit,
            force_dialog=True,
            mode="image",
            initial_files=paths,
            active_model_label=getattr(self.gemini, "model", ""),
            on_escalate_pi=getattr(self, "_escalate_popup_to_pi_chat", None),
        )

    def _model_menu_title(self):
        if hasattr(self.config_manager, "reload_if_changed"):
            self.config_manager.reload_if_changed()
        all_models = self.config_manager.get_all_models()
        self.all_models = all_models
        current_model = str(getattr(self.gemini, "model", "") or "").strip()
        if not current_model:
            current_model = str(self.config_manager.get("agents.defaults.model.primary", "") or "").strip()
        if current_model and current_model in all_models:
            return f"Model: {current_model}"
        return "Model: not configured"

    def _build_provider_menus(self):
        """Build nested provider -> model submenus from normalized model registry."""
        if hasattr(self.config_manager, "reload_if_changed"):
            self.config_manager.reload_if_changed()
        all_models = self.config_manager.get_all_models()
        self.all_models = all_models
        grouped_models = {}
        provider_display_names = {}

        for full_name in sorted(all_models.keys()):
            if not isinstance(full_name, str) or "/" not in full_name:
                continue
            provider_name, model_name = full_name.split("/", 1)
            if not provider_name or not model_name:
                continue
            grouped_models.setdefault(provider_name, []).append((model_name, full_name))
            provider_config = self.config_manager.get_provider_config(provider_name)
            provider_display_names[provider_name] = provider_config.get("displayName", provider_name)

        if not grouped_models:
            return [MenuItem("(no models found in config.yaml)", None, enabled=False)]

        provider_submenus = []
        for provider_name in sorted(grouped_models.keys()):
            model_items = []
            for model_name, full_name in grouped_models[provider_name]:
                model_items.append(
                    MenuItem(
                        text=model_name,
                        action=partial(self.set_model, full_name),
                        checked=lambda item, fn=full_name: self.gemini.model == fn,
                        radio=True,
                    )
                )

            provider_submenus.append(
                MenuItem(
                    provider_display_names.get(provider_name, provider_name),
                    Menu(*model_items),
                    checked=lambda item, pn=provider_name: self.gemini.provider == pn,
                )
            )

        return provider_submenus

    def _on_hotkey_error(self, cfg):
        def show_error():
            try:
                from src.utils.notifier import show_notification

                show_notification(
                    "Hotkey Conflict",
                    f"Could not register '{cfg['keys']}'. Close other apps using this hotkey.",
                )
                if hasattr(self, "animator") and self.animator:
                    self.animator.show_error_frame()
            except Exception:
                pass

        self.root.after(0, show_error)

    def _snapshot_clipboard_payload(self):
        try:
            image = ImageGrab.grabclipboard()
            if isinstance(image, Image.Image):
                return ("image", image.copy())
        except Exception as exc:
            print(f"Error snapshotting clipboard image: {exc}")

        try:
            import pyperclip

            text = pyperclip.paste()
            if isinstance(text, str) and text:
                return ("text", text)
        except Exception as exc:
            print(f"Error snapshotting clipboard text: {exc}")

        return None

    def _select_hotkey_prompt(self, hotkey_config):
        """Select named snap modes independently of clipboard copy success."""
        custom_prompt = hotkey_config.get("system_prompt")
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            return

        prompt_type = hotkey_config.get("prompt_type", "prompt_translate")
        prompt_icons = {
            "prompt_fix_grammar": self.icon_fix,
            "prompt_summarize": self.icon_sum,
            "prompt_translate": self.icon_trans,
            "prompt_extract_text": self.icon_spi,
            "prompt_expert": self.image,
        }
        if prompt_type in prompt_icons and prompt_type != self.gemini.prompt_type:
            self.set_prompt_type(prompt_type, prompt_icons[prompt_type])

    def _process_clipboard(self, hotkey_config):
        """Process content from clipboard (image or text) with specific hotkey config."""
        if self.run_flag == 1:
            print("Hotkey ignored (App is Paused).")
            return

        self.config_manager.reload_if_changed()
        fresh_hotkeys = self.config_manager.get("hotkeys", [])
        hotkey_id = hotkey_config.get("id")
        for hk in fresh_hotkeys:
            if isinstance(hk, dict) and hk.get("id") == hotkey_id:
                hotkey_config = hk
                break

        self.root.after(0, self._select_hotkey_prompt, hotkey_config)

        with self._clipboard_transaction_lock:
            if hotkey_config.get("auto_copy", True):
                from src.utils.HotkeyManager import simulate_ctrl_c

                if not simulate_ctrl_c():
                    print("Clipboard copy timed out; hotkey request cancelled.")
                    return

            clipboard_payload = self._snapshot_clipboard_payload()

        if clipboard_payload is None:
            print("Clipboard is empty or contains unsupported content.")
            return

        self.root.after(
            0,
            self._process_clipboard_on_main_thread,
            hotkey_config,
            clipboard_payload,
        )

    def _process_clipboard_on_main_thread(self, hotkey_config, clipboard_payload=None):
        prompt_type = hotkey_config.get("prompt_type", "prompt_translate")
        custom_system_prompt = hotkey_config.get("system_prompt")
        has_custom_system_prompt = (
            isinstance(custom_system_prompt, str) and custom_system_prompt.strip()
        )
        effective_prompt = (
            custom_system_prompt
            if has_custom_system_prompt
            else self.gemini.get_prompt(prompt_type)
        )
        request_model_name = self.gemini.model
        request_prompt_name = (
            hotkey_config.get("id")
            if has_custom_system_prompt
            else self._format_follow_prompt_name(prompt_type)
        )

        print(f"Hotkey triggered: {hotkey_config.get('id')} (Type: {prompt_type})")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        img = None
        text = None
        if clipboard_payload is not None:
            payload_type, payload_value = clipboard_payload
            if payload_type == "image":
                img = payload_value
            elif payload_type == "text":
                text = payload_value
        else:
            try:
                live_image = ImageGrab.grabclipboard()
                if isinstance(live_image, Image.Image):
                    img = live_image
            except Exception as e:
                print(f"Error checking clipboard for image: {e}")

            if img is None:
                try:
                    text = self.root.clipboard_get()
                except Exception as e:
                    print(f"Error reading clipboard text: {e}")

        if isinstance(img, Image.Image):
            self.animator.start_animation()

            self.gemini.process_image(
                img,
                callback=partial(
                    self.gemini_callback,
                    timestamp_str=timestamp_str,
                    is_comparison_rerun=False,
                    force_new_window=True,
                    custom_prompt_type=prompt_type,
                    follow_user_message=self._build_follow_image_request(
                        prompt_type, "", f"Hotkey {hotkey_config.get('id', '')}".strip()
                    ),
                    request_model_name=request_model_name,
                    request_prompt_name=request_prompt_name,
                ),
                history=self.follow_manager.build_history(),
                custom_prompt_type=prompt_type,
                custom_prompt_text=effective_prompt,
            )
            return

        if text:
            print(f"Processing clipboard text: {text[:50]}...")
            self.animator.start_animation()

            def _cb(result):
                self._remember_follow_exchange(follow_user_message, result)
                self.root.after(
                    0,
                    self._handle_gemini_result,
                    result,
                    timestamp_str,
                    False,
                    True,
                    prompt_type,
                    request_model_name,
                    request_prompt_name,
                )

            follow_user_message = self._build_follow_text_request(
                prompt_type,
                text,
                f"Hotkey {hotkey_config.get('id', '')}".strip(),
            )

            self.gemini.send_text_message(
                text_prompt=f"{effective_prompt}\n\nContent:\n{text}",
                callback=_cb,
                history=self.follow_manager.build_history(),
                model_name=request_model_name,
            )
        else:
            print("Clipboard is empty or contains unsupported content.")

    def open_config_file(self, filename):
        """Open a config file in notepad instead of default editor for speed."""
        import subprocess

        filepath = os.path.join(BASE_DIR, filename)
        if os.path.exists(filepath):
            try:
                subprocess.Popen(["notepad.exe", filepath])
                print(f"Opened {filename} in notepad for editing.")
            except Exception as e:
                print(f"Error opening {filename}: {e}")
        else:
            print(f"Config file not found: {filepath}")

    def set_model(self, model_full_name, icon=None, item=None):
        if hasattr(self.config_manager, "reload_if_changed"):
            self.config_manager.reload_if_changed()
        self.all_models = self.config_manager.get_all_models()
        self.gemini.set_model(model_full_name)
        if hasattr(self, "model_state_store") and self.model_state_store is not None:
            self.model_state_store.set_model(model_full_name, source="tray", persist=True)
        if getattr(self, "icon", None) is not None and hasattr(self.icon, "update_menu"):
            try:
                self.icon.update_menu()
            except Exception:
                pass
        print(f"Model set to: {model_full_name}")
        self.root.after(0, self._refresh_float_window_models, model_full_name)

    def set_prompt_type(
        self, prompt_key, new_icon, icon=None, item=None, fix_prompt_key=0
    ):
        if fix_prompt_key == 1:
            try:
                prompt_text_to_edit = self.gemini.get_prompt(prompt_key, fallback_key=None)
                if not prompt_text_to_edit or not prompt_text_to_edit.strip():
                    prompt_text_to_edit = "Default prompt text not found."
            except Exception as e:
                print(f"Error loading prompt for editing: {e}")
                prompt_text_to_edit = "Could not load prompt."

            editor_packet = DataPacket(
                ans=prompt_text_to_edit,
                model_name=self.gemini.model,
                prompt_name="Fix Prompt",
                create_new=True,
                image_path="",
                original_timestamp_str="",
                rerun_prompt_callback=lambda p, t: self.root.after(
                    0,
                    self._process_clipboard_on_main_thread,
                    {"id": "FixPrompt", "prompt_type": prompt_key},
                ),
                record_audio_callback=self.toggle_audio_recording,
                transcribe_callback=self.capture_transcribe_snapshot,
                select_audio_files_callback=self.select_audio_files_for_stt,
                get_audio_duration_callback=self.recorder.get_duration,
                pause_audio_callback=self.toggle_pause_recording,
                gemini_instance=self.gemini,
                skip_history=True,
                toggle_follow_callback=self.toggle_follow_mode,
                get_follow_state_callback=self._get_follow_state,
                get_audio_stt_state_callback=self._get_audio_stt_state,
            )
            show_floating_window(self.root, editor_packet)

        self.gemini.prompt_type = prompt_key
        self.current_icon = new_icon
        if self.run_flag == 0:
            self.animator.current_icon = new_icon
        if self.run_flag == 0 and self.icon:
            self.icon.icon = self.current_icon
        print(f"Prompt type set to: {prompt_key}")
        self.gemini._save_config()

    TRAY_LEFT_CLICK_ACTIONS = ("toggle_pause", "none")

    def _ensure_tray_state_attrs(self):
        if getattr(self, "_tray_lock", None) is None:
            self._tray_lock = threading.Lock()
            self._last_tray_toggle_at = None
            self._tray_debounce_s = self._double_click_seconds()

    @staticmethod
    def _double_click_seconds():
        try:
            return max(0.1, ctypes.windll.user32.GetDoubleClickTime() / 1000.0)
        except Exception:
            return 0.5

    def _tray_left_click_action(self):
        action = str(
            self.config_manager.get("ui.tray_left_click_action", "toggle_pause")
            or "toggle_pause"
        )
        action = action.strip().lower()
        if action not in self.TRAY_LEFT_CLICK_ACTIONS:
            print(f"Unknown ui.tray_left_click_action {action!r}; using toggle_pause.")
            return "toggle_pause"
        return action

    def _tray_toggle_pause(self, icon=None, item=None):
        """Runs on the pystray thread: debounce the click, then toggle on the Tk thread."""
        self._ensure_tray_state_attrs()
        with self._tray_lock:
            now = time.monotonic()
            last = self._last_tray_toggle_at
            if last is not None and now - last < self._tray_debounce_s:
                return
            self._last_tray_toggle_at = now
        self.root.after(0, self.toggle_pause)

    def toggle_pause(self, icon=None, item=None):
        self.run_flag = 1 - self.run_flag
        if self.run_flag == 1:
            self.input_handler.stop_mouse_listener()
            self.animator.current_icon = self.image_pause
            self.animator.stop_animation()
            if self.icon:
                self.icon.icon = self.image_pause
                self.icon.title = "Tool (Paused)"
            print("App Paused.")
        else:
            self.animator.current_icon = self.current_icon
            if self.icon:
                self.icon.icon = self.current_icon
                self.icon.title = "Tool"
            self.input_handler.start_mouse_listener()
            print("App Resumed.")

    def toggle_left_click_mode(self, icon=None, item=None):
        left_mode_enabled = self.input_handler.toggle_left_click_mode()
        if self.icon:
            self.icon.icon = (
                self.icon_left_on if left_mode_enabled else self.icon_left_off
            )
        self.root.after(1000, self._update_tray_icon_after_toggle)

    def _update_tray_icon_after_toggle(self):
        if self.icon:
            self.icon.icon = (
                self.image_pause if self.run_flag == 1 else self.current_icon
            )

    def show_help_window(self, icon=None, item=None):
        """Displays a help window with dynamically generated hotkey info and settings."""
        help_win = tk.Toplevel(self.root)
        help_win.title("Help & Settings (v1.1)")
        help_win.geometry("540x440")
        help_win.attributes("-topmost", True)

        text_widget = tk.Text(
            help_win, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10
        )
        text_widget.pack(fill=tk.BOTH, expand=True)

        help_text = "=== SCREENSHOT & TRANSLATE CHATBOT v1.1 ===\n\n"
        help_text += "--- Current Settings ---\n"
        help_text += f"Provider: {self.gemini.provider}\n"
        help_text += f"Model: {self.gemini.model}\n"
        help_text += f"Default Prompt: {self.gemini.prompt_type}\n\n"

        help_text += "--- Mouse & Modifier Shortcuts ---\n"
        help_text += "- Right-Drag: Capture & process screen region\n"
        help_text += "- Shift + Right-Drag: Stack multiple screenshots\n"
        help_text += "- Ctrl + Right-Drag: Unified Quick Prompt Popup (Image mode)\n"
        help_text += "- Alt + Right-Drag / Hold: Unified Quick Prompt Popup (Text & File Drag-and-Drop)\n"
        help_text += "- Ctrl + Alt + Hold (0.2s): Radial Plugin Wheel (lolPing style)\n"
        help_text += "- Ctrl + Shift + Alt + X: Toggle Pi Sidecar Chat Screen\n"

        text_widget.insert(tk.END, help_text)
        text_widget.config(state=tk.DISABLED)

    def run(self):
        print("Starting SnapArc (v0.0.1)...")
        try:
            if any(arg in sys.argv[1:] for arg in ("--open-chat", "--open-lanbot")):
                self.root.after(350, lambda: self._open_screenshot_chat({"mode": "show"}))
            self.icon.run_detached()
            self.root.mainloop()
        except KeyboardInterrupt:
            print("Exiting via KeyboardInterrupt.")
        finally:
            self.on_exit(None, None)
        return "restart" if self.restart_requested else "exit"

    def request_exit(self, icon=None, item=None):
        if self.icon:
            self.icon.stop()
        if self.root:
            self.root.quit()

    def on_exit(self, icon, item):
        print("Exiting application...")
        self.animator.stop_animation()
        self.input_handler.stop_mouse_listener()
        self.hotkey_manager.stop()
        self._capture_stop.set()
        self._capture_queue.put(None)
        if self._capture_worker and self._capture_worker.is_alive():
            self._capture_worker.join(timeout=1)
        self._cleanup_temp_files()

        for fw in list(FLOAT_WINDOW_LIST):
            if fw and fw.win.winfo_exists():
                try:
                    fw.close()
                except Exception:
                    pass

        if self.screenshot_chat_module is not None:
            try:
                self.screenshot_chat_module.shutdown()
            except Exception as exc:
                print(f"ScreenshotChat: shutdown failed: {exc}")

        if self.icon:
            self.icon.stop()
        if self.root:
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass
        print("Application exited.")

    def reset_app(self, icon, item):
        self.restart_requested = True
        self.root.after(0, self.root.quit)

    def _capture_and_process(
        self,
        left,
        top,
        right,
        bottom,
        timestamp_str,
        force_new_window,
        modifiers=None,
    ):
        if self.run_flag == 1:
            print("Capture ignored (App is Paused).")
            return

        action = self.snap_action_router.resolve(modifiers or {})
        if action.name == ACTION_RADIAL_WHEEL:
            return
        effective_force_new_window = (
            force_new_window if action.keep_force_new_window else False
        )
        action_name = (
            action.name
            if action.name
            in {
                ACTION_CUSTOM_PROMPT,
                ACTION_CUSTOM_PROMPT_NEW_SESSION,
                ACTION_TEXT_FILE_PROMPT,
            }
            else None
        )
        self._capture_queue.put(
            (
                left,
                top,
                right,
                bottom,
                timestamp_str,
                effective_force_new_window,
                action_name,
                modifiers,
            )
        )

    def _process_image_async(
        self,
        image_path,
        timestamp_str,
        is_comparison_rerun,
        force_new_window,
        custom_prompt_type=None,
        history=None,
        follow_user_message="",
        custom_prompt_text=None,
        request_model_name=None,
        request_prompt_name=None,
    ):
        request_model_name = request_model_name or self.gemini.model
        if request_prompt_name is None:
            request_prompt_name = self._format_follow_prompt_name(
                custom_prompt_type or self.gemini.prompt_type
            )
        callback = partial(
            self.gemini_callback,
            timestamp_str=timestamp_str,
            is_comparison_rerun=is_comparison_rerun,
            force_new_window=force_new_window,
            custom_prompt_type=custom_prompt_type,
            follow_user_message=follow_user_message,
            request_model_name=request_model_name,
            request_prompt_name=request_prompt_name,
        )
        self.gemini.process_image(
            image_path,
            callback=callback,
            history=history,
            custom_prompt_type=custom_prompt_type,
            custom_prompt_text=custom_prompt_text,
            model_full_name=request_model_name,
        )

    def gemini_callback(
        self,
        result,
        timestamp_str,
        is_comparison_rerun,
        force_new_window,
        custom_prompt_type=None,
        follow_user_message="",
        request_model_name=None,
        request_prompt_name=None,
    ):
        self._remember_follow_exchange(follow_user_message, result)
        self.root.after(
            0,
            self._handle_gemini_result,
            result,
            timestamp_str,
            is_comparison_rerun,
            force_new_window,
            custom_prompt_type,
            request_model_name,
            request_prompt_name,
        )

    def _handle_gemini_result(
        self,
        result,
        timestamp_str,
        is_comparison_rerun,
        force_new_window,
        custom_prompt_type=None,
        request_model_name=None,
        request_prompt_name=None,
    ):
        self.animator.stop_animation()
        image_filename = f"screenshot_{timestamp_str}.png"
        save_path = os.path.join(self.screenshots_dir, image_filename)
        if not os.path.exists(save_path):
            clip_path = os.path.join(
                self.screenshots_dir, f"clipboard_{timestamp_str}.png"
            )
            save_path = clip_path if os.path.exists(clip_path) else ""

        current_prompt_type = custom_prompt_type or self.gemini.prompt_type
        request_model_name = request_model_name or self.gemini.model
        request_prompt_name = request_prompt_name or self.PROMPT_DISPLAY_NAMES.get(
            current_prompt_type, current_prompt_type
        )
        packet = DataPacket.from_gemini_result(
            result=result,
            timestamp_str=timestamp_str,
            save_path=save_path,
            is_comparison_rerun=is_comparison_rerun,
            force_new_window=force_new_window,
            model_name=request_model_name,
            prompt_name=request_prompt_name,
            rerun_callback_provider=partial(
                self.rerun_gemini_processing, is_comparison_rerun=True
            ),
            record_audio_callback_provider=self.toggle_audio_recording,
            transcribe_callback_provider=self.capture_transcribe_snapshot,
            select_audio_files_callback_provider=self.select_audio_files_for_stt,
            get_audio_duration_callback_provider=self.recorder.get_duration,
            pause_audio_callback_provider=self.toggle_pause_recording,
            gemini_instance=self.gemini,
            toggle_follow_callback_provider=self.toggle_follow_mode,
            get_follow_state_callback_provider=self._get_follow_state,
            get_audio_stt_state_callback_provider=self._get_audio_stt_state,
        )
        packet.open_chat_screen_callback = getattr(
            self, "_escalate_packet_to_chat_screen", None
        )

        # HTML history writing (result.html) has been removed in v1.1 for anti-lag performance
        # (FloatingWindow already provides < / > navigation and Pin for up to 200 items via SnapHistoryManager).
        if hasattr(self, "memory_guard") and self.memory_guard is not None:
            self.memory_guard.on_capture_completed(FLOAT_WINDOW_LIST, self.screenshots_dir)

        show_floating_window(self.root, packet)
        self._refresh_float_window_models(self.gemini.model)

    def rerun_gemini_processing(self, image_path, timestamp_str, is_comparison_rerun):
        if not os.path.exists(image_path):
            return
        self.root.after(0, self.animator.start_animation)
        follow_prompt_type = self.gemini.prompt_type
        request_model_name = self.gemini.model
        request_prompt_name = self._format_follow_prompt_name(follow_prompt_type)
        request_prompt_text = self.gemini.get_prompt(follow_prompt_type)
        threading.Thread(
            target=self._process_image_async,
            args=(
                image_path,
                timestamp_str,
                is_comparison_rerun,
                False,
                None,
                self.follow_manager.build_history(),
                self._build_follow_image_request(
                    follow_prompt_type,
                    image_path,
                    "Rerun image",
                ),
                request_prompt_text,
                request_model_name,
                request_prompt_name,
            ),
            daemon=True,
        ).start()

    def _tray_toggle_recording(self, icon=None, item=None):
        is_recording, saved_path = self.toggle_audio_recording()
        if is_recording:
            print("Started Recording via Tray.")
        else:
            print(f"Stopped Recording via Tray. Saved at: {saved_path}")
            if saved_path:
                self.capture_transcribe_snapshot(None)

    def toggle_audio_recording(self, icon=None, item=None, window_instance=None):
        if self.recorder.is_recording:
            saved_path = self.recorder.stop_recording()
            if saved_path and window_instance:
                window_instance.set_file_label(saved_path)
            return False, saved_path

        started = self.recorder.start_recording()
        if not started and window_instance:
            window_instance.set_file_label(
                self.recorder.last_error or "Khong the bat ghi am"
            )
        return started, None

    def toggle_pause_recording(self):
        if not self.recorder.is_recording:
            return False
        if self.recorder.is_paused:
            self.recorder.resume_recording()
        else:
            self.recorder.pause_recording()
        return self.recorder.is_paused

    def _cleanup_temp_files(self):
        with self._cleanup_lock:
            for f in self._temp_files_to_cleanup:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except OSError:
                    pass
            self._temp_files_to_cleanup.clear()

    def capture_transcribe_snapshot(self, window_instance):
        self.float_audio_action_service.transcribe_latest(
            selected_model=self.gemini.model,
            window_instance=window_instance,
        )
        return False

    def select_audio_files_for_stt(self, parent=None):
        return self.float_audio_action_service.transcribe_selected_files(
            parent=parent or self.root,
            selected_model=self.gemini.model,
        )

    def _get_audio_stt_state(self):
        return self.float_audio_action_service.get_stt_state()

    def _run_on_ui_thread(self, callback):
        if threading.current_thread() is threading.main_thread():
            return callback()

        if not getattr(self, "root", None) or not hasattr(self.root, "after"):
            return False

        done = threading.Event()
        result = {"value": False, "error": None}

        def wrapped():
            try:
                result["value"] = callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        try:
            self.root.after(0, wrapped)
        except Exception:
            return False

        if not done.wait(timeout=UI_DISPATCH_TIMEOUT_SECONDS):
            return False
        if result["error"] is not None:
            raise result["error"]
        return result["value"]

    def _get_latest_live_float_window(self):
        for floating_window in reversed(list(FLOAT_WINDOW_LIST)):
            try:
                if floating_window and floating_window.win.winfo_exists():
                    return floating_window
            except Exception:
                continue
        return None

    def _create_float_stt_window(self):
        packet = DataPacket(
            ans="",
            model_name=self.gemini.model,
            prompt_name="STT",
            create_new=True,
            skip_history=True,
            record_audio_callback=self.toggle_audio_recording,
            transcribe_callback=self.capture_transcribe_snapshot,
            select_audio_files_callback=self.select_audio_files_for_stt,
            get_audio_duration_callback=self.recorder.get_duration,
            pause_audio_callback=self.toggle_pause_recording,
            gemini_instance=self.gemini,
            toggle_follow_callback=self.toggle_follow_mode,
            get_follow_state_callback=self._get_follow_state,
            get_audio_stt_state_callback=self._get_audio_stt_state,
        )
        show_floating_window(self.root, packet)
        return self._get_latest_live_float_window()


def _restart_process():
    script_path = os.path.abspath(__file__)
    args = [sys.executable, script_path, *sys.argv[1:]]
    os.chdir(BASE_DIR)
    os.execv(sys.executable, args)


if __name__ == "__main__":
    app = ScreenshotApp()
    status = app.run()
    del app
    if status == "restart":
        time.sleep(0.5)
        _restart_process()
