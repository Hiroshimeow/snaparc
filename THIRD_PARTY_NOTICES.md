# Third-party notices — SnapArc v0.0.1 source alpha

SnapArc's **source-only alpha** declares the following direct runtime
dependencies. All third-party packages are resolved from the public
`https://pypi.org/simple` registry; the only other lock source is the local
SnapArc project itself. No private registry, Git dependency or external path
dependency is part of the release candidate.

Each dependency remains governed by its own upstream license.

| Direct dependency | Checked version | Declared license |
|---|---:|---|
| customtkinter | 5.2.2 | CC0-1.0 |
| keyboard | 0.13.5 | MIT |
| numpy | 2.4.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| pillow | 12.2.0 | MIT-CMU |
| pyautogui | 0.9.54 | BSD |
| pygments | 2.20.0 | BSD-2-Clause |
| pyperclip | 1.11.0 | BSD |
| pywebview | 6.2.1 | BSD-3-Clause |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| requests | 2.33.1 | Apache-2.0 |
| soundcard | 0.4.5 | BSD-3-Clause |
| soundfile | 0.13.1 | BSD-3-Clause |
| pypdf | 6.14.2 | BSD-3-Clause |
| tomli | conditional, Python < 3.11 | MIT |

## Distribution notes

- The v0.0.1 release is source-only; no frozen Windows executable is published.
- `tomli` is conditional for Python < 3.11.
- Python, Tcl/Tk, Microsoft WebView2 and optional Pi tooling are external
  runtimes/tools and are not licensed by SnapArc's MIT license.
- Product UI assets under `src/animate/` and `src/icons/` are governed by
  `ASSET_POLICY.md`.
- Optional user-installed plugins are outside SnapArc's bundled dependency set.
  Plugin authors/users must verify the license of any dependency they add.

This file is an engineering inventory, not legal advice. Re-run the release
license audit whenever dependency versions or packaging scope change.
