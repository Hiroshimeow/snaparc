param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$manifest=Get-Content pyproject.toml -Raw
if($manifest -notmatch 'name = "snaparc"' -or $manifest -notmatch 'version = "0.0.1"'){throw 'Unexpected SnapArc release identity'}
if(-not (Get-Command py -ErrorAction SilentlyContinue)){throw 'Official CPython 3.12 with Tkinter is required'}
$python=(& py -3.12 -c 'import sys, tkinter; print(sys.executable)')
if($LASTEXITCODE -ne 0 -or -not $python){throw 'Python 3.12 with Tcl/Tk unavailable'}
uv lock --check --offline
if($LASTEXITCODE -ne 0){throw 'Lockfile mismatch'}
uv sync --locked --python $python --group dev
if($LASTEXITCODE -ne 0){throw 'Dependency setup failed'}
uv run --locked python -m pytest -q tests
if($LASTEXITCODE -ne 0){throw 'Offline tests failed'}
Write-Output 'Offline gate passed. Live provider, desktop, license, standalone packaging, and security approval remain separate gates.'
