#Requires -RunAsAdministrator
# Registers the 4 Reddit Radar scheduled tasks (spec section 12). Run once from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# Re-running updates the tasks in place. To remove them:
#   Get-ScheduledTask Radar_* | Unregister-ScheduledTask -Confirm:$false
#
# Tasks run as the logged-in user (Claude Code needs the user session), wake the PC,
# and run as soon as possible if a start was missed.
#
# Schedule (IST), built around replying once a day at 20:00:
#   Radar_Scan          09:00, 13:00, 17:00, 19:15   read F5Bot emails, score, alert, export
#   Radar_Feedback      09:05, 19:20, 23:30          apply your button taps (Telegram keeps
#                                                    them only 24h, so never leave a 24h gap)
#   Radar_Drafts        19:30                        Claude drafts replies, ready before 20:00
#   Radar_Weekly_Stats  Fri 18:00                    weekly report
# Fewer scans lose nothing: each scan picks up every F5Bot email since the last one.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"

function New-RadarAction([string]$Script, [string]$Arguments = "") {
    $file = Join-Path $Root "scripts\$Script"
    $argLine = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$file`" $Arguments"
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argLine.Trim() -WorkingDirectory $Root
}

# One daily trigger per time of day, e.g. New-DailyTriggers "09:00", "19:15"
function New-DailyTriggers([string[]]$Times) {
    return @($Times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
}

function New-RadarSettings([int]$LimitMinutes) {
    New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $LimitMinutes)
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

$tasks = @(
    @{ Name = "Radar_Scan"; Action = (New-RadarAction "run_scan.ps1");
       Trigger = (New-DailyTriggers "09:00", "13:00", "17:00", "19:15");
       Settings = (New-RadarSettings 15); Description = "Reddit Radar: read F5Bot emails, score, alert (09:00, 13:00, 17:00, 19:15)" },
    @{ Name = "Radar_Drafts"; Action = (New-RadarAction "run_drafts.ps1");
       Trigger = (New-DailyTriggers "19:30");
       Settings = (New-RadarSettings 20); Description = "Reddit Radar: Claude Code reply drafts (19:30, ready before 20:00)" },
    @{ Name = "Radar_Feedback"; Action = (New-RadarAction "run_radar.ps1" "feedback");
       Trigger = (New-DailyTriggers "09:05", "19:20", "23:30");
       Settings = (New-RadarSettings 10); Description = "Reddit Radar: Telegram button taps (09:05, 19:20, 23:30)" },
    @{ Name = "Radar_Weekly_Stats"; Action = (New-RadarAction "run_radar.ps1" "stats --days 7");
       Trigger = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "18:00");
       Settings = (New-RadarSettings 10); Description = "Reddit Radar: weekly stats to Telegram (Fri 18:00)" }
)

foreach ($t in $tasks) {
    Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $t.Trigger -Settings $t.Settings `
        -Principal $principal -Description $t.Description -Force | Out-Null
    Write-Host ("registered {0}" -f $t.Name)
}

Write-Host ""
Write-Host "Done. Check them in Task Scheduler, or run one now with:"
Write-Host "  Start-ScheduledTask -TaskName Radar_Scan"
