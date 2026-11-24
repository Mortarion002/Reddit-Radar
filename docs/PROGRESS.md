# Progress Log

Claude Code updates this after each stage. Record the date, what was built, any deviation from the spec, and open questions.

| Stage | Status | Date | Notes |
|---|---|---|---|
| 1 Setup, config, models | done | 2026-11-25 | Skeleton, Settings, redacting logger, Mention + URL parsing, `config-check`. |
| 2 Storage + scoring | done (awaiting Aman's leads review) | 2026-11-25 | SQLite store with migrations, scorer, `import` / `leads` / `rescore`, 26-mention synthetic sample. |
| 3 Telegram + export | done (real Telegram check pending) | 2026-11-25 | Telegram client with failure preservation + DNS fallback, alerts/digest, leads export, shared pipeline, `telegram-test`. |
| 4 F5Bot email ingest | not started | | |
| 5 Claude reply drafts | not started | | |
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

## Deviations from spec

- Stage 1: Windows Python has no tz database and `tzdata` isn't an allowed dependency, so `Settings.zoneinfo()` falls back to a fixed offset for `Asia/Calcutta` / `Asia/Kolkata` / `UTC` (no DST, so it's exact). Other zones fall back to UTC with a warning. Installing `tzdata` would remove the need for this.
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
