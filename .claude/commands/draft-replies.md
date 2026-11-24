---
description: Draft Reddit replies for the latest Reddit Radar leads
argument-hint: [path to leads json, default data/leads/latest.json]
---

You are drafting Reddit replies for Elvan's Reddit Radar. A human will review, edit and post every reply. You never post anything.

1. Read `docs/REPLY_RULES.md` fully. Those rules are mandatory.
2. Read the leads file: `$ARGUMENTS`, or `data/leads/latest.json` if no argument was given. If it has no leads, write nothing and say "No leads to draft."
3. Look at `elvan_mentions_last_7_days` and `replies_last_7_days`. Work out how many Elvan mentions you may use in this batch so that (mentions + new mentions) / (replies + new drafts) stays at or under 0.2. Give mentions to the leads where Elvan fits best (`tool_request`, `alternative_search`, `competitor_pain` with a priority competitor, a Zendesk `integration_need`).
4. For each lead, write one draft following the shape for its `primary_signal`. Use only the facts allowed in REPLY_RULES.md. Leads have `kind` (post or comment), `thread_title` and `snippet` only, no full text. For comments, reply to the comment. If the snippet is too thin, keep the draft short and flag it in `notes`.
5. Check every draft before writing:
   - no em dashes, no emojis, no hashtags
   - 50 to 150 words
   - if it mentions Elvan, the disclosure sentence is there and at least one real alternative is named
   - no competitor prices, no invented features or numbers
6. Write the output to `data/drafts/` using the same timestamp as the leads file name (if the input is `latest.json`, use the `generated_at` value formatted `YYYY-MM-DD_HHMM`). Exact shape:

```json
{"source_leads_file": "...", "drafts": [{"mention_id": "...", "mentions_elvan": false, "draft": "...", "notes": "..."}]}
```

7. Print a one-line summary: how many drafts, how many mention Elvan, and any lead you skipped and why (skipping is allowed if a lead turns out to be irrelevant; put it in the summary with `"draft": ""` and the reason in `notes`).

Do not edit any other file. Do not run `send-drafts`; the wrapper script does that.
