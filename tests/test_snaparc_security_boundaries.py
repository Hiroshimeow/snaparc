import os
import unittest
from unittest import mock

from src.gui.screenshot_chat import webview_app
from src.services.radial_plugin_service import plugin_child_environment


class SnapArcSecurityBoundaryTests(unittest.TestCase):
    def test_plugin_environment_does_not_inherit_ambient_secrets(self):
        with mock.patch.dict(os.environ, {
            "PATH": "safe-path",
            "OPENAI_API_KEY": "private",
            "SNAPARC_CHAT_SESSION_TOKEN": "private",
            "AWS_SECRET_ACCESS_KEY": "private",
            "UV_CACHE_DIR": "safe-cache",
        }):
            env = plugin_child_environment()
        self.assertEqual(env["PATH"], "safe-path")
        self.assertEqual(env["UV_CACHE_DIR"], "safe-cache")
        for key in ("OPENAI_API_KEY", "SNAPARC_CHAT_SESSION_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(key, env)

    def test_unbundled_plugin_discovery_requires_explicit_opt_in(self):
        import tempfile
        import json
        from pathlib import Path
        from src.services.radial_plugin_service import RadialPluginService
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugins").mkdir()
            (root / "plugins" / "user_tool.py").write_text(
                "PLUGIN_META_JSON = '{}'\\n", encoding="utf-8"
            )
            config = root / "config.yaml"
            config.write_text("{}", encoding="utf-8")
            service = RadialPluginService(repo_root=root)
            self.assertIsNone(service.get_plugin("user_tool"))
            config.write_text(json.dumps(
                {"radial_plugins": {"allow_user_plugins": True}}
            ), encoding="utf-8")
            service.reload(force=True)
            self.assertIsNotNone(service.get_plugin("user_tool"))

    def test_cloud_provider_detection_exempts_real_loopback_only(self):
        from src.core.network_policy import is_loopback_provider
        for url in (
            "http://127.0.0.1:8080/v1", "http://localhost:11434/v1",
            "http://[::1]:9000/v1",
        ):
            self.assertTrue(is_loopback_provider(url), url)
        for url in (
            "https://provider.example/v1", "http://127.0.0.1.evil.example/v1",
            "http://u:p@localhost:9000/v1", "file:///localhost",
            "http://192.168.1.10:9000/v1",
        ):
            self.assertFalse(is_loopback_provider(url), url)

    def test_nonlocal_config_requires_exact_boolean_true(self):
        from src.llm.service import LLMService
        class Config:
            def __init__(self, value):
                self.value = value
            def get(self, key, default=None):
                if key == "privacy.allow_cloud_upload":
                    return self.value
                return default
            def get_provider_config(self, provider_name):
                return {"api":"openai-completions","baseUrl":"https://provider.example/v1",
                        "apiKey":"synthetic","models":["test"]}
            def load_config(self, force=False):
                return None
        for value in (None, "true", 1, "yes", False):
            service = LLMService(config_manager=Config(value))
            self.assertIsNone(service._get_provider("cloud", "test"), value)
        with mock.patch("src.llm.service.OpenAICompatibleProvider") as constructor:
            service = LLMService(config_manager=Config(True))
            self.assertIsNotNone(service._get_provider("cloud", "test"))
            constructor.assert_called_once()

    def test_pi_rpc_remote_endpoint_requires_explicit_cloud_opt_in(self):
        import tempfile
        import json
        from pathlib import Path
        from src.agents.pi_rpc_bridge import NativePiRpcServer
        from src.utils.ConfigManager import ConfigManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_file = root / "config.yaml"
            data = {
                "models": {"providers": {
                    "p": {"api":"openai-completions", "baseUrl":"https://remote.example/v1",
                          "apiKey":"synthetic-key", "models":["m"]}
                }},
                "agents": {"defaults": {"model": {"primary": "p/m"}}},
                "privacy": {"allow_cloud_upload": False},
            }
            server = NativePiRpcServer.__new__(NativePiRpcServer)
            server.project_root = root
            try:
                ConfigManager._instance = None
                config_file.write_text(json.dumps(data), encoding="utf-8")
                self.assertIsNone(server._resolve_openai_endpoint_for_model("p/m", config_path=config_file))
                data["privacy"]["allow_cloud_upload"] = True
                config_file.write_text(json.dumps(data), encoding="utf-8")
                ConfigManager._instance = None
                result = server._resolve_openai_endpoint_for_model("p/m", config_path=config_file)
                self.assertIsNotNone(result)
                self.assertEqual(result["base_url"], "https://remote.example/v1")
            finally:
                ConfigManager._instance = None

    def test_pi_rpc_rejects_non_openai_completions_api_mode(self):
        import tempfile
        import json
        from pathlib import Path
        from src.agents.pi_rpc_bridge import NativePiRpcServer
        from src.utils.ConfigManager import ConfigManager
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_file = root / "config.yaml"
            data = {
                "models": {"providers": {
                    "p": {"api":"google-gemini", "baseUrl":"https://remote.example/v1",
                          "apiKey":"synthetic-key", "models":["m"]}
                }},
                "agents": {"defaults": {"model": {"primary": "p/m"}}},
                "privacy": {"allow_cloud_upload": True},
            }
            server = NativePiRpcServer.__new__(NativePiRpcServer)
            server.project_root = root
            try:
                ConfigManager._instance = None
                config_file.write_text(json.dumps(data), encoding="utf-8")
                self.assertIsNone(
                    server._resolve_openai_endpoint_for_model("p/m", config_path=config_file)
                )
            finally:
                ConfigManager._instance = None

    def test_cloud_upload_default_denied_but_explicit_consent_works(self):
        from src.core.privacy_gate import PrivacyConsentGate, ConsentScope
        gate = PrivacyConsentGate()
        self.assertFalse(gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider="example"))
        gate.set_scope_consent(ConsentScope.CLOUD_UPLOAD, True)
        self.assertTrue(gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider="example"))
        gate.set_offline_mode(True)
        self.assertFalse(gate.check_permission(ConsentScope.CLOUD_UPLOAD, provider="example"))

    def test_llm_service_checks_consent_before_getting_provider(self):
        from src.llm.service import LLMService
        class DummyConfig:
            def get(self, key, default=None):
                if key == "privacy.allow_cloud_upload":
                    return False
                return default
            def get_provider_config(self, provider_name):
                return {}
            def load_config(self, force=False):
                return None
        from src.core.privacy_gate import PrivacyConsentGate
        gate = PrivacyConsentGate()
        with mock.patch("src.llm.service.OpenAICompatibleProvider") as provider:
            service = LLMService(config_manager=DummyConfig(), consent_gate=gate)
            self.assertIsNone(service._get_provider("example"))
            provider.assert_not_called()

    def test_webview_token_is_not_cli_argument(self):
        with mock.patch("sys.argv", ["webview_app", "--server-url", "http://127.0.0.1:2222", "--parent-pid", "12", "--token", "private"]):
            with self.assertRaises(SystemExit):
                webview_app._parse_args()

    def test_poll_rejects_nonlocal_url_before_network(self):
        with mock.patch("urllib.request.build_opener") as open_mock:
            for url in ("https://127.0.0.1:2222/ui", "http://evil.example/ui", "http://user:pass@127.0.0.1:2222/ui"):
                with self.assertRaises(ValueError):
                    webview_app._fetch_json(url, "private")
            open_mock.assert_not_called()

    def test_poller_disables_http_proxy(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return b'{"ok":true}'
        class FakeOpener:
            def open(self, request, timeout=0):
                self.url = request.full_url
                self.timeout = timeout
                return FakeResponse()
        opener = FakeOpener()
        with mock.patch("urllib.request.build_opener", return_value=opener) as factory:
            self.assertEqual(webview_app._fetch_json("http://127.0.0.1:2222/ui", "private"), {"ok": True})
            proxy_handler = factory.call_args.args[0]
            self.assertEqual(proxy_handler.proxies, {})
            self.assertEqual(opener.url, "http://127.0.0.1:2222/ui")


if __name__ == "__main__":
    unittest.main()
