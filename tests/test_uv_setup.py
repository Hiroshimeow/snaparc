
import pathlib
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class UvSetupTests(unittest.TestCase):
    def test_pyproject_defines_runtime_dependencies_for_uv_sync(self):
        pyproject_path = ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.exists(), "pyproject.toml is missing")

        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)

        project = data.get("project", {})
        self.assertEqual(project.get("name"), "snaparc")
        self.assertIn("dependencies", project)
        self.assertIn("httpx>=0.28,<1", project["dependencies"])
        self.assertIn("soundfile", project["dependencies"])
        self.assertNotIn("openai", project["dependencies"])
        self.assertNotIn("pynput", project["dependencies"])
        self.assertNotIn("pystray", project["dependencies"])
        self.assertNotIn("pypdfium2", project["dependencies"])
        self.assertNotIn("pdf", project.get("optional-dependencies", {}))

        tool_uv = data.get("tool", {}).get("uv", {})
        self.assertEqual(tool_uv.get("package"), False)

    def test_python_310_has_runtime_toml_backport(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("tomli>=1; python_version < '3.11'", data["project"]["dependencies"])

    def test_model_client_imports_without_vendor_sdk(self):
        from src.llm.client import ModelClient
        from src.llm.providers.completions import ChatCompletionsProvider

        self.assertTrue(callable(ModelClient))
        self.assertTrue(callable(ChatCompletionsProvider))

    def test_readme_release_and_supported_test_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertIn(f"v{project['version']}", readme.splitlines()[0])
        self.assertIn("uv run python -m pytest -q tests", readme)
        self.assertNotIn("scripts/run_all_tests.py", readme)

    def test_readme_documents_uv_sync_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync", readme)
        self.assertIn("uv run python main.py", readme)


if __name__ == "__main__":
    unittest.main()