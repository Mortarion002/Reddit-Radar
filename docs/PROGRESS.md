# Progress Log

Claude Code updates this after each stage. Record the date, what was built, any deviation from the spec, and open questions.

| Stage | Status | Date | Notes |
|---|---|---|---|
| 1 Setup, config, models | done | 2026-11-25 | Skeleton, Settings, redacting logger, Mention + URL parsing, `config-check`. |
| 2 Storage + scoring | done (awaiting Aman's leads review) | 2026-11-25 | SQLite store with migrations, scorer, `import` / `leads` / `rescore`, 26-mention synthetic sample. |
| 3 Telegram + export | done (real Telegram check pending) | 2026-11-25 | Telegram client with failure preservation + DNS fallback, alerts/digest, leads export, shared pipeline, `telegram-test`. |
| 4 F5Bot email ingest | done, parser fixed against 3 real `.eml` (live scan pending) | 2026-11-26 | IMAP reader, format-tolerant parser, `scan`, `ingest-eml`, throttled inbox warning, pause check. |
| 5 Claude reply drafts | done (live Telegram check pending) | 2026-11-25 | Draft validation, mention ratio, `send-drafts`, README drafting section. |
| 6 Feedback + stats | done (real taps check pending) | 2026-11-25 | Button taps -> lead status, /stats /pause /resume, weekly stats with per-keyword quality, ratio from tap data. |
| 7 Scheduling + ops | done (unattended cycle check pending) | 2026-11-25 | PowerShell wrappers, task registration, headless drafting, `doctor`, housekeeping, README. |

## Background

Reddit shut down public RSS feeds on Nov 13, 2026, stopped accepting new API access requests on Oct 31, 2026, and blocks unauthenticated `.json` access. This project therefore reads F5Bot email alerts and never contacts Reddit directly.

## Stage notes

### Stage 1 (2026-11-25)
- Built: `radar/config.py` (Settings from `.env` + YAML, `zoneinfo()`), `radar/logger.py` (console + rotating `data/logs/radar.log`, redaction of the bot token incl. `bot<id>:<token>` in URLs, and the IMAP app password), `radar/models.py` (`Mention`, `Lead`, `Draft`, `parse_reddit_url`, `Mention.from_url`, `short_id`), `radar/cli.py` with `config-check`, `.env.example`, a local `.env` with `REPLACE_ME` placeholders (gitignored), `pytest.ini`.
- Any value containing `REPLACE_ME` counts as missing, so the placeholder `.env` never triggers real network calls.
- Every subcommand accepts `--dry-run` and `-v` via a shared parent parser.

### Stage 2 (2026-11-25)
- Built: `radar/store.py` (all spec tables, numbered migrations tracked in `state.schema_version`, dry-run opens an in-memory copy so nothing is written), `radar/scorer.py` (`normalize`, `contains`, `classify`, `score`, `evaluate`, `build_lead`), `radar/sources/sample.py` (JSON loader with `-2h`-style offsets), CLI `import`, `leads`, `rescore`, `tests/fixtures/mentions_sample.json` (26 synthetic mentions).
- 31 scorer tests (all 20 spec cases plus extras), store and import tests.
- Sample import result: High 11, Medium 7, Low 3, Dropped 5. **Aman still needs to read `python -m radar leads --tier High,Medium` and say whether he'd reply to these.**

### Stage 3 (2026-11-25)
- Built: `radar/telegram.py` (`send`, split at 4,000 chars with buttons on the last chunk, retry once after 5s, preserve to `data/delivery_failures/{ts}_{kind}.txt`, `resend_failures`), `radar/digest.py` (alert, digest, run summary, `[Replied] [Skip] [Not relevant]` buttons with `s:{status}:{short_id}`), `radar/export.py` (`data/leads/YYYY-MM-DD_HHMM.json` + `latest.json`, cap 10), `radar/pipeline.py` (`process`: resend -> store + score -> hot alerts with the 24h thread rule -> digest -> export), CLI `telegram-test`; `import` now runs the pipeline.
- Schema v2 adds a `short_ids(short_id, mention_id)` table; `short_id` is the first 10 hex chars of sha1(mention_id). Older DBs are backfilled on open.
- **DNS fallback: ported.** On this PC the ISP DNS (powertel) returns NXDOMAIN for `api.telegram.org`, while 1.1.1.1 resolves it. When a plain `requests` call fails with a DNS error, the client switches to `nslookup api.telegram.org 1.1.1.1` + `curl --resolve` for the rest of the run. JSON goes in over stdin, and the token is only in the URL argument, never logged.
- Preserved files start with a `RADAR-META {json}` line holding the kind and buttons, so a resent alert keeps its buttons. Plain hand-written `.txt` files are resent as-is.
- If Telegram rejects the HTML ("can't parse entities"), the message is resent once as plain text.
- With the placeholder `.env`, alerts and digests go straight to `data/delivery_failures/` without a network call. **Before going live, delete `data/` (or at least `data/delivery_failures/`) so test messages aren't resent to your phone.**
- Tests run fully offline: `tests/conftest.py` has an autouse guard that blocks `requests`, sockets and `curl`/`nslookup`/`claude` subprocesses.

### Stage 4 (2026-11-25)
- Built: `radar/sources/f5bot_parser.py` (`parse_email`, `ParseError`, redirect unwrapping), `radar/sources/f5bot_mail.py` (`Mailbox`: `IMAP4_SSL` -> login -> select -> `UID SEARCH FROM "<sender>" SINCE <date>` -> `BODY.PEEK[]` -> `+FLAGS (\Seen)` after success), `radar/ingest.py` (`run_scan`, `run_ingest_eml`, unparsed handling, 6-hour throttled "F5Bot inbox unreachable" warning, pause check), CLI `scan` and `ingest-eml`. Schema unchanged.
- **No real F5Bot emails were available.** The stage file says not to start without them; Aman asked for the whole project built with placeholder data, so the parser was built to be format-tolerant and tested on **synthetic** emails in `tests/fixtures/f5bot_synthetic/`, which follow the best-known F5Bot layout. They are not real F5Bot output.
- **F5Bot format assumed by the synthetic fixtures** (superseded by the real format in the 2026-11-26 fix below; the synthetic emails are kept as extra parser tests):
  - Subject: `F5Bot found something: <keyword>` (used only as the keyword fallback)
  - From: `F5Bot <admin@f5bot.com>` (`.env` keeps `F5BOT_SENDER=REPLACE_ME` until you copy the real address)
  - multipart/alternative, HTML preferred, plain text fallback
  - sections headed `Reddit Posts (/r/sub/):`, `Reddit Comments (/r/sub/):`, `Hacker News Comments:`
  - each match: `<a href="https://f5bot.com/url?u=<urlencoded reddit permalink>&i=..&h=..">thread title</a>`, then `Keyword: <b>kw</b>`, then a `<blockquote>` snippet
  - comments: link text may start `Re: `; the URL has the comment id as the 6th path segment
  - footer with settings/unsubscribe links on f5bot.com
- What the parser actually relies on: the order of links. Each link to a Reddit permalink (after unwrapping `?u=` / `?url=` / `?q=` style redirects) starts a match: link text = title, the text up to the next link = snippet, and an optional `Keyword:` line. HN/Lobsters links are skipped (logged at debug). Other links end the current match. Section headers, footer lines and `--` separators are stripped from snippets.
- **When real emails arrive:** drop them in `tests/fixtures/f5bot/`, run `python -m radar ingest-eml --dir tests/fixtures/f5bot --dry-run`, and check the titles and snippets. `tests/test_f5bot_parser.py::test_real_fixture_parses` runs on every real file automatically, and `expected.json` (see the README there) pins exact counts and fields.

### Stage 4 fix: real F5Bot emails (2026-11-26)
- **Bug:** `ingest-eml` on the 3 real fixtures found 4 hits (the posts) out of 11; all 7 comments were dropped silently. Two causes:
  1. F5Bot comment links unwrap to `/r/{sub}/comments/{post_id}/c/{comment_id}?context=3` (a literal `c` instead of the slug).
  2. Real comment ids are 13 characters (`19t5tnte5nfsw`), and the id check only allowed 2 to 12. This alone rejected every comment; the parser then treated the link as "not a hit" and moved on without a word.
- **Fixes:**
  - `parse_reddit_url` accepts `/comments/{post_id}/c/{comment_id}` (plus `.../comment/{id}` and the normal `.../{slug}/{comment_id}/`), allows ids of 2 to 20 characters, and drops the query string. Comment URLs are stored canonically as `https://www.reddit.com/r/{sub}/comments/{post_id}/_/{comment_id}/`.
  - No hit is skipped silently any more. A link that looks like a hit (an F5Bot `?u=` redirect, or any reddit.com URL) but doesn't parse is logged as a warning with the URL and returned in `ParseResult.failed_hits` (new `parse_email_result`; `parse_email` still returns just the mentions). `ingest` counts these in `RunSummary.skipped_hits`, records each URL in `runs.errors`, marks the email `partial` in `processed_emails`, and saves a copy to `data/unparsed/`. The CLI summary and the Telegram digest show "Skipped hits: N", and a run with skipped hits always sends the digest. An email where every hit fails raises `ParseError` carrying the URLs.
  - Real layout handled: the `Keyword: "csat"` `<h2>` heading applies to the hits below it; the `by <author>` line after the link fills `author`; the `Keyword was found in submission title.` line is dropped from snippets; F5Bot's own text after the last hit ("Do you have comments or suggestions about F5Bot?", the ad block, the footer) ends the current hit, so the sponsor ad copy no longer ends up in the last hit's snippet (it did in `csat (1).eml` and `csat (2).eml`). The plain-text body's `Reddit Posts (/r/x/): 'Title' by author` lines are understood too (fallback only).
  - `config/subreddits.yaml` blocklist: `upsc, upscbharat, upsccritics, gpsc, romania, romania2, bolehland, separation_anxiety, fittheword`.
- **Real F5Bot format** (replaces the assumed format below):
  - From `F5Bot Alerts <alerts@f5bot.com>`, so set `F5BOT_SENDER=alerts@f5bot.com`. Subject `F5Bot found something: <keyword>`.
  - multipart/alternative (text + HTML). HTML: `<h2>Keyword: "csat"</h2>`, then one `<p class="f5-hit">` per hit: `<span>Reddit Posts (/r/UPSC/):</span> <a href="https://f5bot.com/url?u=<encoded URL>&i=..&h=..">thread title</a> <span>by Spare-upsc-80</span><br><span class="f5-excerpt"><strong>Keyword was found in submission title.</strong><br>excerpt...</span>`. Comments carry the thread title as link text.
  - Posts: `/r/{sub}/comments/{post_id}/{slug}/`. Comments: `/r/{sub}/comments/{post_id}/c/{comment_id}?context=3`.
  - A link post with the keyword only in its title has an empty excerpt (e.g. `post:1y56d7z`).
  - After the hits: "Do you have comments or suggestions about F5Bot?", a sponsor block (`f5bot.com/url?a=..`, no `u=`), then footer links (`/dash`, `/disable-all?e=<your address>&k=<key>`).
  - No Hacker News or Lobsters hits in these emails.
- **Fixture results** (asserted in `tests/test_f5bot_parser.py`): `csat.eml` 4 (1 post, 3 comments), `csat (1).eml` 4 (1 post, 3 comments), `csat (2).eml` 3 (2 posts, 1 comment); 11 total. All 11 are exam/politics "CSAT" in blocklisted subreddits, so they're all Dropped (`blocked_subreddit`) end to end (`tests/test_scan.py`).
- 220 tests pass, 0 skipped.

### Stage 5 (2026-11-25)
- Checked `.claude/commands/draft-replies.md` and `docs/REPLY_RULES.md`: both already use `mention_id` and the right paths. Not changed.
- Built: `radar/drafts.py` (`load_drafts`, `validate`, `allowed_mentions`, `ratio_ok`, `send_drafts`), CLI `send-drafts [--file] [--dry-run]`, `README.md` with the "Drafting replies" section, `tests/fixtures/drafts_sample.json`.
- Schema v3 adds `drafts.message_id` (the Telegram message carrying the draft), so stage 6 can tell a "Replied" tap on a draft from one on an alert.
- Ratio: `allowed = (replies_7d + batch) // 5 - elvan_mentions_7d`, capped to the batch, counting only drafts marked posted. Drafts naming Elvan get the allowance in file order; the rest are flagged `NEEDS EDIT: over mention ratio, remove Elvan`.
- Sent files are tracked in `state.sent_draft_files` (JSON list of file names).
- **Pending (Aman):** ask Balram about native Klaviyo/Mailchimp/Braze/HubSpot integrations and update the "Never claim" line in `REPLY_RULES.md`. Until then it stays as written (none of them claimed).

### Stage 6 (2026-11-25)
- Built: `radar/feedback.py` (`getUpdates` with `offset = last_update_id + 1`, `timeout=0`; callbacks `s:{status}:{short_id}` -> `leads.status`/`status_at`, `answerCallbackQuery` "Marked replied/skipped/not relevant", `editMessageReplyMarkup` marking the chosen button with `* `; `/stats`, `/pause`, `/resume`; other chats ignored; `last_update_id` saved after every update; one bad update never blocks the rest), `radar/stats.py` (runs, emails, mentions, parse errors; leads by tier/signal/subreddit/keyword; status counts; precision for High and Medium; per-keyword mentions / sent / not-relevant rate / noise; top 5 terms behind not-relevant leads; replies posted and Elvan mentions), CLI `feedback` and `stats [--days] [--no-send]`, Telegram `get_updates` / `answer_callback` / `edit_buttons`.
- Mention ratio (export + send-drafts) now uses tap data via `Store.reply_stats`: replies = leads marked replied in the window; Elvan mentions = posted drafts naming Elvan.
- "Replied" on a **draft** message sets `drafts.posted = 1` (matched by the stored `message_id`; for drafts resent from `delivery_failures/`, by the "Mentions Elvan:" text). Tapping another button on the same draft message sets it back to 0. "Replied" on an alert only changes the lead status.

### Stage 7 (2026-11-25)
- Built: `scripts/run_scan.ps1`, `scripts/run_drafts.ps1`, `scripts/run_radar.ps1` (generic wrapper used by the feedback and stats tasks), `scripts/register_tasks.ps1` (4 tasks, Interactive logon as the current user, WakeToRun, StartWhenAvailable, IgnoreNew), `radar/doctor.py` (`.env`, Telegram `getMe`, IMAP login, F5Bot email in last 7 days, DB writable, `claude` on PATH), `radar/housekeeping.py` (leads/drafts > 30 days, `data/unparsed/` > 14 days, `tasks.log` rotation; runs after every scan and as `radar housekeeping`), CLI `doctor`, `housekeeping`, `drafts-pending`, `notify`, full `README.md`.
- **Headless drafting command** (flags checked against `claude --help`, Claude Code 2.1.116):
  `claude -p "/draft-replies data/leads/latest.json" --allowedTools "Read,Write"`
  started with `Start-Process` (stdout -> `data/logs/claude_drafts.log`), killed after 10 minutes. The script resolves `claude.exe` or `claude.cmd`, since npm installs a `.cmd` shim and `Start-Process` can't run the `.ps1` shim.
- Verified on this PC with the placeholder `.env`: all 4 scripts parse in Windows PowerShell 5.1; `run_scan.ps1` exits 0 and queues one inbox warning; `run_drafts.ps1` skips with no undrafted leads; with leads pending and `claude` hidden from PATH it queues "Drafts skipped: run /draft-replies manually" and exits 0; `doctor` fails exactly the 4 credential checks and passes DB + claude.
- **Not run:** `register_tasks.ps1` (needs Administrator and changes Task Scheduler) and a real headless `claude -p` run (spends Claude usage). Both are Aman's manual check.

### Schedule change (2026-11-26)
- Aman replies once a day at 20:00 IST, so the tasks run far less often: `Radar_Scan` 09:00, 13:00, 17:00, 19:15 (was every 30 min, 07:00 to 01:00, ~37 runs/day); `Radar_Feedback` 09:05, 19:20, 23:30 (was every 15 min, ~73 runs/day); `Radar_Drafts` 19:30 (was 09:00 and 21:00); `Radar_Weekly_Stats` unchanged (Fri 18:00).
- The 19:15 scan -> 19:20 feedback -> 19:30 drafts chain means the evening's drafts use the latest leads and the latest taps (for the Elvan mention ratio), and arrive by about 19:45.
- Feedback never leaves a gap over 24h (longest is 23:30 -> 09:05), because Telegram drops button taps that `getUpdates` hasn't collected within 24 hours.
- Nothing is lost with fewer scans: each scan fetches every F5Bot email since the last processed one. Leads scored later get a smaller recency bonus (e.g. +4 instead of +12), which can move a borderline lead from High to Medium; it still shows in the digest and the drafts.
- `scripts/register_tasks.ps1`, the README, `docs/PROJECT_SPEC.md` (sections 1, 3, 12) and `HOW_TO_BUILD.md` updated. Re-run `register_tasks.ps1` as Administrator to apply.

## Deviations from spec

- Stage 1: Windows Python has no tz database and `tzdata` isn't an allowed dependency, so `Settings.zoneinfo()` falls back to a fixed offset for `Asia/Calcutta` / `Asia/Kolkata` / `UTC` (no DST, so it's exact). Other zones fall back to UTC with a warning. Installing `tzdata` would remove the need for this.
- Stage 7: two helper commands not in the spec: `drafts-pending` (prints how many leads in `latest.json` still need a draft; `run_drafts.ps1` uses it to skip) and `notify --text` (one Telegram line from the scripts). There is also `run_radar.ps1`, because the spec's `Radar_Feedback` / `Radar_Weekly_Stats` tasks need a wrapper to find the venv.
- Stage 7: `doctor` exits 1 if any check fails, so it can gate scripts. Under `--dry-run` the DB write check is skipped.
- Stage 6: the per-keyword "not-relevant rate" is not_relevant / tapped (leads you actually tapped). A separate "noise" column (share of a keyword's mentions that scored Low or were dropped, so never reached you) shows keywords that flood the inbox without producing taps.
- Stage 6: `stats` prints a plain-text copy to the console and sends the HTML version to Telegram (`--no-send` to skip sending).
- Stage 5: `validate` also flags unicode arrows (CLAUDE.md says write "->"). Bare domains other than `elvan.ai` count as links (e.g. `survicate.com`), and so does `https://elvan.ai`. A "competitor price" is a `$<digit>` in the same sentence as a competitor name from `signals.yaml`.
- Stage 5: a draft whose lead isn't in the DB is still sent, with a minimal header.
- Stage 4 fix: a partly parsed email (some hits skipped) is still processed and marked `\Seen`, but recorded as `status='partial'`, and a copy is saved to `data/unparsed/` so the skipped hits can be checked by hand.
- Stage 4: `ParseError` is also raised for an email with no Reddit match, no HN/Lobsters match **and no links** (stricter than spec), so an unexpected F5Bot email lands in `data/unparsed/` instead of being silently ignored. HN-only emails are recorded as `status='no_reddit'` and marked seen.
- Stage 4: emails that fail to parse are recorded (`status='parse_error'`) so they aren't refetched, but they are **not** marked `\Seen`, so they stay visible in Gmail.
- Stage 4: the folder is opened read-only when `--dry-run` is set or `IMAP_MARK_SEEN=false`.
- Stage 4: `ingest-eml` records files as `file:<name>` in `processed_emails` but never skips them, so the same folder can be re-parsed while debugging. Those rows don't affect the IMAP `SINCE` date.
- Stage 4: after a successful scan the inbox-warning throttle resets, so the next outage warns straight away.
- Stage 3: the digest lists undigested High/Medium leads that were *not* hot-alerted (Medium leads, plus High leads diverted by the thread rule). Hot-alerted leads are only counted in the summary, not listed again. A digest goes out only when the run produced new High/Medium leads or there are leftovers to list.
- Stage 3: a lead is marked `alerted_at` even when the send failed, because the message is preserved and will be resent. Marking it avoids a duplicate alert.
- Stage 3: the timestamped leads file is written only when there are leads; `latest.json` is refreshed on every run (possibly with an empty list) so it always reflects the DB.
- Stage 3: before stage 6, `replies_last_7_days` / `elvan_mentions_last_7_days` count drafts marked `posted`.
- Stage 2: `leads` has an extra `drop_reason` column. Dropped mentions are stored as leads with `tier='Dropped'` and score 0, so stats and `rescore` can see them. They're never sent.
- Stage 2: `signals.yaml` has keys beyond the spec shape, so every keyword stays in YAML: `ambiguous_competitors` (Delighted only counts with feedback context), `generic_terms` (drives the -15 penalty), `delighted_fallout_phrases`, `pmf_phrases`, `staffing_terms`, `question_starts`. `request_phrases`, `switch_phrases` and `pain_phrases` were extended (e.g. "what do you all use", "moving off", "price hike", "double").
- Stage 2: enterprise leads get the -10 and are also capped at Medium. Without the cap, an RFP post in r/CustomerSuccess still reached exactly 70 (High), and the spec says enterprise must not be High.
- Stage 2: the location bonus (+15 title / +8 snippet) checks where the signal's *triggering* phrase is (the request phrase, competitor, integration...), not supporting context words like "nps".
- Stage 2: brand mentions skip the MSP/staffing/blocklist drops and both penalties, but are still dropped when promotional, a job post, or older than 72h.
- Stage 2: `rescore` scores each mention as of its `first_seen` time, so recency and the 72h drop are reproduced rather than everything older than 3 days becoming Dropped.
- Stage 1: `Mention.from_url` stores a canonical `https://www.reddit.com/...` URL (query string dropped, `old.`/`np.` hosts normalized).

## Known issues

1. **F5Bot parser checked against only 3 real emails**, all with the keyword `csat`, all Reddit (no HN/Lobsters) and one keyword group each. Add a multi-keyword email when one arrives. Watch the run summary for "Skipped hits". `.env` should use `F5BOT_SENDER=alerts@f5bot.com`.
1a. The real `.eml` fixtures were redacted before committing: recipient address and name -> `radar@example.com` / `Radar Test`, F5Bot unsubscribe key -> `REDACTEDKEY0`. Redact new fixtures the same way before committing them (the key lets anyone turn off your alerts).
2. **Placeholder `.env`.** Every secret is `REPLACE_ME`. Telegram, IMAP and `F5BOT_SENDER` must be filled in. `doctor` lists what's missing.
3. **Delete `data/` before going live**, so placeholder-run messages in `data/delivery_failures/` aren't resent.
4. Tasks run only while you're logged in (Interactive logon, needed by Claude Code) and while the PC is on or able to wake.
5. `-WindowStyle Hidden` still flashes a console window briefly when a task starts. That's Windows behaviour for `powershell.exe` tasks.
6. Windows PowerShell 5.1 writes a UTF-8 BOM at the start of `data/logs/tasks.log`. Harmless.
7. No `tzdata` dependency: only `Asia/Calcutta`/`Asia/Kolkata`/`UTC` work on Windows without it (stage 1 deviation).
8. Scoring was tuned only on the 26 synthetic sample mentions. Expect to adjust `signals.yaml` after the first week of real data (see tuning notes).
9. The Balram question (Klaviyo/Mailchimp/Braze/HubSpot integrations) is still open; `REPLY_RULES.md` keeps them under "Never claim".

## Tuning notes (false positives, keyword changes)

- 2026-11-26: the first 3 real F5Bot emails for `csat` were all noise: the UPSC/GPSC civil-services aptitude test (India), Romania's CSAT (Supreme Defence Council), South Korea's CSAT exam, and "certified separation anxiety trainer". Those subreddits are now blocklisted. The bare keyword `csat` on f5bot.com will keep producing this; consider replacing it with `csat survey`, `csat tool` and `zendesk csat` (already in the F5Bot list) if the stats show a high noise rate.

- `ltd` is a promo phrase (per spec) and would also drop a post from "Acme Pvt Ltd". Watch for this; remove it from `promo_phrases` if it bites.
- `hiring` drops any post containing the word, even "we're hiring and also need an NPS tool". Kept per spec.
- A title starting with "How" counts as a question even for "How we run NPS...". Kept per spec.
- Removed "switched from" from switch phrases: past tense means they already moved, so it isn't a search.
