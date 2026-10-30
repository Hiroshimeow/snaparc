import unittest

from src.gui.screenshot_chat.mentions import parse_mentions


AGENTS = {
    "critic": {
        "enabled": True,
        "model": "provider/critic",
        "system_prompt": "Review risks.",
    },
    "research": {
        "enabled": True,
        "model": "",
        "system_prompt": "Explore options.",
    },
    "disabled": {
        "enabled": False,
        "model": "provider/disabled",
        "system_prompt": "Do not call.",
    },
}


class MentionParsingTests(unittest.TestCase):
    def test_no_mentions_routes_to_main(self):
        result = parse_mentions("hello", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "hello")
        self.assertEqual([item.name for item in result.responders], ["main"])
        self.assertEqual(result.responders[0].model_name, "provider/main")

    def test_main_mention_routes_to_main_and_strips_known_mention(self):
        result = parse_mentions("@main explain this", AGENTS, "provider/main", "prompt_fix")

        self.assertEqual(result.prompt_text, "explain this")
        self.assertEqual([item.name for item in result.responders], ["main"])
        self.assertEqual(result.responders[0].prompt_type, "prompt_fix")

    def test_agents_mention_routes_all_enabled_agents_sorted(self):
        result = parse_mentions("@agents compare", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "compare")
        self.assertEqual([item.name for item in result.responders], ["critic", "research"])
        self.assertEqual(result.responders[1].model_name, "provider/main")

    def test_named_agent_and_main_mentions_can_mix(self):
        result = parse_mentions("@main @critic abc", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "abc")
        self.assertEqual([item.name for item in result.responders], ["main", "critic"])
        self.assertEqual(result.responders[1].system_prompt, "Review risks.")

    def test_duplicate_mentions_are_deduped_in_order(self):
        result = parse_mentions("@critic @main @critic abc", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual([item.name for item in result.responders], ["critic", "main"])

    def test_pi_runtime_and_metadata_are_preserved_for_agent(self):
        agents = {
            "pi": {
                "enabled": True,
                "runtime": "pi",
                "model": "",
                "metadata": {"synthetic_pi_agent": True},
            }
        }

        result = parse_mentions("@pi inspect", agents, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "inspect")
        self.assertEqual(result.responders[0].name, "pi")
        self.assertEqual(result.responders[0].runtime, "pi")
        self.assertTrue(result.responders[0].metadata["synthetic_pi_agent"])

    def test_unknown_mentions_remain_plain_text_and_warn(self):
        result = parse_mentions("@ghost @critic abc", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "@ghost abc")
        self.assertEqual(result.unknown_mentions, ["ghost"])
        self.assertEqual([item.name for item in result.responders], ["critic"])

    def test_only_unknown_mentions_falls_back_to_main(self):
        result = parse_mentions("@ghost abc", AGENTS, "provider/main", "prompt_translate")

        self.assertEqual(result.prompt_text, "@ghost abc")
        self.assertEqual(result.unknown_mentions, ["ghost"])
        self.assertEqual([item.name for item in result.responders], ["main"])


if __name__ == "__main__":
    unittest.main()
