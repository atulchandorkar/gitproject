# GCC Bank AI Tracker

A mobile-first dashboard and Telegram alert service that tracks **AI initiatives by banks in the GCC**: strategy, investments, GenAI deployments, partnerships, talent programmes, awards and measurable outcomes. It covers the UAE, Saudi Arabia, Qatar, Kuwait, Oman and Bahrain.

- **75 institutions**: conventional, Islamic, digital and development banks, plus the 6 central banks (`config/banks.json`)
- **Bank news only.** An AI agent (Claude with web search) keeps only items about a listed bank's own use of AI. It drops fintech news, general AI news and generic "digital" stories.
- **English and Arabic sources.** Summaries are written in English, and every item links to its original source.
- **2 years of history**, loaded automatically on the first run, then **checked every 12 hours**
- **Telegram push** for each new item, to you, a group or a channel
- **Slice and dice** by country, bank, category, period or free-text search. Each bank has a profile page with its full AI timeline.
- **Shareable links.** Every view has its own URL (for example `…/#/bank/qnb` or `…/#/?country=SA&cat=Generative%20AI`). The share button uses your phone's share sheet.

```
 every 12h ┌──────────────────────┐   new items   ┌───────────┐
──────────►│ GitHub Action        │──────────────►│ Telegram  │
           │  agent/tracker.py    │               └───────────┘
           │  Claude + web search │  data/news.json
           └──────────┬───────────┘──────────────►┌────────────────────┐
                      └─ commits data ───────────►│ GitHub Pages site  │◄── colleagues (mobile)
                                                  └────────────────────┘
```

## One-time setup (about 15 minutes)

### 1. Put the code on `main`
Scheduled GitHub Actions only run on the default branch. Merge this branch into `main`.

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
| Variable *(optional)* | `TRACKER_MODEL` | Leave empty for Claude Opus 5.5, or set `claude-sonnet-5-5` to roughly halve cost |

### 5. First run
**Actions → GCC Bank AI Tracker → Run workflow**
1. `mode = telegram-test`: you should receive a "connected" message.
2. `mode = update`: the first run loads **24 months of history** for every bank (1–2 hours), then finds the latest news. After that it runs by itself every 12 hours, at 07:17 and 19:17 Gulf time.

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
- **Claude API**, at the default model and effort, roughly:
  - **one-time history load:** about $60–150
  - **each 12-hour run:** about $3–8 (7 searches covering all banks), or about **$200–450 a month**

  With `TRACKER_MODEL=claude-sonnet-5-5` these figures roughly halve. Usage depends on how much news exists, so set a **monthly spend limit** in the Anthropic Console. Check the real cost in the Console after the first few runs. Then tune the frequency (the `cron` line in `.github/workflows/tracker.yml`) or the model if needed.

## How the agent decides what counts
The inclusion rules are in `SYSTEM_PROMPT` in `agent/tracker.py`. Each run works in two steps:
1. **Research.** Claude searches English and Arabic news and the banks' newsrooms within a date window. It is told which items are already tracked, so it skips them.
2. **Structure.** The findings are converted into strict JSON with a bank id, date, category, tags, partners, impact and source.

Before an item is saved, the code checks three things:
- the bank id is known
- the date falls inside the window
- the source URL actually appeared in the search results, which stops invented links

The same story from several outlets is merged into one item, with the extra links kept as additional sources.

Categories: Strategy & Investment · Generative AI · Customer Experience · Operations & Automation · Risk, Fraud & Compliance · Partnership · Data & Infrastructure · Talent & Training · Awards & Outcomes · Governance & Regulation.

## Project layout
```
config/banks.json            bank universe (names, Arabic names, domains, aliases)
agent/tracker.py             AI research agent + Telegram notifier
data/news.json               the news database (updated by the Action)
data/state.json              run bookkeeping (last run, history progress)
site/                        static mobile dashboard (HTML/CSS/JS, no build step)
.github/workflows/tracker.yml  12-hourly schedule, manual runs, Pages deploy
```
