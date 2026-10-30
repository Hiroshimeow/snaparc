# Third-party notices — SnapArc v0.0.1 source alpha

SnapArc's **source-only alpha** declares these packages as dependencies and does
not publish a bundled Windows binary. Each dependency remains governed by its
own upstream license. The locked third-party package source is the public
`https://pypi.org/simple` registry; the only other lock source is the local
SnapArc project itself. No private registry, Git dependency or external path
dependency is present in the release candidate. This inventory was checked
against locked/local metadata; upstream license files remain authoritative.

| Direct dependency | Checked version | Declared license |
|---|---:|---|
| customtkinter | 5.2.2 | CC0-1.0 |
| keyboard | 0.13.5 | MIT |
| numpy | 2.4.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openai | 2.30.0 | Apache-2.0 |
| pillow | 12.2.0 | MIT-CMU |
| pyautogui | 0.9.54 | BSD |
| pynput | 1.8.1 | LGPLv3 |
| pygments | 2.20.0 | BSD-2-Clause |
| pyperclip | 1.11.0 | BSD |
| pywebview | 6.2.1 | BSD-3-Clause |
| pystray | 0.19.5 | LGPLv3 |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| requests | 2.33.1 | Apache-2.0 |
| soundcard | 0.4.5 | BSD-3-Clause |
| soundfile | 0.13.1 | BSD-3-Clause |
| pypdf | 6.14.2 | BSD-3-Clause |
| pypdfium2 | 5.14.0 | BSD-3-Clause / Apache-2.0; bundled PDFium dependency notices apply |
| tomli | 2.4.1 when Python < 3.11 | MIT |

## Distribution notes

- `pynput` and `pystray` declare LGPLv3. For the source-only alpha they are
  resolved by the user's package manager and are not redistributed inside a
  SnapArc binary. A future frozen/portable distribution must perform a separate
  LGPL compliance review and ship the required notices/source or replacement
  mechanism.
- `pypdfium2` may install PDFium and associated third-party components. A
  future binary distribution must carry the notices required by the exact
  bundled build.
- `tomli` is conditional for Python < 3.11; version 2.4.1 metadata was
  separately checked and declares MIT.
- Python, Tcl/Tk, Microsoft WebView2 and optional Pi tooling are external
  runtimes/tools, not licensed by SnapArc's MIT license.
- Product UI assets under `src/animate/` and `src/icons/` are governed by
  `ASSET_POLICY.md` and are not automatically relicensed by this inventory.

This file is an engineering inventory, not legal advice. Re-run the release
license audit whenever dependency versions or packaging scope change.
