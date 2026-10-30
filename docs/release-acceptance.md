# Release acceptance gates

`tests/release/test_release_acceptance.py` binds the four release criteria to
real application paths. Default discovery skips these required live gates and
sends no requests. Skips are **not** release evidence.

The tests launch fresh Python interpreters to avoid collection-time module
doubles in other tests. They use `ConfigManager(config_path=...)`, the existing
`LLMService.send_text_message` / `process_image` paths, and the application's
native Pi bridge completion runner. This repository's native Python Pi bridge
is not evidence of an external Node Pi SDK integration.

## Approval prerequisites — do not invent values

Set `STC_RELEASE_APPROVAL` to an operator-reviewed JSON file outside the release
workspace, containing **no credentials**:

- `authorized_by`: authorizing owner and approval reference;
- `config_path`: absolute path to the explicitly approved local configuration file;
- `config_sha256`: digest of that exact approved file;
- `model`: the explicitly confirmed configured `provider/model`;
- `vision_evidence`: provider documentation/owner confirmation for that model;
- `authentication_method`: `protected-models-endpoint`;
- `models_endpoint_requires_authentication`: true, only if independently confirmed;
- `authentication_probe_requests`: 1 (non-completion request per gate);
- `budgets`: separate `arithmetic`, `translation`, and `ocr` objects, each with
  `requests: 1`, an agreed `max_output_tokens` (1–256), and an agreed
  `timeout_seconds` (1–60).

Configuration presence and model listing on a public endpoint do not prove
usable authentication. This harness supports a protected authenticated model
listing only. If this endpoint is public or inaccessible, stop and agree a
provider-specific authentication validation procedure rather than pretending
this gate passed. The actual protected listing must succeed and include the
selected model before a completion is attempted.

After these decisions, the invocation is:

```powershell
$env:PI_SDK_LIVE = '1'
$env:STC_RELEASE_APPROVAL = '<reviewed external approval JSON path>'
Remove-Item Env:PI_OFFLINE -ErrorAction SilentlyContinue
Remove-Item Env:STC_DISABLE_OPENAI_PI_AGENT -ErrorAction SilentlyContinue
uv run python -m pytest -q tests/release/test_release_acceptance.py
```

Do not run this invocation without approval. Each test performs its own auth
probe; running all four requires four authorized non-completion probes and
three separately authorized completions. A rerun needs a new budget decision.
No automatic retry, alternate model, personal Pi store, or fabricated response
is permitted. The HTTP observer forwards actual requests unchanged and blocks
extra attempts or missing output bounds before sending them.

## Known blockers exposed by these tests

Both application OpenAI client constructors now set `max_retries=0`.
`tests/release/test_request_retry_budget.py` exercises the real SDK against an
offline HTTP 503 boundary and requires exactly one HTTP attempt for the service
provider and the no-tools Pi runner. This is offline policy evidence, not a
successful live request.

The service text/image APIs and native Pi completion runner now accept optional
`max_output_tokens` and `timeout_seconds` arguments. They validate the bounds
before dispatch and forward them to the real SDK as `max_completion_tokens` and
`timeout`; the live gates supply the separately approved scenario budget through
these application APIs. The HTTP observer still rejects missing/excessive caps
and extra requests, without injecting or repairing request data. The SDK timeout
bounds network operations, not the entire workflow; the live gate separately
checks elapsed workflow time and its callback/subprocess deadline.
`tests/release/test_request_bounds.py` verifies serialization at an offline HTTP
boundary for all three application paths; this is not live-success evidence.

The native endpoint resolver accepts `config_path=...`, checks that it exists,
and rejects a conflicting ConfigManager singleton instead of silently selecting
another path. The live gate passes the exact external path explicitly; offline
coverage uses a temporary configuration outside the project. This does not add
an external-config CLI to the GUI or change its default launch behavior.
The presence of this harness is not evidence that live acceptance has passed.

Passing these tests does not establish overall release readiness. Record the
tested revision and environment, review remaining desktop and stability gates,
and verify the delivered artifact separately.
