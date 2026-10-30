import os
import shutil
import tempfile
import unittest
from src.utils.custom_prompt_history import PromptHistoryStore

class TestPromptHistoryStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.history_path = os.path.join(self.temp_dir, 'history_test_2.json')
        self.history_store = PromptHistoryStore(self.history_path, limit=5)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_and_list_prompts(self):
        self.history_store.add_prompt('Prompt 1')
        self.history_store.add_prompt('Prompt 2')
        prompts = self.history_store.list_prompts()
        self.assertIn('Prompt 1', prompts)
        self.assertIn('Prompt 2', prompts)

if __name__ == '__main__':
    unittest.main()