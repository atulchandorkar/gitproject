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
    "globenewswire", "consultancy-me", "middle east economic digest",
    # regional Arabic
    "البيان", "الخليج", "الاتحاد", "الإمارات اليوم", "الاقتصادية", "عكاظ", "الرياض", "الشرق", "الراية", "الوطن",
    "القبس", "الراي", "الأنباء", "جريدة عمان", "الأيام", "أخبار الخليج", "الشرق الأوسط", "أرقام", "مباشر",
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


def bank_mentioned(text: str, bank: dict) -> bool:
    if not text:
        return False
    names = [bank["name"], bank["short"], bank.get("name_ar", ""), *bank.get("aliases", [])]
    for n in filter(None, names):
        if len(n) <= 4 and n.isascii():   # short codes like QNB / FAB must match as a whole word
            if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text):
                return True
        elif n.lower() in text.lower():
            return True
    return False


def _nums(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in _NUM_RE.findall(text or "") if n.rstrip(".,")}


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


def trust_level(item: dict, bank: dict) -> str | None:
    if any(s["name"].endswith("newsroom") or bank["domain"] in s["url"] for s in item["sources"]):
        return "official"
    if any(_TRUSTED_RE.search(s["name"] or "") for s in item["sources"]):
        return "trusted"
    if len({s["name"].lower() for s in item["sources"] if s["name"]}) >= 2:
        return "corroborated"
    return None


class FactChecker:
    def __init__(self, model: str, on_fatal: Callable[[Exception], Exception]) -> None:
        self.client = anthropic.Anthropic(max_retries=4)
        self.model = model
        self.on_fatal = on_fatal

    def check(self, rows: list[dict]) -> dict[int, Check]:
        body = "\n\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        try:
            resp = self.client.messages.parse(
                model=self.model, max_tokens=8000,
                system=FACT_CHECK_PROMPT,
                messages=[{"role": "user", "content": f"Items to check:\n\n{body}"}],
                output_format=Checks,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError) as exc:
            if isinstance(exc, anthropic.BadRequestError) and "credit balance" not in str(exc).lower():
                raise
            raise self.on_fatal(exc) from exc
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return {}
        return {c.i: c for c in resp.parsed_output.items}


def verify(items: list[dict], banks: dict, checker: FactChecker, today: dt.date) -> tuple[list, list, list]:
    """Return (publish, pending, rejected); rejected entries carry a 'rejected_reason'."""
    publish, pending, rejected = [], [], []
    staged = []
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
    return publish, pending, rejected
