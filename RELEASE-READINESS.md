# SnapArc v0.0.1 - release readiness

## Release identity

- Product: SnapArc by HCU Lab
- Version: 0.0.1
- Intended publication: source-only public alpha
- Code license: MIT, with branding and product assets handled separately
- Binary/installer publication: out of scope for v0.0.1
- GitHub publication: requires final maintainer approval

## Automated source-alpha gate

The final release gate on the current candidate working tree reported:

- 631 Python tests passed, 14 skipped, 13 subtests passed.
- Release asset audit passed: 172 retained product image assets; no tracked
  screenshot/runtime media.
- Direct dependency license audit passed; LGPLv3 dependencies and PDFium
  redistribution requirements are explicitly flagged for future binary work.
- Release brand audit passed.
- Markdown URL injection regression passed for 9 malicious/safe cases.
- Windows source smoke passed with CPython 3.12.10 / Tcl-Tk 8.6.15.
- Source smoke found 8 product icons, 164 animation frames, 10 bundled plugins
  and a healthy loopback Screenshot Chat server.
- Locked source distribution and wheel build completed successfully.

The committed-history secret scan and independent final Opus review are executed
after the release-prep commit so they can reference an immutable revision.

## Privacy and security posture

- Remote AI upload defaults to denied unless
  `privacy.allow_cloud_upload` is exactly boolean `true`.
- The same remote-upload rule applies to LLMService and native Pi-RPC
  completion dispatch; loopback providers remain local.
- Screenshot Chat binds to loopback, rejects cross-origin local API requests
  and does not pass its session token through process command-line arguments.
- Plugin subprocesses receive a restricted environment and unbundled plugin
  discovery is explicit opt-in.
- Plugins remain trusted local code, not a sandbox.
- The local WebView bootstrap still uses a token-bearing query URL for initial
  page/media access. This is accepted technical debt for the source alpha and
  remains a binary/1.0 hardening item.

## Product assets and rights

The release retains the existing product assets in `src/animate/` and
`src/icons/`, as directed by the maintainer. User/runtime screenshots,
clipboard captures, recordings and histories are excluded.

Legacy Git history provides provenance clues but does not prove ownership.
Final GitHub approval must include the maintainer attestation in
`docs/PUBLIC-ALPHA-APPROVAL.md`.

## Experimental Windows portable evidence

The PyInstaller `onedir` candidate is engineering evidence only and is not a
v0.0.1 release artifact.

Current local evidence:

- PyInstaller build completed.
- Frozen release-smoke exited 0.
- Frozen smoke verified Tcl/Tk 8.6.15, all product assets, 10 bundled plugins
  and loopback Screenshot Chat assets/server.
- Desktop startup with `--open-chat` stayed alive and exposed the visible
  window title `SnapArc Chat`.

Binary publication remains blocked by clean-machine acceptance, LGPL/PDFium
redistribution compliance, optional WebView-mode interactive acceptance and
the later signing/installer/update policy.

## Remaining gates before GitHub source-alpha publication

1. Release-prep commit and committed-history secret scan must pass.
2. Independent M365 Opus final review must find no unresolved source-alpha
   blocker.
3. Maintainer must review and attest the rights/asset statements in
   `docs/PUBLIC-ALPHA-APPROVAL.md`.
4. Maintainer must explicitly approve creation of the public GitHub remote and
   push.

No GitHub remote, push or public release is authorized before item 3-4.
