"""GCC Bank AI Tracker agent.

Searches the web (English + Arabic) for AI initiatives announced by or about
GCC banks, keeps only bank-specific AI news, stores it in data/news.json and
pushes new items to Telegram.

Usage:
  python agent/tracker.py update                 # daily run (auto-backfills banks not yet covered)
  python agent/tracker.py backfill [--country QA] [--banks qnb,qib] [--months 24]
  python agent/tracker.py telegram-test          # send a test message
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
BANKS_FILE = ROOT / "config" / "banks.json"
NEWS_FILE = ROOT / "data" / "news.json"
STATE_FILE = ROOT / "data" / "state.json"

MODEL = os.environ.get("TRACKER_MODEL") or "claude-sonnet-5-5"
SEARCH_EFFORT = os.environ.get("TRACKER_EFFORT", "medium")
WORKERS = int(os.environ.get("TRACKER_WORKERS", "4"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 5

CATEGORIES = [
    "Strategy & Investment",
    "Generative AI",
    "Customer Experience",
    "Operations & Automation",
    "Risk, Fraud & Compliance",
    "Partnership",
    "Data & Infrastructure",
    "Talent & Training",
    "Awards & Outcomes",
    "Governance & Regulation",
]
Category = Literal[
    "Strategy & Investment",
    "Generative AI",
    "Customer Experience",
    "Operations & Automation",
    "Risk, Fraud & Compliance",
    "Partnership",
    "Data & Infrastructure",
    "Talent & Training",
    "Awards & Outcomes",
    "Governance & Regulation",
]


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
class ExtractedItem(BaseModel):
    bank_id: str = Field(description="id of the bank from the provided bank list")
    date: str = Field(description="Announcement/publication date, YYYY-MM-DD")
    title: str = Field(description="Concise English headline, max ~110 chars")
    summary: str = Field(description="2-3 sentence English summary of what the bank is doing with AI")
    category: Category
    tags: list[str] = Field(description="2-5 short AI topic tags, e.g. 'GenAI', 'Chatbot', 'Fraud detection'")
    partners: list[str] = Field(description="Named technology partners/vendors, empty if none")
    impact: str = Field(description="Quantified outcome or investment figure if stated (e.g. '30% faster onboarding'), else empty string")
    source_url: str = Field(description="Exact URL of the source article, copied from search results")
    source_name: str = Field(description="Publisher name, e.g. 'Gulf News', 'QNB press release'")
    language: Literal["en", "ar"]


class Extraction(BaseModel):
    items: list[ExtractedItem]


# --------------------------------------------------------------------------- #
# Storage helpers
# --------------------------------------------------------------------------- #
def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def norm_url(url: str) -> str:
    """Normalise a URL for comparison (scheme, www, tracking params, trailing slash)."""
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = p.netloc.lower().removeprefix("www.")
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    )
    path = urllib.parse.unquote(p.path).rstrip("/")
    return f"{host}{path}" + (f"?{query}" if query else "")


def title_tokens(title: str) -> set[str]:
    stop = {"the", "a", "an", "and", "of", "to", "in", "for", "with", "on", "its", "by", "at", "as", "new"}
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in stop and len(w) > 2}


def similar(a: str, b: str) -> bool:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


# --------------------------------------------------------------------------- #
# Claude agent
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = f"""You are a senior research analyst for the Head of AI at a large GCC bank. \
You track how banks in the GCC (UAE, Saudi Arabia, Qatar, Kuwait, Oman, Bahrain) use artificial intelligence.

INCLUDE an item only when ALL of these hold:
- It is about a specific bank from the provided list (including central banks, Islamic banks, digital banks and their \
named subsidiaries/brands), either published by the bank itself or reported by credible media.
- Its subject is that bank's OWN use of AI: AI strategy or investment, AI/GenAI/ML deployments, AI-powered products \
(assistants, chatbots, robo-advice, credit decisioning), AI for fraud/AML/risk/compliance, automation driven by AI, \
data & AI platforms, AI partnerships/MoUs where the bank is a party, AI talent/academy programmes, AI awards or \
measurable AI outcomes, executive statements about the bank's AI roadmap, and (for central banks) AI regulation, \
guidance or supervisory AI tools.

EXCLUDE: fintech/startup news where no listed bank is a party; general AI or tech-industry news; generic \
"digital transformation" with no AI component; market/stock commentary; sponsored listicles; vendor marketing that \
does not name the bank as a client; duplicates of the same announcement.

Search in English and Arabic (use the Arabic bank names; e.g. "<Arabic name> الذكاء الاصطناعي"). Prefer the bank's own \
press release/newsroom when available, otherwise a reputable outlet (Zawya, Gulf News, The National, Arab News, \
Gulf Times, The Peninsula, Arabian Business, Argaam, Times of Oman, Arab Times, Gulf Daily News, Reuters, Bloomberg, etc.).

Be precise about dates: use the article's publication or announcement date. Only report URLs that actually appeared \
in your search/fetch results — never construct or guess a URL.

Categories to use: {", ".join(CATEGORIES)}.

When you are done searching, write a findings report: one entry per qualifying item with bank id, date (YYYY-MM-DD), \
headline, 2-3 sentence summary, category, tags, partners, any quantified impact/investment, source URL, publisher \
and language. If nothing qualifies, say "NO QUALIFYING ITEMS"."""


def bank_line(b: dict) -> str:
    aliases = f"; aka {', '.join(b['aliases'])}" if b.get("aliases") else ""
    return f"- id={b['id']} | {b['name']} ({b.get('name_ar', '')}){aliases} | site: {b['domain']} | type: {b['type']}"


class Agent:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(max_retries=4, timeout=900)

    # -- search step -------------------------------------------------------- #
    def search(self, prompt: str, max_searches: int) -> tuple[str, set[str]]:
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max(3, max_searches // 2)},
        ]
        user_msg = {"role": "user", "content": prompt}
        messages: list[dict] = [user_msg]
        assistant_content: list = []
        seen_urls: set[str] = set()
        report_parts: list[str] = []

        for _ in range(MAX_CONTINUATIONS + 1):
            with self.client.beta.messages.stream(
                model=MODEL,
                max_tokens=32000,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
                output_config={"effort": SEARCH_EFFORT},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                msg = stream.get_final_message()

            if msg.stop_reason == "refusal":
                print(f"  ! refusal: {getattr(msg.stop_details, 'category', None)}", file=sys.stderr)
                break

            for block in msg.content:
                if block.type == "web_search_tool_result" and isinstance(block.content, list):
                    for r in block.content:
                        if getattr(r, "url", None):
                            seen_urls.add(norm_url(r.url))
                elif block.type == "web_fetch_tool_result":
                    url = getattr(block.content, "url", None)
                    if url:
                        seen_urls.add(norm_url(url))
                elif block.type == "text":
                    report_parts.append(block.text)

            if msg.stop_reason != "pause_turn":
                break
            # Paused mid server-tool loop: resend the turn so far and the server resumes it.
            assistant_content += msg.content
            messages = [user_msg, {"role": "assistant", "content": assistant_content}]

        return "".join(report_parts).strip(), seen_urls

    # -- structuring step --------------------------------------------------- #
    def structure(self, report: str, banks: list[dict]) -> list[ExtractedItem]:
        if not report or "NO QUALIFYING ITEMS" in report and len(report) < 200:
            return []
        ids = ", ".join(f"{b['id']} ({b['short']})" for b in banks)
        resp = self.client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            output_format=Extraction,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": (
                    "Convert this research report into structured items. Use only these bank ids: "
                    f"{ids}.\nKeep only items that are clearly about a listed bank's own AI activity. "
                    "Write titles and summaries in English (translate Arabic). Copy source URLs exactly.\n\n"
                    f"<report>\n{report}\n</report>"
                ),
            }],
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return []
        return resp.parsed_output.items


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class Tracker:
    def __init__(self) -> None:
        cfg = load_json(BANKS_FILE, {})
        self.countries = {c["code"]: c for c in cfg["countries"]}
        self.banks = {b["id"]: b for b in cfg["banks"]}
        self.news = load_json(NEWS_FILE, {"updated_at": None, "items": []})
        self.state = load_json(STATE_FILE, {"last_update": None, "backfilled": {}})
        self.agent = Agent()

    # -- merging ------------------------------------------------------------ #
    def merge(self, extracted: list[ExtractedItem], seen_urls: set[str], min_date: dt.date) -> list[dict]:
        added: list[dict] = []
        items = self.news["items"]
        by_url = {(i["bank_id"], norm_url(i["source_url"])) for i in items}
        for e in extracted:
            bank = self.banks.get(e.bank_id)
            if not bank:
                continue
            try:
                d = dt.date.fromisoformat(e.date[:10])
            except ValueError:
                continue
            if d < min_date or d > today() + dt.timedelta(days=1):
                continue
            nurl = norm_url(e.source_url)
            if seen_urls and nurl not in seen_urls:
                print(f"  - dropped (URL not in search results): {e.source_url}", file=sys.stderr)
                continue
            if (e.bank_id, nurl) in by_url:
                continue
            dup = next(
                (i for i in items if i["bank_id"] == e.bank_id
                 and abs((dt.date.fromisoformat(i["date"]) - d).days) <= 10
                 and similar(i["title"], e.title)),
                None,
            )
            if dup:
                if nurl != norm_url(dup["source_url"]) and e.source_url not in dup.setdefault("other_sources", []):
                    dup["other_sources"].append(e.source_url)
                continue
            item = {
                "id": hashlib.sha1(f"{e.bank_id}|{nurl}".encode()).hexdigest()[:12],
                "bank_id": e.bank_id,
                "country": bank["country"],
                "date": d.isoformat(),
                "title": e.title.strip(),
                "summary": e.summary.strip(),
                "category": e.category,
                "tags": [t.strip() for t in e.tags if t.strip()][:5],
                "partners": [p.strip() for p in e.partners if p.strip()],
                "impact": e.impact.strip(),
                "source_url": e.source_url.strip(),
                "source_name": e.source_name.strip(),
                "language": e.language,
                "added_at": now_iso(),
            }
            items.append(item)
            by_url.add((e.bank_id, nurl))
            added.append(item)
        return added

    def save(self) -> None:
        self.news["items"].sort(key=lambda i: (i["date"], i["added_at"]), reverse=True)
        self.news["updated_at"] = now_iso()
        save_json(NEWS_FILE, self.news)
        save_json(STATE_FILE, self.state)

    def known_titles(self, bank_ids: set[str], since: dt.date) -> str:
        rows = [f"- {i['date']} {self.banks[i['bank_id']]['short']}: {i['title']}"
                for i in self.news["items"] if i["bank_id"] in bank_ids and i["date"] >= since.isoformat()]
        return "\n".join(rows[:150]) or "(none)"

    # -- jobs --------------------------------------------------------------- #
    def run_job(self, banks: list[dict], start: dt.date, end: dt.date, max_searches: int, focus: str):
        known = self.known_titles({b["id"] for b in banks}, start)
        prompt = (
            f"{focus}\n\nTime window: {start.isoformat()} to {end.isoformat()} (today is {today().isoformat()}).\n\n"
            f"Banks in scope:\n" + "\n".join(bank_line(b) for b in banks) +
            f"\n\nAlready tracked (do not report again unless materially new):\n{known}"
        )
        report, seen = self.agent.search(prompt, max_searches)
        return self.agent.structure(report, banks), seen

    def backfill(self, bank_ids: list[str], months: int) -> list[dict]:
        end = today()
        start = end - dt.timedelta(days=int(months * 30.5))
        targets = [self.banks[b] for b in bank_ids]
        print(f"Backfilling {len(targets)} banks from {start} …")
        added_all: list[dict] = []

        def job(b):
            focus = (
                f"Build a HISTORY of {b['name']}'s AI initiatives. Find as many distinct qualifying announcements as "
                "possible across the whole window (aim for full coverage of each year; typical large banks have 10-30). "
                f"Check the bank's newsroom on {b['domain']} and search English and Arabic news."
            )
            return b, *self.run_job([b], start, end, max_searches=12, focus=focus)

        with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(job, b) for b in targets]
            for fut in cf.as_completed(futures):
                try:
                    b, extracted, seen = fut.result()
                except Exception as exc:  # keep going; the bank will be retried next run
                    print(f"  ! backfill failed: {exc!r}", file=sys.stderr)
                    continue
                added = self.merge(extracted, seen, start)
                self.state["backfilled"][b["id"]] = today().isoformat()
                added_all += added
                print(f"  {b['short']}: +{len(added)}")
                self.save()  # checkpoint after every bank
        return added_all

    def update(self) -> list[dict]:
        last = self.state.get("last_update")
        start = (dt.date.fromisoformat(last[:10]) if last else today() - dt.timedelta(days=14)) - dt.timedelta(days=3)
        start = max(start, today() - dt.timedelta(days=45))
        groups: list[tuple[str, list[dict]]] = []
        for code, c in self.countries.items():
            commercial = [b for b in self.banks.values() if b["country"] == code and b["type"] != "central"]
            groups.append((f"commercial banks in {c['name']}", commercial))
        groups.append(("GCC central banks", [b for b in self.banks.values() if b["type"] == "central"]))

        print(f"Incremental update from {start} …")
        added_all: list[dict] = []

        def job(label, banks):
            focus = (
                f"Find NEW AI-related announcements from {label} published in the time window. "
                "Search broadly (e.g. '<bank> AI', '<bank> generative AI', '<bank> artificial intelligence', Arabic "
                "equivalents, and the banks' newsrooms). Report every qualifying item."
            )
            return label, *self.run_job(banks, start, today(), max_searches=10, focus=focus)

        with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(job, label, banks) for label, banks in groups]
            for fut in cf.as_completed(futures):
                try:
                    label, extracted, seen = fut.result()
                except Exception as exc:
                    print(f"  ! update failed: {exc!r}", file=sys.stderr)
                    continue
                added = self.merge(extracted, seen, today() - dt.timedelta(days=int(24 * 30.5)))
                added_all += added
                print(f"  {label}: +{len(added)}")
        self.state["last_update"] = now_iso()
        self.save()
        return added_all


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
class Telegram:
    def __init__(self, banks: dict, countries: dict) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
        self.dashboard = os.environ.get("DASHBOARD_URL", "").strip().rstrip("/")
        self.banks, self.countries = banks, countries

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_ids)

    def send(self, text: str) -> None:
        for chat in self.chat_ids:
            body = urllib.parse.urlencode({
                "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
            }).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body)
            try:
                urllib.request.urlopen(req, timeout=30).read()
            except Exception as exc:
                print(f"  ! telegram send failed for {chat}: {exc!r}", file=sys.stderr)
            time.sleep(1.1)  # stay under Telegram's per-chat rate limit

    def format_item(self, i: dict) -> str:
        b, c = self.banks[i["bank_id"]], self.countries[i["country"]]
        esc = html.escape
        lines = [
            f"{c['flag']} <b>{esc(b['short'])}</b> · {esc(i['category'])}",
            f"<b>{esc(i['title'])}</b>",
            esc(i["summary"]),
        ]
        if i.get("impact"):
            lines.append(f"📈 {esc(i['impact'])}")
        link = f'<a href="{esc(i["source_url"], quote=True)}">{esc(i["source_name"] or "Source")}</a>'
        if self.dashboard:
            link += f' · <a href="{self.dashboard}/#/bank/{i["bank_id"]}">Bank history</a>'
        lines.append(f"🗓 {i['date']} · {link}")
        return "\n".join(lines)

    def notify(self, items: list[dict]) -> None:
        if not self.enabled or not items:
            return
        recent = sorted(items, key=lambda i: i["date"], reverse=True)
        if len(recent) <= 12:
            for i in recent:
                self.send("🤖 <b>New GCC bank AI update</b>\n\n" + self.format_item(i))
            return
        # Large batch: send a compact digest, split to Telegram's 4096-char limit.
        chunks, cur = [], f"🤖 <b>{len(recent)} new GCC bank AI updates</b>\n"
        for i in recent:
            b, c = self.banks[i["bank_id"]], self.countries[i["country"]]
            line = (f"\n{c['flag']} <b>{html.escape(b['short'])}</b> – "
                    f'<a href="{html.escape(i["source_url"], quote=True)}">{html.escape(i["title"])}</a> ({i["date"]})')
            if len(cur) + len(line) > 3900:
                chunks.append(cur)
                cur = ""
            cur += line
        if self.dashboard:
            cur += f'\n\n<a href="{self.dashboard}">Open dashboard</a>'
        chunks.append(cur)
        for chunk in chunks:
            self.send(chunk)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["update", "backfill", "telegram-test"])
    ap.add_argument("--country", help="Backfill only this country code (e.g. QA)")
    ap.add_argument("--banks", help="Comma-separated bank ids to backfill")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--force", action="store_true", help="Backfill banks even if already done")
    ap.add_argument("--max-banks", type=int, default=0, help="Cap banks backfilled in this run (0 = no cap)")
    args = ap.parse_args()

    tracker = Tracker()
    tg = Telegram(tracker.banks, tracker.countries)

    if args.mode == "telegram-test":
        if not tg.enabled:
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        tg.send("✅ GCC Bank AI Tracker is connected. You'll receive new bank AI updates here.")
        return

    if args.mode == "backfill":
        ids = [b.strip() for b in args.banks.split(",")] if args.banks else [
            b["id"] for b in tracker.banks.values() if not args.country or b["country"] == args.country.upper()
        ]
        if not args.force:
            ids = [i for i in ids if i not in tracker.state["backfilled"]]
        if args.max_banks:
            ids = ids[: args.max_banks]
        added = tracker.backfill(ids, args.months)
        if added and tg.enabled:
            tg.send(f"📚 History loaded: <b>{len(added)}</b> AI initiatives added for {len(ids)} banks."
                    + (f'\n<a href="{tg.dashboard}">Open dashboard</a>' if tg.dashboard else ""))
        print(f"Done. {len(added)} items added.")
        return

    # update: first finish any banks that still lack history (no Telegram spam for history).
    pending = [b for b in tracker.banks if b not in tracker.state["backfilled"]]
    max_backfill = int(os.environ.get("AUTO_BACKFILL_PER_RUN", "80"))
    if pending and max_backfill:
        hist = tracker.backfill(pending[:max_backfill], args.months)
        if hist and tg.enabled:
            tg.send(f"📚 History loaded: <b>{len(hist)}</b> past AI initiatives added to the dashboard.")
    added = tracker.update()
    fresh = [i for i in added if i["date"] >= (today() - dt.timedelta(days=30)).isoformat()]
    tg.notify(fresh)
    print(f"Done. {len(added)} new items ({len(fresh)} recent, sent to Telegram).")


if __name__ == "__main__":
    main()
