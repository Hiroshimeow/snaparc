"""Network endpoint classification for on-device AI providers."""
from urllib.parse import urlsplit


def is_loopback_provider(base_url: str) -> bool:
    try:
        parsed = urlsplit(str(base_url or ""))
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and not parsed.username and not parsed.password
    )
