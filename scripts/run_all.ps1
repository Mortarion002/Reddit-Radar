# Run the whole Reddit Radar cycle now, in order, on demand:
#   1. scan        read new F5Bot emails, score, send hot alerts + digest, export leads
#   2. feedback    apply your Telegram button taps (so the Elvan mention ratio is current)
#   3. drafts      Claude Code drafts replies for the new leads, then send-drafts delivers them
#                  (also resends anything that failed to reach Telegram earlier)
#
# Usage (normal PowerShell, from the project folder):
#   scripts\run_all.ps1              full cycle
#   scripts\run_all.ps1 -NoDrafts    scan + feedback only (no Claude run)
#
# Same scripts as the scheduled tasks, so results are logged to data\logs\tasks.log as usual.

param([switch]$NoDrafts)

$ErrorActionPreference = "Continue"
$Scripts = $PSScriptRoot

Write-Host "== 1/3 scan" -ForegroundColor Cyan
& (Join-Path $Scripts "run_scan.ps1")

Write-Host ""
Write-Host "== 2/3 feedback" -ForegroundColor Cyan
& (Join-Path $Scripts "run_radar.ps1") feedback

if ($NoDrafts) {
    Write-Host ""
    Write-Host "== 3/3 drafts skipped (-NoDrafts)" -ForegroundColor Cyan
    exit 0
}

Write-Host ""
Write-Host "== 3/3 drafts (Claude can take a few minutes)" -ForegroundColor Cyan
& (Join-Path $Scripts "run_drafts.ps1")

Write-Host ""
Write-Host "Done. Drafts (if any) are on Telegram. Details: data\logs\tasks.log" -ForegroundColor Green
exit 0
