# SnapArc — HCU Lab free public pre-alpha

## Release identity
- Product: SnapArc
- Repository/package: snaparc
- Version: 0.0.1
- Owner/brand: HCU Lab
- Planned web landing page: https://snaparc.hcu-lab.me (not configured)
- License: UNDECIDED pending ownership and third-party licensing audit
- Publication: NOT AUTHORIZED until maintainer reviews local Git history and release gates

## Free release policy
No subscription or paid tier is planned for the current feature set. Provider API fees, if configured by the user, remain subject to the user's provider agreements.

## Core capabilities
- Windows tray control, screenshots, stacked captures, floating prompt
- Radial action/plugin menu for local tools and scripts
- Optional screenshot chatbot and AI providers
- Audio recording, transcription and speech workflows
- Clipboard and file utilities

## Architectural direction
main.py is currently the application composition root, not a stable plugin SDK. There are separate src/plugins and src/services/radial_plugin_service.py registries. Do not claim plugin isolation, sandboxing, signed plugins, or an SDK. Converge plugin lifecycle in an independent follow-up with regression tests. Until then plugins are explicitly trusted local code.

## Release checklist
- [ ] No secrets or personal information in the NEW repo Git history
- [ ] Independent review P0 findings addressed
- [ ] Legal rights and dependency licenses audited, explicit LICENSE added
- [ ] Clean install and uninstall on Windows 10/11 with CPython/Tcl-Tk-compatible distribution
- [ ] Full offline tests pass in reproducible environment
- [ ] Approved end-to-end screenshots, audio, WebView2, hotkeys and plugin tests
- [ ] Privacy/permission explanation for API uploads and local files
- [ ] Signed, reproducible and inspectable installer, with update integrity
- [ ] Demo video and screenshots contain no personal or secret information
- [ ] No public push until user reviews files, test logs and independent review

## Positioning
SnapArc is a context-first desktop productivity toolkit rather than a general-purpose AI chatbot. In demos, show selecting screen/file/clipboard context and invoking a local action with minimal context switching.

Historical v2.x references may remain in compatibility tests and module internals during migration; the release identity is version 0.0.1.

