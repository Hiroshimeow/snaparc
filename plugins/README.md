# SnapArc plugins

SnapArc ships only a small reviewed set of bundled plugins. Additional plugins
are local user code and are **not sandboxed**.

## Enable local user plugins

Set this in your local, Git-ignored `config.yaml`:

```yaml
radial_plugins:
  allow_user_plugins: true
```

Then place a Python script in `plugins/`. A plugin can expose metadata with a
`PLUGIN_META_JSON` comment and use normal command-line arguments or the
SnapArc plugin context environment variables.

Dependencies required only by a custom plugin should be installed by the user
for that plugin; they must not be added to SnapArc's core dependency set unless
the feature becomes a reviewed bundled feature.

## PDF example

SnapArc v0.0.1 intentionally does **not** bundle a PDF-to-image renderer plugin.
A user who wants one can create a local plugin and choose a PDF library that
fits their own licensing/deployment requirements.

Examples include:

- `pypdfium2` / PDFium-based rendering;
- PyMuPDF, which is available under AGPLv3 or a separate commercial license.

SnapArc does not select, install or redistribute either renderer in the public
source alpha.

## Security

Plugins execute with the user's local filesystem/process privileges. The plugin
subprocess environment is restricted to reduce accidental credential leakage,
but this is not a security sandbox. Review every plugin before enabling it.
