# GCC Banking – AI Pulse Monitor

*Tracking AI in Gulf banks, daily.*

A mobile-first dashboard and Telegram alert service that tracks **AI initiatives by banks in the GCC**: strategy, investments, GenAI deployments, partnerships, talent programmes, awards and measurable outcomes. It covers the UAE, Saudi Arabia, Qatar, Kuwait, Oman and Bahrain.

- **74 institutions**: conventional, Islamic, digital and development banks, plus the 6 central banks (`config/banks.json`)
- **Bank news only.** Free Google News feeds find candidate headlines. Claude Haiku, the cheapest Claude model, keeps only items about a listed bank's own use of AI and drops fintech news, general AI news and generic "digital" stories.
- **English and Arabic sources.** Summaries are written in English, and every item links to its original source.
- **2 years of history**, loaded automatically on the first run, then **checked once a day**
- **Telegram push** for each new item, to you, a group or a channel
- **Slice and dice** by country, bank, category, period or free-text search. Each bank has a profile page with its full AI timeline.
- **Shareable links.** Every view has its own URL (for example `…/#/bank/qnb` or `…/#/?country=SA&cat=Generative%20AI`). The share button uses your phone's share sheet.

```
 daily     ┌──────────────────────┐   new items   ┌───────────┐
──────────►│ GitHub Action        │──────────────►│ Telegram  │
           │  agent/tracker.py    │               └───────────┘
           │ Google News RSS free │  data/news.json
           │ + Claude Haiku screen│
           └──────────┬───────────┘──────────────►┌────────────────────┐
                      └─ commits data ───────────►│ GitHub Pages site  │◄── colleagues (mobile)
                                                  └────────────────────┘
```

## One-time setup (about 15 minutes)

### 1. Put the code on `master`
Scheduled GitHub Actions only run on the default branch. Merge this branch into `master`.

### 2. Turn on GitHub Pages
1. **Settings → General → Danger zone → Change visibility → Public.** Pages is free for public repos. The data is public news, so this is safe. Private Pages needs a paid GitHub plan.
2. **Settings → Pages → Build and deployment → Source: _GitHub Actions_.**

Your dashboard URL will be **`https://atulchandorkar.github.io/gitproject/`**

### 3. Create the Telegram bot
1. In Telegram, open **@BotFather**, send `/newbot` and follow the prompts. Copy the **bot token**.
2. Send any message (for example "hi") to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser. Your **chat id** is the number in `"chat":{"id": 123456789 …}`.
   - *To alert colleagues too:* create a Telegram **channel**, add the bot as an **admin**, and use the channel's id (`@yourchannel` or `-100…`). You can list several ids separated by commas: `123456789,@bankai_news`.

### 4. Add the secrets
In **Settings → Secrets and variables → Actions**:

| Type | Name | Value |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | Your key from console.anthropic.com |
| Secret | `TELEGRAM_BOT_TOKEN` | The token from BotFather |
| Secret | `TELEGRAM_CHAT_ID` | Your chat id(s), comma-separated |
| Variable | `DASHBOARD_URL` | `https://atulchandorkar.github.io/gitproject` |
| Variable *(optional)* | `TRACKER_MODEL` | Leave empty to use the default, Claude Haiku 4.5 (`claude-haiku-4-5`), the cheapest option. Set `claude-sonnet-5-5` for sharper screening at about twice the cost. |

### 5. First run
**Actions → GCC Bank AI Tracker → Run workflow**
1. `mode = telegram-test`: you should receive a "connected" message.
2. `mode = update`: the first run loads **24 months of history** for every bank (about 30–45 minutes), then finds the latest news. After that it runs by itself once a day, at 07:17 Gulf time.

History is saved bank by bank. If a run stops partway, the next run picks up where it left off. History items are not pushed to Telegram one by one; you get a single summary message instead.

## Day-to-day operations

| Task | How |
|---|---|
| Re-load history for a country or bank | Run workflow with `mode = backfill`, plus `country = QA` or `banks = qnb,qib`, and tick `force` |
| Add a bank | Add an entry to `config/banks.json`. Its history loads on the next run. |
| Fix or set a bank's newsroom page | Add `"newsroom": "https://…"` to that bank in `config/banks.json`. The status of every newsroom is saved in `data/state.json` under `newsrooms`. |
| Publish the dashboard only | Run workflow with `mode = deploy` |
| Run locally | `pip install -r agent/requirements.txt && ANTHROPIC_API_KEY=… python agent/tracker.py update` |

## Running costs (estimate)
- **GitHub and Telegram:** free.
- **News discovery:** free (Google News RSS).
- **Claude API (Haiku 4.5)**, which only screens headlines:
  - **one-time history load:** about $3–8, including the fact-checks
  - **each daily run:** a few cents, or about **$1–5 a month**

  Costs are kept low in four ways:
  - A free keyword filter drops headlines with no AI words before Claude sees anything.
  - Copies of the same story from different outlets are merged first, so Claude screens each story once.
  - Headlines are screened in batches of 40.
  - Every screened headline is remembered, so the same headline is never paid for twice.

  To be safe, set a **monthly spend limit** in the Anthropic Console. If credit runs out, the run stops at once, sends you a Telegram alert, and continues from the same point after you top up.

  **Trade-off:** an announcement on a bank's own website is caught only if that bank's newsroom page can be read automatically. Some bank sites need JavaScript or block automated visitors. Summaries are based on the headline, not the full article.

## How the agent decides what counts
Each run works in three steps:
1. **Find (free).** Two sources are used:
   - **The banks' own newsrooms** (daily runs). For each bank, the tracker finds the "Newsroom / Media Centre / المركز الإعلامي" page from the bank's homepage, remembers it, and reads the latest press releases. Release dates come from the link or the article page. To fix a bank's newsroom address by hand, add `"newsroom": "https://…"` to that bank in `config/banks.json`. Pages that need JavaScript, or that block automated visitors, cannot be read; the run log reports how many newsrooms were readable.
   - **Google News RSS.** For every bank, the code queries it in English (bank name, aliases and AI terms) and in Arabic (the Arabic bank name plus "الذكاء الاصطناعي"). Daily runs look at the last 7 days. The history load searches quarter by quarter over 24 months.
2. **Filter (free).** Headlines without AI-related words are dropped. Duplicate stories are merged. Headlines screened in an earlier run are skipped.
3. **Screen (Claude Haiku).** The remaining headlines go to Claude in batches. Claude keeps only a bank's own AI news, names the bank, and assigns a category, tags, partners and any stated impact. It also writes a short English summary that sticks to what the headline says. The inclusion rules are in `system_prompt()` in `agent/tracker.py`.

### Accuracy checks (every item, before it is published)
1. **Real source.** The link always comes from a Google News entry or the bank's own newsroom. Claude never supplies URLs.
2. **Bank named.** The original headline or the article text must name the bank: its English name, Arabic name or alias.
3. **No invented numbers.** Every figure in the title, summary or impact line must appear in the source. If one doesn't, the title falls back to the original headline and the unsupported text is removed.
4. **Independent AI fact-check.** A second Claude pass, with a separate "sceptical fact-checker" prompt, checks every claim. It uses the **full article text** whenever the page can be read, and the original headline otherwise.
   - If the item is about the wrong bank, or isn't the bank's own AI activity, it is rejected.
   - Unsupported titles, summaries, figures or partners are removed.
5. **Trusted source.** The item must come from the bank's own newsroom, an official agency (WAM, SPA, QNA, KUNA, ONA, BNA), or a reputable outlet (the list is in `agent/verify.py`). Anything else is **held back until a second, independent outlet reports the same story**, and dropped after 21 days if none does.

Each card shows:
- the verification result, e.g. "✓ Trusted outlet · checked against full article"
- the original headline
- **every source** that reported the story

Rejected items are logged with their reason in `data/rejected.json` for audit. If a check can't run, for example because the page is unreachable or the API is down, the item is retried on the next two runs. Items collected before these checks existed are re-checked the same way.

### One story, one card
Outlets word the same story differently, so each story is shown once:
- **Word match:** the bank's own names are ignored and words are reduced to their stems. Two headlines count as the same story when they overlap strongly.
- **AI check:** close pairs that are still unclear get one cheap Haiku question, "same event?", and the answer is cached.
- **Merging:** copies are merged into the best-sourced card (the bank's own release first), and every outlet is kept as a source.

The same logic applies to Sector Insights; there, two items from the same publisher are always checked.

### Themes
These themes come from what the collected news actually covers. Each item has one main theme plus every theme that applies:
- AI Strategy & Leadership
- AI Adoption in Operations
- Generative AI & Copilots
- Tech Vendor Partnerships
- Customer-Facing AI
- Fraud, Risk & Compliance
- AI Skills & Talent
- Awards & Rankings
- AI Regulation & Policy

The dashboard charts the share of each theme and the top tech partners, overall and per country. Tap a bar to filter. The definitions are in `CATEGORY_GUIDE` in `agent/tracker.py`.

## Bank annual reports (📘)
The agent also reads each bank's own **annual report**, a PDF on the bank's website, for the last two fiscal years. It extracts the concrete AI implementations the report discloses. Each disclosure becomes a card tagged to that bank, with:
- the exact quote and page number
- a **📘 Annual Report (PDF)** button that opens the official file at that page

The bank's page lists all of its annual reports found so far.

**Finding the report:**
1. `annual_reports` (direct PDF links) or `annual_reports_pages` in `config/banks.json`, if set
2. the investor-relations pages linked from the bank's homepage
3. a DuckDuckGo search restricted to the bank's own domain

**Reading it:**
- `pypdf` extracts the text, and only the sentences that mention AI (plus one sentence of context) go to Claude Haiku. That's roughly 30k characters per report.

**Checks on every disclosure:**
- The quote must appear word for word in the PDF; the page number is corrected if it is wrong.
- Every number must be on that page.
- The independent fact-check must confirm the claims against the page text.
- Failures are logged in `data/rejected.json`.

**Schedule:**
- Up to 30 minutes per daily run; banks not reached continue the next day.
- A report that isn't out yet is looked for again every 14 days.
- `data/annual_reports.json` lists the PDFs found.

PDFs are linked, not copied. 74 banks × 2 years would be several GB, more than GitHub Pages allows.

## Banking Sector Insights (the Sector tab)
This tab covers AI across the **banking sector worldwide**, with GCC items flagged. It holds the last 12 months, in seven categories:
**Studies & Surveys**, **Case Studies & Use Cases**, **Maturity & Rankings**, **Regulation & Guidance**, **Market Data**, **Expert Views** and **Events & Initiatives**.

- **Region filter:** All, 🌐 Global, GCC (all), GCC-wide, or a single GCC country.
- **Where items come from:**
  - Google News searches (English and Arabic) for global studies (McKinsey, BCG, Accenture, PwC, Deloitte, EY, KPMG, Gartner, IDC …), bank AI case studies and use cases, global regulators (BIS, FSB, EBA, ECB, Fed, FCA, MAS …) and market data
  - GCC-specific searches, so Gulf items are never missed
  - the publishers' own press pages, read on a best-effort basis
- **What stays out:** a GCC bank's own news stays in the News tab, so nothing appears twice. AI deployments by non-GCC banks are kept as use cases.
- **Checks on every item:**
  - The source must be about AI in banking.
  - Every number must appear in the source.
  - A **key figure** is shown only when its exact quote is in the source text.
  - The source must be official, trusted or confirmed by a second outlet; otherwise the item is held back for 21 days.
  - Rejected items are logged in `data/sector_rejected.json`.
- **Limits:** up to 250 items are fact-checked per run; the rest wait for the next run.

## Project layout
```
config/banks.json            bank universe (names, Arabic names, domains, aliases)
agent/tracker.py             news finder + Claude screener + Telegram notifier
agent/verify.py              accuracy checks (bank named, numbers, AI fact-check, trusted/corroborated source)
agent/newsrooms.py           reads the banks' own newsroom pages
agent/logos.py               downloads bank logos (site icons, Wikidata, Wikipedia, homepage logo, favicons; checked by file content)
agent/annual_reports.py      finds and reads banks' annual reports, extracts AI disclosures
agent/dedupe.py              merges copies of the same story
agent/sector.py              GCC Banking Sector Insights (studies, rankings, regulation …)
config/sector_sources.json   sector categories and publishers (consultancies, research firms, regulators)
data/sector.json             sector insights database (last 12 months)
data/rejected.json           items that failed verification, with the reason
data/news.json               the news database (updated by the Action)
data/state.json              run bookkeeping (last run, history progress)
site/                        static mobile dashboard (HTML/CSS/JS, no build step)
.github/workflows/tracker.yml  daily schedule, manual runs, Pages deploy
```
