# Reddit Radar

Reddit buying-signal monitor for Elvan. It reads F5Bot keyword alert emails, scores each Reddit
post or comment for buying intent, and sends hot leads, a digest and reply drafts to Telegram.
A human always posts the reply on Reddit; this project never talks to reddit.com.

Full setup, commands and troubleshooting are completed in stage 7. See `docs/PROJECT_SPEC.md`.

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
