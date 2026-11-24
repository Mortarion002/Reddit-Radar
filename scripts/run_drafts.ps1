# Headless reply drafting: Claude Code runs /draft-replies on the latest leads, then send-drafts.
# Used by the Radar_Drafts scheduled task (09:00 and 21:00 IST).
#
# - Skips when data\leads\latest.json has no undrafted leads.
# - If `claude` is missing, fails or runs past 10 minutes: sends
#   "Drafts skipped: run /draft-replies manually" to Telegram and exits 0.

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

# 1. Anything to draft?
$pendingText = (& $Python -m radar drafts-pending | Select-Object -Last 1)
$pending = 0
if (-not [int]::TryParse(("$pendingText").Trim(), [ref]$pending)) { $pending = 0 }
if ($pending -le 0) {
    Write-TaskLog "no undrafted leads in latest.json"
    exit 0
}

# 2. Find the Claude Code CLI (npm installs claude.cmd, the native installer claude.exe).
$claude = Get-Command claude.exe, claude.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $claude) {
    Send-SkipNotice "claude not found on PATH"
    exit 0
}

# 3. Run /draft-replies headless, with only Read and Write allowed, and a 10 minute limit.
$claudeArgs = @("-p", '"/draft-replies data/leads/latest.json"', "--allowedTools", '"Read,Write"')
try {
    $proc = Start-Process -FilePath $claude.Source -ArgumentList $claudeArgs -WorkingDirectory $Root `
        -NoNewWindow -PassThru -RedirectStandardOutput $ClaudeOut -RedirectStandardError $ClaudeErr
} catch {
    Send-SkipNotice ("claude failed to start: " + $_.Exception.Message)
    exit 0
}
if (-not $proc.WaitForExit($TimeoutMs)) {
    try { $proc.Kill() } catch { }
    Send-SkipNotice "claude timed out after 10 minutes"
    exit 0
}
if ($proc.ExitCode -ne 0) {
    Send-SkipNotice ("claude exit code " + $proc.ExitCode)
    exit 0
}
Write-TaskLog ("claude finished: " + ((Get-Content $ClaudeOut -Tail 1 -ErrorAction SilentlyContinue) -join " "))

# 4. Validate and deliver the drafts.
& $Python -m radar send-drafts
$code = $LASTEXITCODE
Write-TaskLog "send-drafts exit $code"
exit 0
