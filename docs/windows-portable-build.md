# Windows portable build - experimental

SnapArc includes `SnapArc.spec` and `scripts/build_windows.ps1` so the
desktop application can be exercised as a frozen Windows `onedir` build.
This is engineering validation only; **the v0.0.1 public release is source-only**.

## Current local evidence

On the G6 Windows environment, the candidate build:

- completed with PyInstaller;
- passed `SnapArc.exe --snaparc-release-smoke`;
- loaded CPython 3.12.10 and Tcl/Tk 8.6.15;
- found 8 icons, 164 animation frames and 10 bundled plugins;
- loaded the Screenshot Chat web assets and healthy loopback server;
- opened `--open-chat` with a visible `SnapArc Chat` window.

The default Screenshot Chat UI mode is Tk. Frozen WebView-child routing is
covered separately by tests and by the `--snaparc-webview-child` entry path;
interactive WebView-mode acceptance is still required before any binary
publication.

## Why the binary is not published with v0.0.1

A future portable/binary release needs additional work:

- clean-machine Windows 10/11 smoke;
- Japanese IME, capture/hotkey, audio, clipboard and WebView2 interactive
  acceptance;
- LGPLv3 redistribution compliance for `pynput` and `pystray`;
- exact PDFium/pypdfium2 third-party notices;
- executable hashes and an explicit unsigned-pre-alpha warning, or later
  Authenticode signing;
- installer/uninstaller/update-integrity validation for a production release.

Do not attach `dist/SnapArc` or an installer to the v0.0.1 GitHub source
release.
