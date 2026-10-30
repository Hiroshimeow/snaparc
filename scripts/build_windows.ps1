param()
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = & py -3.12 -c 'import sys, tkinter; print(sys.executable)'
if ($LASTEXITCODE -ne 0 -or -not $python) {
    throw 'Official CPython 3.12 with Tcl/Tk is required.'
}
uv sync --locked --python $python --group dev
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency install failed.' }
uv run --locked --with pyinstaller pyinstaller --noconfirm --clean SnapArc.spec
if ($LASTEXITCODE -ne 0) { throw 'Portable candidate build failed.' }
Write-Host 'Build produced an experimental onedir candidate only. Desktop acceptance, license compliance, signing and installer validation remain required.'
