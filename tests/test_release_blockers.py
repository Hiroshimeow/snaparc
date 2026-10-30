"""Release regressions: real PDF pixels and no fabricated chat completions."""
import builtins
import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from plugins import pdf2png
from src.agents.pi_rpc_bridge import NativePiRpcServer


@pytest.fixture
def colored_pdf(tmp_path):
    path = tmp_path / "colors.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=144, height=72)
    content = DecodedStreamObject()
    content.set_data(b"1 0 0 rg 0 0 72 72 re f\n0 0 1 rg 72 0 72 72 re f\n")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize("dpi", [72, 144])
def test_pdf_render_preserves_geometry_and_colors(colored_pdf, dpi):
    result = pdf2png.convert_pdf_to_png(colored_pdf, dpi=dpi)
    assert result["ok"], result
    assert result["engine"] == "pypdfium2"
    with Image.open(result["files"][0]).convert("RGB") as image:
        assert image.size == (dpi * 2, dpi)
        assert image.getpixel((dpi // 2, dpi // 2)) == (255, 0, 0)
        assert image.getpixel((dpi + dpi // 2, dpi // 2)) == (0, 0, 255)


def test_pdf_missing_renderer_is_failure_not_imitation(colored_pdf, monkeypatch):
    original = builtins.__import__

    def no_renderer(name, *args, **kwargs):
        if name == "pypdfium2":
            raise ImportError("renderer unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_renderer)
    result = pdf2png.convert_pdf_to_png(colored_pdf)
    assert not result["ok"]
    assert result["files"] == []
    assert not list(colored_pdf.parent.glob("*.png"))


def test_pdf_output_directory_failure_is_structured(colored_pdf, tmp_path):
    output = tmp_path / "not_a_directory"
    output.write_text("occupied", encoding="utf-8")
    result = pdf2png.convert_pdf_to_png(colored_pdf, output_path=str(output))
    assert not result["ok"]
    assert result["error"]
    assert result["files"] == []


@pytest.mark.parametrize("include_good", [False, True])
def test_pdf_batch_failure_is_not_success(colored_pdf, tmp_path, monkeypatch, capsys, include_good):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"not a PDF")
    files = [str(bad)] + ([str(colored_pdf)] if include_good else [])
    monkeypatch.setattr("sys.argv", ["pdf2png", *files])
    assert pdf2png.main() == 0  # Structured JSON failures are the plugin protocol.
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"]
    assert len(result["generated_files"]) == int(include_good)


@pytest.mark.parametrize("outcome", ["missing", "disabled", "placeholder", "exception", "empty", "whitespace", "canceled", "success"])
def test_bridge_never_fabricates_completion(tmp_path, monkeypatch, outcome):
    # Isolate session-directory creation as well as provider access.
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    server = NativePiRpcServer()
    server.project_root = tmp_path
    frames = []
    server._emit = frames.append
    endpoint = None if outcome == "missing" else {"is_placeholder_key": outcome == "placeholder"}
    monkeypatch.delenv("STC_FORCE_LIVE_PI_OPENAI", raising=False)
    if outcome == "disabled":
        monkeypatch.setenv("STC_DISABLE_OPENAI_PI_AGENT", "1")
    else:
        monkeypatch.delenv("STC_DISABLE_OPENAI_PI_AGENT", raising=False)
    server._resolve_openai_endpoint_for_model = lambda model: endpoint

    def complete(**kwargs):
        if outcome == "exception":
            raise RuntimeError("provider failed")
        if outcome == "canceled":
            server.canceled_jobs.add("job")
        return ("real completion" if outcome == "success" else "   " if outcome == "whitespace" else ""), False

    server._run_via_openai_completions_agent = complete
    server._handle_run("request", "job", "session", {"prompt": "synthetic task", "cwd": str(tmp_path)})
    responses = [frame for frame in frames if frame["type"] == "response"]
    assert len(responses) == 1
    response = responses[0]
    assert response["requestId"] == "request"
    assert response["jobId"] == "job"
    if outcome == "success":
        assert response["ok"]
        assert response["data"]["text"] == "real completion"
    else:
        assert not response["ok"], response
        assert response["error"]["code"]
        assert not any(frame.get("event", {}).get("kind") == "message_delta" for frame in frames)
        assert not server.sessions
