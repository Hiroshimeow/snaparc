param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$manifest=Get-Content pyproject.toml -Raw
if($manifest -notmatch 'name = "snaparc"' -or $manifest -notmatch 'version = "0.0.1"'){throw 'Unexpected SnapArc release identity'}
uv lock --check --offline
if($LASTEXITCODE -ne 0){throw 'Lockfile mismatch'}
uv run --locked --python 3.12 python -m pytest -q tests
if($LASTEXITCODE -ne 0){throw 'Offline tests failed'}
Write-Output 'Offline gate passed. Live API, desktop, license, installer and security approval remain separate gates.'
