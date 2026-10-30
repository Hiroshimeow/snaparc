import builtins
import pathlib
import runpy
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import unittest
from unittest import mock


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
        self.assertIn("pystray", project["dependencies"])
        self.assertIn("soundfile", project["dependencies"])

        tool_uv = data.get("tool", {}).get("uv", {})
        self.assertEqual(tool_uv.get("package"), False)

    def test_python_310_has_runtime_toml_backport(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("tomli>=1; python_version < '3.11'", data["project"]["dependencies"])

    def test_model_facade_imports_without_stdlib_tomllib(self):
        original_import = builtins.__import__
        backport_imports = []

        def python_310_import(name, *args, **kwargs):
            if name == "tomllib":
                raise ModuleNotFoundError("No module named 'tomllib'")
            if name == "tomli":
                backport_imports.append(name)
                return tomllib
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=python_310_import):
            namespace = runpy.run_path(str(ROOT / "src/models/Gemini.py"))
        self.assertIn("Gemini", namespace)
        self.assertTrue(backport_imports)

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
