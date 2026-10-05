"""Accuracy checks applied to every news item before it is published.

1. Real source      – the link comes from a Google News entry or a bank newsroom page (never from the AI).
2. Bank named       – the original headline or the article text must name the bank (EN/AR names, aliases).
3. No invented data – every number in the title/summary/impact must appear in the evidence.
4. AI fact-check    – an independent Claude pass checks each claim against the evidence (full article text
                      when the page can be read, otherwise the original headline); unsupported details are
                      removed and wrong-bank / not-AI items are rejected.
5. Trusted source   – bank newsroom, official news agency or reputable outlet; anything else must be
                      corroborated by a second independent outlet, otherwise it is held back (and dropped
                      after PENDING_DAYS).
"""

from __future__ import annotations

import datetime as dt
import html as htmllib
import json
import re
import sys
from typing import Callable

import anthropic
from pydantic import BaseModel

from newsrooms import fetch

PENDING_DAYS = 21
ARTICLE_CHARS = 12000

# Publishers whose reporting is accepted without a second source (matched as whole words, case-insensitive).
TRUSTED_PUBLISHERS = [
    # official news agencies
    "wam", "emirates news agency", "saudi press agency", "spa", "qatar news agency", "qna", "kuwait news agency",
    "kuna", "oman news agency", "ona", "bahrain news agency", "bna", "وكالة الأنباء", "وام",
    # international
    "reuters", "bloomberg", "associated press", "afp", "financial times", "cnbc", "the wall street journal",
    "s&p global", "fitch", "moody's",
    # regional English
    "the national", "gulf news", "khaleej times", "arabian business", "zawya", "agbi", "arab news",
    "saudi gazette", "argaam", "the peninsula", "gulf times", "qatar tribune", "arab times", "kuwait times",
    "times of oman", "muscat daily", "oman observer", "gulf daily news", "daily tribune", "trade arabia",
    "tradearabia", "mubasher", "economy middle east", "forbes middle east", "meed", "asharq al-awsat",
    "asharq business", "finance middle east", "the banker", "the asian banker", "the digital banker", "euromoney",
    "global finance", "ibs intelligence", "fintech news middle east", "finextra", "business wire", "pr newswire",
    "globenewswire", "consultancy-me", "middle east economic digest", "gulf daily news", "gdnonline", "news of bahrain",
    "akhbar al khaleej", "al bayan", "aletihad", "al khaleej", "emarat al youm", "al eqtisadiah", "okaz", "al riyadh",
    "al sharq", "al raya", "al watan", "al qabas", "al rai", "al jarida", "al anba", "al ayam", "oman daily",
    "al arabiya", "cnbc arabia", "sky news arabia", "fintech futures", "ff news", "retail banker international",
    "the paypers", "computer weekly", "itp", "arabian gulf business insight", "gulf business", "khaleej times",
    "saudi press agency", "kuwait news agency", "bahrain news agency", "qatar news agency", "al-sharq",
    # technology vendors' own announcements about their bank deals
    "microsoft", "oracle", "accenture", "ibm", "google cloud", "aws", "amazon web services", "sap", "salesforce",
    "servicenow", "sas", "infosys", "intellect", "temenos", "presight", "g42", "core42", "visa", "mastercard",
    "hcltech", "capgemini", "pwc", "deloitte", "kpmg", "ey", "mckinsey", "mozn", "finastra",
    # regional Arabic
    "البيان", "الخليج", "الاتحاد", "الإمارات اليوم", "الاقتصادية", "عكاظ", "الرياض", "الشرق", "الراية", "الوطن",
    "القبس", "الراي", "الأنباء", "جريدة عمان", "الأيام", "أخبار الخليج", "الشرق الأوسط", "أرقام", "مباشر",
]
# The same outlets as web addresses (Google News sometimes names the source by its domain).
TRUSTED_DOMAINS = [
    "wam.ae", "spa.gov.sa", "qna.org.qa", "kuna.net.kw", "omannews.gov.om", "bna.bh", "reuters.com", "bloomberg.com",
    "thenationalnews.com", "gulfnews.com", "khaleejtimes.com", "arabianbusiness.com", "zawya.com", "agbi.com",
    "arabnews.com", "saudigazette.com.sa", "argaam.com", "thepeninsulaqatar.com", "gulf-times.com", "qatar-tribune.com",
    "arabtimesonline.com", "kuwaittimes.com", "timesofoman.com", "muscatdaily.com", "omanobserver.om",
    "gdnonline.com", "newsofbahrain.com", "akhbar-alkhaleej.com", "alayam.com", "albayan.ae", "aletihad.ae",
    "alkhaleej.ae", "emaratalyoum.com", "aleqt.com", "okaz.com.sa", "alriyadh.com", "al-sharq.com", "raya.com",
    "alqabas.com", "alraimedia.com", "aljarida.com", "alanba.com.kw", "omandaily.om", "aawsat.com", "alarabiya.net",
    "fintechfutures.com", "finextra.com", "businesswire.com", "prnewswire.com", "theasianbanker.com",
    "thedigitalbanker.com", "euromoney.com", "ibsintelligence.com", "fintechnews.ae", "tradearabia.com",
]
_TRUSTED_RE = re.compile(r"(?<![\w])(" + "|".join(re.escape(p) for p in TRUSTED_PUBLISHERS) + r")(?![\w])", re.I)
_NUM_RE = re.compile(r"\d[\d,.]*")


class Check(BaseModel):
    i: int
    bank_correct: bool
    bank_own_ai_activity: bool
    title_supported: bool
    summary_supported: bool
    impact_supported: bool
    partners_supported: bool
    reason: str


class Checks(BaseModel):
    items: list[Check]


class Summary(BaseModel):
    i: int
    summary: str


class Summaries(BaseModel):
    items: list[Summary]


SUMMARY_PROMPT = """You write the two-line summary shown under each headline on a bank AI news dashboard.
For each item, write exactly two short English sentences (about 25-45 words in total) using ONLY the evidence:
1) what the bank announced, launched or achieved with AI; 2) the purpose, scope, partner or significance.
If the evidence is only a headline, rephrase what it states and name the publisher and date in sentence 2
(e.g. "The announcement was reported by Gulf Times on 3 Oct 2026."). Never add facts, numbers, names or
dates that are not in the evidence. Translate Arabic to English. No marketing language."""


FACT_CHECK_PROMPT = """You are a strict, sceptical fact-checker for a banking AI news tracker. For each item you get \
EVIDENCE (either the article text or only the original headline) and the CLAIMS written by another AI.

For each item decide:
- bank_correct: the evidence is about this bank (not a different bank, a namesake or a group company elsewhere).
- bank_own_ai_activity: the evidence describes this bank's own AI activity (strategy, investment, deployment, product, \
partnership, talent, award, outcome, or a central bank's AI regulation) – not general AI or market commentary.
- title_supported / summary_supported / impact_supported / partners_supported: true only if EVERY statement in that \
claim is stated in, or directly implied by, the evidence. Empty claims are supported. Be strict: any detail, number, \
name or date not in the evidence makes the claim unsupported.
- reason: one short sentence explaining any false value (empty if all true).

Judge only against the evidence given, never against your own knowledge."""


# Arabic words too generic to identify a bank on their own (e.g. «الوطني» could be any "National" bank).
_GENERIC_AR = {"الوطني", "الأهلي", "التجاري", "الدولي", "الخليج", "الإسلامي", "المركزي", "الأول", "العربي", "المتحد"}


def arabic_names(bank: dict) -> list[str]:
    """Full Arabic name plus its core without the 'bank' word, as Arabic headlines usually write it."""
    full = bank.get("name_ar", "").strip()
    if not full:
        return []
    core = re.sub(r"^(?:مجموعة\s+)?(?:البنك|بنك|مصرف|المصرف)\s+", "", full).strip()
    names = [full]
    if core != full and len(core) >= 4 and core not in _GENERIC_AR:
        names.append(core)
    return names + list(bank.get("aliases_ar", []))


_QUOTES = re.compile(r"[«»“”„\"'‘’`]")


def bank_mentioned(text: str, bank: dict) -> bool:
    if not text:
        return False
    text = re.sub(r"\s+", " ", _QUOTES.sub(" ", text))   # «الإمارات دبي الوطني» / “المركزي” السعودي
    names = [bank["name"], bank["short"], *arabic_names(bank), *bank.get("aliases", [])]
    for n in filter(None, names):
        if len(n) <= 4 and n.isascii():   # short codes like QNB / FAB must match as a whole word
            if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text):
                return True
        elif n.lower() in text.lower():
            return True
    return False


def _nums(text: str) -> set[str]:
    out = set()
    for n in _NUM_RE.findall(text or ""):
        n = n.rstrip(".,").replace(",", "")
        if n:
            out.add(str(int(n)) if n.isdigit() else n)  # "01" == "1" (dates like 2026-10-01 vs 1 Oct 2026)
    return out


def numbers_supported(claim: str, evidence: str) -> bool:
    """Every number in the claim must appear in the evidence (years and 'AI 2.0'-style digits included)."""
    ev = _nums(evidence)
    return all(n in ev for n in _nums(claim))


def page_text(url: str) -> str | None:
    """Readable text of an article page, or None if it cannot be fetched (Google News links are skipped)."""
    if "news.google.com" in url:
        return None
    try:
        _, raw = fetch(url, timeout=20)
    except Exception:
        return None
    raw = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", raw)
    text = htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) > 200 else None


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def trust_level(item: dict, bank: dict) -> str | None:
    own = [n.lower() for n in [bank["name"], bank["short"], *bank.get("aliases", [])] if len(n) > 2]
    for s in item["sources"]:
        name = (s.get("name") or "").lower()
        if name.endswith("newsroom") or bank["domain"] in s["url"] or _domain(s["url"]).endswith(bank["domain"]) \
                or any(name.startswith(o) for o in own):   # e.g. "Mashreq on X", "ADCB newsroom"
            return "official"
    for s in item["sources"]:
        name = (s.get("name") or "").strip()
        if _TRUSTED_RE.search(name) or name.lower() in TRUSTED_DOMAINS or _domain(s["url"]) in TRUSTED_DOMAINS:
            return "trusted"
    if len({s["name"].lower() for s in item["sources"] if s["name"]}) >= 2:
        return "corroborated"
    return None


class FactChecker:
    def __init__(self, model: str, on_fatal: Callable[[Exception], Exception]) -> None:
        self.client = anthropic.Anthropic(max_retries=4)
        self.model = model
        self.on_fatal = on_fatal

    def ask(self, system: str, content: str, output_format, max_tokens: int = 8000):
        """One structured call; returns the parsed output or None (refusal). Credit/auth errors are fatal."""
        try:
            resp = self.client.messages.parse(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}], output_format=output_format,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError) as exc:
            if isinstance(exc, anthropic.BadRequestError) and "credit balance" not in str(exc).lower():
                raise
            raise self.on_fatal(exc) from exc
        if resp.stop_reason == "refusal":
            return None
        return resp.parsed_output

    def summarize(self, rows: list[dict]) -> dict[int, str]:
        body = "\n\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        out = self.ask(SUMMARY_PROMPT, f"Items:\n\n{body}", Summaries, 4000)
        return {x.i: x.summary.strip() for x in out.items} if out else {}

    def check(self, rows: list[dict]) -> dict[int, Check]:
        body = "\n\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        out = self.ask(FACT_CHECK_PROMPT, f"Items to check:\n\n{body}", Checks)
        return {c.i: c for c in out.items} if out else {}


def fallback_summary(it: dict, bank: dict) -> str:
    """Plain, factual two-line summary built only from the source when nothing better passes the checks."""
    head = (it.get("source_title") or it["title"]).strip().rstrip(".")
    src = (it.get("sources") or [{}])[0].get("name") or it.get("source_name") or "the source"
    when = dt.date.fromisoformat(it["date"]).strftime("%-d %b %Y")
    return f"{bank['name']}: {head}. Reported by {src} on {when}; open the source for full details."


def evidence_for(it: dict, bank: dict) -> tuple[str, str] | None:
    """(evidence text, kind) for an item: article text when the page can be read, else its original headline."""
    text = page_text(it["source_url"])
    if text and bank_mentioned(text, bank):
        names = [n for n in [bank["name"], bank["short"]] if n.lower() in text.lower()]
        pos = max(0, min((text.lower().find(n.lower()) for n in names), default=0) - 1500)
        return text[pos:pos + ARTICLE_CHARS], "article"
    if it.get("source_title"):
        src = (it.get("sources") or [{}])[0].get("name") or it.get("source_name", "")
        return f'{it["source_title"]} ({src}, {it["date"]})', "headline"
    return None


def fill_summaries(staged: list[tuple[dict, str, str]], banks: dict, checker: FactChecker) -> None:
    """Every item gets a two-line summary: written from the evidence, number-checked, else a factual fallback."""
    todo = [(it, ev) for it, ev, _ in staged if len((it.get("summary") or "").strip()) < 40]
    for start in range(0, len(todo), 8):
        chunk = todo[start:start + 8]
        rows = [{"i": n, "bank": banks[it["bank_id"]]["name"], "title": it["title"], "evidence": ev}
                for n, (it, ev) in enumerate(chunk)]
        try:
            written = checker.summarize(rows)
        except Exception as exc:
            if exc.__class__.__name__ == "FatalAPIError":
                raise
            print(f"  ! summary batch failed, using fallback summaries: {exc!r}", file=sys.stderr)
            written = {}
        for n, (it, ev) in enumerate(chunk):
            text = written.get(n, "")
            ok = len(text) >= 40 and numbers_supported(text, ev) and bank_mentioned(text + " " + ev, banks[it["bank_id"]])
            it["summary"] = text if ok else fallback_summary(it, banks[it["bank_id"]])


def verify(items: list[dict], banks: dict, checker: FactChecker, today: dt.date) -> tuple[list, list, list]:
    """Return (publish, pending, rejected); rejected entries carry a 'rejected_reason'."""
    publish, pending, rejected = [], [], []
    staged, accepted = [], []
    for it in items:
        bank = banks[it["bank_id"]]
        url = it["source_url"]
        text = page_text(url)
        if text and bank_mentioned(text, bank):
            # keep the part of the page around the first mention of the bank
            pos = max(0, min((text.lower().find(n.lower()) for n in [bank["name"], bank["short"]]
                              if n.lower() in text.lower()), default=0) - 1500)
            evidence, kind = text[pos:pos + ARTICLE_CHARS], "article"
        elif it.get("source_title"):
            evidence, kind = f'{it["source_title"]} ({it["sources"][0]["name"]}, {it["date"]})', "headline"
        else:
            it["rejected_reason"] = "source could not be read to verify the claims"
            rejected.append(it)
            continue
        if not bank_mentioned(evidence, bank):
            it["rejected_reason"] = f"{bank['short']} is not named in the source"
            rejected.append(it)
            continue
        # deterministic number check
        if it.get("impact") and not numbers_supported(it["impact"], evidence):
            it["impact"] = ""
        if not numbers_supported(it.get("summary", ""), evidence):
            it["summary"] = ""
        if it.get("source_title") and not numbers_supported(it["title"], evidence):
            it["title"] = it["source_title"]
        staged.append((it, evidence, kind))

    # independent AI fact-check, small batches (article evidence is long)
    for start in range(0, len(staged), 6):
        chunk = staged[start:start + 6]
        rows = [{"i": n, "bank": banks[it["bank_id"]]["name"], "evidence_type": kind, "evidence": ev,
                 "claims": {"title": it["title"], "summary": it.get("summary", ""), "impact": it.get("impact", ""),
                            "partners": it.get("partners", [])}}
                for n, (it, ev, kind) in enumerate(chunk)]
        try:
            results = checker.check(rows)
        except Exception as exc:
            if exc.__class__.__name__ == "FatalAPIError":
                raise
            print(f"  ! fact-check batch failed, will retry next run: {exc!r}", file=sys.stderr)
            for it, _, _ in chunk:
                it["rejected_reason"] = "fact-check unavailable (retry)"
                rejected.append(it)
            continue
        for n, (it, ev, kind) in enumerate(chunk):
            c = results.get(n)
            if c is None:
                it["rejected_reason"] = "fact-check returned no verdict (retry)"
                rejected.append(it)
                continue
            if not c.bank_correct or not c.bank_own_ai_activity:
                it["rejected_reason"] = f"fact-check: {c.reason or 'not this bank’s own AI activity'}"
                rejected.append(it)
                continue
            if not c.title_supported and it.get("source_title"):
                it["title"] = it["source_title"]
            if not c.summary_supported:
                it["summary"] = ""
            if not c.impact_supported:
                it["impact"] = ""
            if not c.partners_supported:
                it["partners"] = []
            level = trust_level(it, banks[it["bank_id"]])
            it["verification"] = {"level": level or "pending", "evidence": kind, "checked": today.isoformat()}
            (publish if level else pending).append(it)
            accepted.append((it, ev, kind))
    fill_summaries(accepted, banks, checker)
    return publish, pending, rejected
