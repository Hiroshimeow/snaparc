# Release acceptance gates

SnapArc uses exactly one remote inference protocol: **OpenAI-compatible Chat
Completions** (`POST .../chat/completions`). Live release acceptance must not
introduce a separate model-list, vendor Gemini, Responses, Assistants, or audio
transcription endpoint.

Default test discovery sends **no live requests**. Live checks require explicit
operator authorization through `STC_RELEASE_APPROVAL` and
`PI_SDK_LIVE=1`.

## Approval file

The external approval JSON contains no credentials and must include:

- `authorized_by`: owner/reference for the live probe;
- `config_path`: exact approved local config file;
- `config_sha256`: SHA-256 of that file;
- `model`: approved `provider/model`;
- `vision_evidence`: evidence if the OCR scenario is authorized;
- `authentication_method`: exactly `chat-completions-probe`;
- `budgets`: one-request budgets for `configuration`, `arithmetic`,
  `translation`, and `ocr`, each containing `requests: 1`,
  `max_output_tokens` (1-256) and `timeout_seconds` (1-60).

No API key is stored in the approval file. Credentials are resolved by the
application from the separately approved configuration/secret store.

## Invocation

```powershell
$env:PI_SDK_LIVE = '1'
$env:STC_RELEASE_APPROVAL = '<reviewed external approval JSON path>'
Remove-Item Env:PI_OFFLINE -ErrorAction SilentlyContinue
Remove-Item Env:STC_DISABLE_OPENAI_PI_AGENT -ErrorAction SilentlyContinue
uv run python -m pytest -q tests/release/test_release_acceptance.py
```

Do not run this without an approved request budget.

## What the live harness proves

Each scenario uses one real bounded Chat Completions request and observes the
actual destination, bearer credential, model ID, output cap, timeout and retry
count without replacing the provider response.

- `configuration`: authenticated Chat Completions probe; reply must be `OK`.
- `arithmetic`: native Pi-RPC Chat Completions path; reply must be `4`.
- `translation`: LLMService Chat Completions path.
- `ocr`: image input through the same Chat Completions path.

The application clients use `max_retries=0`; offline retry tests verify a
failed request makes exactly one HTTP attempt.

## Scope

Passing live tests proves only the approved provider/model scenarios. It does
not establish overall release readiness, provider privacy guarantees, or plugin
sandboxing. The v0.0.2 source alpha does not require live-provider acceptance
unless such provider-specific claims are made.
