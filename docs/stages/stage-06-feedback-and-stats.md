# Stage 6: Telegram feedback buttons and weekly stats

## Goal
Aman's taps on [Replied] [Skip] [Not relevant] update the lead status. That feeds the mention ratio and the weekly precision report, which shows what to tune.

## Build
1. **`radar/feedback.py`**, the `python -m radar feedback [--dry-run]` command:
   - `getUpdates` with `offset = last_update_id + 1` from `state` and `timeout=0`
   - for each `callback_query` `s:{status}:{short_id}`: resolve to `mention_id`, update `leads.status` and `status_at`, call `answerCallbackQuery` ("Marked replied" or the matching status), and use `editMessageReplyMarkup` to show the chosen status
   - "Replied" on a draft message also sets `drafts.posted = 1`, which counts toward the ratio when the draft mentions Elvan
   - ignore any chat other than `TELEGRAM_CHAT_ID`
   - save `last_update_id` after each update
   - text commands: `/stats` (this week's numbers), `/pause` and `/resume` (set `paused` in `state`, which `scan` respects)
2. **The `python -m radar stats --days 7` command**, sent to Telegram:
   - runs, emails read, mentions, parse errors
   - leads by tier, signal, subreddit and F5Bot keyword
   - status counts, and precision = (replied + skipped) / (replied + skipped + not_relevant) for High and for Medium
   - **for each F5Bot keyword: mentions and not-relevant rate**, to decide which keywords to drop on f5bot.com
   - the top 5 matched terms behind not-relevant leads
   - replies posted and Elvan mentions
3. Switch the mention ratio in `export.py` and `drafts.py` over to the real posted-reply data.

## Tests
- `test_feedback.py`: a callback updates the status; the offset is saved; a foreign chat is ignored; a malformed callback doesn't crash; a replayed update is a no-op; `/pause` makes scan skip.
- `test_stats.py`: precision math, the per-keyword breakdown, top not-relevant terms.

## Manual check
Tap each button type on real alerts, then run `python -m radar feedback`. The buttons should change. Send `/stats` to the bot and run `python -m radar stats`. Send `/pause`, run `scan` (it should skip), then `/resume`.

## Acceptance
- [ ] Taps reflected in the DB and in Telegram (pending: real bot; tested with mocks)
- [x] Stats report includes per-keyword quality
- [x] Pause/resume works (tested)
- [x] Tests pass
- [x] `docs/PROGRESS.md` updated

Commit: `stage 6: telegram feedback buttons and weekly stats`
