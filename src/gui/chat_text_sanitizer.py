from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_AUTHOR_PREFIXES = ("main",)
ERROR_ONLY_TEXTS = {
    "[error: no response received]",
    "[error: no response received from the api]",
    "[error: response has no content]",
}


def _clean_author(value: object) -> str:
    return str(value or "").strip()


def _author_pattern(authors: Iterable[object]) -> re.Pattern[str] | None:
    names = []
    seen = set()
    for author in authors:
        name = _clean_author(author)
        lower = name.lower()
        if name and lower not in seen:
            names.append(name)
            seen.add(lower)
    if not names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"^(?:\s*(?:{alternatives}):\s*)+", re.IGNORECASE)


def strip_repeated_author_prefixes(
    text: object,
    authors: Iterable[object] = DEFAULT_AUTHOR_PREFIXES,
    extra_authors: Iterable[object] = (),
) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    pattern = _author_pattern((*authors, *extra_authors))
    if pattern is None:
        return value
    return pattern.sub("", value).lstrip()


def sanitize_assistant_text(text: object, author: object = "") -> str:
    author_name = _clean_author(author)
    return strip_repeated_author_prefixes(text, extra_authors=(author_name,))


def is_error_only_assistant_text(text: object) -> bool:
    clean_text = sanitize_assistant_text(text).strip().lower()
    return clean_text in ERROR_ONLY_TEXTS


def history_text_for_author(text: object, author: object = "") -> str:
    author_name = _clean_author(author)
    clean_text = sanitize_assistant_text(text, author_name)
    if not clean_text or is_error_only_assistant_text(clean_text):
        return ""
    if not author_name or author_name.lower() == "main":
        return clean_text
    return f"{author_name}: {clean_text}"
