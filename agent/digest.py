"""Weekly digest: every Sunday (Qatar time) a one-page summary of the past week – new bank AI stories, AI found in
annual reports, global banking-AI studies, the themes and partners on the rise, and 3–5 key takeaways.

Takeaways are written by the AI only from the week's verified items; each must cite the items it is based on, and
a takeaway whose numbers are not in those items is dropped (same rule as everywhere else: no unsupported facts).
Saved to data/digests.json (the dashboard's Weekly page, printable as PDF) and sent to Telegram.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

import verify

KEEP_WEEKS = 104
LEVEL_RANK = {"official": 0, "trusted": 1, "corroborated": 2}


class Takeaway(BaseModel):
    text: str             # one sentence, at most ~30 words
    item_ids: list[str]   # the items it is based on


class Takeaways(BaseModel):
    items: list[Takeaway]


PROMPT = """You write the "key takeaways" of a weekly briefing on AI in GCC banks for bank executives.
You get the week's verified news items (bank AI announcements, AI disclosures from bank annual reports and global
banking-AI studies). Write 3 to 5 takeaways: patterns, notable moves or comparisons across banks and countries.
Rules:
- Use ONLY facts stated in the items. Do not add context, history, market figures or opinions from elsewhere.
- Every takeaway cites the ids of the items it is based on (item_ids, 1–4 ids, copied exactly).
- Only use numbers that appear in the cited items.
- One sentence each, at most 30 words, plain business English, no hype."""


def qatar_today() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).date()


def _score(i: dict, featured: set[str]) -> tuple:
    v = i.get("verification") or {}
    return (LEVEL_RANK.get(v.get("level"), 3), -len(i.get("sources", [])), i.get("bank_id") not in featured,
            v.get("evidence") != "article", -int(bool(i.get("impact"))))


def _in(d: str, start: dt.date, end: dt.date) -> bool:
    return start.isoformat() <= (d or "")[:10] <= end.isoformat()


def build(news: list[dict], sector: list[dict], banks: dict, ask, end: dt.date, now_iso: str) -> dict:
    """Digest for the 7 days ending on `end` (inclusive)."""
    start = end - dt.timedelta(days=6)
    recent = (start - dt.timedelta(days=30)).isoformat()
    # new on the dashboard this week: news of the week, plus older stories first found this week (≤30 days old)
    week = [i for i in news if i.get("source_type") != "annual_report"
            and (_in(i["date"], start, end) or (_in(i.get("added_at", ""), start, end) and i["date"] >= recent))]
    reports = [i for i in news if i.get("source_type") == "annual_report" and _in(i.get("added_at", ""), start, end)]
    studies = [i for i in sector if _in(i["date"], start, end) or (_in(i.get("added_at", ""), start, end)
                                                                    and i["date"] >= recent)]
    featured = {b for b, v in banks.items() if v.get("featured")}
    week.sort(key=lambda i: _score(i, featured))
    studies.sort(key=lambda i: (not any(c != "Global" for c in i.get("countries", [])), -len(i.get("sources", []))))

    themes = Counter(t for i in week + reports for t in (i.get("topics") or [i.get("category")]) if t)
    partners = Counter(p for i in week + reports for p in i.get("partners") or [])
    active = Counter(i["bank_id"] for i in week)
    countries = Counter(i["country"] for i in week)
    d = {
        "id": end.isoformat(), "start": start.isoformat(), "end": end.isoformat(), "created_at": now_iso,
        "counts": {"news": len(week), "banks": len(active), "annual_report_items": len(reports),
                   "annual_report_banks": len({i["bank_id"] for i in reports}), "studies": len(studies)},
        "countries": dict(countries.most_common()),
        "themes": themes.most_common(6), "partners": partners.most_common(6), "banks": active.most_common(6),
        "top": [i["id"] for i in week[:8]], "studies_top": [i["id"] for i in studies[:5]],
        "report_banks": sorted({i["bank_id"] for i in reports}, key=lambda b: -sum(1 for i in reports if i["bank_id"] == b)),
        "takeaways": [],
    }
    pool = week[:40] + sorted(reports, key=lambda i: _score(i, featured))[:15] + studies[:10]
    if ask and len(pool) >= 2:
        d["takeaways"] = takeaways(pool, banks, ask)
    return d


def _item_text(i: dict, banks: dict) -> str:
    b = banks.get(i.get("bank_id"), {})
    return " ".join(str(x) for x in (b.get("name", ""), i.get("publisher", ""), i["title"], i.get("summary", ""),
                                     i.get("impact", ""), " ".join(i.get("partners") or []),
                                     " ".join(s.get("value", "") + " " + s.get("label", "") for s in i.get("key_stats") or [])))


def takeaways(pool: list[dict], banks: dict, ask) -> list[dict]:
    by_id = {i["id"]: i for i in pool}
    rows = "\n".join(json.dumps({
        "id": i["id"], "date": i["date"],
        "kind": "annual report" if i.get("source_type") == "annual_report" else ("study" if "publisher" in i else "news"),
        "bank": banks.get(i.get("bank_id"), {}).get("name", ""), "country": i.get("country", ""),
        "publisher": i.get("publisher", ""), "title": i["title"], "summary": i.get("summary", ""),
        "partners": i.get("partners", []), "impact": i.get("impact", "")}, ensure_ascii=False) for i in pool)
    try:
        out = ask(PROMPT, f"Items of the week:\n{rows}", Takeaways, 2000)
    except Exception as exc:
        if exc.__class__.__name__ == "FatalAPIError":
            raise
        print(f"  ! weekly takeaways failed: {exc!r}", file=sys.stderr)
        return []
    kept = []
    for t in (out.items if out else [])[:5]:
        ids = [x for x in dict.fromkeys(t.item_ids) if x in by_id][:4]
        text = " ".join(t.text.split())
        evidence = " ".join(_item_text(by_id[x], banks) for x in ids)
        if ids and text and len(text) <= 300 and verify.numbers_supported(text, evidence):
            kept.append({"text": text, "ids": ids})
    return kept


def save(path: Path, d: dict) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"digests": []}
    data["digests"] = [x for x in data["digests"] if x["id"] != d["id"]] + [d]
    data["digests"] = sorted(data["digests"], key=lambda x: x["id"], reverse=True)[:KEEP_WEEKS]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def last_end(path: Path) -> str:
    if not path.exists():
        return ""
    ds = json.loads(path.read_text(encoding="utf-8")).get("digests", [])
    return ds[0]["end"] if ds else ""


def due(path: Path, today: dt.date) -> dt.date | None:
    """On Sundays (Qatar time): the week that ended yesterday, unless it was already sent."""
    end = today - dt.timedelta(days=1)
    return end if today.weekday() == 6 and last_end(path) < end.isoformat() else None


def _fmt(d: str) -> str:
    x = dt.date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"


def telegram(d: dict, news: dict, sector: dict, banks: dict, countries: dict, dashboard: str) -> str:
    esc, c = html.escape, d["counts"]
    lines = [f"🗓 <b>Weekly AI Pulse</b> · {_fmt(d['start'])} – {_fmt(d['end'])} {d['end'][:4]}"]
    if c["news"]:
        lines.append(f"<b>{c['news']}</b> new AI stories from <b>{c['banks']}</b> banks · "
                     + " · ".join(f"{countries[k]['flag']} {v}" for k, v in d["countries"].items() if k in countries))
    else:
        lines.append("No new bank AI stories this week.")
    if d["takeaways"]:
        lines.append("\n<b>Key takeaways</b>")
        lines += [f"• {esc(t['text'])}" for t in d["takeaways"]]
    top = [news[i] for i in d["top"][:5] if i in news]
    if top:
        lines.append("\n<b>Top stories</b>")
        for i in top:
            b = banks[i["bank_id"]]
            lines.append(f"{countries[b['country']]['flag']} <b>{esc(b['short'])}</b>: "
                         f"<a href=\"{esc(i['source_url'])}\">{esc(i['title'][:140])}</a>")
    if c["annual_report_items"]:
        names = ", ".join(banks[b]["short"] for b in d["report_banks"][:6])
        lines.append(f"\n📘 <b>{c['annual_report_items']}</b> AI disclosures from annual reports ({esc(names)})")
    st = [sector[i] for i in d["studies_top"][:3] if i in sector]
    if st:
        lines.append(f"\n🌍 <b>{c['studies']}</b> banking-AI studies & insights")
        lines += [f"• <a href=\"{esc(i['source_url'])}\">{esc(i['title'][:120])}</a>" for i in st]
    if d["themes"]:
        lines.append("\n🔥 Top themes: " + ", ".join(f"{esc(t)} ({n})" for t, n in d["themes"][:4]))
    if dashboard:
        lines.append(f'\n<a href="{dashboard}/#/weekly/{d["id"]}">Read the full weekly digest</a>')
    msg = ""
    for ln in lines:                      # whole lines only, so no HTML tag is ever cut (Telegram limit 4096)
        if len(msg) + len(ln) + 1 > 3900:
            break
        msg += ("\n" if msg else "") + ln
    return msg
