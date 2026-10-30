import json
import os
import threading


class PromptHistoryStore:
    """Small JSON-backed MRU store for custom snap prompts."""

    def __init__(self, save_path, limit=20):
        self.save_path = save_path
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()

    def list_prompts(self):
        with self._lock:
            return list(self._load_unlocked())

    def add_prompt(self, prompt_text):
        clean_text = self._normalize_prompt(prompt_text)
        if not clean_text:
            return self.list_prompts()

        with self._lock:
            prompts = [
                item
                for item in self._load_unlocked()
                if item.strip() != clean_text
            ]
            prompts.insert(0, clean_text)
            prompts = prompts[: self.limit]
            self._save_unlocked(prompts)
            return list(prompts)

    def _load_unlocked(self):
        try:
            if not os.path.exists(self.save_path):
                return []
            with open(self.save_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            print(f"PromptHistoryStore load error: {exc}")
            return []

        if isinstance(payload, dict):
            raw_prompts = payload.get("prompts", [])
        elif isinstance(payload, list):
            raw_prompts = payload
        else:
            raw_prompts = []

        prompts = []
        seen = set()
        for item in raw_prompts:
            clean_item = self._normalize_prompt(item)
            if not clean_item or clean_item in seen:
                continue
            prompts.append(clean_item)
            seen.add(clean_item)
            if len(prompts) >= self.limit:
                break
        return prompts

    def _save_unlocked(self, prompts):
        save_dir = os.path.dirname(self.save_path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
        payload = {"prompts": list(prompts)}
        tmp_path = f"{self.save_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.save_path)

    def _normalize_prompt(self, prompt_text):
        if not isinstance(prompt_text, str):
            return ""
        return prompt_text.strip()
