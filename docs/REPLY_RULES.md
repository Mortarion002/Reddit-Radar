# Reddit Reply Rules (for `/draft-replies`)

These rules decide how every reply draft is written. Aman edits and posts by hand from a disclosed Elvan-team account.

## The point of a reply

Help the person first. A reply that solves their problem without naming Elvan is a good reply. Reddit is also one of the most-cited sources in AI answers, so a genuinely useful, specific reply has value even when it doesn't sell.

## Mention rules

- Name Elvan in **at most 1 of every 5 replies**. The leads file gives `elvan_mentions_last_7_days` and `replies_last_7_days`; keep the ratio at or under 20%.
- Only mention Elvan when the post asks for a tool or wants to replace one, and Elvan genuinely fits the need.
- Whenever Elvan is named, disclose it in the same sentence: "Full disclosure, I'm on the Elvan team."
- When Elvan is named, also name 1 or 2 real alternatives with an honest trade-off.
- No links by default. Write "elvan.ai" as plain text only if they asked for a tool. Never use UTM links on Reddit.
- New account rule: for the first 2 to 3 weeks of the account's history, never mention Elvan at all. Aman will say when that period is over.

## When not to mention Elvan

- They need more than 2 questions per survey, in-app targeting, or MSP tools (ConnectWise, Autotask).
- They're an enterprise team running procurement or an RFP.
- They're venting rather than asking.
- They use Nicereply and are happy with it.
- The subreddit bans self-promotion. CustomerService and smallbusiness are strict.

## Posts vs comments

Leads come from F5Bot and can be a post or a comment (`kind`). You only have the thread title and a snippet, not the full thread. For a comment, the draft is a reply to that comment, so speak to the commenter's point, not the original post. If the snippet is too thin to answer well, write a short draft and say in `notes`: "open the thread before posting, snippet is thin".

## Voice

- Sound like a practitioner who has run NPS/CSAT programs. Be specific and concrete.
- 50 to 150 words. Short paragraphs. No bullet lists unless they asked for a list of options.
- No em dashes, no emojis, no hashtags, no "Great question!", no "I hope this helps".
- Don't sound like an ad. No superlatives ("best", "game-changer", "revolutionary").
- Match the subreddit. r/SaaS and r/startups are casual; r/CustomerSuccess is operational.
- End with something useful (a next step, or a question back to them), not a pitch.

## Facts you may use

- Elvan: NPS, CSAT, CES, eNPS, PMF, 5-Star, Thumbs and Smiley surveys. AI summaries of responses in plain English.
- Free plan: 100 responses/month. Pro: $49/month for 1,000 responses.
- Native Zendesk integration (on the Zendesk Marketplace): sends a CSAT survey when a ticket closes. Also a Web SDK, embeds, and email via CSV upload.
- Delighted shut down on June 30, 2026. Past tense only.
- Competitor prices: never quote them from memory. If a price matters, write "[verify price on their site]" in `notes` and keep the price out of the draft.

Never claim: accuracy percentages, "set up in minutes", customer names, case studies, Klaviyo/Mailchimp/Braze/HubSpot native integrations, or any feature not listed above. If the right answer depends on an unknown feature, say so in `notes` ("check with Balram: does X work?") and write the draft without it.

## Shape by signal

| Signal | Approach |
|---|---|
| `tool_request` | Ask or infer their use case, give 2 to 3 options with a trade-off for each. Elvan can be one option if the ratio allows. |
| `competitor_pain` | Acknowledge the specific pain, suggest how to evaluate options (response volume, cost per response, integrations). Mention Elvan only if the ratio allows and it fits. |
| `alternative_search` | Same as `tool_request`, framed around what they're leaving and why. |
| `integration_need` | Answer the how-to directly first (native options, Zendesk's built-in CSAT, automation). Elvan's Zendesk app is fair to mention for Zendesk. For other tools, give no Elvan claims. |
| `delighted_fallout` | Practical help: export the data, keep the question wording the same so scores stay comparable, rebaseline. |
| `pmf_founder` | Explain the Sean Ellis question, sample size, who to survey. No pitch unless asked. |
| `feedback_pain` | Concrete tactics: survey timing, one question, in-context triggers, closing the loop. No pitch. |
| `brand_mention` | Thank them, answer honestly, disclose, flag for Ravi in `notes` if it reads warm. |

## Example

Post: "Survicate pricing went up again, we only run a quarterly NPS and a post-ticket CSAT. What are people moving to?"

Good draft (with a mention, ratio allowing):

> For just NPS plus post-ticket CSAT you probably don't need a full research suite anymore. I'd compare on cost per response at your volume and whether it connects to your helpdesk natively, since that's usually what drives the bill. Simplesat is solid if you live in Zendesk. Full disclosure, I'm on the Elvan team: we do NPS and CSAT with AI summaries, 1,000 responses for $49 a month, with a native Zendesk app. Whatever you pick, keep the same question wording so your NPS trend doesn't reset. How many responses a quarter are you at?

Notes: "Mentions Elvan; ratio at 1/6 before this. Simplesat pitched as fair alternative."
