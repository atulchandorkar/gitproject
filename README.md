# GCC Bank AI Tracker

A mobile-first dashboard and Telegram alert service that tracks **AI initiatives by banks in the GCC**: strategy, investments, GenAI deployments, partnerships, talent programmes, awards and measurable outcomes. It covers the UAE, Saudi Arabia, Qatar, Kuwait, Oman and Bahrain.

- **75 institutions**: conventional, Islamic, digital and development banks, plus the 6 central banks (`config/banks.json`)
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
| Publish the dashboard only | Run workflow with `mode = deploy` |
| Run locally | `pip install -r agent/requirements.txt && ANTHROPIC_API_KEY=… python agent/tracker.py update` |

## Running costs (estimate)
- **GitHub and Telegram:** free.
- **News discovery:** free (Google News RSS).
- **Claude API (Haiku 4.5)**, which only screens headlines:
  - **one-time history load:** about $1–4
  - **each daily run:** a few cents, or about **$1–5 a month**

  Costs are kept low in four ways:
  - A free keyword filter drops headlines with no AI words before Claude sees anything.
  - Copies of the same story from different outlets are merged first, so Claude screens each story once.
  - Headlines are screened in batches of 40.
  - Every screened headline is remembered, so the same headline is never paid for twice.

  To be safe, set a **monthly spend limit** in the Anthropic Console. If credit runs out, the run stops at once, sends you a Telegram alert, and continues from the same point after you top up.

  **Trade-off:** discovery relies on news coverage, so an announcement that appears only on a bank's own website, with no news article, can be missed. Summaries are based on the headline, not the full article.

## How the agent decides what counts
Each run works in three steps:
1. **Find (free).** For every bank, the code queries Google News RSS in English (bank name, aliases and AI terms) and in Arabic (the Arabic bank name plus "الذكاء الاصطناعي"). Daily runs look at the last 7 days. The history load searches quarter by quarter over 24 months.
2. **Filter (free).** Headlines without AI-related words are dropped. Duplicate stories are merged. Headlines screened in an earlier run are skipped.
3. **Screen (Claude Haiku).** The remaining headlines go to Claude in batches. Claude keeps only a bank's own AI news, names the bank, and assigns a category, tags, partners and any stated impact. It also writes a short English summary that sticks to what the headline says. The inclusion rules are in `system_prompt()` in `agent/tracker.py`.

Every item links to the article it came from. The extra outlets for the same story are kept as additional sources.

Categories: Strategy & Investment · Generative AI · Customer Experience · Operations & Automation · Risk, Fraud & Compliance · Partnership · Data & Infrastructure · Talent & Training · Awards & Outcomes · Governance & Regulation.

## Project layout
```
config/banks.json            bank universe (names, Arabic names, domains, aliases)
agent/tracker.py             news finder + Claude screener + Telegram notifier
data/news.json               the news database (updated by the Action)
data/state.json              run bookkeeping (last run, history progress)
site/                        static mobile dashboard (HTML/CSS/JS, no build step)
.github/workflows/tracker.yml  daily schedule, manual runs, Pages deploy
```
