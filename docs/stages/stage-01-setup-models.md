# Stage 1: Repo setup, config, logging and the Mention model

## Goal
A clean Python project skeleton: config loading, logging with secret redaction, the `Mention` model with Reddit URL parsing, and a working CLI entry point. No data sources yet. There is no Reddit network code in this project at all, ever.

## Build
1. Create the layout from `CLAUDE.md`: `radar/`, `radar/sources/`, `config/`, `scripts/`, `tests/fixtures/`. `data/` is created at runtime and is gitignored.
2. `requirements.txt` with `requests`, `python-dotenv`, `PyYAML` and `pytest`. Create `.venv` and install.
3. `.env.example`: every key from spec section 11, with empty values and short comments. Check that `.gitignore` already covers `.env`, `data/`, `*.db`, `.venv/` and `reference/`, and don't loosen it.
4. **`radar/config.py`**: a `Settings` dataclass loaded from `.env` plus `config/signals.yaml` and `config/subreddits.yaml`.
   - Missing Telegram or IMAP values only produce a warning at this stage. Later commands check for the values they need.
   - Provide `settings.zoneinfo()` for `TIMEZONE`.
5. Create **`config/signals.yaml`** and **`config/subreddits.yaml`** exactly in the shapes of spec section 6.3. They're only loaded here; scoring uses them in stage 2.
6. **`radar/logger.py`**: console output plus a rotating file at `data/logs/radar.log`. A redaction filter masks the Telegram bot token, including inside URLs like `.../bot123:AA...`, and the IMAP app password wherever they appear.
7. **`radar/models.py`**:
   - `Mention`, exactly as in spec section 4, with a `.text` property
   - `parse_reddit_url(url) -> (subreddit, post_id, comment_id | None)`. It handles post URLs, comment URLs, `old.` and `np.` hosts, trailing slashes and query strings, and raises `ValueError` for anything that isn't a Reddit URL.
   - `Mention.from_url(...)`, a helper that fills `mention_id`, `kind`, `post_id` and `subreddit` from the URL
8. **`radar/cli.py`** plus **`radar/__main__.py`**: `python -m radar --help` lists the commands, and `python -m radar config-check` prints the loaded settings with secrets masked, along with which values are missing.
9. Fill the header of `docs/PROGRESS.md`.

## Tests
- `test_config.py`: loads `.env.example`-style values, YAML loads, a missing optional value warns but doesn't crash.
- `test_logger.py`: the token is redacted, including inside a Telegram API URL; the password is redacted.
- `test_models.py`: post URL, comment URL, query string, `old.reddit.com`, a non-Reddit URL raises, `mention_id` and `kind` are correct, `.text` joins the title and snippet.
- A guard test, `test_no_reddit_network.py`: it scans `radar/**/*.py` and fails if any file calls `requests` with a `reddit.com` URL or imports `praw`.

## Manual check
```powershell
.venv\Scripts\Activate.ps1
python -m pytest -q
python -m radar --help
python -m radar config-check
```
`config-check` should show your values with secrets masked, and say Telegram/IMAP are missing if you haven't filled them in yet. That's fine at this stage.

## Acceptance
- [x] Tests pass
- [x] `config-check` works and never prints a secret
- [x] No Reddit network code
- [x] `docs/PROGRESS.md` updated

Commit: `stage 1: setup, config, logging and mention model`
