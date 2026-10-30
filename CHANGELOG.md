# Changelog

## 0.0.1 - public-source alpha candidate

First standalone SnapArc source release candidate under the HCU Lab product
identity.

### Changed

- Reset product/package identity to SnapArc 0.0.1 with an independent Git
  history.
- Repositioned chat as an optional workflow inside a desktop productivity tool.
- Added MIT code license, separate brand/asset policies and third-party notices.
- Added Windows CI, release asset/license audits and deterministic source smoke.
- Added experimental PyInstaller `onedir` packaging for engineering validation.

### Security and privacy

- Removed session token transport from child-process command-line arguments.
- Restricted Screenshot Chat server binding to loopback and reject cross-origin
  local API requests.
- Disabled HTTP proxy use for the WebView's local authenticated API polling.
- Restored WebView2 renderer integrity and SmartScreen protections.
- Default-deny remote AI upload unless `privacy.allow_cloud_upload` is exactly
  boolean `true`; applies to LLMService and native Pi-RPC completion dispatch.
- Kept loopback AI endpoints usable without cloud-upload consent.
- Restricted plugin subprocess environment inheritance and made unbundled
  plugin discovery explicit opt-in.
- Added malicious Markdown URL regression checks and release secret scanning.

### Provider protocol

- Removed the legacy `src/models/Gemini.py` facade, vendor-provider compatibility
  files and unused provider fallback paths.
- Enforced a single OpenAI-compatible Chat Completions inference protocol for
  text, image, audio and native Pi-RPC paths.
- Removed legacy vendor Gemini API modes, model-list discovery, separate audio
  transcription endpoint, private endpoint defaults and `NEW_API_*` fallbacks.
- Audio input now uses the Chat Completions `input_audio` content part.
- Provider/model IDs are loaded from local configuration; vendor model names do
  not imply vendor SDK integration.

### Plugins and dependencies

- Removed the bundled `pdf2png` plugin and `pypdfium2` dependency. PDF
  rendering is now a user-installed plugin concern.
- Added `plugins/README.md` describing the local plugin extension model and
  dependency/license responsibility.
- Removed runtime dependencies that the refactored Windows/native transport no
  longer imports; the release lock now contains only packages used by SnapArc.

### Verification

The final release report records exact test counts, smoke results, hashes and
review status. This candidate is not a signed Windows installer.
