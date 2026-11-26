# How to build Reddit Radar with Claude Code

## The loop for each stage

1. Start a fresh Claude Code chat (or `/clear`).
2. Paste, changing the number and the file name:
   ```
   Build stage N. Read CLAUDE.md, docs/PROJECT_SPEC.md, docs/PROGRESS.md and docs/stages/stage-0N-*.md.
   Old code to port from is in reference/ (if present). Don't commit.
   ```
3. When it's done, run `python -m pytest -q` yourself.
4. Do the stage's **Manual check** and anything under **Before starting (Aman)**.
5. If something's off, tell it what to fix in the same chat.
6. When it's good: `commit and push with the stage's commit message`.
7. Before every push, run `git status` and make sure `.env` and `data/` are not listed.

## What you provide, and when

| What | Stage |
|---|---|
| Nothing (just review) | 1, 2 |
| Telegram bot token + chat ID | 3 |
| F5Bot set up, Gmail app password, 3 to 5 real F5Bot emails saved as `.eml` (see `docs/F5BOT_SETUP.md`) | 4 |
| Balram's answer on which integrations exist | 5 |
| Claude Code CLI logged in on your PC | 7 |

## Daily routine once it's built

- Alerts arrive after each scan (09:00, 13:00, 17:00, 19:15 IST). Drafts arrive by about 19:45 IST, ready for the 20:00 reply session.
- Open the thread, edit the draft, and post it yourself from the disclosed Elvan-team account.
- Tap **Replied**, **Skip** or **Not relevant**.
- Warm lead (asking about Elvan, or replying to you)? Slack Ravi the link the same day.
- Fridays: read the stats message, drop noisy keywords on f5bot.com, and tune `config/signals.yaml`.
