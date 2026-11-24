# Stage 5: Reply drafts with Claude Code

## Goal
Claude Code turns `data/leads/latest.json` into reply drafts that follow `docs/REPLY_RULES.md`. `radar send-drafts` validates them and delivers them to Telegram.

## Before starting (Aman)
Ask Balram whether Elvan has native Klaviyo, Mailchimp, Braze or HubSpot integrations. Update the "Never claim" line in `docs/REPLY_RULES.md` to match.

## Build
1. Check that `.claude/commands/draft-replies.md` and `docs/REPLY_RULES.md` exist and use `mention_id`. Don't rewrite them; only fix paths or field names if needed.
2. **`radar/drafts.py`**:
   - `load_drafts(path)`: validate the JSON shape from spec section 10
   - `validate(draft) -> list[issue]`:
     - an em dash or en dash
     - an emoji
     - a hashtag
     - over 180 or under 30 words
     - `mentions_elvan` true but no "I'm on the Elvan team"
     - `mentions_elvan` false but "elvan" in the text
     - a `$` price next to a competitor name
     - a URL other than plain "elvan.ai"
   - `ratio_ok(store, new_mentions)`: the 20% cap over 7 days, counting only posted replies
3. **The `python -m radar send-drafts [--file PATH] [--dry-run]` command**:
   - defaults to the newest unsent file in `data/drafts/` (track sent files in `state`)
   - for each draft: validate, save to `drafts`, then send to Telegram in the spec section 9 draft format (`<pre>` block, buttons)
   - a draft with issues is still sent, prefixed `NEEDS EDIT: <issues>`
   - over the ratio: `NEEDS EDIT: over mention ratio, remove Elvan`
   - empty drafts (skipped leads) are not sent; their notes are logged
4. **README section "Drafting replies"**: run `/draft-replies` in Claude Code, then `python -m radar send-drafts`.

## Tests
- `test_drafts.py`: every validation rule; ratio math (1 mention in 6 replies allows 0 more in a batch of 1, and 0 in 10 allows 2 in a batch of 3); a malformed file gives a clear error; an already-sent file is skipped. Include `tests/fixtures/drafts_sample.json`.

## Manual check
1. `python -m radar scan` (or use an existing `latest.json`).
2. In Claude Code: `/draft-replies`. A file appears in `data/drafts/`.
3. `python -m radar send-drafts`. The drafts arrive on Telegram and are easy to copy.
4. Read 5 drafts. Wording changes go in `REPLY_RULES.md`, not the code.
5. Add an em dash to one draft in the JSON, remove the file from the sent list (or copy it under a new name), and resend. It should arrive flagged NEEDS EDIT.

## Acceptance
- [ ] Drafts arrive copyable on Telegram (pending: real bot token; format tested offline)
- [x] A bad draft arrives flagged (tested)
- [x] Ratio enforcement works
- [x] Tests pass
- [x] `docs/PROGRESS.md` updated

Commit: `stage 5: claude code reply drafts and send-drafts`
