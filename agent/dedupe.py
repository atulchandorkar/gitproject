"""Show every story once.

Outlets word the same story differently ("ADIB appoints first chief AI officer" vs "ADIB appoints Chief AI
Officer as it advances Vision 2035 …", "CBUAE issues guidance …" vs "UAE Central Bank issues new rules …").
Two passes:
  1. word match – bank names removed, words stemmed; a pair is the same story when the headlines overlap
     strongly (Jaccard ≥ 0.45, or ≥ 75 % of the shorter headline's words appear in the other);
  2. AI judge – remaining close pairs (same bank, ≤ 7 days apart; for sector items some word overlap, same day or
     same publisher) are asked once "same news event?" (verdicts cached). A pair is merged only if the AI says
     same event with HIGH confidence and names a shared detail (person, product, partner, figure, document or
     event) that is really in the stories – in both, or in one if they are ≤ 2 days apart or one of them is the
     bank's official source (≤ 7 days). See should_merge.
Duplicates are merged into one item: the best-sourced copy stays, all outlets are kept as its sources.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from typing import Callable, Iterable, Literal

from pydantic import BaseModel

STOP = {"the", "a", "an", "and", "of", "to", "in", "for", "with", "on", "its", "by", "at", "as", "new", "bank",
        "banks", "banking", "launches", "launch", "announces", "first", "through", "via", "from", "into", "over",
        "more", "most", "has", "have", "will", "is", "are", "be", "it", "this", "that", "group", "says", "said",
        "uae", "saudi", "qatar", "kuwait", "oman", "bahrain", "gcc", "after", "amid", "while", "unveil", "unveils",
        # every item is about AI, so these words say nothing about which story it is
        "ai", "genai", "artificial", "intelligence", "generative", "powered", "driven", "digital"}
LEVEL_RANK = {"official": 0, "trusted": 1, "corroborated": 2, "pending": 3}


def _stem(w: str) -> str:
    for suf in ("ments", "ment", "ings", "ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokens(title: str, names: Iterable[str] = ()) -> set[str]:
    t = (title or "").lower()
    for n in sorted((n for n in names if n), key=len, reverse=True):
        t = t.replace(n.lower(), " ")
    return {_stem(w) for w in re.findall(r"\w+", t) if w not in STOP and len(w) > 1}


def overlap(a: set[str], b: set[str]) -> tuple[float, float]:
    if not a or not b:
        return 0.0, 0.0
    i = len(a & b)
    return i / len(a | b), i / min(len(a), len(b))


def lexical_same(titles_a: list[str], titles_b: list[str], names: Iterable[str] = ()) -> bool:
    names = list(names)
    for x in titles_a:
        for y in titles_b:
            ta, tb = tokens(x, names), tokens(y, names)
            jac, cont = overlap(ta, tb)
            if jac >= 0.45 or (cont >= 0.75 and min(len(ta), len(tb)) >= 3):
                return True
    return False


class Verdict(BaseModel):
    pair: int
    same_event: bool
    confidence: Literal["high", "medium", "low"]
    anchor: str          # the specific detail both items name: person, product, partner, figure, document or event


class Verdicts(BaseModel):
    items: list[Verdict]


JUDGE_PROMPT = """You remove duplicate stories from a news dashboard. Wrongly merging two different stories is \
worse than leaving a duplicate, so be conservative. For each numbered pair of items decide:
- same_event: true only if both items report the SAME specific news event (the same announcement, appointment, \
launch, partnership, award, study, report, rule or results release), even if worded differently or by different \
outlets. Different events by the same organisation in the same week (e.g. two different products, two partnerships, \
an appointment and a product launch, two different studies, an event and a follow-up) are NOT the same event.
- confidence: "high" only if the two items clearly describe the same event; otherwise "medium" or "low".
- anchor: the specific detail that BOTH items name and that identifies the event – a person's name, product, \
partner company, figure, document title or event name – copied as written in one of the items. "" if there is none.
Judge only from the text given."""


def _text(i: dict) -> str:
    return " ".join(str(i.get(k) or "") for k in ("title", "source_title", "summary", "impact")) + " " + \
        " ".join(i.get("partners") or [])


def anchor_in_both(anchor: str, a: dict, b: dict, names: Iterable[str] = ()) -> bool:
    """The detail the AI says both stories share must really be in both (bank names don't count)."""
    words = tokens(anchor, names)
    if not words:
        return False
    ta, tb = tokens(_text(a), names), tokens(_text(b), names)
    return bool(words & ta & tb)


def anchor_in_one(anchor: str, a: dict, b: dict, names: Iterable[str] = ()) -> bool:
    """The shared detail named by the AI is really in at least one of the stories (not invented)."""
    words = tokens(anchor)          # bank names kept: a programme like "AI@NBB" is a real detail
    return bool(words and (words & (tokens(_text(a)) | tokens(_text(b)))))


def is_official(i: dict) -> bool:
    """The bank's (or publisher's) own source: its newsroom, website or annual report."""
    return (i.get("verification") or {}).get("level") == "official"


def should_merge(v: dict, a: dict, b: dict, names: Iterable[str] = ()) -> bool:
    """Merge decision from the AI verdict (also re-applied to cached verdicts when these rules change).
    Always required: the AI says SAME event with HIGH confidence. Then any one of:
      - the shared detail it names is in both stories;
      - the stories are ≤ 2 days apart and the detail is in at least one (outlets often leave details out:
        "milestone AI certification" vs "ISO/IEC 42001 certification", or an Arabic article without "AI@NBB");
      - one copy is the bank's official source and they are ≤ 7 days apart (outside reports of an official
        announcement are folded into it; the official copy stays on top)."""
    if not (v.get("same") and v.get("confidence") == "high"):
        return False
    anchor, names = v.get("anchor") or "", list(names)
    if anchor_in_both(anchor, a, b, names):
        return True
    if not anchor_in_one(anchor, a, b, names):
        return False
    days = _days(a, b)
    return days <= 2 or ((is_official(a) or is_official(b)) and days <= 7)


def _titles(i: dict) -> list[str]:
    return list(dict.fromkeys(t for t in (i.get("title"), i.get("source_title")) if t))


def _days(a: dict, b: dict) -> int:
    return abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(b["date"])).days)


def _merge(keep: dict, drop: dict, how: str = "words", anchor: str = "") -> None:
    # keep the merged copy (so a merge can be reviewed and undone)
    keep.setdefault("merged", []).append({k: drop.get(k) for k in ("id", "date", "title", "source_title", "source_url",
                                                                    "source_name", "summary")} | {"how": how, "anchor": anchor})
    keep["merged"] += drop.pop("merged", [])
    have = {s["url"] for s in keep.get("sources", [])}
    keep.setdefault("sources", [])
    keep["sources"] += [s for s in drop.get("sources", []) if s["url"] not in have][: max(0, 8 - len(keep["sources"]))]
    for k in ("topics", "partners", "tags"):
        if k in keep or k in drop:
            keep[k] = list(dict.fromkeys((keep.get(k) or []) + (drop.get(k) or [])))
    if "topics" in keep:
        keep["topics"] = keep["topics"][:3]
    if not keep.get("impact") and drop.get("impact"):
        keep["impact"] = drop["impact"]
    if not keep.get("key_stats") and drop.get("key_stats"):
        keep["key_stats"] = drop["key_stats"]
    if not keep.get("pdf") and drop.get("pdf"):
        keep["pdf"] = drop["pdf"]
    if len(keep.get("summary") or "") < 40 <= len(drop.get("summary") or ""):
        keep["summary"] = drop["summary"]
    keep["date"] = min(keep["date"], drop["date"])   # the day the news first broke


def _rank(i: dict) -> tuple:
    v = (i.get("verification") or {})
    return (LEVEL_RANK.get(v.get("level"), 4), v.get("evidence") != "article", -len(i.get("sources", [])), i["date"])


def dedupe(items: list[dict], group: Callable[[dict], str], names: Callable[[dict], list[str]],
           ask: Callable | None, cache: dict, max_days: int = 10,
           related: Callable[[dict, dict], bool] | None = None, near_days: int = 0) -> list[dict]:
    """Return items with duplicates merged. `group` keeps comparisons within one bank (or one sector topic),
    `names` gives words to ignore (the bank's own names), `ask` is FactChecker.ask (None = word match only),
    `cache` stores AI verdicts by pair id, `related` flags extra pairs worth asking about (e.g. same publisher),
    `near_days`: pairs in the same group at most this many days apart are always asked about, because the same
    event is often worded with no words in common ("hires Pedro Uria-Recio" vs "appoints Chief AI Officer")."""
    items = sorted(items, key=_rank)                  # best-sourced copy first, so it is the one kept
    kept: list[dict] = []
    doubtful: list[tuple[dict, dict]] = []
    for it in items:
        dup = None
        for k in kept:
            if group(k) != group(it) or _days(k, it) > max_days:
                continue
            if lexical_same(_titles(k), _titles(it), names(it)):
                dup = k
                break
            key = "v2|" + "|".join(sorted((k["id"], it["id"])))
            if key in cache and should_merge(cache[key], k, it, names(it)):
                dup = k
                break
            if key not in cache:
                ta = set().union(*(tokens(t, names(it)) for t in _titles(k)))
                tb = set().union(*(tokens(t, names(it)) for t in _titles(it)))
                jac, cont = overlap(ta, tb)
                if cont >= 0.25 or _days(k, it) <= near_days or (related and related(k, it)):
                    doubtful.append((k, it))
        if dup:
            _merge(dup, it, "words" if lexical_same(_titles(dup), _titles(it), names(it)) else "ai (cached)")
        else:
            kept.append(it)
    if not doubtful or ask is None:
        return kept
    # Ask the AI about the close-but-unclear pairs, then merge the confirmed ones.
    confirmed: list[tuple[dict, dict]] = []
    for s in range(0, len(doubtful), 25):
        chunk = doubtful[s:s + 25]
        rows = "\n\n".join(json.dumps({"pair": n, "a": {"date": a["date"], "title": a["title"], "summary": a.get("summary", "")},
                                       "b": {"date": b["date"], "title": b["title"], "summary": b.get("summary", "")}},
                                      ensure_ascii=False) for n, (a, b) in enumerate(chunk))
        try:
            out = ask(JUDGE_PROMPT, f"Pairs:\n\n{rows}", Verdicts, 2000)
        except Exception as exc:
            if exc.__class__.__name__ == "FatalAPIError":
                raise
            print(f"  ! duplicate check failed, will retry next run: {exc!r}", file=sys.stderr)
            continue
        got = {v.pair: v for v in (out.items if out else [])}
        for n, (a, b) in enumerate(chunk):
            v = got.get(n)
            if v is None:
                continue
            verdict = {"anchor": v.anchor, "confidence": v.confidence, "same": v.same_event}
            ok = should_merge(verdict, a, b, names(a))
            cache["v2|" + "|".join(sorted((a["id"], b["id"])))] = {"merge": ok, **verdict}
            if ok:
                confirmed.append((a, b, v.anchor))
    owner: dict[int, dict] = {}          # merged item -> the item that absorbed it (A=B and B=C → one item)

    def root(x: dict) -> dict:
        while id(x) in owner:
            x = owner[id(x)]
        return x

    for a, b, anchor in confirmed:
        ra, rb = root(a), root(b)
        if ra is not rb:
            keep, drop = sorted((ra, rb), key=_rank)
            _merge(keep, drop, "ai", anchor)
            owner[id(drop)] = keep
    return [k for k in kept if id(k) not in owner]
