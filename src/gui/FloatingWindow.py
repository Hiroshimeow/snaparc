# FloatingWindow.py
import tkinter as tk
from tkinter import font as tkfont, simpledialog
import xml.etree.ElementTree as ET
import ctypes
import os
import time
import threading
import sys

from src.utils.DataPacket import DataPacket
from src.plugins.snap_history import SnapHistoryManager
from src.utils.prompt_store import save_prompt_to_toml

# Chat-in-Workspace and legacy ChatUI have been removed in v1.1 in favor of the unified Chat Screen (Ctrl+Shift+Alt+X).
WORKSPACE_UI_AVAILABLE = False
CHAT_UI_AVAILABLE = False
MAX_OPEN_FLOAT_WINDOWS = 6

try:
    import pyautogui

    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    print("Warning: pyautogui not found. Auto-hide feature disabled.")

CONFIG_FILE = "data/float_config.xml"
FLOAT_WINDOW_LIST = []  # Global list to manage active windows
FLOAT_WINDOW_LIST_LOCK = threading.Lock()
MAIN_CONFIG_FILE = "config.yaml"  # Define path to the main config file

FLOAT_UI_THEMES = {
    "dark": {
        "window_bg": "#181818",
        "header_bg": "#262626",
        "text_bg": "#181818",
        "text_fg": "#E6E6E6",
        "muted_fg": "#A0A0A0",
        "button_fg": "#D0D0D0",
        "disabled_fg": "#666666",
        "active_bg": "#3A3A3A",
        "scrollbar_bg": "#333333",
        "popup_bg": "#202020",
        "popup_fg": "#E6E6E6",
        "popup_select_bg": "#3A3A3A",
        "popup_select_fg": "#FFFFFF",
        "stt_idle_fg": "#64B5F6",
    },
    "light": {
        "window_bg": "white",
        "header_bg": "lightgray",
        "text_bg": "white",
        "text_fg": "#DB2E8D",
        "muted_fg": "gray",
        "button_fg": "#333333",
        "disabled_fg": "#BBBBBB",
        "active_bg": "gray",
        "scrollbar_bg": "lightgrey",
        "popup_bg": "#F5EAEA",
        "popup_fg": "#222222",
        "popup_select_bg": "#F4C7C3",
        "popup_select_fg": "#7F1D1D",
        "stt_idle_fg": "#1565C0",
    },
}


class ConfigManager:
    # ... (ConfigManager class giữ nguyên) ...
    DEFAULT_CONFIG = {
        "x": 100,
        "y": 100,
        "width": 400,
        "height": 200,
        "hotkey": "Ctrl+Shift+Z",
        "theme": "dark",
    }

    @staticmethod
    def load_config():
        if not os.path.exists(CONFIG_FILE):
            ConfigManager.save_config(
                **ConfigManager.DEFAULT_CONFIG
            )  # Lưu config mặc định nếu chưa có
            return ConfigManager.DEFAULT_CONFIG.copy()
        try:
            tree = ET.parse(CONFIG_FILE)
            root = tree.getroot()
            config = {
                "x": int(root.findtext("x", ConfigManager.DEFAULT_CONFIG["x"])),
                "y": int(root.findtext("y", ConfigManager.DEFAULT_CONFIG["y"])),
                "width": int(
                    root.findtext("width", ConfigManager.DEFAULT_CONFIG["width"])
                ),
                "height": int(
                    root.findtext("height", ConfigManager.DEFAULT_CONFIG["height"])
                ),
                "hotkey": root.findtext(
                    "hotkey", ConfigManager.DEFAULT_CONFIG["hotkey"]
                ),
                "theme": (
                    root.findtext("theme", ConfigManager.DEFAULT_CONFIG["theme"])
                    or ConfigManager.DEFAULT_CONFIG["theme"]
                ).strip().lower(),
            }
            if config["theme"] not in FLOAT_UI_THEMES:
                config["theme"] = ConfigManager.DEFAULT_CONFIG["theme"]
            # Đảm bảo kích thước tối thiểu
            config["width"] = max(150, config["width"])
            config["height"] = max(80, config["height"])
            return config
        except (ET.ParseError, ValueError, TypeError) as e:
            print(f"Error reading {CONFIG_FILE}: {e}. Using default.")
            return ConfigManager.DEFAULT_CONFIG.copy()

    @staticmethod
    def save_config(x, y, width, height, hotkey=None, theme=None):
        root = ET.Element("config")
        ET.SubElement(root, "x").text = str(int(x))
        ET.SubElement(root, "y").text = str(int(y))
        ET.SubElement(root, "width").text = str(int(width))
        ET.SubElement(root, "height").text = str(int(height))
        # Lấy hotkey hiện tại nếu không được cung cấp
        if hotkey is None:
            try:
                current_config = ConfigManager.load_config()
                hotkey = current_config.get(
                    "hotkey", ConfigManager.DEFAULT_CONFIG["hotkey"]
                )
            except Exception:
                hotkey = ConfigManager.DEFAULT_CONFIG["hotkey"]
        ET.SubElement(root, "hotkey").text = hotkey
        if theme is None:
            try:
                current_config = ConfigManager.load_config()
                theme = current_config.get("theme", ConfigManager.DEFAULT_CONFIG["theme"])
            except Exception:
                theme = ConfigManager.DEFAULT_CONFIG["theme"]
        theme = str(theme).strip().lower()
        if theme not in FLOAT_UI_THEMES:
            theme = ConfigManager.DEFAULT_CONFIG["theme"]
        ET.SubElement(root, "theme").text = theme
        tree = ET.ElementTree(root)
        try:
            tree.write(CONFIG_FILE, encoding="utf-8", xml_declaration=True)
        except Exception as e:
            print(f"Error saving config: {e}")




def fix_prompt_change(
    config_path="config.yaml",
    key="prompt_fix_grammar",
    new_value="NEW VALUE",
):
    return save_prompt_to_toml(config_path, key, new_value)


class FloatingWindow:
    # --- HÀM ĐÃ THAY ĐỔI (SỬA LỖI THỨ TỰ KHỞI TẠO) ---
    def __init__(
        self, master, packet: DataPacket, model_name=None, prompt_name=None
    ):
        # print("FloatingWindow init called from:")
        # traceback.print_stack()

        if not isinstance(master, tk.Tk) and not isinstance(master, tk.Toplevel):
            raise TypeError("master must be a tk.Tk or tk.Toplevel instance")
        self.master = master
        self.packet = packet
        # Lấy dữ liệu từ các thuộc tính của packet
        self.ans = packet.ans
        self.image_path = packet.image_path
        self.original_timestamp_str = packet.original_timestamp_str
        self.rerun_prompt_callback = packet.rerun_prompt_callback
        self.model_name = model_name or packet.model_name or "Unknown"
        self.current_model_name = self.model_name
        self.prompt_name = prompt_name or packet.prompt_name or "Unknown"
        self.record_audio_callback = packet.record_audio_callback
        self.transcribe_callback = packet.transcribe_callback
        self.select_audio_files_callback = getattr(packet, "select_audio_files_callback", None)
        self.toggle_follow_callback = getattr(packet, "toggle_follow_callback", None)
        self.get_follow_state_callback = getattr(packet, "get_follow_state_callback", None)
        self.get_audio_stt_state_callback = getattr(
            packet, "get_audio_stt_state_callback", None
        )
        self._markdown_tags = ("bold", "italic", "h1", "h2", "h3", "code_block", "hidden")

        self.config = ConfigManager.load_config()
        self.theme_name = self.config.get("theme", ConfigManager.DEFAULT_CONFIG["theme"])
        self.theme = FLOAT_UI_THEMES.get(self.theme_name, FLOAT_UI_THEMES["dark"])
        self.visible = True
        self.last_in_corner = False
        self._move_start_x = 0
        self._move_start_y = 0
        self._win_x = 0
        self._win_y = 0
        self._resize_right_start_x = 0
        self._resize_right_start_y = 0
        self._orig_width = 0
        self._orig_height = 0
        self._resize_left_start_x = 0
        self._resize_left_start_y = 0
        self._orig_width_left = 0
        self._orig_height_left = 0
        self._orig_win_x = 0
        self._check_mouse_job = None
        self._auto_hide_interval_visible = 350
        self._auto_hide_interval_hidden = 175
        self.pin_popup = None
        self.pin_listbox = None
        self.pin_item_menu = None
        self._pinned_entries_cache = []
        self._corner_threshold = 10
        self._monitor_rects_cache = []
        self._monitor_trigger_zones = []
        self._monitor_layout_signature = None
        self._next_monitor_layout_refresh = 0.0
        self._monitor_layout_refresh_interval = 1.5

        self.ghost_window = None
        self.offset_x = 0
        self.offset_y = 0

        self.win = tk.Toplevel(self.master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.9)
        self.win.config(bg=self.theme["window_bg"])  # Màu nền cửa sổ

        try:
            x, y = self.config["x"], self.config["y"]
            w, h = max(150, self.config["width"]), max(80, self.config["height"])
            screen_w = self.master.winfo_screenwidth()
            screen_h = self.master.winfo_screenheight()
            if x + w < 50:
                x = 50
            if y + h < 50:
                y = 50
            if x > screen_w - 50:
                x = screen_w - w - 50
            if y > screen_h - 50:
                y = screen_h - h - 50
            x = max(0, x)
            y = max(0, y)
            self.win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception as e:
            print(f"Error setting initial geometry: {e}. Using default.")
            self.win.geometry("400x200+100+100")

        self.header = tk.Frame(self.win, bg=self.theme["header_bg"], height=20, cursor="fleur")
        self.header.pack(fill=tk.X, side=tk.TOP)
        self.header.bind("<ButtonPress-1>", self.start_move)
        self.header.bind("<B1-Motion>", self.do_move)
        self.header.bind(
            "<ButtonRelease-1>", self.end_move
        )  # <<< SỰ KIỆN MỚI KHI THẢ CHUỘT
        # Gán sự kiện cho các thành phần khác trong header nếu có
        for child in self.header.winfo_children():
            child.bind("<ButtonPress-1>", self.start_move)
            child.bind("<B1-Motion>", self.do_move)
            child.bind("<ButtonRelease-1>", self.end_move)

        # BƯỚC 1: TẠO WIDGET LABEL TRƯỚC
        self.info_label = tk.Label(
            self.header, text="", bg=self.theme["header_bg"], fg=self.theme["muted_fg"], font=("Arial", 8)
        )
        self.info_label.pack(side=tk.LEFT, padx=5, pady=2)

        # Explicitly bind drag events to the label so user can drag by the text area
        self.info_label.bind("<ButtonPress-1>", self.start_move)
        self.info_label.bind("<B1-Motion>", self.do_move)
        self.info_label.bind("<ButtonRelease-1>", self.end_move)

        self.close_btn = tk.Button(
            self.header,
            text="X",
            command=self.close,
            bd=0,
            bg="red",
            fg="white",
            padx=5,
            pady=0,
            activebackground="darkred",
            activeforeground="white",
            font=("Arial", 8, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.close_btn.pack(side=tk.RIGHT, padx=2, pady=2)

        # Record button: starts/stops the shared audio capture flow.
        self.record_btn = tk.Button(
            self.header,
            text="⚫",
            command=self.toggle_recording,
            bd=0,
            bg=self.theme["header_bg"],
            fg="red",
            padx=5,
            pady=0,
            activebackground="gray",
            activeforeground="darkred",
            font=("Arial", 8),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.record_btn.pack(side=tk.RIGHT, padx=2, pady=2)

        # ADD: Pause Button (Triangle) - Initially disabled
        self.pause_btn = tk.Button(
            self.header,
            text="▶",
            command=self.toggle_pause,
            bd=0,
            bg=self.theme["header_bg"],
            fg="orange",
            padx=5,
            pady=0,
            activebackground="gray",
            activeforeground="darkorange",
            font=("Arial", 8),
            relief=tk.FLAT,
            cursor="hand2",
            state="disabled",
        )
        self.pause_btn.pack(side=tk.RIGHT, padx=2, pady=2)

        # ADD: STT Button
        self.transcribe_btn = tk.Button(
            self.header,
            text="STT",
            command=self.toggle_transcription,
            bd=0,
            bg=self.theme["header_bg"],
            fg=self.theme["stt_idle_fg"],
            padx=5,
            pady=0,
            activebackground="gray",
            activeforeground="darkblue",
            font=("Arial", 7, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.transcribe_btn.pack(side=tk.RIGHT, padx=2, pady=2)
        self.transcribe_btn.bind("<Button-3>", self.show_stt_menu)
        self.build_stt_menu()

        self.follow_status_label = tk.Label(
            self.header,
            text="F0",
            bg=self.theme["header_bg"],
            fg="#888",
            font=("Arial", 7),
        )
        self.follow_status_label.pack(side=tk.RIGHT, padx=(2, 1), pady=2)

        self.follow_btn = tk.Button(
            self.header,
            text="F",
            command=self.toggle_follow,
            bd=0,
            bg=self.theme["header_bg"],
            fg="#C62828",
            padx=5,
            pady=0,
            activebackground="gray",
            activeforeground="#8E0000",
            font=("Arial", 8, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.follow_btn.pack(side=tk.RIGHT, padx=(2, 2), pady=2)

        # ADD: Filename Label
        self.file_label = tk.Label(
            self.header, text="", bg=self.theme["header_bg"], fg=self.theme["muted_fg"], font=("Arial", 7, "italic")
        )
        self.file_label.pack(side=tk.RIGHT, padx=2, pady=2)
        self.file_label.bind("<Double-Button-1>", self.open_audio_file)

        # ADD: Timer Label
        self.timer_label = tk.Label(
            self.header, text="", bg=self.theme["header_bg"], fg="red", font=("Arial", 8, "bold")
        )
        self.timer_label.pack(side=tk.RIGHT, padx=5, pady=2)

        self.pin_btn = tk.Button(
            self.header,
            text="PIN",
            command=self._show_pin_popup,
            bd=0,
            bg="#C62828",
            fg="white",
            padx=5,
            pady=0,
            activebackground="#8E0000",
            activeforeground="white",
            font=("Arial", 8, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.pin_btn.pack(side=tk.RIGHT, padx=3, pady=2)

        # --- Snap History Navigation ---
        self.snap_history = SnapHistoryManager.get_instance()
        if not getattr(packet, 'skip_history', False):
            self.snap_history.push(packet)  # Push current entry

        self.nav_next_btn = tk.Button(
            self.header, text="▶", command=self._nav_next,
            bd=0, bg=self.theme["header_bg"], fg=self.theme["disabled_fg"], padx=3, pady=0,
            activebackground="gray", font=("Arial", 8),
            relief=tk.FLAT, cursor="hand2", state="disabled",
        )
        self.nav_next_btn.pack(side=tk.RIGHT, padx=1, pady=2)

        self.nav_back_btn = tk.Button(
            self.header, text="◀", command=self._nav_back,
            bd=0, bg=self.theme["header_bg"], fg=self.theme["disabled_fg"], padx=3, pady=0,
            activebackground="gray", font=("Arial", 8),
            relief=tk.FLAT, cursor="hand2", state="disabled",
        )
        self.nav_back_btn.pack(side=tk.RIGHT, padx=1, pady=2)

        self.nav_counter_label = tk.Label(
            self.header, text="", bg=self.theme["header_bg"], fg=self.theme["muted_fg"],
            font=("Arial", 7),
        )
        self.nav_counter_label.pack(side=tk.RIGHT, padx=2, pady=2)
        self._update_nav_buttons()  # Reflect loaded history state
        self._update_pin_button()
        self.refresh_follow_state()
        # --- End Snap History Navigation ---

        self._update_info_label()

        # --- Body setup ---
        self.body = tk.Frame(self.win, bg=self.theme["window_bg"])
        self.body.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self.scrollbar = tk.Scrollbar(
            self.body,
            width=12,
            bg=self.theme["scrollbar_bg"],
            activebackground=self.theme["active_bg"],
            troughcolor=self.theme["window_bg"],
            relief=tk.FLAT,
            borderwidth=0,
        )
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 15), padx=(0, 2))

        self.textbox = tk.Text(
            self.body,
            wrap=tk.WORD,
            bg=self.theme["text_bg"],
            fg=self.theme["text_fg"],
            insertbackground=self.theme["text_fg"],
            font=("Arial", 12),
            bd=0,
            highlightthickness=0,
            yscrollcommand=self.scrollbar.set,
            relief=tk.FLAT,
        )
        self.textbox.insert(tk.END, self.ans)
        self._setup_tags()
        self._parse_markdown()

        self.win.bind("<Control-n>", self.handle_ctrl_n)

        self.textbox.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 15)
        )
        self.scrollbar.config(command=self.textbox.yview)

        self.win.update_idletasks()
        max_initial_height = self.master.winfo_screenheight() * 0.8
        current_height = self.win.winfo_height()
        if current_height > max_initial_height:
            self.win.geometry(f"{self.win.winfo_width()}x{int(max_initial_height)}")

        self.resizer_right = tk.Label(
            self.win, text="⤡", bg=self.theme["header_bg"], cursor="bottom_right_corner", fg=self.theme["muted_fg"]
        )
        self.resizer_right.place(relx=1.0, rely=1.0, anchor="se")
        self.resizer_right.bind("<ButtonPress-1>", self.start_resize_right)
        self.resizer_right.bind("<B1-Motion>", self.do_resize_right)

        self.resizer_left = tk.Label(
            self.win, text="⤢", bg=self.theme["header_bg"], cursor="bottom_left_corner", fg=self.theme["muted_fg"]
        )
        self.resizer_left.place(relx=0.0, rely=1.0, anchor="sw")
        self.resizer_left.bind("<ButtonPress-1>", self.start_resize_left)
        self.resizer_left.bind("<B1-Motion>", self.do_resize_left)

        # --- THAY ĐỔI: THÊM MENU CHUỘT PHẢI ---
        self.build_context_menu()
        self.win.bind("<Escape>", self.close)
        self.win.bind(
            "<Triple-Button-1>", self.tripple_left_click_rerun
        ) 
        self.win.bind("<Double-Button-3>", self.show_context_menu)  # Bind chuột phải

        # -----------------------------------------

        self._refresh_monitor_layout(force=True)

        if PYAUTOGUI_AVAILABLE:
            self.check_global_mouse_position()
        else:
            print("Auto-hide disabled as pyautogui is not available.")

        # Start timer loop LAST
        self._start_timer_loop()

        # global FLOAT_WINDOW_LIST
        with FLOAT_WINDOW_LIST_LOCK:
            FLOAT_WINDOW_LIST.append(self)
        self.win.deiconify()
        self.win.lift()
        print(f"FloatingWindow initialized. Total windows: {len(FLOAT_WINDOW_LIST)}")

    def _start_timer_loop(self):
        """Polls audio duration to update timer label."""
        if not self.win.winfo_exists():
            return
        duration = 0

        # print("DEBUG: Timer Loop Tick")
        if (
            self.packet
            and hasattr(self.packet, "get_audio_duration_callback")
            and self.packet.get_audio_duration_callback
        ):
            try:
                duration = self.packet.get_audio_duration_callback()
                # print(f"DEBUG: Duration {duration}")
                if duration > 0:
                    minutes = int(duration // 60)
                    seconds = int(duration % 60)
                    timer_text = f"{minutes:02}:{seconds:02}"
                    if self.timer_label.cget("text") != timer_text:
                        self.timer_label.config(text=timer_text)
                    # Sync record button if needed (simple visual check)
                    if (
                        self.record_btn.cget("text") != "🟥"
                        or self.record_btn.cget("fg") != "darkred"
                    ):
                        self.record_btn.config(text="🟥", fg="darkred")
                    # Enable pause button when recording
                    if self.pause_btn.cget("state") == "disabled":
                        self.pause_btn.config(state="normal")
                else:
                    if self.timer_label.cget("text"):
                        self.timer_label.config(text="")
                    if self.record_btn.cget("text") == "🟥":  # Reset if stopped
                        self.record_btn.config(text="⚫", fg="red")
                        self.pause_btn.config(state="disabled", text="▶", fg="orange")

            except Exception as e:
                print(f"Timer error: {e}")
                pass
        self.refresh_audio_stt_state()
        next_interval = 500 if duration > 0 else 1000
        self._timer_job = self.win.after(next_interval, self._start_timer_loop)

    def _cancel_timer_loop(self):
        if getattr(self, "_timer_job", None):
            try:
                self.win.after_cancel(self._timer_job)
            except Exception:
                pass
            self._timer_job = None

    def toggle_recording(self):
        """Toggles audio recording via callback."""
        if self.packet and self.packet.record_audio_callback:
            try:
                # Expect tuple now, passing self as window_instance
                if self.packet.record_audio_callback:
                    # Check if callback accepts kwargs (flexible check not easy in Py, assuming we update main.py)
                    result = self.packet.record_audio_callback(window_instance=self)
                else:
                    return

                if isinstance(result, tuple):
                    is_recording, saved_path = result
                else:
                    is_recording = result
                    saved_path = None

                # Update button visual
                if is_recording:
                    self.record_btn.config(text="🟥", fg="darkred")
                    self.pause_btn.config(
                        state="normal", text="⏸", fg="red"
                    )  # Enable and show Pause icon
                    self.file_label.config(text="Recording...")
                else:
                    self.record_btn.config(text="⚫", fg="red")
                    self.pause_btn.config(
                        state="disabled", text="▶", fg="orange"
                    )  # Disable and reset pause button
                    if saved_path:
                        self.set_file_label(saved_path)
            except Exception as e:
                print(f"Error toggling recording: {e}")
        else:
            print("DEBUG: No record callback available")

    def toggle_pause(self):
        """Toggles pause/resume state via callback."""
        if self.packet and self.packet.pause_audio_callback:
            try:
                is_paused = self.packet.pause_audio_callback()
                # Update button visual
                # Update button visual
                if is_paused:
                    self.pause_btn.config(
                        text="▶", fg="orange"
                    )  # Paused -> Show Play to resume
                    self.file_label.config(text="Paused")
                else:
                    self.pause_btn.config(
                        text="⏸", fg="red"
                    )  # Recording -> Show Pause to pause
                    self.file_label.config(text="Recording...")
            except Exception as e:
                print(f"Error toggling pause: {e}")
        else:
            print("DEBUG: No pause callback available")

    def toggle_transcription(self):
        """Runs STT on the latest shared recording snapshot."""
        self.trigger_stt_latest()

    def build_stt_menu(self):
        self.stt_menu = tk.Menu(self.win, tearoff=0)
        self.stt_menu.add_command(label="Transcribe latest recording", command=self.trigger_stt_latest)
        self.stt_menu.add_command(label="Transcribe audio files...", command=self.trigger_stt_files)

    def show_stt_menu(self, event=None):
        if not getattr(self, "stt_menu", None):
            return
        try:
            if event is not None:
                x_root = event.x_root
                y_root = event.y_root
            else:
                x_root = self.transcribe_btn.winfo_rootx()
                y_root = self.transcribe_btn.winfo_rooty() + self.transcribe_btn.winfo_height()
            self.stt_menu.tk_popup(x_root, y_root)
        finally:
            self.stt_menu.grab_release()

    def trigger_stt_latest(self):
        if self.packet and self.packet.transcribe_callback:
            try:
                self.packet.transcribe_callback(self)
            except Exception as e:
                print(f"Error triggering latest STT: {e}")

    def trigger_stt_files(self):
        callback = getattr(self.packet, "select_audio_files_callback", None)
        if callback:
            try:
                callback(parent=self.win)
            except Exception as e:
                print(f"Error triggering file STT: {e}")

    def _stt_idle_fg(self):
        theme = getattr(self, "theme", None) or FLOAT_UI_THEMES["light"]
        return theme.get("stt_idle_fg", FLOAT_UI_THEMES["light"]["stt_idle_fg"])

    def refresh_audio_stt_state(self):
        state = {"running": False, "job_count": 0}
        callback = getattr(self.packet, "get_audio_stt_state_callback", None)
        if callable(callback):
            try:
                candidate = callback()
                if isinstance(candidate, dict):
                    state.update(candidate)
            except Exception as e:
                print(f"Error getting STT state: {e}")

        running = bool(state.get("running"))
        self.transcribe_btn.config(
            fg="#C62828" if running else self._stt_idle_fg(),
            activeforeground="#8E0000" if running else "#0D47A1",
        )

    def refresh_follow_state(self):
        state = {"enabled": False, "history_count": 0}
        if callable(self.get_follow_state_callback):
            try:
                candidate = self.get_follow_state_callback()
                if isinstance(candidate, dict):
                    state.update(candidate)
            except Exception as e:
                print(f"Error getting follow state: {e}")

        enabled = bool(state.get("enabled"))
        history_count = int(state.get("history_count", 0) or 0)
        self.follow_btn.config(
            fg="#2E7D32" if enabled else "#C62828",
            activeforeground="#1B5E20" if enabled else "#8E0000",
        )
        self.follow_status_label.config(
            text=f"F{history_count}",
            fg="#2E7D32" if enabled else "#888",
        )

    def toggle_follow(self):
        if not callable(self.toggle_follow_callback):
            return
        try:
            self.toggle_follow_callback(window_instance=self)
        except Exception as e:
            print(f"Error toggling follow mode: {e}")
        self.refresh_follow_state()

    def set_file_label(self, text):
        """Sets the text of the filename label and stores path."""
        self.current_audio_path = text
        if self.win.winfo_exists():
            clean_text = os.path.basename(text) if text else ""
            if len(clean_text) > 15:
                clean_text = "..." + clean_text[-12:]
            self.file_label.config(
                text=clean_text,
                fg="blue" if text else "gray",
                cursor="hand2" if text else "",
            )

    def open_audio_file(self, event=None):
        """Opens the recording file with default system player."""
        if hasattr(self, "current_audio_path") and self.current_audio_path:
            # Sanitize: resolve to absolute path and verify it exists
            abs_path = os.path.abspath(self.current_audio_path)
            if os.path.exists(abs_path) and abs_path.endswith(
                (".wav", ".mp3", ".ogg", ".flac")
            ):
                try:
                    os.startfile(abs_path)
                    print(f"Opening audio file: {abs_path}")
                except Exception as e:
                    print(f"Error opening file: {e}")
            else:
                print(f"Invalid or non-existent audio path: {abs_path}")

    def append_text(self, text):
        """Appends text to the textbox safely."""
        if not text:
            return
        self.textbox.config(state=tk.NORMAL)
        self.textbox.insert(tk.END, f"\n\n[Live]: {text}")
        self.textbox.see(tk.END)
        # self.textbox.config(state=tk.DISABLED)

    def _update_pin_button(self):
        is_pinned = self.snap_history.is_pinned(self.packet)
        self.pin_btn.config(
            bg="#2E7D32" if is_pinned else "#C62828",
            activebackground="#1B5E20" if is_pinned else "#8E0000",
            relief=tk.FLAT,
        )

    def _close_pin_popup(self):
        if self.pin_popup and self.pin_popup.winfo_exists():
            self.pin_popup.destroy()
        self.pin_popup = None
        self.pin_listbox = None
        self.pin_item_menu = None
        self._pinned_entries_cache = []

    def _is_widget_inside_pin_popup(self, widget):
        if not widget or not self.pin_popup or not self.pin_popup.winfo_exists():
            return False
        try:
            current = widget
            while current is not None:
                if current == self.pin_popup:
                    return True
                current = current.master
        except Exception:
            return False
        return False

    def _handle_pin_popup_focus_out(self, event=None):
        if not self.pin_popup or not self.pin_popup.winfo_exists():
            return

        def _maybe_close():
            if not self.pin_popup or not self.pin_popup.winfo_exists():
                return
            focused_widget = self.pin_popup.focus_get()
            if self._is_widget_inside_pin_popup(focused_widget):
                return
            self._close_pin_popup()

        self.pin_popup.after(10, _maybe_close)

    def _hide_pin_popup_close_button(self):
        if not self.pin_popup or not self.pin_popup.winfo_exists():
            return
        try:
            self.pin_popup.protocol("WM_DELETE_WINDOW", self._close_pin_popup)
            if sys.platform == "win32":
                self.pin_popup.attributes("-toolwindow", True)
        except Exception as e:
            print(f"Error configuring pin popup titlebar: {e}")

    def toggle_current_pin(self):
        current_entry = self.snap_history.entry_from_packet(self.packet)
        if self.snap_history.is_pinned(self.packet):
            self.snap_history.unpin(current_entry["id"])
        else:
            self.snap_history.pin(self.packet)
        self._update_pin_button()

    def _toggle_current_pin_from_menu(self):
        self.toggle_current_pin()
        if self.pin_popup and self.pin_popup.winfo_exists():
            self._show_pin_popup()

    def _show_pin_popup(self):
        pins = self.snap_history.get_pins()
        self._close_pin_popup()

        self.pin_popup = tk.Toplevel(self.win)
        self.pin_popup.title("Pinned")
        self.pin_popup.transient(self.win)
        self.pin_popup.attributes("-topmost", True)
        self.pin_popup.resizable(False, False)
        self.pin_popup.configure(bg=self.theme["popup_bg"])
        self.pin_popup.bind("<Escape>", lambda event: self._close_pin_popup())
        self.pin_popup.bind("<FocusOut>", self._handle_pin_popup_focus_out)
        self.pin_popup.protocol("WM_DELETE_WINDOW", self._close_pin_popup)

        title_label = tk.Label(
            self.pin_popup,
            text="Pinned Floats",
            bg=self.theme["popup_bg"],
            fg=self.theme["popup_fg"],
            font=("Arial", 9, "bold"),
        )
        title_label.pack(fill=tk.X, padx=10, pady=(8, 4))

        body = tk.Frame(self.pin_popup, bg=self.theme["popup_bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        if not pins:
            empty_label = tk.Label(
                body,
                text="Chưa có bản ghim nào.",
                bg=self.theme["popup_bg"],
                fg=self.theme["muted_fg"],
                font=("Arial", 9),
                padx=12,
                pady=12,
            )
            empty_label.pack()
        else:
            self._pinned_entries_cache = pins
            self.pin_listbox = tk.Listbox(
                body,
                width=42,
                height=min(10, len(pins)),
                activestyle="none",
                bg=self.theme["popup_bg"],
                fg=self.theme["popup_fg"],
                selectbackground=self.theme["popup_select_bg"],
                selectforeground=self.theme["popup_select_fg"],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground="#E2B4B4",
                font=("Arial", 9),
            )
            self.pin_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            scrollbar = tk.Scrollbar(body, orient=tk.VERTICAL, command=self.pin_listbox.yview)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.pin_listbox.config(yscrollcommand=scrollbar.set)

            current_entry_id = self.snap_history.entry_from_packet(self.packet)["id"]
            selected_index = 0
            for index, item in enumerate(pins):
                self.pin_listbox.insert(tk.END, item["display_title"])
                if item.get("id") == current_entry_id:
                    selected_index = index
            self.pin_listbox.selection_set(selected_index)
            self.pin_listbox.activate(selected_index)

            self.pin_listbox.bind("<Double-Button-1>", self._open_selected_pin)
            self.pin_listbox.bind("<Return>", self._open_selected_pin)
            self.pin_listbox.bind("<Button-3>", self._show_pin_item_menu)

        button_x = self.pin_btn.winfo_rootx()
        button_y = self.pin_btn.winfo_rooty() + self.pin_btn.winfo_height()
        self.pin_popup.geometry(f"+{button_x-220}+{button_y+4}")
        self.pin_popup.after(0, self._hide_pin_popup_close_button)
        self.pin_popup.after(0, self.pin_popup.focus_set)

    def _get_selected_pin_entry(self):
        if not self.pin_listbox:
            return None
        selection = self.pin_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if 0 <= index < len(self._pinned_entries_cache):
            return self._pinned_entries_cache[index]
        return None

    def _open_selected_pin(self, event=None):
        entry = self._get_selected_pin_entry()
        if not entry:
            return
        packet = self._entry_to_packet(entry)
        self._display_packet(packet)
        self._update_nav_buttons()
        self._update_pin_button()
        self._close_pin_popup()

    def _rename_selected_pin(self):
        entry = self._get_selected_pin_entry()
        if not entry:
            return
        current_title = entry.get("pin_title") or entry.get("display_title") or ""
        new_title = simpledialog.askstring(
            "Rename pin",
            "Tiêu đề pin mới. Để trống để quay về tiêu đề mặc định:",
            initialvalue=current_title,
            parent=self.pin_popup or self.win,
        )
        if new_title is None:
            return
        self.snap_history.rename_pin(entry["id"], new_title)
        self._show_pin_popup()
        self._update_pin_button()

    def _unpin_selected_item(self):
        entry = self._get_selected_pin_entry()
        if not entry:
            return
        self.snap_history.unpin(entry["id"])
        self._update_pin_button()
        self._show_pin_popup()

    def _show_pin_item_menu(self, event):
        if not self.pin_listbox:
            return
        index = self.pin_listbox.nearest(event.y)
        if index < 0 or index >= len(self._pinned_entries_cache):
            return
        self.pin_listbox.selection_clear(0, tk.END)
        self.pin_listbox.selection_set(index)
        self.pin_listbox.activate(index)

        self.pin_item_menu = tk.Menu(self.pin_popup, tearoff=0)
        self.pin_item_menu.add_command(label="Open", command=self._open_selected_pin)
        self.pin_item_menu.add_command(label="Rename Title", command=self._rename_selected_pin)
        self.pin_item_menu.add_command(label="Unpin", command=self._unpin_selected_item)
        self.pin_item_menu.tk_popup(event.x_root, event.y_root)

    # --- HÀM MỚI: XÂY DỰNG VÀ HIỂN THỊ MENU ---
    def build_context_menu(self):
        """Tạo menu chuột phải một cách chính xác."""
        self.context_menu = tk.Menu(self.win, tearoff=0)
        self.context_menu.add_command(
            label="Pin",
            command=self._toggle_current_pin_from_menu,
        )
        self.context_menu.add_command(
            label="Pinned List",
            command=self._show_pin_popup,
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Rerun (Tripple-left-click)", command=self.tripple_left_click_rerun
        )

        # <<< MENU CHAT - Hoạt động cho cả ảnh và text >>>
        if WORKSPACE_UI_AVAILABLE:
            self.context_menu.add_command(
                label="Continue Chat in Workspace", command=self.open_chat_ui
            )
        elif CHAT_UI_AVAILABLE:
            self.context_menu.add_command(
                label="Continue Chat", command=self.open_chat_ui
            )
        else:
            self.context_menu.add_command(
                label="Chat UI not available", state="disabled"
            )
        # <<< -------------------------------------------- >>>

        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Re run", command=self.tripple_left_click_rerun
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label=self._theme_menu_label(),
            command=self.toggle_theme,
        )
        self._style_context_menu()

    def _theme_menu_label(self):
        return "Switch to Light Mode" if self.theme_name == "dark" else "Switch to Dark Mode"

    def _style_context_menu(self):
        try:
            self.context_menu.configure(
                bg=self.theme["popup_bg"],
                fg=self.theme["popup_fg"],
                activebackground=self.theme["popup_select_bg"],
                activeforeground=self.theme["popup_select_fg"],
            )
        except tk.TclError:
            pass

    def _apply_theme(self):
        self.theme = FLOAT_UI_THEMES.get(self.theme_name, FLOAT_UI_THEMES["dark"])
        self.win.configure(bg=self.theme["window_bg"])
        self.header.configure(bg=self.theme["header_bg"])
        self.body.configure(bg=self.theme["window_bg"])
        self.textbox.configure(
            bg=self.theme["text_bg"],
            fg=self.theme["text_fg"],
            insertbackground=self.theme["text_fg"],
        )
        self.scrollbar.configure(
            bg=self.theme["scrollbar_bg"],
            activebackground=self.theme["active_bg"],
            troughcolor=self.theme["window_bg"],
        )
        for widget in (
            self.info_label,
            self.file_label,
            self.timer_label,
            self.follow_status_label,
            self.nav_counter_label,
            self.resizer_left,
            self.resizer_right,
        ):
            widget.configure(bg=self.theme["header_bg"])
        for widget in (
            self.close_btn,
            self.record_btn,
            self.pause_btn,
            self.transcribe_btn,
            self.follow_btn,
            self.pin_btn,
            self.nav_back_btn,
            self.nav_next_btn,
        ):
            widget.configure(bg=self.theme["header_bg"], activebackground=self.theme["active_bg"])
        self._style_context_menu()
        self.context_menu.entryconfigure(self.context_menu.index("end"), label=self._theme_menu_label())
        self.refresh_audio_stt_state()

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.config["theme"] = self.theme_name
        self._apply_theme()
        with FLOAT_WINDOW_LIST_LOCK:
            other_windows = [fw for fw in FLOAT_WINDOW_LIST if fw is not self]
        for fw in other_windows:
            try:
                if not fw.win.winfo_exists():
                    continue
                fw.theme_name = self.theme_name
                fw.config["theme"] = self.theme_name
                fw._apply_theme()
            except Exception as e:
                print(f"Error applying Float UI theme to another window: {e}")
        try:
            ConfigManager.save_config(
                self.win.winfo_x(),
                self.win.winfo_y(),
                self.win.winfo_width(),
                self.win.winfo_height(),
                hotkey=self.config.get("hotkey", ConfigManager.DEFAULT_CONFIG["hotkey"]),
                theme=self.theme_name,
            )
        except Exception as e:
            print(f"Error saving Float UI theme: {e}")

    def show_context_menu(self, event):
        """Hiển thị menu chuột phải tại vị trí con trỏ."""
        try:
            pin_label = "Unpin" if self.snap_history.is_pinned(self.packet) else "Pin"
            self.context_menu.entryconfigure(0, label=pin_label)
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def open_chat_ui(self):
        """Escalate current FloatingWindow result to the reworked Chat Screen (Ctrl+Shift+Alt+X)."""
        cb = getattr(self.packet, "open_chat_screen_callback", None)
        if callable(cb):
            self.master.after(0, cb, self.packet)
            return
        print("Chat Screen callback not attached to packet.")

    # ----------------------------------------------------

    # --- HÀM MỚI ĐƯỢC THÊM VÀO ---
    def _update_info_label(self):
        """Helper method to format and set the info label text."""
        if not self.win.winfo_exists():
            return

        # Xử lý tên model và provider
        short_model_name = self.model_name.split("/")[-1] if self.model_name else "?"
        provider_name = self.model_name.split("/")[0] if "/" in (self.model_name or "") else "?"
        current_model_name = getattr(self, "current_model_name", self.model_name)
        short_current_model = (
            current_model_name.split("/")[-1] if current_model_name else "?"
        )

        # Lấy thông tin API key (fallback to provider)
        api_index = getattr(self.packet, "api_key_index", None)
        display_source = api_index if api_index and api_index != "?" else provider_name

        current_provider = (
            current_model_name.split("/")[0]
            if "/" in (current_model_name or "")
            else provider_name
        )
        if current_model_name != self.model_name:
            prov_suffix = f" ({current_provider})" if current_provider and current_provider != provider_name else ""
            info_text = (
                f"[{self.prompt_name}] result: {short_model_name} | {display_source} "
                f"| current: {short_current_model}{prov_suffix}"
            )
        else:
            info_text = f"[{self.prompt_name}] {short_model_name} | {display_source}"
        self.info_label.config(text=info_text)

    def set_current_model(self, model_name):
        """Update the model used by new actions without rewriting result metadata."""
        self.current_model_name = model_name or "Unknown"
        self._update_info_label()

    def _setup_tags(self):
        """Initializes tags for markdown rendering."""
        # Try to use a nice monospace font for code
        try:
            code_font = tkfont.Font(family="Consolas", size=11)
        except Exception:
            try:
                code_font = tkfont.Font(family="Courier", size=11)
            except Exception:
                code_font = ("monospace", 11)

        # Basic formatting
        self.textbox.tag_configure(
            "bold", font=("Arial", 12, "bold"), foreground="#569CD6"
        )
        self.textbox.tag_configure(
            "italic", font=("Arial", 12, "italic"), foreground="#CE9178"
        )

        # Headings
        self.textbox.tag_configure(
            "h1",
            font=("Arial", 18, "bold"),
            foreground="#D16969",
            spacing1=10,
            spacing3=5,
        )
        self.textbox.tag_configure(
            "h2",
            font=("Arial", 16, "bold"),
            foreground="#D16969",
            spacing1=8,
            spacing3=4,
        )
        self.textbox.tag_configure(
            "h3",
            font=("Arial", 14, "bold"),
            foreground="#D16969",
            spacing1=6,
            spacing3=3,
        )

        # Code blocks
        self.textbox.tag_configure(
            "code_block",
            font=code_font,
            background="#1E1E1E",
            foreground="#D4D4D4",
            spacing1=5,
            spacing3=5,
            lmargin1=10,
            lmargin2=10,
        )

        # Hidden tag for markdown symbols
        self.textbox.tag_configure("hidden", elide=True)
        self.textbox._md_has_tags = False

    def _parse_markdown(self):
        """Schedules markdown parsing to avoid blocking the main thread."""
        if hasattr(self.textbox, "_md_timer") and self.textbox._md_timer is not None:
            self.win.after_cancel(self.textbox._md_timer)
        self.textbox._md_timer = self.win.after(60, self._do_parse_markdown)

    def _text_needs_markdown(self, text):
        if not text:
            return False
        return any(marker in text for marker in ("```", "**", "*", "\n#", "\n##", "\n###")) or text.startswith(("# ", "## ", "### "))

    def _do_parse_markdown(self):
        """Parses markdown-like syntax in the textbox and applies tags."""
        if not self.textbox.winfo_exists():
            return
        self.textbox._md_timer = None
        current_text = self.textbox.get("1.0", "end-1c")
        needs_markdown = self._text_needs_markdown(current_text)
        
        self.textbox.config(state=tk.NORMAL)

        if getattr(self.textbox, "_md_has_tags", False) or needs_markdown:
            for tag in self._markdown_tags:
                self.textbox.tag_remove(tag, "1.0", tk.END)
        if not needs_markdown:
            self.textbox._md_has_tags = False
            return

        # 1. Code Blocks (```block```)
        start = "1.0"
        while True:
            start = self.textbox.search(r"```", start, stopindex=tk.END, regexp=True)
            if not start:
                break
            end_marker = self.textbox.search(
                r"```", f"{start} + 3 chars", stopindex=tk.END, regexp=True
            )
            if not end_marker:
                break

            real_end = f"{end_marker} + 3 chars"
            self.textbox.tag_add("code_block", start, real_end)
            self.textbox.tag_add("hidden", start, f"{start} + 3 chars")
            self.textbox.tag_add("hidden", end_marker, real_end)
            start = real_end

        # 2. Headings (# Heading)
        for i in range(3, 0, -1):
            pattern = r"^" + "#" * i + r"\s+"
            start = "1.0"
            while True:
                start = self.textbox.search(
                    pattern, start, stopindex=tk.END, regexp=True
                )
                if not start:
                    break

                # Check if it's at line start and not in code block
                if self.textbox.index(f"{start} linestart") == self.textbox.index(
                    start
                ) and "code_block" not in self.textbox.tag_names(start):
                    line_end = self.textbox.index(f"{start} lineend")
                    self.textbox.tag_add(f"h{i}", start, line_end)
                    # Hide the # symbols
                    hash_match = self.textbox.search(
                        r"#+\s+", start, stopindex=line_end, regexp=True
                    )
                    if hash_match:
                        # Find end of hashes
                        hash_end = self.textbox.search(
                            r"\s+", start, stopindex=line_end, regexp=True
                        )
                        if hash_end:
                            # Hide until text starts
                            text_start = self.textbox.search(
                                r"\S", hash_end, stopindex=line_end, regexp=True
                            )
                            if text_start:
                                self.textbox.tag_add("hidden", start, text_start)
                    start = line_end
                else:
                    start = f"{start} + 1 char"

        # 3. Bold (**text**)
        start = "1.0"
        while True:
            start = self.textbox.search(r"\*\*", start, stopindex=tk.END, regexp=True)
            if not start:
                break

            if "code_block" in self.textbox.tag_names(start):
                start = f"{start} + 2 chars"
                continue

            end = self.textbox.search(
                r"\*\*", f"{start} + 2 chars", stopindex=tk.END, regexp=True
            )
            if not end:
                break

            self.textbox.tag_add("bold", start, f"{end} + 2 chars")
            self.textbox.tag_add("hidden", start, f"{start} + 2 chars")
            self.textbox.tag_add("hidden", end, f"{end} + 2 chars")
            start = f"{end} + 2 chars"

        # 4. Italic (*text*)
        start = "1.0"
        while True:
            # Match single * but avoid double ** or already tagged bold/code
            start = self.textbox.search(r"\*", start, stopindex=tk.END, regexp=True)
            if not start:
                break

            # Check if it's a double **
            next_char = self.textbox.get(f"{start} + 1 char")
            if next_char == "*":
                start = f"{start} + 2 chars"
                continue

            current_tags = self.textbox.tag_names(start)
            if (
                "code_block" in current_tags
                or "bold" in current_tags
                or "hidden" in current_tags
            ):
                start = f"{start} + 1 char"
                continue

            # Look for closing *
            end = None
            search_from = f"{start} + 1 char"
            while True:
                candidate = self.textbox.search(
                    r"\*", search_from, stopindex=tk.END, regexp=True
                )
                if not candidate:
                    break
                # Check it's not part of **
                prev_char = self.textbox.get(f"{candidate} - 1 char")
                next_char = self.textbox.get(f"{candidate} + 1 char")
                if prev_char != "*" and next_char != "*":
                    end = candidate
                    break
                search_from = f"{candidate} + 1 char"
            if not end:
                break

            self.textbox.tag_add("italic", start, f"{end} + 1 char")
            self.textbox.tag_add("hidden", start, f"{start} + 1 char")
            self.textbox.tag_add("hidden", end, f"{end} + 1 char")
            start = f"{end} + 1 char"
        self.textbox._md_has_tags = True

    # --- ADD CTRL+N HANDLER ---
    def _save_prompt_to_toml(self, key, new_value):
        """Save a prompt value to prompts.toml."""
        return fix_prompt_change(
            config_path=MAIN_CONFIG_FILE,
            key=key,
            new_value=new_value,
        )

    def handle_ctrl_n(self, event=None):
        """Save the current FixPrompt textbox text to prompt_fix_grammar."""
        if not self.win.winfo_exists():
            return

        try:
            new_prompt_value = self.textbox.get("1.0", "end-1c")

            if not new_prompt_value.strip():
                print("Textbox is empty. Not updating FixPrompt.")
                return

            print("Ctrl+N pressed. Saving FixPrompt to config.yaml.")

            success = self._save_prompt_to_toml(
                key="prompt_fix_grammar",
                new_value=new_prompt_value,
            )

            if success:
                original_bg = self.textbox.cget("bg")
                self.textbox.config(bg="lightgreen")
                self.win.after(
                    500,
                    lambda: self.textbox.config(bg=original_bg)
                    if self.win.winfo_exists()
                    else None,
                )
            else:
                print("prompt_fix_grammar update in config.yaml failed (see previous errors).")
                original_bg = self.textbox.cget("bg")
                self.textbox.config(bg="salmon")
                self.win.after(
                    1000,
                    lambda: self.textbox.config(bg=original_bg)
                    if self.win.winfo_exists()
                    else None,
                )

        except Exception as e:
            print(f"Error in handle_ctrl_n: {e}")

        return "break"  # Prevent default behavior (like inserting 'n')

    # ... (Các phương thức khác: update_ans, start_move, do_move, ..., close, double_right_click giữ nguyên) ...
    def update_ans(self, packet: DataPacket):
        """Updates the content and associated data from a DataPacket object."""
        if not self.win.winfo_exists():
            return

        # Cập nhật dữ liệu từ các thuộc tính của packet
        self.ans = packet.ans
        self.image_path = packet.image_path
        self.original_timestamp_str = packet.original_timestamp_str
        self.rerun_prompt_callback = packet.rerun_prompt_callback
        self.model_name = packet.model_name
        self.prompt_name = packet.prompt_name
        self.packet = packet  # Quan trọng: cập nhật reference packet để _update_info_label lấy được data mới
        self.record_audio_callback = (
            packet.record_audio_callback
        )  # Cập nhật callback mới nếu có

        self._update_info_label()  # Cập nhật label với thông tin mới

        self.textbox.config(state=tk.NORMAL)
        self.textbox.delete("1.0", tk.END)
        self.textbox.insert(tk.END, self.ans)
        self._parse_markdown()
        # self.textbox.config(state=tk.DISABLED) # Bỏ comment nếu muốn text chỉ đọc

        # --- Snap History: push new entry ---
        if not getattr(packet, 'skip_history', False):
            self.snap_history.push(packet)
        self._update_nav_buttons()
        self._update_pin_button()
        self.transcribe_callback = packet.transcribe_callback
        self.select_audio_files_callback = getattr(packet, "select_audio_files_callback", None)
        self.toggle_follow_callback = getattr(packet, "toggle_follow_callback", None)
        self.get_follow_state_callback = getattr(packet, "get_follow_state_callback", None)
        self.get_audio_stt_state_callback = getattr(
            packet, "get_audio_stt_state_callback", None
        )
        self.refresh_follow_state()
        self.refresh_audio_stt_state()

        if not self.visible:
            self.toggle_visibility()
        else:
            self.win.lift()
        print(
            f"FloatingWindow content updated for: {os.path.basename(self.image_path)}"
        )

    # --- Snap History Navigation Methods ---
    def _nav_back(self):
        """Navigate to previous snap entry."""
        entry = self.snap_history.go_back()
        if entry:
            packet = self._entry_to_packet(entry)
            self._display_packet(packet)
            self._update_nav_buttons()

    def _nav_next(self):
        """Navigate to next snap entry."""
        entry = self.snap_history.go_next()
        if entry:
            packet = self._entry_to_packet(entry)
            self._display_packet(packet)
            self._update_nav_buttons()

    def _entry_to_packet(self, entry: dict) -> DataPacket:
        """Convert a saved history dict to a DataPacket with current callbacks."""
        return DataPacket(
            ans=entry.get("ans", ""),
            image_path=entry.get("image_path", ""),
            original_timestamp_str=entry.get("timestamp", ""),
            model_name=entry.get("model_name", "Unknown"),
            prompt_name=entry.get("prompt_name", "Unknown"),
            api_key_index=entry.get("api_key_index", "?"),
            api_key_mask=entry.get("api_key_mask", "????"),
            # Fallback to current packet's callbacks
            rerun_prompt_callback=getattr(self.packet, "rerun_prompt_callback", None),
            record_audio_callback=getattr(self.packet, "record_audio_callback", None),
            transcribe_callback=getattr(self.packet, "transcribe_callback", None),
            select_audio_files_callback=getattr(self.packet, "select_audio_files_callback", None),
            get_audio_duration_callback=getattr(self.packet, "get_audio_duration_callback", None),
            pause_audio_callback=getattr(self.packet, "pause_audio_callback", None),
            gemini_instance=getattr(self.packet, "gemini_instance", None),
            toggle_follow_callback=getattr(self.packet, "toggle_follow_callback", None),
            get_follow_state_callback=getattr(self.packet, "get_follow_state_callback", None),
            get_audio_stt_state_callback=getattr(self.packet, "get_audio_stt_state_callback", None),
        )

    def _display_packet(self, packet: DataPacket):
        """Display a historical packet WITHOUT adding to history."""
        if not self.win.winfo_exists():
            return
        self.ans = packet.ans
        self.image_path = packet.image_path
        self.original_timestamp_str = packet.original_timestamp_str
        self.rerun_prompt_callback = packet.rerun_prompt_callback
        self.model_name = packet.model_name
        self.prompt_name = packet.prompt_name
        self.packet = packet
        self.record_audio_callback = packet.record_audio_callback
        self.transcribe_callback = packet.transcribe_callback
        self.select_audio_files_callback = getattr(packet, "select_audio_files_callback", None)
        self.toggle_follow_callback = getattr(packet, "toggle_follow_callback", None)
        self.get_follow_state_callback = getattr(packet, "get_follow_state_callback", None)
        self.get_audio_stt_state_callback = getattr(
            packet, "get_audio_stt_state_callback", None
        )

        self._update_info_label()
        self.textbox.config(state=tk.NORMAL)
        self.textbox.delete("1.0", tk.END)
        self.textbox.insert(tk.END, self.ans)
        self._parse_markdown()
        self._update_pin_button()
        self.refresh_follow_state()
        self.refresh_audio_stt_state()
        self.win.lift()

    def _update_nav_buttons(self):
        """Update Back/Next button states and counter label."""
        if not self.win.winfo_exists():
            return
        self.nav_back_btn.config(
            state="normal" if self.snap_history.can_go_back else "disabled",
            fg="#333" if self.snap_history.can_go_back else "#bbb",
        )
        self.nav_next_btn.config(
            state="normal" if self.snap_history.can_go_next else "disabled",
            fg="#333" if self.snap_history.can_go_next else "#bbb",
        )
        self.nav_counter_label.config(text=self.snap_history.position_text)

    # --- Drag and Resize Methods (Giữ nguyên) ---
    def start_move(self, event):
        """
        Bắt đầu quá trình kéo: Tạo cửa sổ ma và ẩn cửa sổ thật.
        """
        # Nếu đã có cửa sổ ma (trường hợp hiếm), hủy nó đi
        if self.ghost_window:
            self.ghost_window.destroy()

        # Tính toán độ lệch giữa vị trí click và góc trên bên trái của cửa sổ
        self.offset_x = event.x
        self.offset_y = event.y

        # Tạo cửa sổ ma
        self.ghost_window = tk.Toplevel(self.master)
        self.ghost_window.overrideredirect(True)  # Không có viền, thanh tiêu đề
        self.ghost_window.attributes("-alpha", 0.5)  # Bán trong suốt
        self.ghost_window.config(
            bg="skyblue", relief="solid", borderwidth=2
        )  # Một khung viền đơn giản

        # Đặt vị trí và kích thước của cửa sổ ma giống hệt cửa sổ thật
        x, y = self.win.winfo_x(), self.win.winfo_y()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        self.ghost_window.geometry(f"{w}x{h}+{x}+{y}")

        # Quan trọng: Ẩn cửa sổ thật đi
        self.win.withdraw()

    def do_move(self, event):
        """
        Trong khi kéo: Chỉ di chuyển cửa sổ ma siêu nhẹ.
        """
        if not self.ghost_window:
            return

        # Tính vị trí mới dựa trên vị trí chuột và độ lệch ban đầu
        new_x = event.x_root - self.offset_x
        new_y = event.y_root - self.offset_y
        self.ghost_window.geometry(f"+{new_x}+{new_y}")

    def end_move(self, event):
        """
        Khi thả chuột: Hủy cửa sổ ma, di chuyển và hiển thị lại cửa sổ thật.
        """
        if not self.ghost_window:
            return

        # Lấy vị trí cuối cùng của cửa sổ ma
        final_x = self.ghost_window.winfo_x()
        final_y = self.ghost_window.winfo_y()

        # Hủy cửa sổ ma
        self.ghost_window.destroy()
        self.ghost_window = None

        # Di chuyển cửa sổ thật đến vị trí cuối cùng (chỉ 1 lần)
        self.win.geometry(f"+{final_x}+{final_y}")

        # Hiển thị lại cửa sổ thật
        self.win.deiconify()
        self.win.lift()

    def start_resize_right(self, event):
        self._resize_right_start_x = event.x_root
        self._resize_right_start_y = event.y_root
        self._orig_width = self.win.winfo_width()
        self._orig_height = self.win.winfo_height()

    def do_resize_right(self, event):
        if not self.win.winfo_exists():
            return
        dx = event.x_root - self._resize_right_start_x
        dy = event.y_root - self._resize_right_start_y
        new_w = max(150, self._orig_width + dx)
        new_h = max(80, self._orig_height + dy)  # Kích thước tối thiểu
        # max_h = self.master.winfo_screenheight() - 50; new_h = min(new_h, max_h) # Giới hạn chiều cao max nếu cần
        self.win.geometry(f"{new_w}x{new_h}")
        # Không cần place lại resizer vì anchor="se" và "sw" tự xử lý

    def start_resize_left(self, event):
        self._resize_left_start_x = event.x_root
        self._resize_left_start_y = event.y_root
        self._orig_width_left = self.win.winfo_width()
        self._orig_height_left = self.win.winfo_height()
        self._orig_win_x = self.win.winfo_x()

    def do_resize_left(self, event):
        if not self.win.winfo_exists():
            return
        dx = self._resize_left_start_x - event.x_root  # Ngược lại cho resize trái
        dy = event.y_root - self._resize_left_start_y
        new_w = max(150, self._orig_width_left + dx)
        new_h = max(80, self._orig_height_left + dy)
        # max_h = self.master.winfo_screenheight() - 50; new_h = min(new_h, max_h)
        # Tính toán vị trí x mới để cạnh trái di chuyển theo chuột
        new_x = self._orig_win_x - (new_w - self._orig_width_left)
        self.win.geometry(f"{new_w}x{new_h}+{new_x}+{self.win.winfo_y()}")
        # Không cần place lại resizer

    def _enumerate_monitor_rects(self):
        monitor_rects = []

        try:
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            monitor_enum_proc = ctypes.WINFUNCTYPE(
                ctypes.c_int,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.POINTER(RECT),
                ctypes.c_double,
            )

            def _callback(h_monitor, hdc_monitor, lprc_monitor, dw_data):
                rect = lprc_monitor.contents
                monitor_rects.append((rect.left, rect.top, rect.right, rect.bottom))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(
                0, 0, monitor_enum_proc(_callback), 0
            )
        except Exception as e:
            print(f"Error enumerating monitors: {e}")

        if not monitor_rects:
            screen_w = self.master.winfo_screenwidth()
            screen_h = self.master.winfo_screenheight()
            monitor_rects.append((0, 0, screen_w, screen_h))

        return sorted(monitor_rects)

    def _point_in_rect(self, x, y, rect):
        left, top, right, bottom = rect
        return left <= x < right and top <= y < bottom

    def _ranges_overlap(self, start_a, end_a, start_b, end_b):
        return max(start_a, start_b) < min(end_a, end_b)

    def _is_side_blocked(
        self,
        current_rect,
        other_rect,
        segment,
        side,
        tolerance,
    ):
        left, top, right, bottom = current_rect
        other_left, other_top, other_right, other_bottom = other_rect
        seg_start, seg_end = segment

        if side == "top":
            return (
                other_bottom <= top
                and top - other_bottom <= tolerance
                and self._ranges_overlap(other_left, other_right, seg_start, seg_end)
            )
        if side == "bottom":
            return (
                other_top >= bottom
                and other_top - bottom <= tolerance
                and self._ranges_overlap(other_left, other_right, seg_start, seg_end)
            )
        if side == "left":
            return (
                other_right <= left
                and left - other_right <= tolerance
                and self._ranges_overlap(other_top, other_bottom, seg_start, seg_end)
            )
        return (
            other_left >= right
            and other_left - right <= tolerance
            and self._ranges_overlap(other_top, other_bottom, seg_start, seg_end)
        )

    def _is_corner_exposed(
        self,
        current_rect,
        all_rects,
        horizontal_segment,
        vertical_segment,
        horizontal_side,
        vertical_side,
    ):
        h_start, h_end = horizontal_segment
        v_start, v_end = vertical_segment
        left, top, right, bottom = current_rect
        side_tolerance = max(self._corner_threshold * 2, 40)

        for rect in all_rects:
            if rect == current_rect:
                continue

            if self._is_side_blocked(
                current_rect,
                rect,
                (h_start, h_end),
                horizontal_side,
                side_tolerance,
            ):
                return False

            if self._is_side_blocked(
                current_rect,
                rect,
                (v_start, v_end),
                vertical_side,
                side_tolerance,
            ):
                return False

        corner_probes = {
            ("top", "left"): [(left - 1, top), (left, top - 1), (left - 1, top - 1)],
            ("bottom", "left"): [(left - 1, bottom - 1), (left, bottom), (left - 1, bottom)],
            ("top", "right"): [(right, top), (right - 1, top - 1), (right, top - 1)],
            ("bottom", "right"): [(right, bottom - 1), (right - 1, bottom), (right, bottom)],
        }

        for probe_x, probe_y in corner_probes[(horizontal_side, vertical_side)]:
            for rect in all_rects:
                if rect == current_rect:
                    continue
                if self._point_in_rect(probe_x, probe_y, rect):
                    return False
        return True

    def _build_corner_zone(self, rect, corner_name):
        left, top, right, bottom = rect
        threshold = self._corner_threshold

        if corner_name == "top_left":
            return (left, top, min(right, left + threshold), min(bottom, top + threshold))
        if corner_name == "bottom_left":
            return (left, max(top, bottom - threshold), min(right, left + threshold), bottom)
        if corner_name == "top_right":
            return (max(left, right - threshold), top, right, min(bottom, top + threshold))
        return (max(left, right - threshold), max(top, bottom - threshold), right, bottom)

    def _pick_corner_trigger(self, rect, all_rects):
        left, top, right, bottom = rect
        threshold = self._corner_threshold
        candidates = [
            (
                "top_left",
                (left, min(right, left + threshold)),
                (top, min(bottom, top + threshold)),
                "top",
                "left",
            ),
            (
                "bottom_left",
                (left, min(right, left + threshold)),
                (max(top, bottom - threshold), bottom),
                "bottom",
                "left",
            ),
            (
                "top_right",
                (max(left, right - threshold), right),
                (top, min(bottom, top + threshold)),
                "top",
                "right",
            ),
            (
                "bottom_right",
                (max(left, right - threshold), right),
                (max(top, bottom - threshold), bottom),
                "bottom",
                "right",
            ),
        ]

        for corner_name, horizontal_segment, vertical_segment, horizontal_side, vertical_side in candidates:
            if self._is_corner_exposed(
                rect,
                all_rects,
                horizontal_segment,
                vertical_segment,
                horizontal_side,
                vertical_side,
            ):
                return {
                    "corner": corner_name,
                    "zone": self._build_corner_zone(rect, corner_name),
                    "rect": rect,
                }

        return {
            "corner": "top_left",
            "zone": self._build_corner_zone(rect, "top_left"),
            "rect": rect,
        }

    _GLOBAL_MONITOR_RECTS = []
    _GLOBAL_TRIGGER_ZONES = []
    _GLOBAL_LAYOUT_SIG = None
    _GLOBAL_NEXT_REFRESH = 0.0

    def _refresh_monitor_layout(self, force=False):
        current_time = time.time()
        cls = type(self)
        if not force and current_time < cls._GLOBAL_NEXT_REFRESH and cls._GLOBAL_MONITOR_RECTS:
            self._monitor_rects_cache = cls._GLOBAL_MONITOR_RECTS
            self._monitor_trigger_zones = cls._GLOBAL_TRIGGER_ZONES
            self._monitor_layout_signature = cls._GLOBAL_LAYOUT_SIG
            return

        rects = self._enumerate_monitor_rects()
        layout_signature = tuple(rects)
        if force or layout_signature != cls._GLOBAL_LAYOUT_SIG:
            cls._GLOBAL_MONITOR_RECTS = rects
            cls._GLOBAL_TRIGGER_ZONES = [
                self._pick_corner_trigger(rect, rects) for rect in rects
            ]
            cls._GLOBAL_LAYOUT_SIG = layout_signature
        self._monitor_rects_cache = cls._GLOBAL_MONITOR_RECTS
        self._monitor_trigger_zones = cls._GLOBAL_TRIGGER_ZONES
        self._monitor_layout_signature = cls._GLOBAL_LAYOUT_SIG
        cls._GLOBAL_NEXT_REFRESH = current_time + max(30.0, float(self._monitor_layout_refresh_interval))

    def _get_trigger_corner_at(self, x, y):
        for trigger in self._monitor_trigger_zones:
            left, top, right, bottom = trigger["zone"]
            if left <= x < right and top <= y < bottom:
                return trigger
        return None

    # --- Auto Hide/Show Logic ---
    def check_global_mouse_position(self):
        # Hủy job cũ nếu có
        if self._check_mouse_job:
            try:
                self.master.after_cancel(self._check_mouse_job)
            except ValueError:
                pass
            self._check_mouse_job = None

        # Kiểm tra cửa sổ còn tồn tại không
        if not self.win.winfo_exists() or not self.master.winfo_exists():
            return  # Dừng kiểm tra nếu cửa sổ đã đóng

        try:
            # Chỉ kiểm tra nếu pyautogui khả dụng
            if PYAUTOGUI_AVAILABLE:
                self._refresh_monitor_layout()
                x, y = pyautogui.position()
                active_trigger = self._get_trigger_corner_at(x, y)
                in_corner = active_trigger is not None

                # Chỉ toggle khi chuột *đi vào* góc (trạng thái in_corner thay đổi từ False sang True)
                if in_corner and not self.last_in_corner:
                    print(
                        f"Mouse entered trigger corner: cursor=({x}, {y}) corner={active_trigger['corner']} rect={active_trigger['rect']}"
                    )
                    self.toggle_visibility()
                    self.last_in_corner = True  # Đánh dấu đã xử lý việc vào góc
                elif not in_corner:
                    self.last_in_corner = False  # Reset cờ khi chuột ra khỏi góc

            next_interval = (
                self._auto_hide_interval_visible
                if self.visible
                else self._auto_hide_interval_hidden
            )
            self._check_mouse_job = self.master.after(
                next_interval, self.check_global_mouse_position
            )

        except pyautogui.FailSafeException:
            print("PyAutoGUI Fail-Safe triggered. Pausing auto-hide check.")
            self._check_mouse_job = self.master.after(
                2000, self.check_global_mouse_position
            )
        except Exception as e:
            print(f"Error checking mouse position: {e}. Pausing auto-hide check.")
            self._check_mouse_job = self.master.after(
                1000, self.check_global_mouse_position
            )

    def toggle_visibility(self):
        if not self.win.winfo_exists():
            return
        if self.visible:
            self.win.withdraw()
            self.visible = False
            print("FloatingWindow hidden.")
        else:
            self.win.deiconify()
            self.win.lift()
            self.visible = True
            print("FloatingWindow shown.")

    # --- Close and Double-Click Logic ---
    def close(self, event=None):
        self._cancel_timer_loop()
        if not self.win.winfo_exists():
            return
        print(f"Closing FloatingWindow for: {os.path.basename(self.image_path)}")

        # Hủy job kiểm tra chuột
        if self._check_mouse_job:
            try:
                self.master.after_cancel(self._check_mouse_job)
            except ValueError:
                pass
            self._check_mouse_job = None

        # Lưu config trước khi hủy cửa sổ
        try:
            x, y = self.win.winfo_x(), self.win.winfo_y()
            width, height = self.win.winfo_width(), self.win.winfo_height()
            # Chỉ lưu nếu kích thước hợp lệ
            if width >= 50 and height >= 50:
                # Lấy hotkey hiện tại từ config đã load để lưu lại
                current_hotkey = self.config.get(
                    "hotkey", ConfigManager.DEFAULT_CONFIG["hotkey"]
                )
                ConfigManager.save_config(x, y, width, height, hotkey=current_hotkey)
        except Exception as e:
            print(f"Error saving config on close: {e}")

        self._close_pin_popup()

        # Hủy cửa sổ
        try:
            self.snap_history.flush()
        except Exception as e:
            print(f"Error flushing snap history: {e}")
        self.win.destroy()

        # Xóa khỏi danh sách global
        global FLOAT_WINDOW_LIST
        with FLOAT_WINDOW_LIST_LOCK:
            try:
                if self in FLOAT_WINDOW_LIST:
                    FLOAT_WINDOW_LIST.remove(self)
            except ValueError:
                pass  # Bỏ qua nếu không tìm thấy (đã bị xóa?)
        print(f"FloatingWindow closed. Remaining windows: {len(FLOAT_WINDOW_LIST)}")

    def tripple_left_click_rerun(self, event=None):
        """Handles double-right-click or menu selection: Triggers the rerun callback."""
        if not self.win.winfo_exists():
            return
            
        if event and hasattr(event, "widget"):
            # Ignore triple clicks on the header and its children (buttons)
            widget_path = str(event.widget)
            header_path = str(self.header)
            if widget_path == header_path or widget_path.startswith(header_path + "."):
                return "break"

        print(f"Rerun requested for prompt: {self.prompt_name}")
        if self.rerun_prompt_callback and callable(self.rerun_prompt_callback):
            try:
                self.rerun_prompt_callback(self.image_path, self.original_timestamp_str)
            except Exception as e:
                print(f"Error executing rerun_prompt_callback: {e}")
        else:
            print("Warning: rerun_prompt_callback is not set or not callable.")
            
        return "break"


def show_floating_window(master_root, packet: DataPacket):
    if not isinstance(master_root, tk.Tk) and not isinstance(master_root, tk.Toplevel):
        print(
            "CRITICAL ERROR: master_root is not a valid Tk instance in show_floating_window."
        )
        return

    # Lấy cờ create_new từ thuộc tính của packet
    create_new = packet.create_new

    with FLOAT_WINDOW_LIST_LOCK:
        has_windows = len(FLOAT_WINDOW_LIST) > 0
        primary_window = FLOAT_WINDOW_LIST[-1] if has_windows else None

    if has_windows and not create_new and primary_window:
        try:
            if primary_window.win.winfo_exists():
                primary_window.update_ans(packet)  # Truyền cả packet
            else:
                print("Primary window was closed. Creating new.")
                with FLOAT_WINDOW_LIST_LOCK:
                    if primary_window in FLOAT_WINDOW_LIST:
                        FLOAT_WINDOW_LIST.remove(primary_window)
                FloatingWindow(master_root, packet)  # Truyền cả packet
        except Exception as e:
            print(f"Error updating primary window: {e}. Attempting to create new.")
            with FLOAT_WINDOW_LIST_LOCK:
                try:
                    if primary_window in FLOAT_WINDOW_LIST:
                        FLOAT_WINDOW_LIST.remove(primary_window)
                except ValueError:
                    pass
            try:
                # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
                # SỬA LỖI Ở ĐÂY: Thay thế lệnh gọi cũ bằng lệnh gọi đúng sử dụng packet
                FloatingWindow(master_root, packet)
                # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
            except Exception as e_create:
                print(f"Error creating fallback window: {e_create}")

    else:
        with FLOAT_WINDOW_LIST_LOCK:
            count = len(FLOAT_WINDOW_LIST)
        action = (
            "Forcing new"
            if create_new
            else ("Creating primary" if count == 0 else "Creating additional")
        )
        print(f"{action} FloatingWindow ({count} existing)...")
        try:
            with FLOAT_WINDOW_LIST_LOCK:
                live = [w for w in FLOAT_WINDOW_LIST if w and hasattr(w, "win") and w.win.winfo_exists()]
                FLOAT_WINDOW_LIST[:] = live
                while len(FLOAT_WINDOW_LIST) >= MAX_OPEN_FLOAT_WINDOWS:
                    oldest = FLOAT_WINDOW_LIST.pop(0)
                    try:
                        oldest.close()
                    except Exception:
                        pass
            FloatingWindow(master_root, packet)  # Truyền cả packet
        except Exception as e:
            print(f"Error creating new floating window: {e}")
