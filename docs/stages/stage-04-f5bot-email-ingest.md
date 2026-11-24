# Stage 4: F5Bot email ingest

## Goal
`python -m radar scan` reads new F5Bot alert emails from Gmail over IMAP, turns them into mentions, and runs the stage 3 pipeline. This is what makes the app live.

## Before starting (Aman), see `docs/F5BOT_SETUP.md`
- F5Bot is set up with the keywords, and alerts are arriving in the dedicated Gmail.
- **At least 3 real F5Bot emails are saved as `.eml` in `tests/fixtures/f5bot/`.** Do not start this stage without them; the parser must be built from the real format.
- `.env` has `IMAP_USER`, `IMAP_APP_PASSWORD` and `F5BOT_SENDER`.

## Build
1. **Study the real emails first.** Open every `.eml` in `tests/fixtures/f5bot/`. Write down in `docs/PROGRESS.md` what you find:
   - subject pattern
   - HTML vs plain text
   - how each match is laid out (keyword, title, snippet, link)
   - whether links are direct or redirects
   - how posts and comments differ
   - whether Hacker News or Lobsters matches appear
   Show Aman this summary before writing the parser.
2. **`radar/sources/f5bot_parser.py`**: `parse_email(raw_bytes) -> list[Mention]`.
   - Prefer the HTML part; fall back to plain text. Use `email` and `html.parser` from the stdlib.
   - Each Reddit match becomes one Mention. Use `parse_reddit_url` from stage 1 for the IDs and subreddit. Unwrap redirect links to the real reddit.com URL.
   - `received_at` comes from the email `Date` header.
   - Skip non-Reddit matches (HN, Lobsters) and log them at debug level.
   - If an email has zero parseable Reddit matches but does have links, raise `ParseError` so it lands in `data/unparsed/`.
3. **`radar/sources/f5bot_mail.py`**: `fetch_new(settings, store, dry_run) -> list[(uid, raw_bytes)]`.
   - `imaplib.IMAP4_SSL`, then login with the app password, then select the folder, then `UID SEARCH FROM "<F5BOT_SENDER>" SINCE <date>` (the first run uses `INGEST_LOOKBACK_DAYS`; later runs use the last processed date minus 1 day)
   - filter out UIDs already in `processed_emails`
   - fetch with `BODY.PEEK[]` so reading doesn't mark mail seen. After a successful parse and store, set `\Seen` if `IMAP_MARK_SEEN`. Never delete.
   - catch connection and auth errors: log them, send one Telegram warning at most every 6 hours ("F5Bot inbox unreachable: <reason>"), and exit the run cleanly
   - the password is never logged
4. **The `python -m radar scan [--dry-run]` command**: fetch new emails, parse each one (on a parse error: save to `data/unparsed/{uid}.eml`, record it, continue), then `pipeline.process(all_new_mentions)`, then record the uids in `processed_emails` and finish the run with `emails_read` and `mentions_found`.
5. **The `python -m radar ingest-eml --dir tests/fixtures/f5bot [--dry-run]` command**: same as scan, but reads `.eml` files from a folder. Useful for debugging.
6. Skip cleanly when `/pause` is active (set up in stage 6; for now just check the `paused` state key).

## Tests
- `test_f5bot_parser.py`: for each real fixture, assert the number of mentions and the fields of the first one (as written in PROGRESS.md). Also cover a non-Reddit match being skipped, a broken email raising `ParseError`, and redirect unwrapping.
- If `tests/fixtures/f5bot/` is gitignored, the tests `pytest.skip` when the folder is empty.
- `test_f5bot_mail.py` with a mocked `imaplib`: only new UIDs are fetched; `PEEK` is used; seen is set only after success; an auth error goes to a warning and a clean exit; the warning is throttled; dry-run changes nothing.
- `test_scan.py`: end to end with a mocked mailbox returning the fixture emails, through to the store and leads, with Telegram mocked.

## Manual check
```powershell
python -m radar ingest-eml --dir tests/fixtures/f5bot --dry-run   # parse check, nothing written
python -m radar scan                                              # live inbox
python -m radar scan                                              # 0 new emails the second time
python -m radar leads --tier High,Medium --days 3
```
Check the Telegram alerts match real Reddit threads; click 2 or 3 links. Put a wrong app password in `.env` and run scan: there should be one warning on Telegram and no crash. Then restore the password.

## Acceptance
- [ ] Parser built from real emails, with the format documented in PROGRESS.md
- [ ] Live scan produces real leads on Telegram
- [ ] Re-running scan doesn't reprocess emails
- [ ] Bad credentials give a warning, not a crash
- [ ] Tests pass
- [ ] `docs/PROGRESS.md` updated

Commit: `stage 4: f5bot email ingest and live scan`
