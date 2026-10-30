param([Parameter(ValueFromRemainingArguments = $true)][string[]]$LaunchArgs)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'SnapArc requires uv (https://docs.astral.sh/uv/) and Python 3.12.'
}
& uv run --locked --python 3.12 python main.py @LaunchArgs
exit $LASTEXITCODE
