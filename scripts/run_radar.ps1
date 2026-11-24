# Run any Reddit Radar command with the project's venv and log the exit code.
# Usage: scripts\run_radar.ps1 feedback
#        scripts\run_radar.ps1 stats --days 7
# Used by the Radar_Feedback and Radar_Weekly_Stats scheduled tasks.

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

& $Python -m radar @args
$code = $LASTEXITCODE
Add-Content -Path $TaskLog -Encoding UTF8 -Value ("{0} radar {1} exit {2}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture), ($args -join " "), $code)
exit $code
