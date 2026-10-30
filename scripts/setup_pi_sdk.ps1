param(
    [switch]$Development,
    [string]$NodeCommand = "node",
    [string]$NpmCommand = "npm"
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sidecarRoot = Join-Path $repoRoot "sidecars\pi-sdk"

$nodeOutput = @(& $NodeCommand --version 2>&1)
$nodeExit = $LASTEXITCODE
$nodeVersion = if ($nodeOutput.Count -gt 0) { $nodeOutput[0].ToString().Trim() } else { "" }
if ($nodeExit -ne 0 -or $nodeVersion -notmatch '^v?(\d+)\.') {
    [Console]::Error.WriteLine("Unable to determine Node.js version. Node.js 22+ is required.")
    exit 2
}
if ([int]$Matches[1] -lt 22) {
    [Console]::Error.WriteLine("Node.js 22+ is required; found $nodeVersion.")
    exit 2
}

$npmArgs = @("ci")
if (-not $Development) {
    $npmArgs += "--omit=dev"
}

Push-Location $sidecarRoot
try {
    & $NpmCommand @npmArgs
    $npmExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($npmExit -ne 0) {
    exit $npmExit
}

Write-Output "Pi SDK bridge dependencies installed."
Write-Output "Next: uv run python scripts/diagnostics/pi_sdk_health.py --human"
