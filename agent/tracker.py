"""GCC Bank AI Tracker agent (low-cost edition).

Discovery is free: Google News RSS search feeds (English + Arabic) per bank.
Claude Haiku only screens the new headlines in batches: it keeps bank-specific AI news,
assigns the bank and category, and writes a short English summary.

Usage:
  python agent/tracker.py update                 # daily run (auto-loads history for banks not yet covered)
  python agent/tracker.py backfill [--country QA] [--banks qnb,qib] [--months 24] [--force]
  python agent/tracker.py telegram-test          # send a test message
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
BANKS_FILE = ROOT / "config" / "banks.json"
NEWS_FILE = ROOT / "data" / "news.json"
STATE_FILE = ROOT / "data" / "state.json"

MODEL = os.environ.get("TRACKER_MODEL") or "claude-haiku-4-5"
BATCH = 40                 # headlines per Claude call
WORKERS = 4                # parallel Claude calls
FEED_PAUSE = 0.8           # seconds between Google News requests (be polite)
SEEN_KEEP_DAYS = 800       # remember screened headlines so they are never paid for twice

CATEGORIES = [
    "Strategy & Investment", "Generative AI", "Customer Experience", "Operations & Automation",
    "Risk, Fraud & Compliance", "Partnership", "Data & Infrastructure", "Talent & Training",
    "Awards & Outcomes", "Governance & Regulation",
]
Category = Literal[
    "Strategy & Investment", "Generative AI", "Customer Experience", "Operations & Automation",
    "Risk, Fraud & Compliance", "Partnership", "Data & Infrastructure", "Talent & Training",
    "Awards & Outcomes", "Governance & Regulation",
]

AI_TERMS_EN = ('AI OR "artificial intelligence" OR "generative AI" OR GenAI OR "machine learning" OR chatbot '
               'OR "virtual assistant" OR "AI-powered" OR agentic OR LLM')
AI_TERMS_AR = '"الذكاء الاصطناعي" OR "ذكاء اصطناعي" OR "التعلم الآلي"'
# Cheap local pre-filter: a headline must mention AI-ish words before Claude ever sees it.
AI_RE = re.compile(
    r"\b(ai|a\.i\.|genai|gen ai|llm|llms|gpt|copilot|chatbot|chat bot|agentic|machine learning|"
    r"artificial intelligence|generative|virtual assistant|digital assistant|ai-powered|ai-driven|"
    r"automation|robotic|robo|algorithm|predictive|analytics|data platform|digital human|avatar|"
    r"intelligent|smart assistant|voice assistant|biometric|facial recognition)\b"
    r"|الذكاء الاصطناعي|ذكاء اصطناعي|الذكاء الإصطناعي|التعلم الآلي|روبوت|مساعد افتراضي|المساعد الرقمي|التحليلات",
    re.IGNORECASE,
)
# Short codes that are distinctive enough to search on their own.
SHORT_OK = {"QNB", "QIB", "FAB", "SNB", "NBK", "KFH", "DIB", "ADIB", "ADCB", "ENBD", "QIIB", "BisB", "SAMA",
            "CBUAE", "SAIB", "NBB", "KIB", "BBK", "RAKBANK"}


class FatalAPIError(RuntimeError):
    """Billing/auth problems: retrying other batches is pointless."""


# --------------------------------------------------------------------------- #
# Models for Claude's structured output
# --------------------------------------------------------------------------- #
class Kept(BaseModel):
    i: int = Field(description="index of the headline in the input list")
    bank_id: str = Field(description="id of the bank the news is about, from the bank list")
    title: str = Field(description="clear English headline (translate Arabic), max ~110 chars")
    summary: str = Field(description="one or two English sentences, ONLY facts stated or directly implied by the headline")
    category: Category
    tags: list[str] = Field(description="1-4 short AI topic tags, e.g. 'GenAI', 'Chatbot', 'Fraud detection'")
    partners: list[str] = Field(description="technology partners named in the headline, else empty")
    impact: str = Field(description="number/outcome stated in the headline (e.g. '$100m investment'), else empty")


class Screened(BaseModel):
    items: list[Kept]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def title_tokens(title: str) -> set[str]:
    stop = {"the", "a", "an", "and", "of", "to", "in", "for", "with", "on", "its", "by", "at", "as", "new",
            "bank", "launches", "announces"}
    return {w for w in re.findall(r"[\w]+", title.lower()) if w not in stop and len(w) > 2}


def similar(a: str, b: str, threshold: float = 0.5) -> bool:
    ta, tb = title_tokens(a), title_tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= threshold


def key_of(title: str) -> str:
    return hashlib.sha1(" ".join(sorted(title_tokens(title))).encode()).hexdigest()[:14]


def is_arabic(s: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", s))


# --------------------------------------------------------------------------- #
# Free discovery: Google News RSS
# --------------------------------------------------------------------------- #
def search_terms(b: dict) -> list[str]:
    terms = [b["name"]] + list(b.get("aliases", [])) + [b["short"]]
    out = []
    for t in terms:
        t = t.strip()
        if t and t not in out and (" " in t or len(t) >= 5 or t in SHORT_OK):
            out.append(t)
    return out


def gnews(query: str, lang: str) -> list[dict]:
    params = {"q": query, "hl": lang, "gl": "AE", "ceid": f"AE:{lang}"}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (GCC-Bank-AI-Tracker)"})
    for attempt in range(3):
        try:
            body = urllib.request.urlopen(req, timeout=25).read()
            break
        except Exception as exc:
            if attempt == 2:
                print(f"  ! feed failed ({exc!r}): {query[:60]}", file=sys.stderr)
                return []
            time.sleep(3 * (attempt + 1))
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            date = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            continue
        link = (it.findtext("link") or "").strip()
        if title and link:
            out.append({"title": title, "url": link, "source": source, "date": date.isoformat(),
                        "language": "ar" if is_arabic(title) else "en"})
    return out


def bank_queries(b: dict, window: tuple[dt.date, dt.date] | None) -> list[tuple[str, str]]:
    names = " OR ".join(f'"{t}"' for t in search_terms(b))
    when = f" after:{window[0].isoformat()} before:{window[1].isoformat()}" if window else " when:7d"
    qs = [(f"({names}) ({AI_TERMS_EN}){when}", "en")]
    if b.get("name_ar"):
        qs.append((f'"{b["name_ar"]}" ({AI_TERMS_AR}){when}', "ar"))
    return qs


def discover(banks: list[dict], windows: list[tuple[dt.date, dt.date] | None]) -> list[dict]:
    """Fetch headlines for each bank/window, pre-filter for AI words, collapse duplicate stories."""
    found: list[dict] = []
    for b in banks:
        for w in windows:
            for q, lang in bank_queries(b, w):
                for c in gnews(q, lang):
                    if AI_RE.search(c["title"]):
                        c["bank_hint"] = b["id"]
                        found.append(c)
                time.sleep(FEED_PAUSE)
    # Drop exact repeats, then collapse the same story from several outlets (same bank, similar title, ≤7 days).
    found = list({(c["bank_hint"], c["url"]): c for c in found}.values())
    found.sort(key=lambda c: c["date"])
    clusters: list[dict] = []
    for c in found:
        for k in clusters:
            if (k["bank_hint"] == c["bank_hint"] and similar(k["title"], c["title"])
                    and abs((dt.date.fromisoformat(k["date"]) - dt.date.fromisoformat(c["date"])).days) <= 7):
                if c["url"] != k["url"] and c["url"] not in k["other_sources"] and len(k["other_sources"]) < 4:
                    k["other_sources"].append(c["url"])
                break
        else:
            c["other_sources"] = []
            clusters.append(c)
    return clusters


# --------------------------------------------------------------------------- #
# Claude screening (the only paid step)
# --------------------------------------------------------------------------- #
def system_prompt(banks: dict) -> str:
    lines = "\n".join(f"{b['id']} | {b['name']} | {b.get('name_ar', '')} | {b['country']} | {b['type']}"
                      for b in banks.values())
    return f"""You screen news headlines for the Head of AI at a GCC bank. Keep ONLY headlines about a specific \
bank from the list below doing something with AI itself: AI strategy or investment, AI/GenAI/ML deployments, \
AI-powered products or assistants, AI for fraud/AML/risk/compliance, AI-driven automation, data & AI platforms, \
AI partnerships where the bank is a party, AI talent programmes, AI awards or measurable AI outcomes, executives \
describing the bank's own AI plans, and central-bank AI regulation or supervisory AI tools.

REJECT: general AI/tech news; fintech or startup news where no listed bank is a party; market, economy or stock \
commentary (e.g. a bank's research report about AI in the economy); generic digital news with no AI element; \
headlines where the bank is unclear; anything you are not confident about. Most headlines should be rejected.

The bank_hint tells you which bank's search found the headline, but assign the bank the headline is actually about \
(use its id) or reject it. Write everything in English. The summary must not invent details that are not in the \
headline. Categories: {", ".join(CATEGORIES)}.

Return only the kept headlines; return an empty list if none qualify.

Banks (id | name | Arabic name | country | type):
{lines}"""


class Screener:
    def __init__(self, banks: dict) -> None:
        self.client = anthropic.Anthropic(max_retries=4)
        self.system = system_prompt(banks)
        self.banks = banks

    def screen(self, batch: list[dict]) -> list[Kept]:
        rows = "\n".join(json.dumps({"i": i, "bank_hint": c["bank_hint"], "date": c["date"], "source": c["source"],
                                     "title": c["title"]}, ensure_ascii=False) for i, c in enumerate(batch))
        try:
            resp = self.client.messages.parse(
                model=MODEL,
                max_tokens=8000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Headlines:\n{rows}"}],
                output_format=Screened,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise FatalAPIError(str(exc)) from exc
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                raise FatalAPIError("Anthropic credit balance is too low") from exc
            raise
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return []
        return [k for k in resp.parsed_output.items if 0 <= k.i < len(batch) and k.bank_id in self.banks]

    def screen_all(self, cands: list[dict]) -> list[tuple[dict, Kept]]:
        batches = [cands[i:i + BATCH] for i in range(0, len(cands), BATCH)]
        kept: list[tuple[dict, Kept]] = []
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(self.screen, b): b for b in batches}
            for fut in cf.as_completed(futures):
                batch = futures[fut]
                try:
                    kept += [(batch[k.i], k) for k in fut.result()]
                except FatalAPIError:
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as exc:
                    print(f"  ! screening batch failed: {exc!r}", file=sys.stderr)
                    for c in batch:          # let these be retried next run
                        c["_failed"] = True
        return kept


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class Tracker:
    def __init__(self) -> None:
        cfg = load_json(BANKS_FILE, {})
        self.countries = {c["code"]: c for c in cfg["countries"]}
        self.banks = {b["id"]: b for b in cfg["banks"]}
        self.news = load_json(NEWS_FILE, {"updated_at": None, "items": []})
        self.state = load_json(STATE_FILE, {})
        self.state.setdefault("seen", {})
        self.state.setdefault("history_loaded", {})
        self.state.pop("backfilled", None)  # from the old web-search edition

    def save(self) -> None:
        cutoff = (today() - dt.timedelta(days=SEEN_KEEP_DAYS)).isoformat()
        self.state["seen"] = {k: v for k, v in self.state["seen"].items() if v >= cutoff}
        self.news["items"].sort(key=lambda i: (i["date"], i["added_at"]), reverse=True)
        self.news["updated_at"] = now_iso()
        save_json(NEWS_FILE, self.news)
        save_json(STATE_FILE, self.state)

    def process(self, cands: list[dict], screener: Screener, min_date: dt.date) -> list[dict]:
        seen = self.state["seen"]
        fresh = [c for c in cands if key_of(c["title"]) not in seen and c["date"] >= min_date.isoformat()]
        print(f"  {len(cands)} headlines found, {len(fresh)} new to screen")
        if not fresh:
            return []
        kept = screener.screen_all(fresh)
        for c in fresh:
            if not c.get("_failed"):
                seen[key_of(c["title"])] = c["date"]
        return self.merge(kept)

    def merge(self, kept: list[tuple[dict, Kept]]) -> list[dict]:
        items, added = self.news["items"], []
        for c, k in kept:
            bank = self.banks[k.bank_id]
            dup = next((i for i in items if i["bank_id"] == k.bank_id
                        and abs((dt.date.fromisoformat(i["date"]) - dt.date.fromisoformat(c["date"])).days) <= 10
                        and (similar(i["title"], k.title, 0.45) or i["source_url"] == c["url"])), None)
            if dup:
                extra = [u for u in [c["url"], *c["other_sources"]] if u != dup["source_url"]]
                dup["other_sources"] = list(dict.fromkeys(dup.get("other_sources", []) + extra))[:6]
                continue
            item = {
                "id": hashlib.sha1(f"{k.bank_id}|{c['url']}".encode()).hexdigest()[:12],
                "bank_id": k.bank_id, "country": bank["country"], "date": c["date"],
                "title": k.title.strip(), "summary": k.summary.strip(), "category": k.category,
                "tags": [t.strip() for t in k.tags if t.strip()][:5],
                "partners": [p.strip() for p in k.partners if p.strip()], "impact": k.impact.strip(),
                "source_url": c["url"], "source_name": c["source"], "language": c["language"],
                "added_at": now_iso(),
            }
            if c["other_sources"]:
                item["other_sources"] = c["other_sources"]
            items.append(item)
            added.append(item)
        return added

    def history(self, bank_ids: list[str], months: int, screener: Screener) -> list[dict]:
        end = today()
        start = end - dt.timedelta(days=int(months * 30.5))
        windows, s = [], start
        while s < end:  # quarterly windows: Google News returns at most ~100 results per query
            e = min(s + dt.timedelta(days=92), end + dt.timedelta(days=1))
            windows.append((s, e))
            s = e
        added_all: list[dict] = []
        print(f"Loading history for {len(bank_ids)} banks since {start} ({len(windows)} windows each) …")
        for bid in bank_ids:
            b = self.banks[bid]
            added = self.process(discover([b], windows), screener, start)
            self.state["history_loaded"][bid] = today().isoformat()
            added_all += added
            print(f"  {b['short']}: +{len(added)}")
            self.save()  # checkpoint after every bank
        return added_all

    def update(self, screener: Screener) -> list[dict]:
        print("Daily update (last 7 days of headlines) …")
        added = self.process(discover(list(self.banks.values()), [None]), screener, today() - dt.timedelta(days=30))
        self.state["last_update"] = now_iso()
        self.save()
        return added


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
        if not self.enabled:
            return
        for chat in self.chat_ids:
            body = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                           "disable_web_page_preview": "true"}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body)
            try:
                urllib.request.urlopen(req, timeout=30).read()
            except Exception as exc:
                print(f"  ! telegram send failed for {chat}: {exc!r}", file=sys.stderr)
            time.sleep(1.1)  # stay under Telegram's per-chat rate limit

    def format_item(self, i: dict) -> str:
        b, c = self.banks[i["bank_id"]], self.countries[i["country"]]
        esc = html.escape
        lines = [f"{c['flag']} <b>{esc(b['short'])}</b> · {esc(i['category'])}", f"<b>{esc(i['title'])}</b>",
                 esc(i["summary"])]
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
    ap.add_argument("--force", action="store_true", help="Reload history even for banks already loaded")
    args = ap.parse_args()

    tracker = Tracker()
    tg = Telegram(tracker.banks, tracker.countries)

    if args.mode == "telegram-test":
        if not tg.enabled:
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        tg.send("✅ GCC Bank AI Tracker is connected. You'll receive new bank AI updates here.")
        return

    screener = Screener(tracker.banks)
    try:
        if args.mode == "backfill":
            ids = [b.strip() for b in args.banks.split(",")] if args.banks else [
                b["id"] for b in tracker.banks.values() if not args.country or b["country"] == args.country.upper()]
            ids = [i for i in ids if i in tracker.banks and (args.force or i not in tracker.state["history_loaded"])]
            added = tracker.history(ids, args.months, screener)
            if added:
                tg.send(f"📚 History loaded: <b>{len(added)}</b> AI initiatives added for {len(ids)} banks."
                        + (f'\n<a href="{tg.dashboard}">Open dashboard</a>' if tg.dashboard else ""))
            print(f"Done. {len(added)} items added.")
            return

        pending = [b for b in tracker.banks if b not in tracker.state["history_loaded"]]
        if pending:
            hist = tracker.history(pending, args.months, screener)
            if hist:
                tg.send(f"📚 History loaded: <b>{len(hist)}</b> past AI initiatives added to the dashboard."
                        + (f'\n<a href="{tg.dashboard}">Open dashboard</a>' if tg.dashboard else ""))
        added = tracker.update(screener)
        fresh = [i for i in added if i["date"] >= (today() - dt.timedelta(days=30)).isoformat()]
        tg.notify(fresh)
        print(f"Done. {len(added)} new items ({len(fresh)} recent, sent to Telegram).")
    except FatalAPIError as exc:
        tracker.save()
        tg.send(f"⚠️ <b>GCC Bank AI Tracker stopped</b>\n{html.escape(str(exc))}.\n"
                "Top up credits at console.anthropic.com → Billing; the next run continues where it stopped.")
        sys.exit(f"Stopped: {exc}")


if __name__ == "__main__":
    main()
