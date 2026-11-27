# Reddit Radar

Reddit buying-signal monitor for Elvan (elvan.ai). It finds Reddit posts and comments where someone
is shopping for, unhappy with, or struggling without an NPS/CSAT/feedback tool, and gets each one to
Telegram the same day with a reply draft. **A human always posts the reply.** This project never logs
in to Reddit, never fetches Reddit pages, and never posts, votes or DMs.

## Why F5Bot

Reddit shut down its public RSS feeds on Nov 13, 2026, stopped accepting new API access requests on
Oct 31, 2026, and blocks unauthenticated `.json` access. So Reddit Radar never talks to Reddit. Its
source is [F5Bot](https://f5bot.com), a free service that emails keyword alerts for new Reddit posts
and comments. Reddit Radar reads those emails from a dedicated Gmail inbox.

```
F5Bot -> Gmail -> radar scan (09:00, 13:00, 17:00, 19:15): IMAP -> parse -> score -> SQLite -> Telegram alerts + digest
                                                                   -> data/leads/latest.json
       run_drafts.ps1 (19:30): claude -p "/draft-replies" -> data/drafts/*.json -> radar send-drafts
       radar feedback (09:05, 19:20, 23:30): your [Replied] [Skip] [Not relevant] taps -> lead status
```

## Setup

All commands are PowerShell, from the repo folder.

### 1. Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q
```

### 2. `.env`

Copy `.env.example` to `.env` if you don't have one. A `.env` with `REPLACE_ME` placeholders is
already there. Anything still set to `REPLACE_ME` counts as missing, and nothing is sent anywhere until you fill it in.
`.env` is gitignored. Never commit it or paste its values into chat.

```powershell
python -m radar config-check   # shows what's set, secrets masked
```

### 3. Telegram bot

1. In Telegram, open @BotFather, send `/newbot`, and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number at
   `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`.
4. `python -m radar telegram-test` should deliver "Reddit Radar connected".

If your ISP's DNS doesn't resolve `api.telegram.org` (this PC's ISP doesn't), the app automatically
resolves it through 1.1.1.1 and sends with `curl`.

### 4. Gmail + F5Bot

Follow `docs/F5BOT_SETUP.md`: a dedicated Gmail with 2-step verification, an app password, IMAP on,
an F5Bot account with the keyword list, then:

```
IMAP_USER=<the gmail address>
IMAP_APP_PASSWORD=<16-character app password>
F5BOT_SENDER=<exact From address of a real F5Bot email>
```

Save 3 to 5 real F5Bot emails as `.eml` into `tests/fixtures/f5bot/` and check the parser:

```powershell
python -m radar ingest-eml --dir tests/fixtures/f5bot --dry-run
python -m pytest -q tests/test_f5bot_parser.py
```

### 5. Start clean, then go live

The placeholder setup leaves test messages in `data/delivery_failures/`, which the first live scan
would resend to your phone. Before the first real run:

```powershell
Remove-Item -Recurse -Force data
python -m radar doctor          # every line should say PASS
python -m radar scan
```

### 6. Schedule it

Claude Code CLI must be installed and logged in (`claude --version` works in PowerShell). Then, in an
**elevated** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
```

Built around replying once a day at 20:00 IST. To change the times, edit
`scripts\register_tasks.ps1` and run it again as Administrator; it replaces the tasks in place.
Feedback must run at least once every 24 hours, because Telegram only keeps button taps that long.

| Task | Runs | When (IST) |
|---|---|---|
| `Radar_Scan` | `scripts\run_scan.ps1` | 09:00, 13:00, 17:00, 19:15 |
| `Radar_Drafts` | `scripts\run_drafts.ps1` | 19:30 (drafts ready before 20:00) |
| `Radar_Feedback` | `scripts\run_radar.ps1 feedback` | 09:05, 19:20, 23:30 |
| `Radar_Weekly_Stats` | `scripts\run_radar.ps1 stats --days 7` | Fri 18:00 |

Tasks run as you (Claude Code needs your session), wake the PC, and catch up if a start was missed.
They only run while the PC is on or able to wake, so check your sleep settings.
To remove them: `Get-ScheduledTask Radar_* | Unregister-ScheduledTask -Confirm:$false`.

## Commands

Every command accepts `--dry-run` (no Telegram sends, no DB writes, no mailbox changes) and `-v`.

| Command | What it does |
|---|---|
| `python -m radar scan` | Read new F5Bot emails, score, alert, digest, export leads |
| `python -m radar ingest-eml --dir PATH` | Same, from saved `.eml` files (parser debugging) |
| `python -m radar import --file PATH` | Same, from a mentions JSON file (testing, manual adds) |
| `python -m radar leads [--tier High,Medium] [--days 3]` | Print leads as a table |
| `python -m radar rescore --days 7` | Re-score stored mentions with the current `signals.yaml` |
| `python -m radar send-drafts [--file PATH]` | Validate and send reply drafts |
| `python -m radar feedback` | Apply Telegram button taps and `/stats` `/pause` `/resume` |
| `python -m radar stats [--days 7] [--no-send]` | Weekly report, printed and sent to Telegram |
| `python -m radar doctor` | Pass/fail health check |
| `python -m radar config-check` | Show settings, secrets masked |
| `python -m radar telegram-test` | Send a test message |
| `python -m radar housekeeping` | Delete old leads/drafts (30 days) and unparsed emails (14 days) |

In Telegram you can also send the bot `/stats`, `/pause` (scans skip until resumed) and `/resume`.

## Daily routine

- Reply session: 20:00 IST. The 19:15 scan, 19:20 feedback and 19:30 drafting run first, so
  drafts for the day's leads are on Telegram by about 19:45.
- Hot alerts and digests arrive after each scan (09:00, 13:00, 17:00, 19:15). Fewer scans lose
  nothing: each one picks up every F5Bot email since the last.
- Open the thread, edit the draft, and post it yourself from the disclosed Elvan-team account.
- Tap **Replied**, **Skip** or **Not relevant**. Tapping Replied on a *draft* message counts it as
  posted, which feeds the 1-in-5 Elvan mention limit.
- Warm lead (asking about Elvan, or replying to you)? Slack Ravi the link the same day.

## Drafting replies

1. Make sure there are leads: `python -m radar scan` (or `import`) writes `data/leads/latest.json`.
2. In Claude Code, run `/draft-replies` (optionally with a path: `/draft-replies data/leads/2026-11-25_0900.json`).
   It follows `docs/REPLY_RULES.md` and writes `data/drafts/<stamp>.json`.
3. Run `python -m radar send-drafts`. It picks the newest drafts file not sent yet, checks every
   draft against the rules, saves it in the DB and sends it to Telegram in a copyable block with
   [Replied] [Skip] [Not relevant] buttons.
   - A draft that breaks a rule (em dash, emoji, hashtag, length, missing disclosure, a link,
     a competitor price...) still arrives, prefixed `NEEDS EDIT: <issues>`.
   - If too many drafts name Elvan (more than 1 in 5 replies over 7 days), the extras arrive as
     `NEEDS EDIT: over mention ratio, remove Elvan`.
   - Leads Claude skipped (empty draft) aren't sent; the reason is logged.
   - Use `--file PATH` to send a specific file and `--dry-run` to preview without sending.
4. Open the thread, edit the draft, post it yourself, then tap a button.

Scheduled runs do steps 2 and 3 with
`claude -p "/draft-replies data/leads/latest.json" --allowedTools "Read,Write"` (10-minute limit).
Wording changes belong in `docs/REPLY_RULES.md`, not in the code.

## When Telegram can't be reached

Nothing is lost if the internet or Telegram is down when the radar sends something:

1. Each message is retried once after 5 seconds. If your ISP can't look up Telegram's address, the
   app resolves it through 1.1.1.1 and sends with `curl`.
2. If it still fails, the message (alert, digest, draft or warning, with its buttons) is saved to
   `data\delivery_failures\`. The rest of that run doesn't wait on the network again; it saves
   straight away, so an outage can't make a task run past its time limit.
3. **Every** command that sends (`scan`, `send-drafts`, `feedback`, `stats`, `telegram-test`) first
   resends everything waiting there, oldest first. A draft that failed at 19:30 goes out with the
   next drafts, at the 23:30 feedback run, or at the next scan, whichever comes first. Once you're
   back online, `python -m radar telegram-test` delivers the backlog straight away.
4. A resent draft keeps working buttons: its Telegram message id is recorded when it finally arrives.

Other safeguards:
- Two runs at the same time never send the same saved message twice (each file is claimed first).
- `send-drafts` sends every drafts file that hasn't gone out yet, not only the newest, and never sends
  the same draft twice. Files older than 72 hours are skipped (their leads have expired).
- `run_drafts.ps1` retries Claude once after a quick failure, and always ends with `send-drafts`, so
  queued drafts go out even on days with nothing new to draft.
- Telegram rate limits (when a backlog is resent) are waited out, and resends are spaced slightly.
- A message Telegram rejects outright (HTTP 400) moves to `data\delivery_failures\dead\` so
  it can't block the queue. `python -m radar doctor` lists waiting and refused messages.
- Button taps are safe for 24 hours (Telegram keeps them); feedback runs never leave a longer gap.

## Tuning (Fridays, first 2 weeks)

Read the stats message (`python -m radar stats`):

- **F5Bot keywords** (`mentions | sent | not relevant | noise`): drop keywords on f5bot.com with a
  high not-relevant rate, or high noise (lots of mentions that never score High/Medium).
- **Top terms behind not-relevant leads**: tune those in `config/signals.yaml`.
- After editing `signals.yaml`: `python -m radar rescore --days 14`, then
  `python -m radar leads --tier High,Medium --days 14` to see the effect. Rescore never touches your taps.
- Subreddit weights and the blocklist live in `config/subreddits.yaml`.

## Troubleshooting

| Problem | Check |
|---|---|
| No emails / no leads | `python -m radar doctor`: "F5Bot emails in last 7 days" failing means F5Bot isn't sending, or `F5BOT_SENDER` doesn't match the exact From address. Look in Gmail for the alerts. Emails that failed to parse are in `data/unparsed/`. |
| IMAP auth failed | Use a Gmail **app password** (needs 2-step verification), not the account password. Check IMAP is on in Gmail settings. You get one Telegram warning per 6 hours while the inbox is unreachable. |
| Nothing on Telegram | `python -m radar telegram-test` (it also delivers anything waiting). Undelivered messages wait in `data/delivery_failures/` and are resent by the next scan, send-drafts or feedback run. `python -m radar doctor` shows how many are waiting, and any Telegram refused (`data/delivery_failures/dead/`). Check `TELEGRAM_CHAT_ID` is your chat with the bot. |
| `claude` not found | Install Claude Code CLI and log in so `claude --version` works in PowerShell (the VS Code extension alone isn't enough). Until then you get "Drafts skipped: run /draft-replies manually". Claude's output is in `data/logs/claude_drafts.log`. |
| Scans not running | Task Scheduler -> `Radar_Scan` -> History. Task exit codes go to `data/logs/tasks.log`; app logs to `data/logs/radar.log`. Is the bot paused? Send `/resume`. |

## Layout

`radar/` app code, `config/` signals and subreddits, `scripts/` PowerShell wrappers and task
registration, `docs/` spec, reply rules, progress log and build stages, `tests/` offline tests and
fixtures, `data/` (gitignored) DB, logs, leads, drafts, failed sends, unparsed emails.
