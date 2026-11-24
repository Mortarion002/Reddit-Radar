# Stage 2: SQLite storage and signal scoring

## Goal
Store mentions and score them for buying intent. Mentions come from a JSON file for now (`radar import`); F5Bot email arrives in stage 4.

This is the stage that decides whether the tool is useful, so take the scorer tests seriously.

## Build
1. **`radar/store.py`**: every table from spec section 7, plus a `schema_version` row in `state`. Methods:
   - `upsert_mentions(mentions)`, which returns only the new mentions
   - `save_lead`, `get_leads(tiers, since)` and `set_status`
   - `start_run` and `finish_run`
   - `get_state` and `set_state`
2. **`radar/scorer.py`**, pure functions with no IO:
   - `normalize` and `contains`, with word boundaries and plurals. Port them from `reference/reddit_scorer.py` if it's there.
   - `classify(mention, cfg) -> Classification(primary, signals, matched_terms, competitor)`, following spec 6.1. The primary signal is the one with the highest base.
   - `score(mention, classification, cfg, now) -> (score, tier)`, following spec 6.2, including drops and penalties.
3. **`radar/sources/sample.py`**: loads a list of mentions from JSON. Each item has a `url`, `title`, `snippet`, `keyword`, `received_at` and, optionally, an `author`; the IDs come from the URL.
4. CLI:
   - `python -m radar import --file PATH [--dry-run]` loads, dedupes, stores and scores, then prints a summary by tier.
   - `python -m radar leads [--tier High,Medium] [--days 3]` prints a table.
   - `python -m radar rescore --days 7` re-scores stored mentions with the current config. It never touches `status`.
5. **`tests/fixtures/mentions_sample.json`**: about 25 hand-written, realistic mentions, mixing posts and comments and covering every signal plus the drop cases. Start the file with `"_note": "synthetic test data"`. Use realistic `received_at` values relative to "now" (the loader can accept `"-2h"`-style offsets, so the sample never goes stale).

## Tests
- `test_store.py`: tables are created, upsert returns only new rows, the run log works, state works.
- `test_scorer.py`, **at least 20 cases**, including:
  - "Survicate alternatives for a small CS team?" -> `alternative_search`, High
  - "Survicate just doubled our renewal price" -> `competitor_pain`, High
  - "What do you all use for NPS surveys?" in r/CustomerSuccess -> `tool_request`, High
  - "How do I auto-send CSAT when a Zendesk ticket closes?" -> `integration_need`
  - "We lost our Delighted history after the shutdown" -> `delighted_fallout`
  - "How did you know you hit product market fit?" -> `pmf_founder`
  - "Our CSAT response rate is 4%, ideas?" -> `feedback_pain`
  - a comment saying "we use simplesat for this" -> `competitor_mention`, Low
  - "Anyone tried elvan.ai?" -> `brand_mention`, High
  - "I built a survey tool, roast my landing page" -> dropped
  - "[Hiring] Customer Success Manager" -> dropped
  - "Best ConnectWise survey integration for our MSP" -> dropped
  - a mention received 4 days ago -> dropped
  - an enterprise RFP post -> penalized, not High
  - the same text as a post and as a comment: the post scores higher
  - a title match beats a snippet-only match
  - recency: 1 hour old beats 20 hours old
  - plurals: "NPS tools" matches "nps tool"
  - "SurveySparrow's pricing" matches
  - a blocklisted subreddit -> dropped
- `test_import.py`: importing twice gives no duplicates; `--dry-run` writes nothing.

## Manual check
```powershell
python -m pytest -q
python -m radar import --file tests/fixtures/mentions_sample.json
python -m radar leads --tier High,Medium
python -m radar import --file tests/fixtures/mentions_sample.json   # should report 0 new
```
Aman reads the High/Medium table and asks himself whether he'd reply to these. Any disagreement goes in `docs/PROGRESS.md` under "Tuning notes" and gets fixed in `signals.yaml`.

## Acceptance
- [ ] 20+ scorer tests pass, and all other tests pass
- [ ] All keywords live in YAML
- [ ] Aman reviewed the leads table
- [ ] `docs/PROGRESS.md` updated

Commit: `stage 2: sqlite store and signal scoring`
