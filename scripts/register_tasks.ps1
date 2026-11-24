#Requires -RunAsAdministrator
# Registers the 4 Reddit Radar scheduled tasks (spec section 12). Run once from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# Re-running updates the tasks in place. To remove them:
#   Get-ScheduledTask Radar_* | Unregister-ScheduledTask -Confirm:$false
#
# Tasks run as the logged-in user (Claude Code needs the user session), wake the PC,
# and run as soon as possible if a start was missed.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"

function New-RadarAction([string]$Script, [string]$Arguments = "") {
    $file = Join-Path $Root "scripts\$Script"
    $argLine = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$file`" $Arguments"
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argLine.Trim() -WorkingDirectory $Root
}

# Daily from 07:00, repeating every N minutes for 18 hours (until 01:00).
function New-DayTrigger([int]$EveryMinutes) {
    $trigger = New-ScheduledTaskTrigger -Daily -At "07:00"
    $repeat = New-ScheduledTaskTrigger -Once -At "07:00" `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Hours 18)
    $trigger.Repetition = $repeat.Repetition
    return $trigger
}

function New-RadarSettings([int]$LimitMinutes) {
    New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $LimitMinutes)
}

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

$tasks = @(
    @{ Name = "Radar_Scan"; Action = (New-RadarAction "run_scan.ps1"); Trigger = @(New-DayTrigger 30);
       Settings = (New-RadarSettings 20); Description = "Reddit Radar: read F5Bot emails, score, alert (every 30 min, 07:00-01:00)" },
    @{ Name = "Radar_Drafts"; Action = (New-RadarAction "run_drafts.ps1");
       Trigger = @((New-ScheduledTaskTrigger -Daily -At "09:00"), (New-ScheduledTaskTrigger -Daily -At "21:00"));
       Settings = (New-RadarSettings 20); Description = "Reddit Radar: Claude Code reply drafts (09:00 and 21:00)" },
    @{ Name = "Radar_Feedback"; Action = (New-RadarAction "run_radar.ps1" "feedback"); Trigger = @(New-DayTrigger 15);
       Settings = (New-RadarSettings 10); Description = "Reddit Radar: Telegram button taps (every 15 min, 07:00-01:00)" },
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
