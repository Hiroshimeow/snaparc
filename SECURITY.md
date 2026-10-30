# SnapArc security policy

SnapArc is a free Windows productivity application developed under HCU Lab.

## Status
Version 0.0.1 is a pre-alpha source candidate, not approved for public release. No security audit or code-signing certification is implied.

## Responsible disclosure
Before a public issue tracker is created, privately contact the repository maintainer using the contact mechanism at https://hcu-lab.me. Never include working credentials, personal screenshots, recordings, or private files in bug reports.

## Sensitive data
API keys and provider credentials belong in local, Git-ignored configuration. Treat content sent to an external AI provider as uploaded data; use provider-specific privacy controls and disclose such behavior in the UI and documentation. Do not include personal data in diagnostics.

## Plugins
Configurable plugins can launch external commands and inherit environment variables and files. Until explicit trust boundaries are implemented, users must run only reviewed plugins they trust. Do not install arbitrary plugins from strangers.

## Release gate
Before a public GitHub push: review all tracked source for secrets and personal data; validate license ownership, dependencies and redistribution terms; test a fresh Windows installation and uninstallation; run offline and authorized live-provider acceptance; inspect application logs, local HTTP endpoints and subprocess arguments for credential exposure.

No public download should be labeled production-ready until those checks pass.
