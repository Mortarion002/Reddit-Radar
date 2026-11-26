# Reddit Radar: Project Spec (v2, F5Bot source)

## 1. Goal

Find Reddit posts and comments where someone is shopping for, unhappy with, or struggling without a feedback/survey tool. Get each one to Aman's Telegram the same day with a ready-to-edit reply draft. Aman reads, edits and posts by hand.

Signals like these lose most of their value in 24 to 48 hours (per the Elvan master plan), so speed and precision matter more than volume.

Success after 4 weeks:

- At least 60% of leads tagged High get marked "relevant" by Aman.
- Every High lead that arrives by 19:15 IST reaches Telegram, with a draft, before the 20:00 IST reply session that day.
- Zero runs lose data because of an IMAP, parsing or Telegram failure.

## 2. Why F5Bot

Reddit removed every free direct route: public RSS ended Nov 13, 2026, new API access requests closed Oct 31, 2026, and unauthenticated `.json` access returns 403. F5Bot is a free service that watches Reddit posts and comments for keywords and emails each match. It's also what Aman's manager wants used.

What this changes compared with the old plan:

- **No fetching, no rate limits.** The app reads email.
- **Comments are covered too**, not only posts.
- **Matching happens across all of Reddit**, driven by the keywords set on f5bot.com, not by a subreddit list. Subreddits only affect the score.
- **We get the alert content, not the full post**: the thread title, a snippet around the keyword, the link, and the subreddit. Scoring and drafting work from that. Aman opens the link before replying anyway.

## 3. Architecture

```
F5Bot --email--> dedicated Gmail inbox
                       |
          [radar scan]  (09:00, 13:00, 17:00, 19:15)
     IMAP: fetch new F5Bot emails (by UID) -> parse -> Mentions
     dedupe -> score -> store (SQLite)
     hot alerts + digest -> Telegram (buttons)
     export -> data/leads/YYYY-MM-DD_HHMM.json (+ latest.json)
                       |
          [run_drafts.ps1]  (19:30)  claude -p "/draft-replies"
     reads latest.json + docs/REPLY_RULES.md -> data/drafts/*.json
          [radar send-drafts] -> Telegram, one message per draft
                       |
          [radar feedback]  (09:05, 19:20, 23:30)
     Telegram getUpdates -> button taps -> lead status in DB
```

Commands:

- `python -m radar scan [--dry-run]`: ingest from IMAP (from stage 4; before that, scan processes imported mentions)
- `python -m radar import --file PATH [--dry-run]`: load mentions from a JSON file (for testing and manual adds)
- `python -m radar ingest-eml --dir PATH`: parse saved `.eml` files (for testing the F5Bot parser offline)
- `python -m radar leads [--tier High,Medium]`
- `python -m radar rescore --days 7`
- `python -m radar send-drafts [--file PATH] [--dry-run]`
- `python -m radar feedback [--dry-run]`
- `python -m radar stats [--days 7]`
- `python -m radar doctor`

## 4. The Mention model

```python
@dataclass(slots=True)
class Mention:
    mention_id: str      # "post:<post_id>" or "comment:<comment_id>"
    kind: str            # "post" | "comment"
    post_id: str         # Reddit thread id (from /comments/<post_id>/)
    subreddit: str
    title: str           # thread title
    snippet: str         # text F5Bot sent (post body excerpt or comment excerpt)
    url: str             # direct reddit permalink
    author: str | None   # if the email includes it
    keyword: str         # the F5Bot keyword that fired
    received_at: datetime  # email Date header, timezone-aware
    source: str = "f5bot"
    @property
    def text(self) -> str: ...  # title + snippet
```

IDs are parsed from the Reddit URL: `/r/{sub}/comments/{post_id}/{slug}/` for posts, and `/r/{sub}/comments/{post_id}/{slug}/{comment_id}/` for comments. If F5Bot wraps links in a redirect, unwrap it to the real reddit.com URL (stage 4 confirms the real format from saved emails).

## 5. F5Bot keywords (set on f5bot.com)

These live on F5Bot, not in code. The full list and setup steps are in `docs/F5BOT_SETUP.md`. In short:

- Competitors: `survicate`, `surveysparrow`, `simplesat`, `asknicely`, `retently`, `zonka`, `nicereply`
- Intent: `nps tool`, `nps software`, `csat tool`, `csat survey`, `zendesk csat`, `delighted alternative`, `nps survey`, `customer feedback tool`, `pmf survey`
- Brand: `elvan.ai`, `getelvan`

Every keyword should be specific. Generic words like "survey" or "feedback" will flood the inbox.

## 6. Signal classification and scoring

Each mention gets **one primary signal** plus any secondary ones, a **score from 0 to 100**, and a **tier**.

### 6.1 Signal categories

| Signal | What it looks like | Base | Example |
|---|---|---|---|
| `tool_request` | Asking for a recommendation for NPS/CSAT/feedback/survey software | 40 | "What do you use for NPS surveys?" |
| `competitor_pain` | Named competitor plus a complaint (price, limits, support, bugs, complexity) | 40 | "Survicate just doubled our bill" |
| `alternative_search` | "{competitor} alternative", "switching from", "replace" | 38 | "Simplesat alternatives?" |
| `integration_need` | Wants surveys in a tool Elvan touches (Zendesk, Klaviyo, Mailchimp, Braze, HubSpot, Shopify, Slack) | 30 | "Auto-send CSAT when a Zendesk ticket closes?" |
| `delighted_fallout` | Still dealing with the Delighted shutdown | 30 | "We lost our Delighted history" |
| `pmf_founder` | Founder asking how to measure PMF, Sean Ellis survey, NPS reliability early on | 25 | "How did you know you had PMF?" |
| `feedback_pain` | Operational pain with no tool named (low response rate, no post-ticket feedback) | 20 | "Our CSAT response rate is 3%" |
| `competitor_mention` | A competitor is named with no clear pain or intent | 12 | "We use Survicate for this" |
| `brand_mention` | Mentions Elvan | 50, always High | "Anyone tried elvan.ai?" |

### 6.2 Score

```
score = base(primary signal)
      + location bonus      (+15 if the matching phrase is in the title, +8 if only in the snippet)
      + question bonus      (+10 if the text asks a question: "?" or starts "how / what / which / any / is there")
      + secondary signals   (+5 each, max +10)
      + subreddit bonus     (config/subreddits.yaml)
      + recency             (from received_at: +12 under 2h, +8 under 12h, +4 under 24h, 0 under 72h)
      + kind adjustment     (post +5, comment +0)
      - penalties
clamped to 0..100
```

Drops and penalties:

- **Drop** if `received_at` is older than 72 hours.
- **Drop** if promotional (the poster is selling their own tool: "I built", "we launched", "check out my", "roast my", "beta testers", "lifetime deal").
- **Drop** for job posts and hiring threads.
- **Drop** for MSP tooling (ConnectWise, Autotask, Kaseya) or staffing agencies.
- **Drop** for subreddits on the blocklist in `config/subreddits.yaml` (for example meme or NSFW subs where a keyword fires by accident).
- **-15** if the only matches are generic words with no feedback-tool context (the old repo's context rule, kept).
- **-10** for enterprise scale ("fortune 500", "10,000 employees", "global rollout", "procurement", "rfp").
- `brand_mention` ignores penalties except promo and job drops.

Tiers: **High** at 70 and above, **Medium** at 45 to 69, **Low** below 45. Low leads are stored but never sent.

### 6.3 `config/signals.yaml` (shape)

```yaml
competitors:
  priority: [survicate, surveysparrow, "survey sparrow", simplesat]
  tracked: [qualtrics, medallia, asknicely, retently, zonka, typeform, surveymonkey, hotjar, delighted, nicereply]
brand: [elvan, elvan.ai, getelvan]
product_terms: [nps, csat, ces, enps, "net promoter", "customer satisfaction score", "pmf survey", "sean ellis", "feedback tool", "survey tool", "survey software", "feedback software", "voice of customer", "post-purchase survey"]
integrations: [zendesk, klaviyo, mailchimp, braze, hubspot, shopify, intercom, slack]
request_phrases: ["looking for", "recommend", "what do you use", "what are you using", "any good", "best tool", "best way to", "suggestions for", "is there a tool", "anyone using", "anyone tried"]
switch_phrases: [alternative, alternatives, "switching from", "switch from", "moved away from", "replace", "replacement", "migrate from", "migrating"]
pain_phrases: ["too expensive", "price increase", "pricing", "doubled", "response limit", "limits", "caps", "support is", "buggy", "clunky", "complicated", "hate", "frustrated", "cancel", "not worth"]
feedback_pain_phrases: ["response rate", "no one answers", "survey fatigue", "churn without", "don't know why customers", "after ticket", "post-ticket", "feedback loop"]
promo_phrases: ["i built", "we built", "we launched", "i launched", "check out my", "roast my", "beta testers", "lifetime deal", "ltd", "waitlist"]
job_phrases: ["[hiring]", "hiring", "job opening", "we're hiring", "for hire"]
msp_terms: [connectwise, autotask, kaseya, msp]
enterprise_terms: ["fortune 500", "10,000 employees", "global rollout", "procurement", "rfp"]
```

`config/subreddits.yaml`:

```yaml
bonus: {customersuccess: 8, zendesk: 8, saas: 6, customerservice: 6, startups: 4, productmanagement: 4, shopify: 3, ecommerce: 3, smallbusiness: 2, entrepreneur: 2}
blocklist: []
```

Matching is word-boundary and case-insensitive on normalized text, with plurals handled.

## 7. Data model (SQLite, `data/radar.db`)

```sql
mentions(
  mention_id TEXT PRIMARY KEY, kind TEXT, post_id TEXT, subreddit TEXT, title TEXT,
  snippet TEXT, url TEXT, author TEXT, keyword TEXT, received_at TEXT,
  first_seen TEXT, source TEXT, email_uid TEXT
)
leads(
  mention_id TEXT PRIMARY KEY REFERENCES mentions(mention_id),
  primary_signal TEXT, signals TEXT /* json */, matched_terms TEXT /* json */,
  competitor TEXT, score REAL, tier TEXT, scored_at TEXT,
  alerted_at TEXT, digested_at TEXT, exported_at TEXT,
  status TEXT DEFAULT 'new',  -- new | replied | skipped | not_relevant
  status_at TEXT
)
drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mention_id TEXT, created_at TEXT,
  mentions_elvan INTEGER, draft_text TEXT, notes TEXT, sent_at TEXT, posted INTEGER DEFAULT 0
)
processed_emails(uid TEXT PRIMARY KEY, message_id TEXT, received_at TEXT, mentions_found INTEGER, status TEXT)
runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, started_at TEXT, finished_at TEXT,
  emails_read INTEGER, mentions_found INTEGER, new_mentions INTEGER,
  leads_high INTEGER, leads_medium INTEGER, errors TEXT /* json */
)
state(key TEXT PRIMARY KEY, value TEXT)  -- schema_version, last_update_id, paused, sent draft files
```

Rules:

- A mention is scored once, at first sight. A lead is alerted once.
- **Thread rule:** if a High lead was already alerted for the same `post_id` in the last 24 hours, new mentions in that thread go to the digest instead of a fresh alert.
- The digest includes High and Medium leads where `digested_at` is null.
- The DB is the source of truth. Telegram is only a delivery channel.

## 8. Email ingest (stage 4)

- Gmail account used only for F5Bot alerts. IMAP over SSL (`imap.gmail.com:993`) with a **Gmail app password** (needs 2-step verification on that account).
- Search the folder `IMAP_FOLDER` (default `INBOX`) for mail `FROM F5BOT_SENDER`, then keep only UIDs not in `processed_emails`.
- The first run only looks back `INGEST_LOOKBACK_DAYS` (default 3).
- After an email is parsed, mark it `\Seen` if `IMAP_MARK_SEEN=true`. Never delete it.
- One email can contain several matches. Each match becomes a `Mention`.
- An email that fails to parse: save the raw `.eml` to `data/unparsed/`, record `status='parse_error'`, and continue.
- The F5Bot email format must come from **real saved emails** in `tests/fixtures/f5bot/*.eml`. Do not guess it.

## 9. Telegram

Bot via `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. HTML parse mode, with text escaped. Messages are split at 4,000 characters.

**Hot alert:**

```
HIGH 86 | competitor_pain | r/CustomerSuccess | comment | 40m ago
Thread: What are people moving to after Survicate's price change?

"...we're on a legacy plan and survicate wants to double it..."

Keyword: survicate | Matched: survicate, double
https://www.reddit.com/r/CustomerSuccess/comments/abc123/.../def456/

[Replied] [Skip] [Not relevant]
```

**Digest** (once per scan, only when there's something new): a run summary (emails read, mentions, new leads by tier), then Medium leads in compact form, capped at 15.

**Draft message** (stage 5): the lead header, the draft in `<pre>` so it's easy to copy, whether it mentions Elvan, and the same three buttons.

Callback data: `s:{status}:{short_id}`, under 64 bytes. Use a short hash of `mention_id` and map it back in the DB.

Failed sends: retry once after 5 seconds, then write to `data/delivery_failures/{ts}_{kind}.txt`. Each `scan` resends any files left there, deleting each one once it sends.

## 10. Reply drafting with Claude Code

Claude Code reads the leads file and the reply rules, writes drafts, and `radar send-drafts` validates them and sends them.

- **Manual:** run `/draft-replies` in Claude Code, then `python -m radar send-drafts`.
- **Scheduled (stage 7):** `scripts/run_drafts.ps1` runs `claude -p "/draft-replies data/leads/latest.json" --allowedTools "Read,Write"`, then `send-drafts`.

Leads file (`data/leads/latest.json`):

```json
{
  "generated_at": "2026-11-25T09:00:00+05:30",
  "elvan_mentions_last_7_days": 1,
  "replies_last_7_days": 6,
  "leads": [
    {"mention_id": "comment:def456", "kind": "comment", "post_id": "abc123", "subreddit": "CustomerSuccess",
     "thread_title": "...", "snippet": "...", "url": "...", "age_hours": 0.7,
     "keyword": "survicate", "primary_signal": "competitor_pain", "signals": ["competitor_pain"],
     "competitor": "survicate", "score": 86, "tier": "High"}
  ]
}
```

It includes High and Medium leads with no draft and `status = 'new'`, at most 10.

Drafts file (`data/drafts/{same stamp}.json`):

```json
{"source_leads_file": "...", "drafts": [{"mention_id": "...", "mentions_elvan": false, "draft": "...", "notes": "..."}]}
```

The mention ratio (at most 1 in 5 replies names Elvan) is enforced both by the slash command and by `send-drafts`.

## 11. Config and env

```
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TIMEZONE=Asia/Calcutta
# F5Bot inbox (stage 4)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=
IMAP_APP_PASSWORD=
IMAP_FOLDER=INBOX
F5BOT_SENDER=        # fill from a real F5Bot email's From address
IMAP_MARK_SEEN=true
INGEST_LOOKBACK_DAYS=3
# Scoring
HIGH_THRESHOLD=70
MEDIUM_THRESHOLD=45
MAX_LEAD_AGE_HOURS=72
```


## 12. Schedule (Windows Task Scheduler)

| Task | Command | When (IST) |
|---|---|---|
| `Radar_Scan` | `scripts/run_scan.ps1` | 09:00, 13:00, 17:00, 19:15 |
| `Radar_Drafts` | `scripts/run_drafts.ps1` | 19:30 |
| `Radar_Feedback` | `python -m radar feedback` | 09:05, 19:20, 23:30 |
| `Radar_Weekly_Stats` | `python -m radar stats --days 7` | Fri 18:00 |

## 13. Out of scope

Any direct Reddit access, posting to Reddit, X, Hacker News (F5Bot can alert on HN, but ignore those emails for now), Product Hunt, dashboards, and LLM calls from Python.
