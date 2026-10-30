param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LaunchArgs)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'SnapArc requires uv: https://docs.astral.sh/uv/'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install official Python 3.12 with Tcl/Tk and the Windows Python launcher.'
}
$pythonPath = (& py -3.12 -c 'import sys, tkinter; print(sys.executable)')
if ($LASTEXITCODE -ne 0 -or -not $pythonPath) {
    throw 'Install official CPython 3.12 with the optional Tcl/Tk feature.'
}
& uv sync --locked --python $pythonPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& uv run --locked python main.py @LaunchArgs
exit $LASTEXITCODE
