# One Reddit Radar scan: read new F5Bot emails, score, alert, digest, export.
# Used by the Radar_Scan scheduled task (09:00, 13:00, 17:00 and 19:15 IST).
# Extra arguments are passed through, e.g. scripts\run_scan.ps1 --dry-run

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Activate = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (Test-Path $Activate) { . $Activate }
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$TaskLog = Join-Path $LogDir "tasks.log"

& $Python -m radar scan @args
$code = $LASTEXITCODE
Add-Content -Path $TaskLog -Encoding UTF8 -Value ("{0} scan exit {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture), $code)
exit $code
