# Stage 7: Scheduling, headless drafting and ops

## Goal
Everything runs by itself on Aman's Windows PC: scans every 30 minutes, drafts twice a day, feedback every 15 minutes, stats on Fridays.

## Before starting (Aman)
- Claude Code CLI is installed and logged in: `claude --version` works in PowerShell. The VS Code extension alone isn't enough for scheduled runs.
- Check your PC's sleep settings. Tasks only run while the PC is on or can wake.

## Build
1. **`scripts/run_scan.ps1`**: activates `.venv`, runs `python -m radar scan`, logs the exit code.
2. **`scripts/run_drafts.ps1`**:
   - skips if `latest.json` has no undrafted leads
   - runs `claude -p "/draft-replies data/leads/latest.json" --allowedTools "Read,Write"`; check the flags against `claude --help` and record the exact command in PROGRESS.md
   - then runs `python -m radar send-drafts`
   - if `claude` is missing or fails: send the Telegram line "Drafts skipped: run /draft-replies manually" and exit 0
   - 10-minute timeout on the claude call
3. **`scripts/register_tasks.ps1`** (run as Administrator): the 4 tasks from spec section 12, with "wake the computer" on and "run as soon as possible if missed" on. Tasks run as the logged-in user, because Claude Code needs the user session.
4. **The `python -m radar doctor` command** checks:
   - `.env` is present
   - Telegram is reachable
   - an IMAP login works
   - at least one F5Bot email arrived in the last 7 days (a sign that F5Bot is still sending)
   - the DB is writable
   - `claude` is on the PATH

   It prints pass/fail for each.
5. **Housekeeping**: rotate the logs; delete leads and drafts files older than 30 days and `data/unparsed/` files older than 14 days. The DB keeps everything.
6. **`README.md`**:
   - what it does, and why F5Bot (with the Reddit RSS shutdown note)
   - setup: venv, `.env`, BotFather, Gmail app password, F5Bot
   - commands and the daily routine
   - tuning `signals.yaml` with `rescore`, and pruning F5Bot keywords using `stats`
   - troubleshooting: no emails, IMAP auth, no Telegram, `claude` not found

## Tests
- `test_doctor.py` with mocks.
- `test_housekeeping.py`.

## Manual check
Run `register_tasks.ps1` as Administrator. In Task Scheduler, run `Radar_Scan` and `Radar_Drafts` by hand. Alerts, the digest and drafts should arrive with no terminal involved. Then run `python -m radar doctor`; everything should pass.

## Acceptance
- [ ] One full unattended cycle works
- [ ] A missing `claude` degrades to a Telegram note
- [ ] README complete
- [ ] Tests pass
- [ ] `docs/PROGRESS.md` updated, with a known-issues list

Commit: `stage 7: scheduling, headless drafting, doctor and readme`

## After stage 7
For the first 2 weeks, review `radar stats` every Friday:
- Drop F5Bot keywords with a high not-relevant rate.
- Tune `signals.yaml` and run `rescore --days 14`.
- Update `REPLY_RULES.md` based on how you edit drafts.
