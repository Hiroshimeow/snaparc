# SnapArc v0.0.2 - by HCU Lab

SnapArc is a free Windows desktop productivity toolkit for screenshot workflows,
translation, audio, quick actions, and configurable local plugins. Chat is an
optional workflow, not the product identity.

> **Status:** source-only public-alpha candidate. It is not yet approved for
> public release and is not a production-ready Windows installer.
>
> **Brand:** SnapArc by HCU Lab - https://hcu-lab.me. The proposed product page
> is https://snaparc.hcu-lab.me.

## Main workflows

- Capture a screen region and use it as context for a question or translation.
- Collect captures with `Shift + Snap`; open Quick Prompt with `Ctrl + Snap`
  or `Alt + Snap`.
- Open the radial plugin menu with `Ctrl + Alt + Hold`.
- Open Screenshot Chat with `Ctrl + Shift + Alt + X`.
- Record audio and use configured transcription/read-aloud providers.
- Run bundled local tools for OCR, document conversion and text/file utilities.

## Requirements

- Windows 10/11 with an interactive desktop session.
- Official Windows CPython 3.12 installed with Tcl/Tk is the validated
  development runtime. The manifest permits Python >=3.10.
- [uv](https://docs.astral.sh/uv/) for dependency management.
- Microsoft Edge WebView2 Runtime for Screenshot Chat.
- Provider credentials only for remote AI features you explicitly enable.

## Quick start

From the repository root:

```powershell
uv sync --locked --python (py -3.12 -c 'import sys; print(sys.executable)')
Copy-Item sample/config.example.yaml config.yaml
uv run python main.py
```

`config.yaml` and runtime state under `data/` are local and Git-ignored.

For normal Windows launches, `run.bat` delegates to the supported `main.bat` / `main.ps1` uv launcher. It does not depend on a machine-specific Conda environment.

## Model/provider protocol

SnapArc has one inference protocol: **OpenAI-compatible Chat Completions**.
Every text, image, audio and native Pi-RPC inference path ultimately sends
`POST <baseUrl>/chat/completions` using the provider configuration in
`config.yaml`.

- Gemini, Claude, GPT or other names may appear as **model IDs** behind an
  OpenAI-compatible provider; SnapArc does not use their vendor SDK/API modes.
- Audio transcription is sent as an `input_audio` content part through Chat
  Completions; SnapArc does not call a separate audio transcription endpoint.
- Model IDs are configured locally; SnapArc does not call `/models` discovery.
- The provider `api` value must be exactly `openai-completions`;
  vendor-specific modes and legacy aliases are rejected.
- Provider endpoint/key/model values come from `config.yaml` and the local
  secret store. There is no `NEW_API_*`, vendor-key or provider fallback path.
- SnapArc uses `httpx` directly for the OpenAI-compatible wire protocol; it
  does not depend on a vendor SDK or hard-code an OpenAI cloud host.

## Privacy and AI provider consent

Remote AI requests are disabled by default. To explicitly permit transmitting
selected text, screenshots or audio to a configured remote provider, set:

```yaml
privacy:
  allow_cloud_upload: true
```

Only the boolean value `true` enables remote provider dispatch. Loopback
providers are not gated; a local service may itself forward data to remote
services under its own configuration. The native Pi-RPC completion path
applies the same remote-upload rule. Provider privacy and billing terms still
apply.

Plugins are trusted local code, not a sandbox. Bundled plugins are enabled;
unbundled plugin discovery requires explicit local opt-in:

```yaml
radial_plugins:
  allow_user_plugins: true
```

## Security model

- Screenshot Chat binds only to loopback and uses a per-launch session token.
- The WebView child does not receive that token through command-line arguments.
- Local WebView API polling bypasses environment/system HTTP proxies.
- Cross-origin local API requests are rejected.
- Plugin subprocesses do not inherit ambient provider/session credentials by
  default.
- Secret scanning and release asset/license audits are part of CI.

The current WebView bootstrap still uses a token-bearing local query URL for the
initial page/media path. This is accepted technical debt for the source alpha,
not a claim of final browser-session architecture.

See `SECURITY.md`.

## Licensing and assets

Repository files are MIT-licensed except the product assets identified in
`ASSET_POLICY.md` and branding covered by `TRADEMARKS.md`. MIT permits
commercial reuse and proprietary derivatives of the licensed code subject to
its notice requirements.

SnapArc/HCU Lab names and branding are governed separately by
`TRADEMARKS.md`. Product UI assets under `src/animate/` and `src/icons/`
are governed by `ASSET_POLICY.md`; they are not automatically relicensed by
the code license. User screenshots, recordings and runtime media must never be
committed.

Third-party dependency notices are recorded in `THIRD_PARTY_NOTICES.md`.
The source-only alpha does not redistribute the experimental PyInstaller build.

## Development and verification

```powershell
uv sync --locked --python (py -3.12 -c 'import sys; print(sys.executable)') --group dev
uv run python scripts/release_asset_audit.py
uv run python scripts/release_license_audit.py
uv run python scripts/release_protocol_audit.py
node scripts/test_markdown_url_security.cjs
uv run python -m src.release_smoke
uv run python -m pytest -q tests
uv build --offline
```

Test counts depend on the revision and environment. Provider-dependent live
acceptance requires separate authorization and bounded request budgets; see
`docs/release-acceptance.md`.

## Windows packaging

`SnapArc.spec` and `scripts/build_windows.ps1` produce an experimental
PyInstaller `onedir` build for engineering validation. It is **not** part of
v0.0.2 source-alpha publication. Binary redistribution has separate full
transitive-license, clean-machine, signing and installer gates.

## Repository layout

| Path | Purpose |
| --- | --- |
| `main.py` | Desktop application entry point |
| `src/` | Application, UI, provider and service code |
| `plugins/` | Bundled trusted plugin implementations |
| `tests/` | Automated and explicitly gated live tests |
| `scripts/` | Release checks, diagnostics and build helpers |
| `sample/` | Non-secret example configuration |
| `docs/` | Release, ownership and acceptance documentation |

## Release governance

No GitHub remote, public push or public release should occur until the
maintainer reviews `docs/PUBLIC-ALPHA-APPROVAL.md` and explicitly approves
the final commit/hash package.
