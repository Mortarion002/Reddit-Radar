# Progress Log

Claude Code updates this after each stage. Record the date, what was built, any deviation from the spec, and open questions.

| Stage | Status | Date | Notes |
|---|---|---|---|
| 1 Setup, config, models | done | 2026-11-25 | Skeleton, Settings, redacting logger, Mention + URL parsing, `config-check`. |
| 2 Storage + scoring | done (awaiting Aman's leads review) | 2026-11-25 | SQLite store with migrations, scorer, `import` / `leads` / `rescore`, 26-mention synthetic sample. |
| 3 Telegram + export | done (real Telegram check pending) | 2026-11-25 | Telegram client with failure preservation + DNS fallback, alerts/digest, leads export, shared pipeline, `telegram-test`. |
| 4 F5Bot email ingest | built on synthetic emails (real `.eml` check pending) | 2026-11-25 | IMAP reader, format-tolerant parser, `scan`, `ingest-eml`, throttled inbox warning, pause check. |
| 5 Claude reply drafts | done (live Telegram check pending) | 2026-11-25 | Draft validation, mention ratio, `send-drafts`, README drafting section. |
| 6 Feedback + stats | not started | | |
| 7 Scheduling + ops | not started | | |

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
- **F5Bot format assumed by the synthetic fixtures (to confirm against real emails):**
  - Subject: `F5Bot found something: <keyword>` (used only as the keyword fallback)
  - From: `F5Bot <admin@f5bot.com>` (`.env` keeps `F5BOT_SENDER=REPLACE_ME` until you copy the real address)
  - multipart/alternative, HTML preferred, plain text fallback
  - sections headed `Reddit Posts (/r/sub/):`, `Reddit Comments (/r/sub/):`, `Hacker News Comments:`
  - each match: `<a href="https://f5bot.com/url?u=<urlencoded reddit permalink>&i=..&h=..">thread title</a>`, then `Keyword: <b>kw</b>`, then a `<blockquote>` snippet
  - comments: link text may start `Re: `; the URL has the comment id as the 6th path segment
  - footer with settings/unsubscribe links on f5bot.com
- What the parser actually relies on: the order of links. Each link to a Reddit permalink (after unwrapping `?u=` / `?url=` / `?q=` style redirects) starts a match: link text = title, the text up to the next link = snippet, and an optional `Keyword:` line. HN/Lobsters links are skipped (logged at debug). Other links end the current match. Section headers, footer lines and `--` separators are stripped from snippets.
- **When real emails arrive:** drop them in `tests/fixtures/f5bot/`, run `python -m radar ingest-eml --dir tests/fixtures/f5bot --dry-run`, and check the titles and snippets. `tests/test_f5bot_parser.py::test_real_fixture_parses` runs on every real file automatically, and `expected.json` (see the README there) pins exact counts and fields.

### Stage 5 (2026-11-25)
- Checked `.claude/commands/draft-replies.md` and `docs/REPLY_RULES.md`: both already use `mention_id` and the right paths. Not changed.
- Built: `radar/drafts.py` (`load_drafts`, `validate`, `allowed_mentions`, `ratio_ok`, `send_drafts`), CLI `send-drafts [--file] [--dry-run]`, `README.md` with the "Drafting replies" section, `tests/fixtures/drafts_sample.json`.
- Schema v3 adds `drafts.message_id` (the Telegram message carrying the draft), so stage 6 can tell a "Replied" tap on a draft from one on an alert.
- Ratio: `allowed = (replies_7d + batch) // 5 - elvan_mentions_7d`, capped to the batch, counting only drafts marked posted. Drafts naming Elvan get the allowance in file order; the rest are flagged `NEEDS EDIT: over mention ratio, remove Elvan`.
- Sent files are tracked in `state.sent_draft_files` (JSON list of file names).
- **Pending (Aman):** ask Balram about native Klaviyo/Mailchimp/Braze/HubSpot integrations and update the "Never claim" line in `REPLY_RULES.md`. Until then it stays as written (none of them claimed).

## Deviations from spec

- Stage 1: Windows Python has no tz database and `tzdata` isn't an allowed dependency, so `Settings.zoneinfo()` falls back to a fixed offset for `Asia/Calcutta` / `Asia/Kolkata` / `UTC` (no DST, so it's exact). Other zones fall back to UTC with a warning. Installing `tzdata` would remove the need for this.
- Stage 5: `validate` also flags unicode arrows (CLAUDE.md says write "->"). Bare domains other than `elvan.ai` count as links (e.g. `survicate.com`), and so does `https://elvan.ai`. A "competitor price" is a `$<digit>` in the same sentence as a competitor name from `signals.yaml`.
- Stage 5: a draft whose lead isn't in the DB is still sent, with a minimal header.
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

## Tuning notes (false positives, keyword changes)

- `ltd` is a promo phrase (per spec) and would also drop a post from "Acme Pvt Ltd". Watch for this; remove it from `promo_phrases` if it bites.
- `hiring` drops any post containing the word, even "we're hiring and also need an NPS tool". Kept per spec.
- A title starting with "How" counts as a question even for "How we run NPS...". Kept per spec.
- Removed "switched from" from switch phrases: past tense means they already moved, so it isn't a search.
