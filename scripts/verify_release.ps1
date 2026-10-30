param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root

$manifest=Get-Content pyproject.toml -Raw
if($manifest -notmatch 'name = "snaparc"' -or $manifest -notmatch 'version = "0.0.1"'){
    throw 'Unexpected SnapArc release identity'
}
if($manifest -notmatch 'license = "MIT"'){
    throw 'Unexpected SnapArc code license'
}
if(-not (Get-Command py -ErrorAction SilentlyContinue)){
    throw 'Official CPython 3.12 with Tkinter is required'
}
$python=(& py -3.12 -c 'import sys, tkinter; print(sys.executable)')
if($LASTEXITCODE -ne 0 -or -not $python){
    throw 'Python 3.12 with Tcl/Tk unavailable'
}

uv lock --check --offline
if($LASTEXITCODE -ne 0){throw 'Lockfile mismatch'}
uv sync --locked --python $python --group dev
if($LASTEXITCODE -ne 0){throw 'Dependency setup failed'}

uv run --locked python scripts/release_asset_audit.py
if($LASTEXITCODE -ne 0){throw 'Release asset audit failed'}
uv run --locked python scripts/release_license_audit.py
if($LASTEXITCODE -ne 0){throw 'Release license audit failed'}
uv run --locked python scripts/release_brand_audit.py
if($LASTEXITCODE -ne 0){throw 'Release brand audit failed'}
uv run --locked python scripts/release_protocol_audit.py
if($LASTEXITCODE -ne 0){throw 'OpenAI-compatible protocol audit failed'}
node scripts/test_markdown_url_security.cjs
if($LASTEXITCODE -ne 0){throw 'Markdown URL security regression failed'}
uv run --locked python -m src.release_smoke
if($LASTEXITCODE -ne 0){throw 'Windows source smoke failed'}
uv run --locked python -m pytest -q tests
if($LASTEXITCODE -ne 0){throw 'Offline tests failed'}
uv build --offline --no-build-isolation
if($LASTEXITCODE -ne 0){throw 'Source package build failed'}

Write-Output 'SnapArc source-alpha automated release gate passed.'
Write-Output 'Still separate: final rights/asset attestation, final committed-history secret scan, GitHub approval.'
Write-Output 'Portable/installer publishing remains out of scope for v0.0.1 source alpha.'
