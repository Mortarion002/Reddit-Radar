# Real F5Bot emails go here

Save 3 to 5 real F5Bot alert emails as `.eml` (Gmail -> three-dot menu -> Download message)
and drop them in this folder. See `docs/F5BOT_SETUP.md`.

- `tests/test_f5bot_parser.py` parses every `.eml` here automatically. With no files, those
  tests skip.
- Optionally add `expected.json` to pin exact results per file, for example:

```json
{
  "alert1.eml": {"count": 2, "first": {"mention_id": "comment:abc123", "subreddit": "SaaS", "kind": "comment", "keyword": "nps tool"}},
  "alert2.eml": {"count": 1}
}
```

The synthetic emails in `../f5bot_synthetic/` are **not** real F5Bot output. They were
written to the best-known F5Bot layout before any real email was available.

If you'd rather not commit real emails, add `tests/fixtures/f5bot/*.eml` to `.gitignore`.
