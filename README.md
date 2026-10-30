# SnapArc v0.0.1 ? by HCU Lab

Free Windows desktop productivity toolkit with screenshot workflows, translation, audio processing, quick actions, and configurable plugins. The chatbot is an optional workflow, not the product identity.

> **Status:** free, public-source candidate (0.0.1 pre-alpha); NOT approved for release. Tests and security checks are required before distribution. Historical version labels in code are migration artifacts, not release evidence.
> **Brand:** SnapArc by HCU Lab ? https://hcu-lab.me (proposed product page: https://snaparc.hcu-lab.me).

## Overview

The application combines a system-tray interface, screenshot capture, a floating prompt window, a chat window, audio services, and a radial plugin menu. The codebase uses Python, Tk/customtkinter, WebView, and OpenAI-compatible provider APIs.

### Main workflows

- Capture a screen region and use it as context for a question or translation.
- Collect screenshots with `Shift + Snap`, or open Quick Prompt with `Ctrl + Snap` / `Alt + Snap`.
- Open the radial plugin menu with `Ctrl + Alt + Hold`.
- Open ScreenshotChat with `Ctrl + Shift + Alt + X`.
- Use configured audio providers for recording, speech recognition, and read-aloud.

These workflows depend on local configuration, available providers, and Windows desktop capabilities. Native drag-and-drop, clipboard import, and live model completion are distinct paths and require separate verification.

## Requirements

- Windows 10/11 with an interactive desktop session.
- Python 3.12 recommended for desktop use. The manifest permits Python >=3.10, but not every desktop dependency supports every newer Python version; Python 3.14+ is not verified.
- [uv](https://docs.astral.sh/uv/) for dependency management.
- Microsoft Edge WebView2 Runtime for the WebView chat interface.
- Explicit provider/model configuration and credentials for remote AI features.

## Quick start

Run from the project root:

```powershell
uv sync --locked --python 3.12
uv run python main.py

# Open the chat window at startup
uv run python main.py --open-chat
```

Use the commands above as the documented launch path. Local configuration is stored in `config.yaml`; runtime state belongs in `data/`. Neither should be committed. Example configuration files are available under `sample/`; review them before use and supply credentials locally.

## Runtime notes

The optional Screenshot Chat workflow currently uses the Python JSONL bridge in `src/agents/pi_rpc_bridge.py`, with an OpenAI-compatible completion loop and workspace tools. Pi-related names and CLI discovery helpers do not establish an external Node Pi SDK integration.

Provider-dependent features need separate end-to-end checks. A queued action, an existing output file, or a nonempty response alone is not sufficient evidence of successful translation, rendering, or recognition.

## Release status

SnapArc v0.0.1 is free pre-alpha source, not a production release. Before publishing, complete RELEASE-READINESS.md, review SECURITY.md and validate every enabled plugin. Plugins currently execute as trusted local code. The legal redistribution license is undecided pending review. A public GitHub repository is not a Windows installation artifact.

For a local start, copy sample/config.example.yaml into the ignored config.yaml and configure your own credentials. The example endpoint is deliberately nonfunctional.

## Development and testing

Install development dependencies and run the automated suite:

```powershell
uv sync --locked --python 3.12 --group dev
uv run python -m pytest -q tests
```

Test counts and skips depend on the environment. Inspect pytest output rather than relying on a fixed count. Desktop/WebView2 checks and live-provider tests have additional prerequisites; an offline test pass does not establish either.

See [release acceptance gates](docs/release-acceptance.md) before running tests that send real requests. Live tests require explicit approval and bounded request budgets.

Build the Python package with the declared Hatchling backend:

```powershell
uv build
```

A successful package build does not by itself establish a standalone Windows distribution.

## Repository layout

| Path | Purpose |
| --- | --- |
| `main.py` | Desktop application entry point |
| `src/` | Application, UI, provider, and service code |
| `plugins/` | Plugin implementations |
| `tests/` | Automated tests and explicit live acceptance gates |
| `scripts/` | Setup, migration, benchmarking, and diagnostic utilities |
| `sample/` | Example configuration files |
| `docs/` | Maintained project documentation |

## Privacy and contribution guidelines

- Keep credentials, personal configuration, chat history, screenshots, recordings, and runtime state out of commits.
- Install dependencies from manifests and lockfiles; do not vendor virtual environments or `node_modules`.
- Keep agent transcripts, temporary plans, generated build outputs, and source ZIP bundles out of pull requests.
- Use focused changes and report the exact checks performed. Distinguish unit-test results from real Windows desktop and live-provider verification.
- Do not include raw provider responses, private endpoints, or user data in issue reports or test logs.

## Free-to-use direction and privacy

SnapArc is planned as a free public application. The legal license and redistribution terms must be selected and audited before any public source release. No paid features or account are required by this pre-alpha source. Remote AI capabilities are optional and require explicitly configured credentials; sending content to an AI provider is subject to that provider's policies. Do not put secrets, screenshots, recordings, history, or runtime configuration into the repository. Review [release gates](docs/release-acceptance.md) and [security policy](SECURITY.md).
