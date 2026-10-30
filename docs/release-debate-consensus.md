# SnapArc 0.0.1 — M365 Opus debate and release decision

Date: 2026-10-30
Review: https://m365.cloud.microsoft/chat/conversation/9e92eac9-9d32-4ef0-be10-ad916d0c3c52
Review turns: original security review; source-grounded debate 7feeb3b0-m365-worker-20261030-181822-580b0884; implementation checkpoint 7feeb3b0-m365-worker-20261030-182726-15954dd1.

## Consensus

- Security P0: no demonstrated remote exploit in reviewed excerpts; full proof is incomplete.
- Source-only alpha 0.0.x is viable **conditionally** with a proper license, privacy opt-in, regression checks, and explicit owner sign-off. No Authenticode installer is needed for a *source-only* alpha.
- Commercial-quality Windows binary release requires its own runtime/signer/update/uninstaller and manual desktop acceptance gate.
- Existing plugins are trusted local code, not sandboxed. Nonbundled discovery is opt-in; subprocess environments are restricted.
- Browser session token must be handled cautiously: keep the loopback server and Host validation, do not use command-line args, disable proxy use, restore WebView2 protections. Query-string bootstrap is residual technical debt for the binary release.
- Cloud requests must be denied unless explicitly opted in, including independent Pi-RPC dispatch; loopback-only AI providers are local and need no cloud grant.
- The renderer's URL handling must pass malicious Markdown regression tests; do not rely only on prose review.
- License/ownership and third-party redistribution rights cannot be validated by an LLM.

## Implemented this cycle

WebView2 flags and loopback-only API fetch; Origin and non-LAN server bind checks;
plugin subprocess environment allowlist; unbundled plugin opt-in; default-deny
cloud configuration on both LLMService and Pi-RPC; MIT license draft and dependency
inventory; new regression tests and CI Node Markdown URL check; SnapArc UI labels.

## Remaining gates before public GitHub push

1. Operator/legal written confirmation of rights to the migrated code, fonts, UI assets and brand.
2. Manual acceptance of tray, capture/hotkeys, WebView2, Japanese IME, image attachments, audio, local plugins and opted-in provider requests.
3. Review exact provider destination for Pi-RPC and other optional tools; no blanket privacy promise for independently trusted plugins.
4. Validate conditional LGPLv3 notices (pystray) and binary distribution requirements.
5. Run Gitleaks against final tracked tree and history, complete locked full test suite and record passing evidence.
6. Explicit user approval to create a public remote/push. No automation should push without approval.

## Alpha versus installer

Source-only alpha: Git repository + explicit LICENSE after ownership clearance,
documented Python/uv setup, security disclosure and all relevant tests.
Installer: onedir/onefile runtime acceptance, reproducible packaging,
publisher signing, verified clean install/uninstall and license compliance.
Never label a wheel or an untested PyInstaller build a ready installer.
