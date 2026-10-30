# SnapArc security policy

SnapArc is a free Windows productivity application under the HCU Lab product
identity.

## Status

Version 0.0.1 is a **source-only public-alpha candidate**. It is not a
production-ready, signed Windows binary or installer. Publication still
requires the maintainer's final rights/asset attestation and explicit GitHub
approval.

## Responsible disclosure

Before a public issue tracker is created, privately contact the repository
maintainer using the contact mechanism at https://hcu-lab.me. Never include
working credentials, personal screenshots, recordings or private files in bug
reports.

## Sensitive data and cloud providers

API keys and provider credentials belong in local, Git-ignored configuration.
Remote provider dispatch is disabled unless `privacy.allow_cloud_upload` is
exactly boolean `true`. A loopback provider is not gated as a remote upload;
the local service may itself forward data elsewhere under its own configuration,
so users must understand and trust that service.

Do not include personal data in diagnostics or commits.

## Plugins

Plugins are **trusted local code**, not a sandbox. Bundled plugin subprocesses
receive a restricted environment rather than inheriting ambient provider and
session credentials by default. Plugins still run with the user's filesystem
and process privileges and may perform side effects or network activity through
their own implementation/configuration.

Unbundled plugin discovery is disabled by default and requires explicit local
opt-in. Run only plugins you have reviewed and trust.

## Source-alpha release gate

Before a public **source-only** GitHub push:

- scan tracked/staged content and the final Git history for secrets/private
  runtime data;
- run the locked automated test suite, release audits, Markdown security check
  and Windows source smoke;
- validate code-license metadata, dependency notices and retained asset policy;
- inspect local HTTP/session-token and subprocess boundaries;
- obtain the maintainer rights/asset attestation;
- obtain explicit maintainer approval for the final GitHub push.

Authorized live-provider acceptance is optional and separately budgeted; it is
required only for claims about a particular live provider/model, not for the
source-only alpha itself.

## Binary/installer gate

A future portable binary or production installer has additional requirements:
clean-machine Windows acceptance, interactive capture/hotkey/audio/WebView/IME
tests, a complete transitive dependency/license redistribution audit, artifact
hashes, and later signing/install/uninstall/update-integrity validation.

Do not label the experimental PyInstaller build production-ready or attach it
to the v0.0.1 source release.
