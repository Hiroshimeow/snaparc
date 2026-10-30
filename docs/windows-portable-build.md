# Windows portable build — experimental

The spec SnapArc.spec is a packaging experiment, NOT a release-certified artifact.
Run scripts/build_windows.ps1 on Windows 11 with official CPython 3.12.
The output is dist/SnapArc. Do not publish it without a complete clean-machine
smoke test, a separate executable WebView child strategy for frozen builds,
LGPLv3 compliance for pystray, and hashes/signing/installer acceptance.
The existing Python-source launch path remains the reference implementation.

The frozen build now uses SnapArc.exe --snaparc-webview-child rather than
launching a Python interpreter with -m. This path has an offline unit test,
but still requires interactive desktop acceptance on the built executable.
Do not claim the packaged WebView is functional until that test succeeds.
