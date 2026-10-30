# SnapArc asset policy

## Included product assets

The public-source candidate intentionally retains the existing product UI
assets under:

- `src/animate/` — tray animation frames.
- `src/icons/` — product and action icons such as translate, summarize,
  capture, pause and voice state icons.

These assets are treated separately from the MIT-licensed code until the final
GitHub release approval records that the maintainer has the right to publish
them. Do not infer asset rights from the repository's MIT code license.

## Excluded user/runtime media

The public repository must not contain user screenshots, clipboard captures,
recordings, generated thumbnails, logs, history, tokens, runtime configuration
or similar personal/runtime state. The release audit enforces this for tracked
paths and common generated names.

## Future assets

New external icons, fonts, images or audio require a recorded source and
redistribution license before they may be committed to a public release.
