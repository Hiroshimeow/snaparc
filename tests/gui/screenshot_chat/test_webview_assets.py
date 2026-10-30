import json
import shutil
import subprocess
import unittest
from pathlib import Path


CSS_SOURCE = Path("src/gui/screenshot_chat/web_assets/app.css").read_text(encoding="utf-8")
JS_SOURCE = Path("src/gui/screenshot_chat/web_assets/app.js").read_text(encoding="utf-8")
INDEX_SOURCE = Path("src/gui/screenshot_chat/web_assets/index.html").read_text(encoding="utf-8")
BRIDGE_SOURCE = Path("src/gui/screenshot_chat/webview_bridge.py").read_text(encoding="utf-8")
CONTROLLER_SOURCE = Path("src/gui/screenshot_chat/controller.py").read_text(encoding="utf-8")


class ScreenshotChatWebAssetsTests(unittest.TestCase):
    def test_message_area_uses_outer_scroll_without_nested_message_scroll(self):
        self.assertIn(".messages", CSS_SOURCE)
        self.assertIn("overflow-y: auto", CSS_SOURCE)
        message_block = CSS_SOURCE.split(".message {", 1)[1].split(".message.user", 1)[0]
        self.assertNotIn("overflow-y", message_block)
        self.assertIn("width: min(82%", message_block)

    def test_jump_bottom_uses_near_bottom_state_not_random_render_state(self):
        self.assertIn("function nearBottom", JS_SOURCE)
        self.assertIn("el.messages.addEventListener(\"scroll\", updateJumpButton)", JS_SOURCE)
        self.assertIn("hasUnseenBottom", JS_SOURCE)
        self.assertIn("requestAnimationFrame(apply)", JS_SOURCE)
        self.assertIn("setTimeout(apply, 80)", JS_SOURCE)
        self.assertIn("cancelScrollTimers", JS_SOURCE)
        self.assertIn("activeSessionId", JS_SOURCE)
        self.assertIn("scroll-padding-bottom: 104px", CSS_SOURCE)
        self.assertIn("el.jumpBottom.hidden = state.atBottom && !state.hasUnseenBottom", JS_SOURCE)

    def test_messages_render_incrementally_without_rebuilding_whole_list(self):
        self.assertIn("const renderedMessages = new Map()", JS_SOURCE)
        self.assertIn("function upsertMessage", JS_SOURCE)
        self.assertNotIn("el.messages.innerHTML = data.messages.map", JS_SOURCE)
        self.assertIn("replaceChildren", JS_SOURCE)
        self.assertIn("set_interval", JS_SOURCE)
        self.assertIn("drain_events", JS_SOURCE)
        self.assertIn("result.unchanged", JS_SOURCE)
        self.assertIn("msg.signature", JS_SOURCE)
        self.assertIn("childrenMatchOrder", JS_SOURCE)

    def test_frontend_uses_http_transport_with_shell_bridge_only_for_local_actions(self):
        self.assertIn("window.__SCREENSHOT_CHAT_CONFIG__", JS_SOURCE)
        self.assertIn("fetch(`${runtimeConfig.serverUrl}${path}`", JS_SOURCE)
        self.assertIn("shellApi()", JS_SOURCE)
        self.assertIn("bridge.pick_file", JS_SOURCE)
        self.assertIn('return fetchJson("/import_file"', JS_SOURCE)

    @unittest.skipUnless(shutil.which("node"), "node is required to execute app.js helpers")
    def test_markdown_links_only_become_anchors_for_http_https_and_mailto(self):
        start = JS_SOURCE.index("function escapeHtml")
        end = JS_SOURCE.index("function renderAttachment")
        helpers = JS_SOURCE[start:end]
        script = helpers + "\nconst cases = JSON.parse(process.argv[1]);\n" \
            "console.log(JSON.stringify(cases.map(text => inlineMarkdown(escapeHtml(text)))));"
        cases = [
            "[ok](http://example.com/a?b=1)",
            "[ok](HTTPS://example.com)",
            "[ok](MailTo:me@example.com)",
            "[ok](  https://example.com/trim  )",
            "[x](JaVaScRiPt:alert(1))",
            "[x]( javascript:alert(1))",
            "[x](data:text/html,<script>1</script>)",
            "[x](file:///C:/secret.txt)",
            "[x](//evil.example/a)",
            "[x](docs/page.html)",
            "[x](/state)",
            "[x](https://a.example/\" onmouseover=\"alert(1))",
        ]
        result = subprocess.run(
            [shutil.which("node"), "-e", script, json.dumps(cases)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        rendered = json.loads(result.stdout)

        rel = 'rel="noopener noreferrer"'
        self.assertEqual(rendered[0], f'<a href="http://example.com/a?b=1" {rel}>ok</a>')
        self.assertEqual(rendered[1], f'<a href="HTTPS://example.com" {rel}>ok</a>')
        self.assertEqual(rendered[2], f'<a href="MailTo:me@example.com" {rel}>ok</a>')
        self.assertEqual(rendered[3], f'<a href="https://example.com/trim" {rel}>ok</a>')
        for html in rendered[4:11]:
            self.assertNotIn("<a ", html)
            self.assertNotIn("href", html)
        # Quotes stay escaped, so an attribute cannot be injected through the URL.
        self.assertNotIn('" onmouseover', rendered[11])
        self.assertIn("&quot;", rendered[11])

    def test_frontend_sends_launch_token_and_classifies_http_errors(self):
        self.assertIn("X-STC-Token", JS_SOURCE)
        self.assertIn("runtimeConfig.token", JS_SOURCE)
        self.assertIn("error.httpStatus", JS_SOURCE)
        self.assertIn("Screenshot Chat server error.", JS_SOURCE)

    def test_markdown_renderer_strips_heading_and_fence_markers(self):
        self.assertIn("function flushParagraph", JS_SOURCE)
        self.assertIn("line.match(/^```([A-Za-z0-9_-]*)\\s*$/)", JS_SOURCE)
        self.assertIn("line.match(/^(#{1,6})\\s+(.+)$/)", JS_SOURCE)
        self.assertNotIn("split(/\\n{2,}/)", JS_SOURCE)

    def test_web_menu_supports_file_and_system_prompt_actions(self):
        self.assertIn("class ScreenshotChatStateAdapter", BRIDGE_SOURCE)
        self.assertIn("data-file", JS_SOURCE)
        self.assertIn("data-system-blank", JS_SOURCE)
        self.assertIn("data-system-custom", JS_SOURCE)

    def test_web_menu_hides_paste_image_but_preserves_image_paste_handler(self):
        self.assertNotIn("data-clipboard", JS_SOURCE)
        self.assertNotIn("Paste image", JS_SOURCE)
        self.assertIn('el.input.addEventListener("paste"', JS_SOURCE)
        self.assertIn("function hasImageClipboard", JS_SOURCE)
        self.assertIn('call("import_clipboard_image")', JS_SOURCE)

    def test_file_dialog_uses_tk_root_not_webview_window_parent(self):
        self.assertIn("dialog_parent = self.root if self._is_tk_parent(self.root) else None", CONTROLLER_SOURCE)
        self.assertIn("def _is_tk_parent", CONTROLLER_SOURCE)

    def test_image_preview_delete_history_and_copy_response_ui_exist(self):
        self.assertIn("data-open-image", JS_SOURCE)
        self.assertIn("attachment-open", JS_SOURCE)
        self.assertIn("function openImageViewer", JS_SOURCE)
        self.assertIn("data-delete-session", JS_SOURCE)
        self.assertIn("delete_session", JS_SOURCE)
        self.assertIn("contextmenu", JS_SOURCE)
        self.assertIn("data-copy-message", JS_SOURCE)
        self.assertIn("Copy all response", JS_SOURCE)
        self.assertIn("data-delete-message", JS_SOURCE)
        self.assertIn("delete_message", JS_SOURCE)
        self.assertIn('id="modeToggle"', INDEX_SOURCE)
        self.assertIn("set_mode", JS_SOURCE)
        self.assertIn(".mode-toggle", CSS_SOURCE)
        self.assertIn(".image-viewer", CSS_SOURCE)
        self.assertIn(".context-menu", CSS_SOURCE)

    def test_tts_controls_are_wired_to_final_assistant_messages(self):
        self.assertIn('id="autoReadBtn"', INDEX_SOURCE)
        self.assertIn("data-read-message", JS_SOURCE)
        self.assertIn("data-stop-speech", JS_SOURCE)
        self.assertIn('read_message: ["/read_message"', JS_SOURCE)
        self.assertIn('stop_speech: ["/stop_speech"', JS_SOURCE)
        self.assertIn('set_auto_read: ["/set_auto_read"', JS_SOURCE)
        self.assertIn('msg.role === "assistant" && msg.status === "done"', JS_SOURCE)
        self.assertIn("speech.auto_read", JS_SOURCE)

    def test_pi_timeline_renderer_exists_without_approval_buttons(self):
        self.assertIn("function renderTimeline", JS_SOURCE)
        self.assertIn("msg.events", JS_SOURCE)
        self.assertIn(".timeline", CSS_SOURCE)
        self.assertNotIn("approval_response", JS_SOURCE)

    def test_system_prompt_is_available_from_add_menu_without_topbar_button(self):
        self.assertNotIn('id="promptBtn"', INDEX_SOURCE)
        self.assertNotIn("Prompt: ${systemPromptLabel", JS_SOURCE)
        self.assertIn("renderSystemPromptMenu", JS_SOURCE)
        self.assertIn("data-system-blank", JS_SOURCE)
        self.assertIn("data-system-custom", JS_SOURCE)
        self.assertNotIn(".prompt-pill", CSS_SOURCE)
        self.assertIn(".menu-heading", CSS_SOURCE)

    def test_primary_workspace_has_conversation_rail_and_runtime_header(self):
        self.assertIn('id="conversationRail"', INDEX_SOURCE)
        self.assertIn('id="railSessions"', INDEX_SOURCE)
        self.assertIn('id="historyBtn"', INDEX_SOURCE)
        self.assertIn('id="runtimeStatus"', INDEX_SOURCE)
        self.assertIn('class="conversation-pane"', INDEX_SOURCE)
        self.assertIn(".conversation-rail", CSS_SOURCE)
        self.assertIn(".conversation-pane", CSS_SOURCE)
        self.assertIn("@media (max-width: 619px)", CSS_SOURCE)
        self.assertIn("minmax(420px, 1fr)", CSS_SOURCE)

    def test_composer_has_one_primary_send_or_stop_action_and_keyboard_parity(self):
        self.assertIn('id="actionBtn"', INDEX_SOURCE)
        self.assertNotIn('id="sendBtn"', INDEX_SOURCE)
        self.assertNotIn('id="stopBtn"', INDEX_SOURCE)
        self.assertIn('call("stop_jobs")', JS_SOURCE)
        self.assertIn('call("create_session")', JS_SOURCE)
        self.assertIn('call("hide")', JS_SOURCE)
        self.assertIn("event.ctrlKey", JS_SOURCE)
        self.assertIn('event.key.toLowerCase() === "l"', JS_SOURCE)
        self.assertIn("el.input.focus()", JS_SOURCE)

    def test_runtime_projection_drives_status_and_transport_offline_state(self):
        self.assertIn("runtime_status", JS_SOURCE)
        self.assertIn("function renderRuntime", JS_SOURCE)
        self.assertIn('state: "offline"', JS_SOURCE)
        self.assertIn("el.actionBtn.disabled", JS_SOURCE)
        self.assertNotIn("el.status.textContent = data.status", JS_SOURCE)
        self.assertIn("runtime.detail", JS_SOURCE)

    def test_history_rail_renders_sessions_without_replacing_narrow_history_menu(self):
        self.assertIn("function renderSessionRail", JS_SOURCE)
        self.assertIn("el.railSessions", JS_SOURCE)
        self.assertIn("el.historyBtn", JS_SOURCE)
        self.assertIn("data-delete-session", JS_SOURCE)
        self.assertIn("renderHistoryMenu", JS_SOURCE)


if __name__ == "__main__":
    unittest.main()

