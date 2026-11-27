# Headless reply drafting: Claude Code runs /draft-replies on the latest leads, then send-drafts.
# Used by the Radar_Drafts scheduled task (19:30 IST, so drafts are ready for the 20:00 reply session).
#
# - Skips drafting when data\leads\latest.json has no undrafted leads.
# - If `claude` fails quickly (e.g. a network blip) it is retried once after 60 seconds.
# - If `claude` is missing, fails twice or runs past 10 minutes: sends
#   "Drafts skipped: run /draft-replies manually" to Telegram.
# - send-drafts runs at the end in every case, so drafts that failed to reach Telegram earlier
#   (saved in data\delivery_failures\) and any drafts file not sent yet always go out.
# - Always exits 0 so Task Scheduler doesn't mark the task as broken.

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
$ClaudeOut = Join-Path $LogDir "claude_drafts.log"
$ClaudeErr = Join-Path $LogDir "claude_drafts.err.log"
$TimeoutMs = 10 * 60 * 1000
$SkipMessage = "Drafts skipped: run /draft-replies manually"

function Write-TaskLog([string]$Message) {
    Add-Content -Path $TaskLog -Encoding UTF8 -Value ("{0} drafts: {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture), $Message)
}

function Send-SkipNotice([string]$Reason) {
    Write-TaskLog "skipped ($Reason)"
    & $Python -m radar notify --text $SkipMessage | Out-Null
}

# Delivers queued drafts/messages and any unsent drafts file. Runs on every exit path.
function Send-Drafts {
    & $Python -m radar send-drafts
    Write-TaskLog "send-drafts exit $LASTEXITCODE"
}

# Runs claude once. Returns "ok", "timeout", or the failure reason.
function Invoke-Claude($claudePath) {
    $claudeArgs = @("-p", '"/draft-replies data/leads/latest.json"', "--allowedTools", '"Read,Write"')
    try {
        $proc = Start-Process -FilePath $claudePath -ArgumentList $claudeArgs -WorkingDirectory $Root `
            -NoNewWindow -PassThru -RedirectStandardOutput $ClaudeOut -RedirectStandardError $ClaudeErr
    } catch {
        return ("claude failed to start: " + $_.Exception.Message)
    }
    if (-not $proc.WaitForExit($TimeoutMs)) {
        try { $proc.Kill() } catch { }
        return "timeout"
    }
    if ($proc.ExitCode -ne 0) { return ("claude exit code " + $proc.ExitCode) }
    return "ok"
}

# 1. Anything to draft?
$pendingText = (& $Python -m radar drafts-pending | Select-Object -Last 1)
$pending = 0
if (-not [int]::TryParse(("$pendingText").Trim(), [ref]$pending)) { $pending = 0 }
if ($pending -le 0) {
    Write-TaskLog "no undrafted leads in latest.json"
    Send-Drafts
    exit 0
}

# 2. Find the Claude Code CLI (npm installs claude.cmd, the native installer claude.exe).
$claude = Get-Command claude.exe, claude.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $claude) {
    Send-SkipNotice "claude not found on PATH"
    Send-Drafts
    exit 0
}

# 3. Run /draft-replies headless, with only Read and Write allowed, and a 10 minute limit.
$outcome = Invoke-Claude $claude.Source
if ($outcome -ne "ok" -and $outcome -ne "timeout") {
    Write-TaskLog "first claude attempt failed ($outcome); retrying in 60s"
    Start-Sleep -Seconds 60
    $outcome = Invoke-Claude $claude.Source
}
if ($outcome -eq "ok") {
    Write-TaskLog ("claude finished: " + ((Get-Content $ClaudeOut -Tail 1 -ErrorAction SilentlyContinue) -join " "))
} elseif ($outcome -eq "timeout") {
    Send-SkipNotice "claude timed out after 10 minutes"
} else {
    Send-SkipNotice $outcome
}

# 4. Validate and deliver the drafts (and anything queued from earlier failed sends).
Send-Drafts
exit 0
