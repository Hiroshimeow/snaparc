import json
import os
import shutil
import subprocess
import textwrap
import time
import tkinter as tk
import unittest

from src.gui.screenshot_chat.models import ScreenshotChatMessage, ScreenshotChatSession


PI_MODEL_ID = "pi://agent/pi"


class FakeTkController:
    def __init__(self):
        self.selected_models = []
        self.status = ""
        self.pending_attachments = []
        self.held_images_getter = lambda: []
        self.session = ScreenshotChatSession(
            id="s1",
            title="Existing",
            model_name="provider/main",
            prompt_type="prompt_translate",
            messages=[ScreenshotChatMessage(id="m1", role="assistant", author="main", text="rendered")],
        )

    def hide_window(self):
        return None

    def list_display_sessions(self):
        return [self.session]

    def get_active_session(self):
        return self.session

    def available_model_options(self):
        return list(getattr(self, "model_options", [
            {"id": "provider/main", "label": "main", "runtime": "llm", "available": True, "reason": ""},
            {"id": "provider/critic", "label": "critic", "runtime": "llm", "available": True, "reason": ""},
            {"id": PI_MODEL_ID, "label": "Pi Agent", "runtime": "pi", "available": True, "reason": "available"},
        ]))

    def model_display_name(self, model_name):
        if model_name == PI_MODEL_ID:
            return "Pi Agent"
        return model_name.rsplit("/", 1)[-1] if model_name else "Model"

    def set_session_model(self, model_name):
        self.selected_models.append(model_name)
        self.session.model_name = model_name

    def set_session_mode(self, mode):
        self.session.mode = mode

    def get_status(self):
        return self.status

    def drain_runner_events(self):
        return None

    def held_images_consumer(self, paths):
        return None

    def import_held_snaps(self):
        return 0

    def list_pinned_snaps(self):
        return []

    def import_file(self):
        return None

    def clear_pending_attachment(self, attachment_id):
        return None

    def system_prompt_summary(self):
        return "Blank"

    def is_system_prompt_selected(self, prompt_type, system_prompt, label):
        return label == "Blank"

    def prompt_definitions(self):
        return {}

    def set_session_system_prompt(self, prompt_type, system_prompt, label):
        return None

    def is_pinned_snap_used(self, entry):
        return False


class RealTkUiTests(unittest.TestCase):
    def _create_window(self):
        try:
            import customtkinter
            from src.gui.screenshot_chat.window import ScreenshotChatWindow
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"customtkinter unavailable: {exc}")
        try:
            root = customtkinter.CTk()
            root.withdraw()
            controller = FakeTkController()
            window = ScreenshotChatWindow(root, controller)
            return root, window, controller
        except (tk.TclError, AttributeError) as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"Tk display unavailable: {exc}")

    def test_tk_model_menu_renders_pi_agent_and_hides_paste_image(self):
        root, window, controller = self._create_window()
        try:
            window.update_idletasks()
            window.update()

            model_menu = tk.Menu(window, tearoff=0)
            window._populate_model_menu(model_menu)
            labels = [model_menu.entrycget(index, "label") for index in range(model_menu.index("end") + 1) if model_menu.type(index) != "separator"]
            self.assertIn("[ ] Pi Agent", labels)
            model_menu.invoke(labels.index("[ ] Pi Agent"))
            self.assertEqual(controller.selected_models[-1], PI_MODEL_ID)

            controller.model_options = [
                {"id": "provider/main", "label": "main", "runtime": "llm", "available": True, "reason": ""},
                {"id": PI_MODEL_ID, "label": "Pi Agent", "runtime": "pi", "available": False, "reason": "Pi disabled"},
            ]
            disabled_menu = tk.Menu(window, tearoff=0)
            window._populate_model_menu(disabled_menu)
            disabled_labels = [disabled_menu.entrycget(index, "label") for index in range(disabled_menu.index("end") + 1) if disabled_menu.type(index) != "separator"]
            self.assertIn("[x] Pi Agent (Pi disabled)", disabled_labels)
            self.assertEqual(disabled_menu.entrycget(disabled_labels.index("[x] Pi Agent (Pi disabled)"), "state"), "disabled")
            controller.model_options[-1]["available"] = True
            controller.model_options[-1]["reason"] = "available"

            window.refresh_all()
            window.update_idletasks()
            window.update()
            self.assertEqual(window.model_button.cget("text"), "Pi Agent")
            self.assertIn("m1", window._message_widgets)

            attach_menu = tk.Menu(window, tearoff=0)
            window._populate_attach_menu(attach_menu)
            attach_labels = [attach_menu.entrycget(index, "label") for index in range(attach_menu.index("end") + 1) if attach_menu.type(index) != "separator"]
            self.assertNotIn("Paste image", attach_labels)
            self.assertIn("Add file", attach_labels)
        finally:
            try:
                window.destroy()
            finally:
                root.destroy()

    def test_tk_resize_updates_existing_message_width_without_rebuild(self):
        root, window, controller = self._create_window()
        try:
            window.deiconify()
            controller.session.messages = [
                ScreenshotChatMessage(
                    id="m-resize",
                    role="assistant",
                    author="main",
                    text="A wrapped response that should grow with the window width.",
                    status="done",
                )
            ]
            window.refresh_all()
            window.update_idletasks()
            window.update()

            widgets = window._message_widgets["m-resize"]
            original_frame = widgets["frame"]
            initial_width = original_frame.winfo_width()

            window.geometry("1000x780")
            window.update_idletasks()
            window.update()
            time.sleep(0.15)
            window.update_idletasks()
            window.update()

            expected_width = window._bubble_width()
            self.assertIs(window._message_widgets["m-resize"]["frame"], original_frame)
            self.assertGreater(original_frame.winfo_width(), initial_width)
            self.assertAlmostEqual(original_frame.winfo_width(), expected_width, delta=8)
        finally:
            try:
                window.destroy()
            finally:
                root.destroy()

    def test_tk_long_message_is_bounded_and_streaming_reuses_text_widget(self):
        root, window, controller = self._create_window()
        try:
            long_text = "# Large response\n\n" + ("rendered content line\n" * 5_000)
            controller.session.messages = [
                ScreenshotChatMessage(
                    id="m-long",
                    role="assistant",
                    author="main",
                    text=long_text,
                    status="done",
                )
            ]

            window.refresh_all()
            window.update_idletasks()
            window.update()

            long_widgets = window._message_widgets["m-long"]
            self.assertTrue(long_widgets["is_bounded"])
            self.assertIn("text_scrollbar", long_widgets)
            self.assertEqual(int(long_widgets["text"].cget("height")), 32)
            self.assertLess(long_widgets["text"].winfo_height(), 1_000)
            self.assertEqual(long_widgets["text"].get("1.0", "end-1c"), long_text)

            streaming_message = ScreenshotChatMessage(
                id="m-stream",
                role="assistant",
                author="main",
                text="first",
                status="thinking",
                job_id="job-stream",
            )
            controller.session.messages = [streaming_message]
            window.refresh_messages()
            window.update_idletasks()
            window.update()

            original_text_widget = window._message_widgets["m-stream"]["text"]
            streaming_message.text += " second"
            window.refresh_messages()
            window.update_idletasks()
            window.update()

            updated_widgets = window._message_widgets["m-stream"]
            self.assertIs(updated_widgets["text"], original_text_widget)
            self.assertEqual(original_text_widget.get("1.0", "end-1c"), "first second")
        finally:
            try:
                window.destroy()
            finally:
                root.destroy()


class WebViewDomUiTests(unittest.TestCase):
    def test_webview_dom_model_menu_pi_agent_and_no_paste_image_button(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is unavailable")
        probe = subprocess.run(
            ["node", "-e", "require('jsdom')"],
            cwd=os.getcwd(),
            text=True,
            capture_output=True,
        )
        if probe.returncode != 0:
            self.skipTest("jsdom is unavailable")

        state = {
            "version": "v1",
            "status": "",
            "model_label": "main",
            "models": ["provider/main", "provider/critic"],
            "model_options": [
                {"id": "provider/main", "label": "main", "runtime": "llm", "available": True, "reason": ""},
                {"id": PI_MODEL_ID, "label": "Pi Agent", "runtime": "pi", "available": True, "reason": "available"},
            ],
            "prompts": {},
            "session": {
                "id": "s1",
                "title": "Existing",
                "model_name": "provider/main",
                "model_label": "main",
                "prompt_type": "prompt_translate",
                "mode": "chat",
                "pending_job_ids": [],
                "system_prompt": "",
                "system_prompt_label": "Blank",
            },
            "sessions": [],
            "messages": [{"id": "m1", "role": "assistant", "author": "main", "text": "rendered", "status": "done", "attachments": [], "events": []}],
            "pending_attachments": [],
            "pinned_snaps": [],
        }
        script = textwrap.dedent(
            f"""
            const assert = require('assert');
            const fs = require('fs');
            const {{ JSDOM }} = require('jsdom');
            const dom = new JSDOM(`<!doctype html><body>
              <div id="app"></div><aside id="conversationRail"></aside><button id="railCollapseBtn"></button><div id="railSessions"></div><button id="newChatBtn"></button>
              <button id="historyBtn"></button><button id="closeBtn"></button><div id="modeToggle"><button data-mode="chat"></button><button data-mode="note"></button></div>
              <button id="modelBtn"></button><button id="runtimeStatus"></button><span id="runtimeLabel"></span><section id="runtimeBanner"></section><div id="menus"></div>
              <main id="messages"></main><button id="jumpBottom"></button><div id="status"></div><div id="attachments"></div>
              <button id="addBtn"></button><textarea id="input"></textarea><button id="actionBtn"></button><div id="title"></div>
            </body>`, {{ runScripts: 'dangerously', url: 'http://localhost' }});
            const calls = [];
            dom.window.__SCREENSHOT_CHAT_CONFIG__ = {{ serverUrl: 'http://test' }};
            dom.window.fetch = async (url, options={{}}) => {{ calls.push([url, options.body || '']); return {{ json: async () => null }}; }};
            dom.window.confirm = () => true;
            dom.window.eval(fs.readFileSync('src/gui/screenshot_chat/web_assets/app.js', 'utf8'));
            dom.window.ScreenshotChat.setState({json.dumps(state)});
            assert.strictEqual(dom.window.document.getElementById('modelBtn').textContent, 'main');
            dom.window.document.getElementById('modelBtn').click();
            const menus = dom.window.document.getElementById('menus');
            assert(menus.textContent.includes('Pi Agent'));
            menus.querySelector('[data-model="{PI_MODEL_ID}"]').click();
            state.model_options[1].available = false;
            state.model_options[1].reason = 'Pi disabled';
            dom.window.ScreenshotChat.setState(state);
            dom.window.document.getElementById('modelBtn').click();
            assert(menus.querySelector('[data-model="{PI_MODEL_ID}"]').disabled);
            assert.strictEqual(menus.querySelector('[data-model="{PI_MODEL_ID}"]').getAttribute('title'), 'Pi disabled');
            dom.window.document.getElementById('addBtn').click();
            assert(!menus.textContent.includes('Paste image'));
            assert.strictEqual(menus.querySelector('[data-clipboard]'), null);
            assert(dom.window.document.getElementById('messages').textContent.includes('rendered'));
            const textPaste = {{ clipboardData: {{ files: [], items: [] }} }};
            assert.strictEqual(dom.window.ScreenshotChat.hasImageClipboard(textPaste), false);
            const imagePaste = {{ clipboardData: {{ files: [{{ type: 'image/png' }}], items: [] }} }};
            assert.strictEqual(dom.window.ScreenshotChat.hasImageClipboard(imagePaste), true);
            setTimeout(() => {{
              assert(calls.some(([url, body]) => url.endsWith('/set_model') && body.includes('{PI_MODEL_ID}')));
            }}, 0);
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=os.getcwd(), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_webview_dom_session_change_resets_scroll_state(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is unavailable")
        probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=os.getcwd(), text=True, capture_output=True)
        if probe.returncode != 0:
            self.skipTest("jsdom is unavailable")

        base_state = {
            "version": "v1",
            "status": "",
            "model_label": "main",
            "models": ["provider/main"],
            "model_options": [{"id": "provider/main", "label": "main", "runtime": "llm", "available": True, "reason": ""}],
            "prompts": {},
            "session": {"id": "s1", "title": "Existing", "model_name": "provider/main", "prompt_type": "prompt_translate", "mode": "chat", "pending_job_ids": [], "system_prompt": "", "system_prompt_label": "Blank"},
            "sessions": [],
            "messages": [{"id": "m1", "role": "assistant", "author": "main", "text": "rendered", "status": "done", "attachments": [], "events": []}],
            "pending_attachments": [],
            "pinned_snaps": [],
        }
        new_state = json.loads(json.dumps(base_state))
        new_state["active_session_id"] = "s2"
        new_state["session"]["id"] = "s2"
        new_state["session"]["title"] = "New Chat"
        new_state["messages"] = []
        base_state["active_session_id"] = "s1"
        script = textwrap.dedent(
            f"""
            const assert = require('assert');
            const fs = require('fs');
            const {{ JSDOM }} = require('jsdom');
            const dom = new JSDOM(`<!doctype html><body>
              <div id="app"></div><aside id="conversationRail"></aside><button id="railCollapseBtn"></button><div id="railSessions"></div><button id="newChatBtn"></button>
              <button id="historyBtn"></button><button id="closeBtn"></button><div id="modeToggle"><button data-mode="chat"></button><button data-mode="note"></button></div>
              <button id="modelBtn"></button><button id="runtimeStatus"></button><span id="runtimeLabel"></span><section id="runtimeBanner"></section><div id="menus"></div>
              <main id="messages"></main><button id="jumpBottom"></button><div id="status"></div><div id="attachments"></div>
              <button id="addBtn"></button><textarea id="input"></textarea><button id="actionBtn"></button><div id="title"></div>
            </body>`, {{ runScripts: 'dangerously', url: 'http://localhost' }});
            dom.window.__SCREENSHOT_CHAT_CONFIG__ = {{ serverUrl: 'http://test' }};
            dom.window.fetch = async () => {{ return {{ json: async () => null }}; }};
            dom.window.eval(fs.readFileSync('src/gui/screenshot_chat/web_assets/app.js', 'utf8'));
            const messages = dom.window.document.getElementById('messages');
            Object.defineProperty(messages, 'clientHeight', {{ value: 100, configurable: true }});
            Object.defineProperty(messages, 'scrollHeight', {{ value: 1000, configurable: true }});
            dom.window.ScreenshotChat.setState({json.dumps(base_state)});
            messages.scrollTop = 900;
            dom.window.ScreenshotChat.setState({json.dumps(new_state)});
            assert.strictEqual(messages.scrollTop, 0);
            assert.strictEqual(messages.children.length, 0);
            """
        )
        result = subprocess.run(["node", "-e", script], cwd=os.getcwd(), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

class SourceUiRegressionTests(unittest.TestCase):
    def test_window_uses_single_wheel_router_and_viewport_clamp(self):
        with open("src/gui/screenshot_chat/window.py", "r", encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("MOUSEWHEEL_SCROLL_UNITS = 140", source)
        self.assertIn("_normalized_wheel_delta", source)
        self.assertIn("outer chat owns scrolling", source)
        self.assertNotIn("_message_viewport_height", source)
        self.assertNotIn("_text_can_scroll", source)
        self.assertNotIn('bind("<MouseWheel>", self._on_mousewheel_fast, add="+")', source)
        self.assertNotIn("for delay in (0, 30, 120, 260)", source)
        self.assertIn("_thumbnail_bounds", source)

    def test_composers_measure_displaylines_not_only_literal_newlines(self):
        with open("src/gui/screenshot_chat/composer.py", "r", encoding="utf-8") as handle:
            composer_source = handle.read()

        self.assertIn('count("1.0", "end-1c", "displaylines")', composer_source)
        self.assertNotIn('text.count("\\n") + 1', composer_source)
