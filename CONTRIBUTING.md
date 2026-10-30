# Contributing to SnapArc

SnapArc is a pre-alpha Windows desktop project. Keep changes small, reviewable
and testable.

## Contribution license

By submitting source-code changes, you agree that your contribution may be
distributed under the repository's MIT code license. Do not submit code,
images, icons, fonts, audio, datasets or other material you do not have the
right to contribute.

Product branding and retained UI assets are governed separately by
`TRADEMARKS.md` and `ASSET_POLICY.md`.

## Required checks

Before opening a pull request on a public repository:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_release.ps1
```

For focused changes, run the smallest relevant tests first, then the complete
release gate before merge.

## Security and privacy

Never commit:

- credentials, API keys or provider tokens;
- `config.yaml`, `.env` files or DPAPI/token caches;
- screenshots, clipboard captures, recordings, chat/session history or logs;
- customer, employer or other private source material.

Report security issues according to `SECURITY.md`; do not publish working
credentials or private user content in an issue.

## Plugins

Plugins run as trusted local code. New bundled plugins require code review,
bounded side effects and tests. Do not describe plugins as sandboxed unless an
actual isolation boundary is implemented and verified.
