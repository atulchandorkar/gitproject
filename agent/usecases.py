"""AI use cases: which AI use case each bank item is about, in which channel, and how far it is (live / pilot /
planned), from a fixed list in config/use_cases.json. Powers the Use cases tab (most popular use cases, which banks
run them, channels, bank × use case matrix).

Claude Haiku tags each news / annual-report item once, using only the item's own text (title, summary, outcome,
tags, partners, annual-report quote). Ids outside the fixed lists are dropped. Items that are not a use case
(strategy statements, hires, training, awards, events) get no use case. The tags are stored on the item
(`use_cases`, `channels`, `uc_status`, `uc_note`, `uc_v`), so each item is read only once; changing the list in
the config (its `version`) has every item tagged again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

CFG = json.loads((Path(__file__).resolve().parent.parent / "config" / "use_cases.json").read_text(encoding="utf-8"))
UC_IDS = {u["id"] for u in CFG["use_cases"]}
CH_IDS = {c["id"] for c in CFG["channels"]}
BATCH = 20
MAX_PER_RUN = 800


class Tag(BaseModel):
    i: int
    use_cases: list[str]                     # 0-3 ids from the list, main one first
    channels: list[str]                      # ids, only when the text states or clearly implies them
    status: Literal["live", "pilot", "planned", "unknown"]
    note: str                                # one short line: what the AI does, from the text only ("" if none)


class Tags(BaseModel):
    items: list[Tag]


PROMPT = """You classify news about AI at GCC banks into AI USE CASES for an analytics dashboard.
For each numbered item decide, using ONLY the item's text:
- use_cases: 0 to 3 ids from the list below (main one first) for the concrete AI application the bank implements,
  pilots or plans. Give NO use case when the item is not about a specific application: AI strategy statements,
  leadership hires, budgets, training programmes, awards, events, general partnerships without a named use.
- channels: ids of the channels where customers or staff meet it, only if the text states or clearly implies it
  (e.g. "in the mobile app" -> mobile; "WhatsApp" -> messaging; "contact centre", "IVR", "call" -> contact_centre;
  employees / back office / internal process -> internal). Empty if unclear.
- status: "live" if launched / deployed / in use / rolled out; "pilot" if pilot, trial, proof of concept, testing;
  "planned" if plans, will, MoU, agreement to explore; "unknown" otherwise.
- note: one short line (max 14 words) saying what the AI does, using only facts in the item; "" if no use case.

Use cases (id: meaning):
{use_cases}

Channels (id: meaning):
{channels}"""


def _row(n: int, i: dict, banks: dict) -> dict:
    b = banks.get(i["bank_id"], {})
    text = " ".join(x for x in (i.get("summary") or "", i.get("impact") or "") if x)
    row = {"i": n, "bank": b.get("name", ""), "title": i["title"], "text": text[:600],
           "tags": i.get("tags", [])[:4], "partners": i.get("partners", [])[:4]}
    if i.get("source_type") == "annual_report" and i.get("report"):
        row["annual_report_quote"] = (i["report"].get("quote") or "")[:400]
    return row


def tag_all(items: list[dict], banks: dict, ask) -> int:
    """Tag items that have no tags for the current list version. Returns how many were tagged."""
    v = CFG["version"]
    todo = [i for i in items if i.get("uc_v") != v][:MAX_PER_RUN]
    if not todo:
        return 0
    system = PROMPT.format(use_cases="\n".join(f"- {u['id']}: {u['name']} – {u['guide']}" for u in CFG["use_cases"]),
                           channels="\n".join(f"- {c['id']}: {c['name']}" for c in CFG["channels"]))
    done = 0
    for s in range(0, len(todo), BATCH):
        chunk = todo[s:s + BATCH]
        rows = "\n".join(json.dumps(_row(n, i, banks), ensure_ascii=False) for n, i in enumerate(chunk))
        try:
            out = ask(system, f"Items:\n{rows}", Tags, 4000)
        except Exception as exc:
            if exc.__class__.__name__ == "FatalAPIError":
                raise
            print(f"  ! use-case tagging batch failed, will retry next run: {exc!r}", file=sys.stderr)
            continue
        got = {t.i: t for t in (out.items if out else [])}
        for n, i in enumerate(chunk):
            t = got.get(n)
            if t is None:
                continue
            ucs = [u for u in dict.fromkeys(t.use_cases) if u in UC_IDS][:3]
            i["use_cases"] = ucs
            i["channels"] = [c for c in dict.fromkeys(t.channels) if c in CH_IDS] if ucs else []
            i["uc_status"] = t.status if ucs else "unknown"
            i["uc_note"] = " ".join(t.note.split())[:140] if ucs else ""
            i["uc_v"] = v
            done += 1
    return done
