from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from src.agents.pi_contract import PiRunRequest, PiRuntimeConfig
from src.agents.pi_sdk_runtime import (
    DEFAULT_MAX_IMAGE_BYTES,
    PiSdkAttachmentError,
    PiSdkRuntime,
    serialize_sdk_attachments,
)


class RecordingTransport:
    def __init__(self, *, max_image_bytes=DEFAULT_MAX_IMAGE_BYTES, max_run_frame_bytes=28 * 1024 * 1024):
        self.max_image_bytes = max_image_bytes
        self.max_run_frame_bytes = max_run_frame_bytes
        self.requests = []

    def request(self, command, payload, **kwargs):
        self.requests.append((command, payload, kwargs))
        return {"text": "ok", "sideEffectStarted": False}

    def notify(self, *args, **kwargs):
        return "n1"


def make_request(attachments):
    return PiRunRequest(
        job_id="job-1",
        session_id="session-1",
        message_id="message-1",
        responder_name="pi",
        prompt_text="inspect",
        history=[],
        attachments=attachments,
    )


@pytest.mark.parametrize(
    ("suffix", "mime"),
    [(".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".gif", "image/gif"), (".webp", "image/webp")],
)
def test_supported_images_map_mime_and_encode_exact_bytes(tmp_path, suffix, mime):
    data = b"image-bytes-\x00\xff"
    path = tmp_path / f"sample{suffix}"
    path.write_bytes(data)

    encoded = serialize_sdk_attachments([{"path": str(path), "label": "screen"}])

    assert len(encoded) == 1
    item = encoded[0]
    assert item == {
        "displayName": "screen",
        "mimeType": mime,
        "byteLength": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode("ascii"),
    }
    assert str(path) not in json.dumps(item)


@pytest.mark.parametrize("suffix", [".txt", ".md", ".csv", ".json", ".log", ".pdf"])
def test_text_and_pdf_attachments_are_not_encoded_as_sdk_images(tmp_path, suffix):
    path = tmp_path / f"context{suffix}"
    path.write_text("bounded context", encoding="utf-8")
    assert serialize_sdk_attachments([{"path": str(path), "kind": "file"}]) == []


def test_missing_directory_unreadable_and_unsupported_fail_without_path_leak(tmp_path, monkeypatch):
    cases = []
    cases.append(({"path": str(tmp_path / "missing.png"), "label": "missing"}, "missing"))
    directory = tmp_path / "folder.png"
    directory.mkdir()
    cases.append(({"path": str(directory), "label": "folder"}, "regular file"))
    unsupported = tmp_path / "secret.bmp"
    unsupported.write_bytes(b"bitmap")
    cases.append(({"path": str(unsupported), "label": "bitmap"}, "unsupported"))

    for attachment, expected in cases:
        with pytest.raises(PiSdkAttachmentError) as caught:
            serialize_sdk_attachments([attachment])
        message = str(caught.value)
        assert expected in message.lower()
        assert attachment["path"] not in message
        assert attachment["label"] in message

    unreadable = tmp_path / "unreadable.png"
    unreadable.write_bytes(b"data")
    original_open = open

    def failing_open(file, *args, **kwargs):
        if os.fspath(file) == str(unreadable):
            raise PermissionError("private path detail")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    with pytest.raises(PiSdkAttachmentError) as caught:
        serialize_sdk_attachments([{"path": str(unreadable), "label": "locked"}])
    assert "unreadable" in str(caught.value).lower()
    assert str(unreadable) not in str(caught.value)


def test_oversized_file_is_rejected_before_content_read(tmp_path, monkeypatch):
    path = tmp_path / "large.png"
    path.write_bytes(b"123456")
    opened = False

    def should_not_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("content should not be read")

    monkeypatch.setattr("builtins.open", should_not_open)
    with pytest.raises(PiSdkAttachmentError, match="too large"):
        serialize_sdk_attachments([{"path": str(path), "label": "large"}], max_image_bytes=5)
    assert opened is False


def test_file_changing_during_read_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "changing.png"
    path.write_bytes(b"abc")
    real_open = open

    class ChangingFile:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def read(self, size):
            data = self.handle.read(size)
            with real_open(path, "ab") as writer:
                writer.write(b"x")
            return data
        def fileno(self):
            return self.handle.fileno()

    def changing_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if os.fspath(file) == str(path) and mode == "rb":
            return ChangingFile(handle)
        return handle

    monkeypatch.setattr("builtins.open", changing_open)
    with pytest.raises(PiSdkAttachmentError, match="changed while reading") as caught:
        serialize_sdk_attachments([{"path": str(path), "label": "changing"}])
    assert str(path) not in str(caught.value)


def test_attachment_failure_occurs_before_transport_run(tmp_path):
    transport = RecordingTransport()
    runtime = PiSdkRuntime(PiRuntimeConfig(fallback_to_llm=True), lambda *_args, **_kwargs: None, transport)
    result = runtime.run(
        make_request([{"path": str(tmp_path / "missing.png"), "label": "missing"}]),
        threading.Event(),
    )
    assert transport.requests == []
    assert result.fallback_to_llm is True
    assert "missing" in result.error.lower()
    assert str(tmp_path) not in result.error


def test_aggregate_run_payload_limit_is_checked_before_transport_run(tmp_path):
    path = tmp_path / "screen.png"
    path.write_bytes(b"x" * 120)
    transport = RecordingTransport(max_run_frame_bytes=200)
    runtime = PiSdkRuntime(PiRuntimeConfig(fallback_to_llm=True), lambda *_args, **_kwargs: None, transport)

    result = runtime.run(make_request([{"path": str(path), "label": "screen"}]), threading.Event())

    assert transport.requests == []
    assert result.fallback_to_llm is True
    assert "frame" in result.error.lower()
    assert str(path) not in result.error


def test_runtime_sends_only_sanitized_encoded_image_metadata(tmp_path):
    path = tmp_path / "screen.png"
    path.write_bytes(b"png-data")
    transport = RecordingTransport()
    runtime = PiSdkRuntime(PiRuntimeConfig(), lambda *_args, **_kwargs: None, transport)

    result = runtime.run(
        make_request([
            {"path": str(path), "label": "screen"},
            {"path": str(tmp_path / "notes.txt"), "label": "notes", "kind": "file"},
        ]),
        threading.Event(),
    )

    assert result.text == "ok"
    sent = transport.requests[0][1]["attachments"]
    assert len(sent) == 1
    assert sent[0]["displayName"] == "screen"
    assert "path" not in sent[0]
