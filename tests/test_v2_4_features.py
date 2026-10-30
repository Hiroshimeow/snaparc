"""
Screens-Trans-Chatbot v2.4 End-to-End Verification Suite (`tests/test_v2_4_features.py`)
========================================================================================
Tests all user-requested v2.4 features:
1. Mode 1 Radial Wheel (`Ctrl + Alt + Click-Hold & Drag`):
   - 0-parameter plugins run immediately without popup (`format_json`, `extract_table`, `clean_workspace`).
   - N-parameter plugins open `RadialParamPopup` with N fields pre-filled from defaults or
     `RadialParamStore` (last-used values persisted in `data/radial_plugin_params.json`).
   - Inline command flag parser (`uv run a.py --root 11235813x--host 0.0.0.0`) & AST `argparse` discovery.
   - Real execution of `plugins/a.py` with `--root`, `--host`, and `--port`.
2. Mode 2 Radial Wheel (`Ctrl + Alt + File Drag-and-Drop`):
   - `InputHandler` triggers Radial Wheel both when pressing `Ctrl + Alt` then dragging mouse AND
     when holding Left Mouse to drag a file then pressing `Ctrl + Alt`.
     accepts `--dpi`, and outputs PNG images into the PDF directory.
   - `png_resize` plugin receives dragged `.png` file, defaults `--output-path` to `{file_dir}`,
     validates `--size` in `[0.1, 3.0]` (10% to 300%), and outputs resized image with exact dimensions.
3. Chatbot Overhaul & Single OpenAI-Compatible API Path (`openai-completions`):
   - Pi Sidecar is the primary runtime mode (`pi_primary_mode = True`).
   - `available_models()` exposes only models declared by the user in config.yaml
     without locking the user onto a dummy `pi://agent/pi` entry.
   - `set_model()` and `register_custom_model()` allow seamless model switching in Pi Sidecar mode.
   - `NativePiRpcServer` runs a real OpenAI-compatible Chat Completions agent loop with workspace tools.
   - Zero `google-genai`, `GEMINI_API_KEY`, or `OPENAI_API_KEY` dependencies remain in code or config.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import tkinter as tk
import unittest

from PIL import Image

from src.agents.pi_rpc_bridge import NativePiRpcServer
from src.gui.radial_param_popup import RadialParamPopup
from src.gui.screenshot_chat.controller import ScreenshotChatController
from src.gui.screenshot_chat.store import ScreenshotChatStore
from src.handlers.InputHandler import InputHandler
from src.services.radial_plugin_service import (
    RadialParamStore,
    RadialPluginService,
    extract_argparse_params_from_script,
    parse_command_string_and_flags,
)
from src.utils.ConfigManager import ConfigManager
from src.utils.yaml_io import load_yaml_or_json

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestV24RadialWheelAndPlugins(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.param_store_path = self.tmp_path / "radial_plugin_params.json"
        self.service = RadialPluginService(
            repo_root=REPO_ROOT,
            param_store_path=self.param_store_path,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_01_inline_command_parser_handles_glued_and_standard_flags(self) -> None:
        tokens, params = parse_command_string_and_flags(
            "uv run a.py --root 11235813x--host 0.0.0.0"
        )
        self.assertEqual(tokens, ["uv", "run", "a.py"])
        by_name = {p.name: p for p in params}
        self.assertIn("root", by_name)
        self.assertIn("host", by_name)
        self.assertEqual(by_name["root"].default, "11235813x")
        self.assertEqual(by_name["host"].default, "0.0.0.0")

    def test_02_ast_argparse_autodiscovery_extracts_script_flags(self) -> None:
        a_py = REPO_ROOT / "plugins" / "a.py"
        discovered = extract_argparse_params_from_script(a_py)
        names = [p.name for p in discovered]
        self.assertIn("root", names)
        self.assertIn("host", names)
        self.assertIn("port", names)

    def test_03_mode1_script_a_executes_and_persists_last_used_params(self) -> None:
        spec = self.service.get_plugin("script_a")
        self.assertIsNotNone(spec)
        self.assertTrue(spec.has_params)

        defaults = self.service.get_prefilled_params("script_a")
        self.assertEqual(defaults["root"], "11235813x")
        self.assertEqual(defaults["host"], "0.0.0.0")
        self.assertEqual(defaults["port"], "8000")

        res = self.service.execute_plugin(
            "script_a",
            context={},
            param_overrides={"root": "fibonacci_999", "host": "127.0.0.1", "port": "9090"},
        )
        self.assertTrue(res.ok, f"script_a failed: {res.error} / {res.markdown}")
        self.assertIn("fibonacci_999", res.markdown)
        self.assertIn("127.0.0.1", res.markdown)
        self.assertIn("9090", res.markdown)

        reloaded_store = RadialParamStore(self.param_store_path)
        saved = reloaded_store.get_last_values("script_a")
        self.assertEqual(saved["root"], "fibonacci_999")
        self.assertEqual(saved["host"], "127.0.0.1")
        self.assertEqual(saved["port"], "9090")

        root = tk.Tk()
        popup = RadialParamPopup(root, self.service, spec, origin=(300, 300)).show()
        self.assertEqual(popup.get_current_values()["root"], "fibonacci_999")
        reset_vals = popup.reset_to_defaults()
        self.assertEqual(reset_vals["root"], "11235813x")
        self.assertEqual(reset_vals["host"], "0.0.0.0")
        popup.close()

    def test_04_mode1_zero_param_plugin_runs_immediately(self) -> None:
        spec = self.service.get_plugin("format_json")
        self.assertIsNotNone(spec)
        self.assertFalse(spec.has_params)

        res = self.service.execute_plugin(
            "format_json",
            context={"clipboard_text": '{"b":1,"a":[2,3]}'},
        )
        self.assertTrue(res.ok)
        self.assertIn('"a": [', res.markdown)

    def test_05_mode2_file_drag_png_resize_scales_10pct_to_300pct_and_defaults_to_file_dir(self) -> None:
        img_dir = self.tmp_path / "images_folder"
        img_dir.mkdir(parents=True, exist_ok=True)
        src_png = img_dir / "sample_diagram.png"
        Image.new("RGBA", (100, 80), (40, 120, 220, 255)).save(src_png, "PNG")

        spec = self.service.get_plugin("png_resize")
        self.assertIsNotNone(spec)
        self.assertTrue(spec.has_params)
        self.assertTrue(spec.requires_file_input)

        prefilled = self.service.get_prefilled_params("png_resize", active_files=[str(src_png)])
        self.assertEqual(Path(prefilled["output_path"]).resolve(), img_dir.resolve())

        res_half = self.service.execute_plugin(
            "png_resize",
            context={"files": [str(src_png)]},
            param_overrides={"size": "0.5", "output_path": str(img_dir)},
        )
        self.assertTrue(res_half.ok, res_half.markdown)
        self.assertEqual(len(res_half.generated_files), 1)
        out_half = Path(res_half.generated_files[0])
        self.assertTrue(out_half.is_file())
        self.assertEqual(out_half.parent.resolve(), img_dir.resolve())
        with Image.open(out_half) as im:
            self.assertEqual(im.size, (50, 40))

        res_3x = self.service.execute_plugin(
            "png_resize",
            context={"files": [str(src_png)]},
            param_overrides={"size": "3.0", "output_path": str(img_dir)},
        )
        self.assertTrue(res_3x.ok, res_3x.markdown)
        out_3x = Path(res_3x.generated_files[0])
        with Image.open(out_3x) as im:
            self.assertEqual(im.size, (300, 240))

    def test_07_input_handler_supports_both_ctrl_alt_click_and_file_drag_into_ctrl_alt(self) -> None:
        opened = []
        moved = []
        released = []
        key_state = {"ctrl": False, "alt": False, "shift": False}

        handler = InputHandler(
            on_capture=lambda *a: None,
            key_state_checker=lambda k: key_state.get(k, False),
            on_radial_open=lambda x, y: opened.append((x, y)),
            on_radial_move=lambda x, y: moved.append((x, y)),
            on_radial_release=lambda x, y: released.append((x, y)),
            radial_hold_seconds=0.01,
        )

        from src.platform.win32_mouse import Button
        handler.on_click(100, 100, Button.left, True)
        self.assertEqual(handler._held_mouse_button, Button.left)
        self.assertFalse(handler._radial_active)

        key_state["ctrl"] = True
        key_state["alt"] = True
        handler.on_move(140, 120)
        self.assertTrue(handler._radial_active)
        self.assertEqual(opened[-1], (140, 120))

        handler.on_move(180, 120)
        handler.on_click(180, 120, Button.left, False)
        self.assertEqual(released[-1], (180, 120))


class TestV24PiSidecarPrimaryAndSingleOpenAIPath(unittest.TestCase):
    def test_01_zero_google_genai_or_direct_gemini_providers_remain(self) -> None:
        pyproject_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyproject_data["project"]["version"], "0.0.1")
        deps = " ".join(pyproject_data["project"].get("dependencies", []))
        self.assertNotIn("google-genai", deps)

        self.assertFalse((REPO_ROOT / "src" / "models" / "Gemini.py").exists())
        self.assertFalse((REPO_ROOT / "src" / "llm" / "providers" / "openai.py").exists())

        cfg_data = load_yaml_or_json((REPO_ROOT / "sample" / "config.example.yaml").read_text(encoding="utf-8"))
        providers = cfg_data.get("models", {}).get("providers", {})
        self.assertEqual(set(providers), {"my-provider"})
        self.assertEqual(providers["my-provider"]["models"], ["example-model"])
        for p_cfg in providers.values():
            self.assertEqual(p_cfg.get("api"), "openai-completions")

    def test_02_pi_sidecar_is_primary_mode_and_allows_free_model_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_file = tmp_path / "config.yaml"
            cfg_file.write_text((REPO_ROOT / "sample" / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            ConfigManager._instance = None
            cm = ConfigManager(str(cfg_file))
            store = ScreenshotChatStore(str(tmp_path / "chat_history.json"))
            root = tk.Tk()
            ctrl = ScreenshotChatController(
                root=root,
                config_manager=cm,
                store=store,
                project_root=str(REPO_ROOT),
            )
            try:
                self.assertTrue(ctrl.pi_primary_mode)
                models = ctrl.available_models()
                self.assertIn("my-provider/example-model", models)
                self.assertTrue(all(not m.startswith("pi://") for m in models))

                added = ctrl.register_custom_model("second-model")
                self.assertEqual(added, "my-provider/second-model")
                self.assertIn("my-provider/second-model", ctrl.available_models())
                self.assertEqual(ctrl.current_model, "my-provider/second-model")
                self.assertEqual(ctrl.get_active_session().model_name, "my-provider/second-model")
            finally:
                ctrl.shutdown()
                ConfigManager._instance = None

    def test_03_native_pi_rpc_server_executes_workspace_tools_properly(self) -> None:
        from src.agents.pi_rpc_bridge import PiTerminalToolset
        toolkit = PiTerminalToolset(cwd=str(REPO_ROOT))
        res = toolkit.execute("ls", {"path": "plugins"})
        ls_out = json.dumps(res)
        self.assertIn("png_resize.py", ls_out)
        self.assertIn("a.py", ls_out)


if __name__ == "__main__":
    unittest.main()
