# CLAUDE.md

Instructions for Claude Code working in this repo. Read this file fully before any task.

## What this project is

**Reddit Radar**: a Reddit buying-signal monitor for Elvan (elvan.ai), a B2B NPS/CSAT/CES survey SaaS.

Reddit shut down its public RSS feeds on Nov 13, 2026, stopped accepting new API access requests on Oct 31, 2026, and has blocked unauthenticated `.json` access since mid-2026. So this project **never talks to Reddit directly**. Its source is **F5Bot** (f5bot.com), a free service that emails keyword alerts for new Reddit posts and comments.

Every run it:

1. reads new F5Bot alert emails from a dedicated Gmail inbox over IMAP
2. parses each alert into a `Mention` (a Reddit post or comment, with its title, snippet, URL and the matched keyword)
3. scores each mention for buying intent and competitor pain
4. stores everything in SQLite
5. sends hot-lead alerts and a digest to Aman's Telegram
6. writes a leads file, which Claude Code (`/draft-replies`) turns into reply drafts that go back to Telegram
7. records Aman's Telegram button taps (Replied / Skip / Not relevant) to tune scoring

**A human always posts the reply on Reddit.** This project never logs in to Reddit, never fetches Reddit pages, and never posts, votes or DMs.

Full spec: `docs/PROJECT_SPEC.md`. Reply voice rules: `docs/REPLY_RULES.md`. Build plan: `docs/stages/`.

## How we build: one stage at a time

The stages are `docs/stages/stage-01-*.md` to `stage-07-*.md`. When Aman says "build stage N":

1. Read `CLAUDE.md`, `docs/PROJECT_SPEC.md`, `docs/PROGRESS.md` and only the stage file for N.
2. Do only what that stage file lists. Do not start the next stage, and do not refactor earlier stages unless the stage file says to.
3. Write the tests the stage asks for. Run `python -m pytest -q` and make sure everything passes, including earlier stages' tests.
4. Show Aman how to run the stage's manual check.
5. Tick the acceptance checklist in the stage file. Update `docs/PROGRESS.md` with the stage number, date, what was built and any deviation from the spec.
6. Ship it with the git workflow below, using the commit message given in the stage file.

If the spec and the stage file disagree, the stage file wins for that stage. Note the conflict in `docs/PROGRESS.md`.

## Git workflow (every change, not just stages)

Aman's standing rule (2026-11-27): never commit straight to `main`.

1. Start from an up-to-date `main` (`git switch main && git pull`), then create a branch named for the change: `feat/...` (new behaviour), `fix/...` (bug fix), `docs/...`, `chore/...` (config, scripts, cleanup), `test/...`. Short and descriptive, e.g. `fix/f5bot-comment-links`.
2. Commit the work on that branch and push it: `git push -u origin <branch>`.
3. Run `python -m pytest -q`. Every test must pass.
4. Only then merge into `main` with `git merge --no-ff <branch>` and push `main`. If anything fails, fix it on the branch first; `main` only ever receives passing code.
5. Never commit `.env`, `data/` or unredacted real emails (redact the F5Bot unsubscribe key).

## Stack

- Python 3.11+, running on Windows 11 (PowerShell). Code must also run on Linux for tests.
- Dependencies: `requests`, `python-dotenv`, `PyYAML`, `pytest`. Email uses the standard library only (`imaplib`, `email`, `html.parser`). Ask before adding anything else.
- No LLM SDKs in the Python code. Reply drafting is done by Claude Code itself (stage 5), not by an API call from Python.
- Storage: SQLite at `data/radar.db`.
- Delivery: Telegram Bot API over plain HTTPS (`requests`).

## Layout (target)

```
radar/
  __init__.py
  __main__.py
  config.py          # loads .env + config/*.yaml into a Settings dataclass
  logger.py
  models.py          # Mention, Lead, Draft dataclasses
  sources/
    f5bot_mail.py    # IMAP reader (stage 4)
    f5bot_parser.py  # F5Bot email -> list[Mention] (stage 4)
    sample.py        # load mentions from a JSON file (stage 2)
  scorer.py          # signal classification + scoring
  store.py           # SQLite
  telegram.py        # send, split, preserve failures, inline buttons
  digest.py          # message formatting
  export.py          # leads file for Claude Code
  drafts.py          # draft validation + send
  feedback.py        # Telegram callback listener
  cli.py             # entry points
config/
  signals.yaml       # keywords, competitors, phrases, weights
  subreddits.yaml    # subreddit bonuses and blocklist
docs/
  PROJECT_SPEC.md  REPLY_RULES.md  PROGRESS.md  F5BOT_SETUP.md  stages/
.claude/commands/
  draft-replies.md
data/                # gitignored: radar.db, leads/, drafts/, delivery_failures/, logs/
scripts/             # run_scan.ps1, run_drafts.ps1, register_tasks.ps1
tests/fixtures/
```

## Hard rules

- **Never request anything from reddit.com.** No RSS, no `.json`, no OAuth, no PRAW, no HTML scraping, no Selenium, no cookies. Reddit data comes only from F5Bot emails. Reddit URLs are stored and shown, never fetched.
- **The mailbox is read-only except for marking processed mail.** Track processed emails by IMAP UID in the DB. Optionally mark them seen or apply a label. Never delete or send email.
- **Never post, vote, comment or DM on Reddit.** Not even behind a flag.
- **Failures degrade, they don't erase.** A failed IMAP connection, a failed Telegram send or a failed draft run must never delete collected data. A failed Telegram send writes the message to `data/delivery_failures/`. An email that fails to parse is logged and saved to `data/unparsed/`, and the run continues.
- **Network calls are never made in tests.** Use `tests/fixtures/` and mocks for `imaplib` and `requests`.
- **Secrets live only in `.env`**: the Gmail app password and the Telegram token. `.env`, `data/` and `*.db` are gitignored. Never print secrets in logs.
- Every CLI command supports `--dry-run`, which means no Telegram sends, no DB writes and no mailbox changes.
- Keep functions small and typed. Use dataclasses for records. No global mutable state.
- Log to `data/logs/radar.log` with rotation, plus the console.

## Elvan context the code depends on

- Elvan sells NPS, CSAT, CES, eNPS, PMF, 5-Star, Thumbs and Smiley surveys with AI summaries in plain English. Free plan: 100 responses/month. Pro: $49/month for 1,000 responses. Native Zendesk integration (on the Zendesk Marketplace), Web SDK, embeds, email via CSV.
- Honest gaps: max 2 questions per survey, no in-app targeting, no MSP integrations (ConnectWise, Autotask). Never claim features beyond this; say "check with Balram" instead.
- Delighted (Qualtrics) shut down on June 30, 2026. Always refer to it in the past tense.
- Priority competitors: Survicate, SurveySparrow, Simplesat. Also tracked: Qualtrics, Medallia, AskNicely, Retently, Zonka, Typeform, SurveyMonkey, Hotjar, Delighted, Nicereply, Zendesk's built-in CSAT.
- Do not flag as leads: MSPs, staffing agencies, companies with 1,000+ employees, posts promoting the poster's own tool, job posts.

## Writing rules for anything Aman will read or post

No em dashes. No emojis in reply drafts. No hashtags. Write "->" rather than a unicode arrow. Keep it short and casual. Full Reddit rules are in `docs/REPLY_RULES.md`.
