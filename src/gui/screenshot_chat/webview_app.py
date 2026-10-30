from __future__ import annotations

import argparse
import ctypes
import json
import os
import threading
import time
import urllib.request

import webview


class ScreenshotChatChildBridge:
    def __init__(self, window):
        self._window = window

    def set_window(self, window):
        self._window = window

    def hide(self):
        self._window.hide()
        return True

    def pick_file(self):
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                "Supported files (*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp;*.pdf;*.txt)",
                "Images (*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp)",
                "All files (*.*)",
            ),
        )
        return list(result or [])


class WindowCommandPoller:
    def __init__(self, window, server_url: str, token: str = ""):
        self.window = window
        self.server_url = server_url.rstrip("/")
        self.token = token
        self._stop = threading.Event()
        self._last_seq = -1
        self._visible = True

    def start(self):
        threading.Thread(target=self._run, name="ScreenshotChatWindowCommands", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                ui = _fetch_json(f"{self.server_url}/ui", self.token)
                seq = int(ui.get("seq", -1))
                action = str(ui.get("action") or "hide")
                if seq != self._last_seq:
                    self._last_seq = seq
                    if action == "show" and not self._visible:
                        self.window.show()
                        self._visible = True
                    elif action == "hide" and self._visible:
                        self.window.hide()
                        self._visible = False
                        _trim_child_working_set()
            except Exception:
                pass
            self._stop.wait(0.4 if self._visible else 0.85)


def _trim_child_working_set() -> None:
    import gc
    gc.collect()
    if os.name == "nt":
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
            ctypes.windll.psapi.EmptyWorkingSet(handle)
        except Exception:
            pass


def _fetch_json(url: str, token: str = "") -> dict:
    request = urllib.request.Request(url, headers={"X-STC-Token": token})
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def _parent_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    if os.name != "nt":
        try:
            os.kill(parent_pid, 0)
            return True
        except OSError:
            return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
    if not handle:
        # On Windows, failing to open the process is not enough evidence to kill the child.
        return True
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _monitor_parent(parent_pid: int):
    while True:
        if not _parent_is_alive(parent_pid):
            os._exit(0)
        time.sleep(2)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--poll-interval-ms", type=int, default=900)
    parser.add_argument("--ready-file", default="")
    parser.add_argument("--gui", default="edgechromium")
    parser.add_argument("--token", default=os.environ.get("SNAPARC_CHAT_SESSION_TOKEN", ""))
    return parser.parse_args()


def main():
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection,Translate,"
        "BackForwardCache,RendererCodeIntegrity,SpareRendererForSitePerProcess "
        "--process-per-site --disk-cache-size=16777216 --media-cache-size=16777216 "
        "--js-flags=--max-old-space-size=192",
    )
    args = _parse_args()
    threading.Thread(target=_monitor_parent, args=(args.parent_pid,), name="ScreenshotChatParentMonitor", daemon=True).start()

    page_url = f"{args.server_url.rstrip('/')}/?t={args.token}"
    bridge_holder = {}
    bridge = ScreenshotChatChildBridge(None)

    def finalize_startup():
        try:
            payload = _fetch_json(f"{args.server_url}/ui", args.token)
            action = str(payload.get("action") or "hide")
            if action == "hide":
                window.hide()
                bridge_holder["poller"]._visible = False
        except Exception:
            try:
                window.hide()
                bridge_holder["poller"]._visible = False
            except Exception:
                pass
        if args.ready_file:
            try:
                with open(args.ready_file, "w", encoding="utf-8") as handle:
                    handle.write("ready")
            except OSError:
                pass
        bridge_holder["poller"].start()

    window = webview.create_window(
        "SnapArc Chat",
        url=page_url,
        js_api=bridge,
        width=900,
        height=760,
        min_size=(480, 560),
        frameless=True,
        on_top=True,
        hidden=False,
        easy_drag=False,
    )
    bridge.set_window(window)
    bridge_holder["poller"] = WindowCommandPoller(window, args.server_url, args.token)
    webview.start(finalize_startup, gui=args.gui or "edgechromium", debug=False)


if __name__ == "__main__":
    main()
