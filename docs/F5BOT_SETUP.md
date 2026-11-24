# F5Bot setup (Aman does this by hand)

Do this as soon as you can. It doesn't block stages 2 and 3, but stage 4 needs at least 3 real F5Bot emails saved as `.eml` files.

## 1. Gmail

1. Create a Gmail account used only for this, e.g. `elvan.radar@gmail.com`.
2. Turn on 2-step verification (Google Account -> Security).
3. Create an app password (Google Account -> Security -> App passwords). Name it "reddit radar". Copy the 16-character password.
4. Make sure IMAP access is on (Gmail settings -> See all settings -> Forwarding and POP/IMAP).

## 2. F5Bot

1. Go to f5bot.com and sign up with that Gmail address. Confirm the email.
2. Add these keywords, one at a time. Keep them specific; generic words flood the inbox.

| Group | Keywords |
|---|---|
| Priority competitors | `survicate`, `surveysparrow`, `simplesat` |
| Other competitors | `asknicely`, `retently`, `zonka`, `nicereply` |
| Intent | `nps tool`, `nps software`, `nps survey`, `csat tool`, `csat survey`, `zendesk csat`, `customer feedback tool`, `pmf survey`, `delighted alternative` |
| Brand | `elvan.ai`, `getelvan` |

3. If F5Bot also sends Hacker News or Lobsters matches, that's fine. The app ignores them for now.
4. Leave out very common words like `delighted`, `qualtrics` and `typeform`. They match lots of unrelated posts. Add them later only if the stats say you're missing leads.

## 3. Save sample emails for stage 4

After a day or two of alerts:

1. Open 3 to 5 different F5Bot emails in Gmail on desktop. Pick a mix: one with a post, one with a comment, and one with several matches if you have it.
2. For each one: three-dot menu -> **Download message**. That saves a `.eml` file.
3. Put them in `tests/fixtures/f5bot/` in the repo.
4. Look at who the emails come **From** (the exact address) and put it in `.env` as `F5BOT_SENDER=`.
5. These files contain only public Reddit text and your alert address. If you'd rather not commit them, add `tests/fixtures/f5bot/` to `.gitignore`. Tests should then skip cleanly when the folder is empty.

## 4. `.env` lines you'll add at stage 4

```
IMAP_USER=elvan.radar@gmail.com
IMAP_APP_PASSWORD=xxxxxxxxxxxxxxxx
F5BOT_SENDER=<exact From address>
```
