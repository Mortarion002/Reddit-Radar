# Stage 3: Telegram alerts, digest and leads export

## Goal
High leads ping Aman's Telegram with buttons, a digest follows, and a leads JSON file is written for Claude Code. In this stage it all runs off imported mentions.

## Before starting (Aman)
1. In Telegram, open @BotFather, send `/newbot`, and copy the token.
2. Send your new bot any message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number at `"chat":{"id": ...}`.
4. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Never paste them into chat.

## Build
1. **`radar/telegram.py`**:
   - `send(text, buttons=None) -> message_id | None` (HTML mode, text escaped)
   - split messages at 4,000 characters (port `_split_message` from `reference/notifier.py`); buttons go on the last chunk only
   - retry once after 5 seconds, then preserve the message to `data/delivery_failures/`
   - `resend_failures()`, which deletes each file once it sends
   - port the curl IPv4 DNS fallback from `reference/notifier.py` only if a plain send fails with a DNS error; note in PROGRESS.md whether you ported it
   - the token is never logged
2. **`radar/digest.py`**: hot alert, digest and run summary in the formats of spec section 9. Buttons `[Replied] [Skip] [Not relevant]` with callback data `s:{status}:{short_id}`. Add a `short_id` column (or table) mapping a short hash to `mention_id`.
3. **`radar/export.py`**: write `data/leads/YYYY-MM-DD_HHMM.json` plus `latest.json`, in the spec section 10 format, with the cap of 10 and the 7-day reply/mention counts. Set `exported_at`.
4. **A `radar/pipeline.py` function `process(new_mentions)`**, shared by `import` and later `scan`: resend failures, then store and score, then send hot alerts (following the thread rule in spec 7), then the digest, then export. `radar import` now calls it.
5. **`python -m radar telegram-test`** sends "Reddit Radar connected".

## Tests
- `test_digest.py`: formats, HTML escaping, digest cap of 15, callback data under 64 bytes, a comment vs a post label.
- `test_telegram.py` with mocked `requests`: success, failure then retry then a preserved file, splitting keeps the buttons on the last chunk, resend deletes the file.
- `test_export.py`: shape, cap, already-drafted leads excluded.
- `test_pipeline.py`: a High lead is alerted only once; a second High mention in the same thread within 24h goes to the digest, not a new alert.

## Manual check
```powershell
python -m radar telegram-test
python -m radar import --file tests/fixtures/mentions_sample.json
```
(To re-test with the same sample: delete `data/radar.db` first.)
- Alerts with buttons, plus a digest, arrive on Aman's phone.
- `data/leads/latest.json` exists.
- Turn Wi-Fi off and import again (after deleting the DB). A file should appear in `data/delivery_failures/`. Wi-Fi back on, run `import` with an empty file. The failure should be resent.

## Acceptance
- [ ] Real alerts and digest received (pending: needs the real bot token in `.env`)
- [x] Failure preservation and resend work (tested offline; Wi-Fi-off check pending)
- [x] Leads file valid
- [x] Tests pass
- [x] `docs/PROGRESS.md` updated

Commit: `stage 3: telegram alerts, digest and leads export`
