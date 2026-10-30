import unittest

from src.gui.screenshot_chat.markdown_render import apply_markdown


class FakeText:
    def __init__(self):
        self.content = ""
        self.tags = {}
        self.config = {}
        self._screenshot_md_ready = False

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text):
        self.content = text

    def get(self, start, end):
        return self.content

    def tag_configure(self, tag, **kwargs):
        self.tags.setdefault(tag, [])

    def tag_add(self, tag, start, end):
        self.tags.setdefault(tag, []).append((start, end))

    def tag_remove(self, tag, start, end):
        self.tags[tag] = []

    def tag_ranges(self, tag):
        return self.tags.get(tag, [])

    def tag_names(self, index):
        return []


class MarkdownRenderTests(unittest.TestCase):
    def setUp(self):
        self.text = FakeText()

    def tag_ranges(self, tag):
        return self.text.tag_ranges(tag)

    def test_applies_basic_markdown_tags(self):
        apply_markdown(
            self.text,
            "# Title\n**bold** and *italic* plus `code`\n> quote\n- item",
            {},
        )

        self.assertTrue(self.tag_ranges("md_h1"))
        self.assertTrue(self.tag_ranges("md_bold"))
        self.assertTrue(self.tag_ranges("md_italic"))
        self.assertTrue(self.tag_ranges("md_inline_code"))
        self.assertTrue(self.tag_ranges("md_quote"))
        self.assertTrue(self.tag_ranges("md_list"))

    def test_applies_code_block_and_pygments_fallback_without_crashing(self):
        apply_markdown(
            self.text,
            "```unknownlang\nprint('hi')\n```",
            {},
        )

        self.assertTrue(self.tag_ranges("md_code_block"))


if __name__ == "__main__":
    unittest.main()
