import os
import shutil
import tempfile
import unittest

from src.gui.screenshot_chat.mermaid import extract_mermaid_blocks, write_mermaid_preview


class MermaidSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extracts_mermaid_fenced_blocks(self):
        blocks = extract_mermaid_blocks("before\n```mermaid\ngraph TD\nA-->B\n```\nafter")

        self.assertEqual(blocks, ["graph TD\nA-->B"])

    def test_preview_html_is_written_under_history_mermaid_preview(self):
        path = write_mermaid_preview(self.temp_dir, "graph TD\nA[<bad>]-->B")

        self.assertIn(os.path.join("data", "mermaid_preview"), path)
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("&lt;bad&gt;", html)
        self.assertIn("mermaid", html)


if __name__ == "__main__":
    unittest.main()
