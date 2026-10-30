import base64
import mimetypes
import os
from dataclasses import dataclass


IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def guess_mime_type(path: str, default: str = "application/octet-stream") -> str:
    suffix = os.path.splitext(str(path))[1].lower()
    if suffix in IMAGE_MIME_TYPES:
        return IMAGE_MIME_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or default


@dataclass(frozen=True)
class LLMAttachment:
    path: str
    kind: str = "image"
    mime_type: str = ""

    @property
    def resolved_mime_type(self) -> str:
        if self.mime_type:
            return self.mime_type
        default = "image/jpeg" if self.kind == "image" else "application/octet-stream"
        return guess_mime_type(self.path, default=default)

    def read_base64(self) -> str:
        with open(self.path, "rb") as handle:
            return base64.b64encode(handle.read()).decode("utf-8")

    def data_url(self) -> str:
        return f"data:{self.resolved_mime_type};base64,{self.read_base64()}"

    def exists(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)
