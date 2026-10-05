"""GCC Banking Sector Insights: AI across GCC banking as a whole, not one bank's own announcement.

Covers studies & surveys (McKinsey, BCG, PwC, …), maturity indices & rankings, regulation & guidance,
market data, expert views and sector-wide events/initiatives from the last 12 months.

Same low-cost pipeline as the bank news: free Google News RSS (EN + AR) and the publishers' own
press/insight pages → cheap keyword pre-filter → Claude screening → strict verification:
  1. real source (feed / publisher page, never the AI)
  2. about GCC + banking + AI (deterministic word check on the evidence, then the AI fact-check)
  3. sector-wide, not a single bank's own news (those stay in the main feed)
  4. every number and key figure must appear verbatim in the source text
  5. trusted / official publisher or a second outlet; otherwise held back for 21 days
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

import dedupe
import newsrooms
import verify

MONTHS_BACK = 12
FEED_PAUSE = 0.8
PENDING_DAYS = 21

CountryCode = Literal["GCC", "QA", "AE", "SA", "KW", "OM", "BH"]
SectorCategory = Literal["Studies & Surveys", "Maturity & Rankings", "Regulation & Guidance", "Market Data",
                         "Expert Views", "Events & Initiatives"]

AI_EN = '"artificial intelligence" OR AI OR "generative AI" OR GenAI OR "machine learning" OR "agentic AI"'
AI_AR = '"الذكاء الاصطناعي" OR "ذكاء اصطناعي" OR "التعلم الآلي"'
GEO_EN = 'GCC OR Gulf OR "Middle East" OR MENA OR Saudi OR UAE OR Emirates OR Qatar OR Kuwait OR Oman OR Bahrain'
GEO_AR = "الخليج OR الخليجية OR السعودية OR الإمارات OR قطر OR الكويت OR عمان OR البحرين"
SECTOR_EN = ('"GCC banks" OR "Gulf banks" OR "GCC banking" OR "Gulf banking" OR "Middle East banks" OR "Saudi banks" '
             'OR "UAE banks" OR "Qatari banks" OR "Kuwaiti banks" OR "Omani banks" OR "Bahraini banks" '
             'OR "banking sector" OR "banks in the GCC" OR "regional banks"')
REGULATORS_EN = ('"central bank" OR SAMA OR CBUAE OR QCB OR "Central Bank of Kuwait" OR "Central Bank of Oman" '
                 'OR "Central Bank of Bahrain" OR DFSA OR FSRA OR QFCRA OR "Saudi Central Bank"')

# Deterministic relevance checks (strict AI words: no "analytics"/"automation" here).
STRICT_AI_RE = re.compile(
    r"\b(ai|a\.i\.|genai|gen ai|generative|artificial intelligence|machine learning|llms?|agentic|copilots?|chatgpt|gpt)\b"
    r"|الذكاء الاصطناعي|ذكاء اصطناعي|الذكاء الإصطناعي|التعلم الآلي|التوليدي", re.I)
BANKING_RE = re.compile(
    r"\b(banks?|banking|banker|lenders?|financial (services|institutions?|sector|industry)|fintech regulation)\b"
    r"|بنوك|البنوك|بنك|مصارف|المصارف|مصرف|المصرفي|المصرفية|القطاع المالي|المؤسسات المالية", re.I)
REGULATOR_RE = re.compile(
    r"\b(central bank|sama|cbuae|qcb|dfsa|fsra|qfcra|regulator|supervisor)\b|المركزي|ساما|الجهات الرقابية", re.I)
GEO_RE = re.compile(
    r"\b(gcc|gulf|middle east|mena|arabian|saudi|ksa|uae|emirat\w*|dubai|abu dhabi|sharjah|ajman|ras al[- ]khaimah|"
    r"fujairah|al ain|qatar\w*|doha|lusail|kuwait\w*|oman\w*|muscat|salalah|sohar|bahrain\w*|manama|riyadh|"
    r"jeddah|jiddah|makkah|mecca|madinah|medina|dammam|khobar|dhahran|neom|vision 2030)\b"
    r"|الخليج|الخليجية|الخليجي|خليجي|مجلس التعاون|دول المجلس|الشرق الأوسط|السعودية|السعودي|السعوديه|المملكة|"
    r"الرياض|جدة|مكة|المكرمة|المدينة المنورة|الدمام|الخبر|الظهران|نيوم|رؤية 2030|"
    r"الإمارات|الإماراتي|الإماراتية|دبي|أبوظبي|أبو ظبي|الشارقة|عجمان|رأس الخيمة|الفجيرة|العين|"
    r"قطر|القطري|القطرية|الدوحة|لوسيل|الكويت|الكويتي|الكويتية|عمان|العماني|العمانية|مسقط|صلالة|صحار|"
    r"البحرين|البحريني|البحرينية|المنامة", re.I)


def queries(when: str) -> list[tuple[str, str]]:
    firms = [p["short"] if " " not in p["short"] else f'"{p["short"]}"' for p in PUBLISHERS
             if p["kind"] in ("consultancy", "research", "ratings", "institution")]
    groups = [firms[i:i + 8] for i in range(0, len(firms), 8)]
    qs = [(f"({' OR '.join(g)}) (bank OR banks OR banking OR \"financial services\") ({AI_EN}) ({GEO_EN}){when}", "en")
          for g in groups]
    qs += [
        (f"({SECTOR_EN}) ({AI_EN}){when}", "en"),
        (f"(report OR survey OR study OR index OR ranking OR benchmark OR maturity OR readiness) (banks OR banking) "
         f"({AI_EN}) ({GEO_EN}){when}", "en"),
        (f"({REGULATORS_EN}) ({AI_EN}) (guidelines OR framework OR principles OR regulation OR rules OR sandbox "
         f"OR consultation OR policy){when}", "en"),
        (f"(market OR spending OR investment OR forecast OR adoption) (banks OR banking OR \"financial services\") "
         f"(\"generative AI\" OR \"artificial intelligence\") ({GEO_EN}){when}", "en"),
        (f"(summit OR forum OR conference OR initiative OR programme OR consortium) (banks OR banking) ({AI_EN}) "
         f"({GEO_EN}){when}", "en"),
        (f"(البنوك OR المصارف OR \"القطاع المصرفي\") ({AI_AR}) ({GEO_AR}){when}", "ar"),
        (f"(تقرير OR دراسة OR استطلاع OR مؤشر OR تصنيف) (البنوك OR المصارف OR \"القطاع المصرفي\") ({AI_AR}){when}", "ar"),
        (f"(\"البنك المركزي\" OR \"المصرف المركزي\" OR ساما) ({AI_AR}) (إرشادات OR إطار OR مبادئ OR تنظيم OR ضوابط){when}", "ar"),
    ]
    return qs


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class SKept(BaseModel):
    i: int
    category: SectorCategory
    publisher: str        # organisation behind the study/rule/data (as named in the headline), "" if not named
    countries: list[CountryCode]
    title: str            # clear English headline, only facts in the original headline
    summary: str          # two short sentences, only facts in the headline


class SScreened(BaseModel):
    items: list[SKept]


class Stat(BaseModel):
    value: str            # the figure, e.g. "73%", "US$1.2bn", "1 in 3"
    label: str            # what it measures, short English phrase
    quote: str            # exact words from the evidence containing the figure (copied verbatim)


class SCheck(BaseModel):
    i: int
    about_gcc_banking_ai: bool
    single_bank_announcement: bool
    category_correct: bool
    publisher_supported: bool
    title_supported: bool
    summary_supported: bool
    summary_from_evidence: str
    key_stats: list[Stat]
    reason: str


class SChecks(BaseModel):
    items: list[SCheck]


SCREEN_PROMPT = """You screen headlines for the "GCC Banking Sector Insights" section of a bank AI tracker.

KEEP a headline only if it is about ARTIFICIAL INTELLIGENCE in BANKING / BANKS in the GCC (Qatar, UAE, Saudi \
Arabia, Kuwait, Oman, Bahrain) as a sector or market, e.g.:
{categories}

Middle East / MENA studies count if they cover GCC banks. REJECT:
- one named bank's own announcement, product, partnership, award or result (that belongs to the bank news feed);
- AI news not about banking/financial services, or banking news without AI;
- global studies with no GCC/Gulf/Middle East angle; fintech start-up funding; adverts; job posts.

For each kept headline return: i; category; publisher = the organisation that produced the study, ranking, rule \
or data if the headline names it (e.g. "PwC", "Saudi Central Bank"), else ""; countries = GCC country codes it \
covers, or ["GCC"] when it is Gulf/Middle East-wide; title = a clear English headline; summary = two short English \
sentences. Use ONLY facts in the headline: never add numbers, names or dates. Translate Arabic to English.

Known publishers: {publishers}"""

CHECK_PROMPT = """You are a strict, sceptical fact-checker for the "GCC Banking Sector Insights" section of a \
banking AI tracker. For each item you get EVIDENCE (the article text, or only the original headline) and CLAIMS.

Decide, judging ONLY against the evidence (never your own knowledge):
- about_gcc_banking_ai: the evidence is substantially about AI in banking/financial services in the GCC or \
Gulf/Middle East (not just a passing mention).
- single_bank_announcement: true if it is really one named bank's own announcement/product/award rather than a \
sector-wide study, ranking, rule, market figure, expert view or initiative.
- category_correct: the claimed category fits.
- publisher_supported: the claimed publisher is named in the evidence as the source of the study/rule/data \
(empty publisher counts as supported).
- title_supported / summary_supported: true only if every statement is in the evidence.
- summary_from_evidence: two short English sentences (25-45 words) using only the evidence: what was found or \
announced, and why it matters for GCC banks. Never add facts or numbers.
- key_stats: up to 3 headline figures about AI in GCC banking found in the evidence (e.g. adoption %, spend, \
value). value = the figure; label = what it measures (short English phrase); quote = the exact words from the \
evidence that contain the figure, copied character for character (Arabic stays Arabic). Return [] if none.
- reason: one short sentence explaining any problem (empty if none)."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def stat_ok(st: Stat, evidence: str) -> bool:
    """A key figure is shown only if its quote is verbatim in the evidence and contains the figure's number."""
    q, ev = _norm(st.quote), _norm(evidence)
    nums = verify._nums(st.value)
    return bool(q) and len(q) >= 8 and q in ev and bool(nums) and nums <= verify._nums(st.quote)


def _cfg() -> dict:
    p = Path(__file__).resolve().parent.parent / "config" / "sector_sources.json"
    return json.loads(p.read_text(encoding="utf-8"))


CFG = _cfg()
PUBLISHERS = CFG["publishers"]
CATEGORY_TEXT = "\n".join(f"- {c['id']}: {c['guide']}" for c in CFG["categories"])


PDF_LINK_RE = re.compile(r"\.pdf($|[?#])", re.I)
PDF_TEXT_RE = re.compile(r"download|report|pdf|full study|read the|تحميل|التقرير|تقرير|الدراسة", re.I)


def is_pdf(url: str) -> bool:
    """The link really serves a PDF (not a sign-up page): checks the first bytes of the file."""
    req = newsrooms.urllib.request.Request(url, headers={**newsrooms.HEADERS, "Range": "bytes=0-1023",
                                                         "Accept-Encoding": "identity"})
    try:
        with newsrooms.urllib.request.urlopen(req, timeout=20) as resp:
            head = resp.read(1024)
            return head.startswith(b"%PDF") or ("pdf" in (resp.headers.get("Content-Type") or "").lower()
                                                and b"<html" not in head.lower())
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Sector tracker
# --------------------------------------------------------------------------- #
class Sector:
    def __init__(self, root: Path, banks: dict, state: dict, news_items: list[dict], checker: verify.FactChecker,
                 gnews: Callable[[str, str], list[dict]], helpers: dict, today: dt.date, now_iso: Callable[[], str]):
        self.file = root / "data" / "sector.json"
        self.rej_file = root / "data" / "sector_rejected.json"
        self.data = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {"updated_at": None, "items": []}
        self.rejected = json.loads(self.rej_file.read_text(encoding="utf-8")) if self.rej_file.exists() else []
        self.banks, self.state, self.news_items = banks, state, news_items
        self.checker, self.gnews, self.today, self.now_iso = checker, gnews, today, now_iso
        self.similar, self.key_of = helpers["similar"], helpers["key_of"]
        for k in ("sector_seen", "sector_known"):
            state.setdefault(k, {})
        for k in ("sector_pending", "sector_retry"):
            state.setdefault(k, [])
        self.central = [b for b in banks.values() if b["type"] == "central"]

    # ---- persistence ----
    def save(self) -> None:
        cutoff = (self.today - dt.timedelta(days=int(MONTHS_BACK * 30.5))).isoformat()
        self.data["items"] = [i for i in self.data["items"] if i["date"] >= cutoff]
        self.data["items"].sort(key=lambda i: (i["date"], i["added_at"]), reverse=True)
        self.data["updated_at"] = self.now_iso()
        for path, obj in ((self.file, self.data), (self.rej_file, self.rejected[-2000:])):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(path)

    # ---- discovery ----
    def _relevant_headline(self, t: str) -> bool:
        return bool(STRICT_AI_RE.search(t) and (BANKING_RE.search(t) or REGULATOR_RE.search(t)))

    def _in_bank_feed(self, c: dict) -> bool:
        return any(c["url"] == i["source_url"] or self.similar(c["title"], i.get("source_title") or i["title"], 0.6)
                   for i in self.news_items)

    def publisher_pages(self) -> list[dict]:
        """Press / insight pages of the consultancies and research firms (free; many block robots, that's fine)."""
        out = []
        for p in PUBLISHERS:
            for page in p.get("pages", []):
                try:
                    final, raw = newsrooms.fetch(page, timeout=20)
                except Exception as exc:
                    print(f"  · {p['short']} page not readable ({exc.__class__.__name__})")
                    continue
                links = newsrooms.parse(raw, final).links
                found = 0
                for url, text in links:
                    text = re.sub(r"\s+", " ", text or "").strip()
                    if len(text) < 25 or not self._relevant_headline(text) or url in self.state["sector_known"]:
                        continue
                    self.state["sector_known"][url] = self.today.isoformat()
                    date = newsrooms.date_from_url(url) or newsrooms.date_from_article(url)
                    if not date:
                        continue
                    out.append({"title": text, "url": url, "source": p["name"], "date": date[:10],
                                "language": "ar" if re.search(r"[؀-ۿ]", text) else "en"})
                    found += 1
                print(f"  · {p['short']}: {found} new AI-in-banking links")
        return out

    def discover(self, windows: list[tuple[dt.date, dt.date] | None], with_pages: bool) -> list[dict]:
        found = self.publisher_pages() if with_pages else []
        for w in windows:
            when = f" after:{w[0].isoformat()} before:{w[1].isoformat()}" if w else " when:7d"
            for q, lang in queries(when):
                found += [c for c in self.gnews(q, lang) if self._relevant_headline(c["title"])]
                time.sleep(FEED_PAUSE)
        found = list({c["url"]: c for c in found}.values())
        found.sort(key=lambda c: c["date"])
        clusters: list[dict] = []
        for c in found:
            for k in clusters:
                if self.similar(k["title"], c["title"]) and \
                        abs((dt.date.fromisoformat(k["date"]) - dt.date.fromisoformat(c["date"])).days) <= 14:
                    if len(k["other_sources"]) < 5 and all(c["url"] != o["url"] for o in k["other_sources"]):
                        k["other_sources"].append({"url": c["url"], "name": c["source"]})
                    break
            else:
                c["other_sources"] = []
                clusters.append(c)
        return [c for c in clusters if not self._in_bank_feed(c)]

    # ---- screening ----
    def screen(self, cands: list[dict]) -> list[tuple[dict, SKept]]:
        system = SCREEN_PROMPT.format(categories=CATEGORY_TEXT, publishers=", ".join(p["name"] for p in PUBLISHERS))
        kept = []
        for s in range(0, len(cands), 40):
            batch = cands[s:s + 40]
            rows = "\n".join(json.dumps({"i": n, "date": c["date"], "source": c["source"], "title": c["title"]},
                                        ensure_ascii=False) for n, c in enumerate(batch))
            try:
                out = self.checker.ask(system, f"Headlines:\n{rows}", SScreened)
            except Exception as exc:
                if exc.__class__.__name__ == "FatalAPIError":
                    raise
                print(f"  ! sector screening batch failed: {exc!r}", file=sys.stderr)
                for c in batch:
                    c["_failed"] = True
                continue
            kept += [(batch[k.i], k) for k in (out.items if out else []) if 0 <= k.i < len(batch)]
        return kept

    def draft(self, c: dict, k: SKept) -> dict:
        return {
            "id": hashlib.sha1(f"sector|{c['url']}".encode()).hexdigest()[:12],
            "date": c["date"], "category": k.category, "publisher": k.publisher.strip(),
            "countries": list(dict.fromkeys(k.countries)) or ["GCC"],
            "title": k.title.strip(), "summary": k.summary.strip(), "key_stats": [],
            "source_url": c["url"], "source_name": c["source"], "source_title": c["title"],
            "sources": [{"url": c["url"], "name": c["source"]}] + list(c.get("other_sources", [])),
            "language": c["language"], "added_at": self.now_iso(),
        }

    # ---- verification ----
    def _publisher_cfg(self, name: str) -> dict | None:
        n = name.lower()
        for p in PUBLISHERS:
            if n and any(n == x.lower() or n.startswith(x.lower()) or x.lower().startswith(n)
                         for x in [p["name"], p["short"], *p.get("aliases", [])]):
                return p
        return None

    def trust_level(self, it: dict) -> str | None:
        owners = []
        p = self._publisher_cfg(it.get("publisher", ""))
        if p:
            owners.append((p["domain"], [p["name"], p["short"], *p.get("aliases", [])]))
        owners += [(b["domain"], [b["name"], b["short"], *b.get("aliases", [])]) for b in self.central]
        for s in it["sources"]:
            name, dom = (s.get("name") or "").lower(), verify._domain(s["url"])
            for domain, names in owners:
                if dom.endswith(domain) or any(len(x) > 2 and name.startswith(x.lower()) for x in names):
                    return "official"
        for s in it["sources"]:
            name = (s.get("name") or "").strip()
            if verify._TRUSTED_RE.search(name) or name.lower() in verify.TRUSTED_DOMAINS \
                    or verify._domain(s["url"]) in verify.TRUSTED_DOMAINS:
                return "trusted"
        if len({s["name"].lower() for s in it["sources"] if s.get("name")}) >= 2:
            return "corroborated"
        return None

    def official_domains(self, it: dict) -> list[str]:
        p = self._publisher_cfg(it.get("publisher", ""))
        return ([p["domain"]] if p else []) + [b["domain"] for b in self.central]

    def find_pdf(self, it: dict) -> dict | None:
        """Link to the official report PDF on the publisher's / regulator's own site, if a source page links to it."""
        official = self.official_domains(it)
        pages = [s["url"] for s in it["sources"] if "news.google.com" not in s["url"]][:4]
        for page in pages:
            if PDF_LINK_RE.search(page) and any(verify._domain(page).endswith(d) for d in official):
                links = [(page, "")]
            else:
                try:
                    final, raw = newsrooms.fetch(page, timeout=20)
                except Exception:
                    continue
                links = [(u, t) for u, t in newsrooms.parse(raw, final).links if PDF_LINK_RE.search(u)]
            links = [(u, t or "") for u, t in links if u.startswith("http")
                     and any(verify._domain(u) == d or verify._domain(u).endswith("." + d) for d in official)]
            links.sort(key=lambda l: not PDF_TEXT_RE.search(l[1] + " " + l[0]))   # "Download the report" first
            for url, _ in links[:3]:
                if is_pdf(url):
                    return {"url": url, "host": verify._domain(url)}
        return None

    def attach_pdfs(self) -> None:
        """Look once for each published item's official PDF (free; no AI calls)."""
        todo = [i for i in self.data["items"] if "pdf_checked" not in i]
        for it in todo:
            pdf = self.find_pdf(it)
            if pdf:
                it["pdf"] = pdf
            it["pdf_checked"] = self.today.isoformat()
        if todo:
            print(f"  sector: looked for official PDFs on {len(todo)} items, "
                  f"{sum(1 for i in todo if i.get('pdf'))} found")

    def evidence(self, it: dict) -> tuple[str, str] | None:
        text = verify.page_text(it["source_url"])
        if text and STRICT_AI_RE.search(text) and GEO_RE.search(text):
            m = STRICT_AI_RE.search(text)
            pos = max(0, m.start() - 2500)
            return text[pos:pos + verify.ARTICLE_CHARS], "article"
        if it.get("source_title"):
            return f'{it["source_title"]} ({it["sources"][0]["name"]}, {it["date"]})', "headline"
        return None

    def fallback_summary(self, it: dict) -> str:
        head = (it.get("source_title") or it["title"]).strip().rstrip(".")
        when = dt.date.fromisoformat(it["date"]).strftime("%-d %b %Y")
        lead = f"{it['publisher']}: " if it.get("publisher") and it["publisher"].lower() not in head.lower() else ""
        return f"{lead}{head}. Reported by {it['sources'][0]['name'] or 'the source'} on {when}; open the source for full details."

    def verify(self, drafts: list[dict]) -> tuple[list, list, list]:
        publish, pending, rejected, staged = [], [], [], []
        for it in drafts:
            got = self.evidence(it)
            if not got:
                it["rejected_reason"] = "source could not be read to verify the claims"
                rejected.append(it)
                continue
            ev, kind = got
            if not (STRICT_AI_RE.search(ev) and (BANKING_RE.search(ev) or REGULATOR_RE.search(ev))):
                it["rejected_reason"] = "source is not about AI in banking"
                rejected.append(it)
                continue
            if not GEO_RE.search(ev):
                it["rejected_reason"] = "source has no GCC / Gulf angle"
                rejected.append(it)
                continue
            if not verify.numbers_supported(it["summary"], ev):
                it["summary"] = ""
            if not verify.numbers_supported(it["title"], ev):
                it["title"] = it["source_title"]
            staged.append((it, ev, kind))

        for s in range(0, len(staged), 6):
            chunk = staged[s:s + 6]
            rows = [{"i": n, "evidence_type": kind, "evidence": ev,
                     "claims": {"category": it["category"], "publisher": it["publisher"], "title": it["title"],
                                "summary": it["summary"]}} for n, (it, ev, kind) in enumerate(chunk)]
            try:
                out = self.checker.ask(CHECK_PROMPT, "Items to check:\n\n" + "\n\n".join(
                    json.dumps(r, ensure_ascii=False) for r in rows), SChecks)
                results = {c.i: c for c in out.items} if out else {}
            except Exception as exc:
                if exc.__class__.__name__ == "FatalAPIError":
                    raise
                print(f"  ! sector fact-check batch failed, will retry: {exc!r}", file=sys.stderr)
                results = {}
            for n, (it, ev, kind) in enumerate(chunk):
                c = results.get(n)
                if c is None:
                    it["rejected_reason"] = "fact-check returned no verdict (retry)"
                    rejected.append(it)
                    continue
                if not c.about_gcc_banking_ai or c.single_bank_announcement:
                    it["rejected_reason"] = "fact-check: " + (c.reason or ("one bank's own news (kept in the bank feed)"
                                                                           if c.single_bank_announcement else
                                                                           "not about AI in GCC banking"))
                    rejected.append(it)
                    continue
                if not c.publisher_supported:
                    it["publisher"] = ""
                if not c.title_supported:
                    it["title"] = it["source_title"]
                summary = it["summary"] if c.summary_supported else ""
                alt = c.summary_from_evidence.strip()
                if len(summary) < 40 and len(alt) >= 40 and verify.numbers_supported(alt, ev):
                    summary = alt
                it["summary"] = summary if len(summary) >= 40 else self.fallback_summary(it)
                it["key_stats"] = [st.model_dump() for st in c.key_stats if stat_ok(st, ev)][:3]
                level = self.trust_level(it)
                it["verification"] = {"level": level or "pending", "evidence": kind, "checked": self.today.isoformat()}
                (publish if level else pending).append(it)
        return publish, pending, rejected

    # ---- merge / pending ----
    def _same(self, a: dict, b: dict) -> bool:
        days = abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(b["date"])).days)
        if a["source_url"] == b["source_url"]:
            return True
        if days > 21:
            return False
        return dedupe.lexical_same([a["title"], a.get("source_title") or a["title"]],
                                   [b["title"], b.get("source_title") or b["title"]])

    def dedupe(self) -> None:
        before = len(self.data["items"])
        self.data["items"] = dedupe.dedupe(self.data["items"], lambda i: "sector", lambda i: [], self.checker.ask,
                                           self.state.setdefault("sector_dup_verdicts", {}), max_days=21,
                                           related=lambda a, b: bool(a.get("publisher")) and
                                           a["publisher"].lower() == b.get("publisher", "").lower())
        if len(self.data["items"]) < before:
            print(f"  sector: merged {before - len(self.data['items'])} duplicate items")

    def _merge(self, d: dict) -> bool:
        dup = next((i for i in self.data["items"] if self._same(i, d)), None)
        if not dup:
            return False
        have = {s["url"] for s in dup["sources"]}
        dup["sources"] += [s for s in d["sources"] if s["url"] not in have][: max(0, 8 - len(dup["sources"]))]
        if not dup.get("key_stats") and d.get("key_stats"):
            dup["key_stats"] = d["key_stats"]
        return True

    def _reject(self, it: dict, reason: str) -> None:
        self.rejected.append({"checked": self.today.isoformat(), "date": it["date"],
                              "title": it.get("source_title") or it["title"], "source_url": it["source_url"],
                              "source": it["source_name"], "reason": reason})

    def corroborate_pending(self, cands: list[dict]) -> list[dict]:
        keep, promoted = [], []
        cutoff = (self.today - dt.timedelta(days=PENDING_DAYS)).isoformat()
        for p in self.state["sector_pending"]:
            names = {s["name"].lower() for s in p["sources"]}
            for c in cands:
                if c["source"].lower() not in names and self.similar(c["title"], p["source_title"], 0.45):
                    p["sources"].append({"url": c["url"], "name": c["source"]})
                    break
            level = self.trust_level(p)
            if level:
                p["verification"]["level"] = level
                if not self._merge(p):
                    self.data["items"].append(p)
                    promoted.append(p)
            elif p["added_at"][:10] < cutoff:
                self._reject(p, f"no second outlet confirmed it within {PENDING_DAYS} days")
            else:
                keep.append(p)
        self.state["sector_pending"] = keep
        return promoted

    def process(self, cands: list[dict], min_date: dt.date) -> list[dict]:
        seen = self.state["sector_seen"]
        promoted = self.corroborate_pending(cands)
        fresh = [c for c in cands if self.key_of(c["title"]) not in seen and c["date"] >= min_date.isoformat()]
        print(f"  sector: {len(cands)} headlines found, {len(fresh)} new to screen")
        kept = self.screen(fresh) if fresh else []
        for c in fresh:
            if not c.get("_failed"):
                seen[self.key_of(c["title"])] = c["date"]
        drafts = [d for d in (self.draft(c, k) for c, k in kept) if not self._merge(d)]
        drafts += [r for r in self.state["sector_retry"] if r.get("tries", 0) < 3]
        self.state["sector_retry"] = []
        if not drafts:
            return promoted
        publish, pending, rejected = self.verify(drafts)
        for it in rejected:
            reason = it.pop("rejected_reason")
            if "(retry)" in reason:
                it["tries"] = it.get("tries", 0) + 1
                if it["tries"] < 3:
                    self.state["sector_retry"].append(it)
                    continue
            self._reject(it, reason)
        self.state["sector_pending"] += [p for p in pending if not any(self._same(p, q) for q in self.state["sector_pending"])]
        added = []
        for it in publish:
            it.pop("tries", None)
            if not self._merge(it):
                self.data["items"].append(it)
                added.append(it)
        print(f"  sector verified: {len(added)} published, {len(pending)} awaiting a second source, "
              f"{len(rejected)} rejected/retrying")
        return promoted + added

    def recheck_geo_rejections(self) -> None:
        """One-time: GCC city names (Makkah, Jeddah, Dammam, …) were missing from the place check, so items
        rejected for 'no GCC / Gulf angle' are screened again with the full 12-month reload."""
        done = self.state.setdefault("sector_migrations", [])
        if "geo-v2" in done:
            return
        geo = [r for r in self.rejected if r["reason"] == "source has no GCC / Gulf angle"]
        for r in geo:
            self.state["sector_seen"].pop(self.key_of(r["title"]), None)
        self.rejected = [r for r in self.rejected if r not in geo]
        if geo:
            self.state["sector_history"] = None
            print(f"Sector insights: re-checking {len(geo)} items with the extended GCC place list")
        done.append("geo-v2")

    def run(self) -> list[dict]:
        """Daily: last 7 days + publisher pages. First run (or after a reset): load the last 12 months."""
        start = self.today - dt.timedelta(days=int(MONTHS_BACK * 30.5))
        self.recheck_geo_rejections()
        if not self.state.get("sector_history"):
            windows, s = [], start
            while s < self.today:
                e = min(s + dt.timedelta(days=61), self.today + dt.timedelta(days=1))
                windows.append((s, e))
                s = e
            print(f"Sector insights: loading the last {MONTHS_BACK} months ({len(windows)} windows) …")
            added = self.process(self.discover(windows, with_pages=True), start)
            self.state["sector_history"] = self.today.isoformat()
            self.dedupe()
            self.attach_pdfs()
            self.save()
            return [a for a in added if any(a is i for i in self.data["items"])]
        print("Sector insights: daily update …")
        added = self.process(self.discover([None], with_pages=True), self.today - dt.timedelta(days=60))
        self.dedupe()
        self.attach_pdfs()
        self.save()
        return [a for a in added if any(a is i for i in self.data["items"])]


def format_telegram(it: dict, dashboard: str, countries: dict) -> str:
    esc = html.escape
    flags = " ".join(countries[c]["flag"] for c in it["countries"] if c in countries) or "🌍 GCC"
    lines = [f"📊 <b>Sector insight</b> · {esc(it['category'])} · {flags}", f"<b>{esc(it['title'])}</b>", esc(it["summary"])]
    for st in it.get("key_stats", []):
        lines.append(f"• <b>{esc(st['value'])}</b> {esc(st['label'])}")
    link = f'<a href="{esc(it["source_url"], quote=True)}">{esc(it["source_name"] or "Source")}</a>'
    if it.get("pdf"):
        link += f' · <a href="{esc(it["pdf"]["url"], quote=True)}">📄 Report PDF</a>'
    if dashboard:
        link += f' · <a href="{dashboard}/#/sector">All sector insights</a>'
    lines.append(f"🗓 {it['date']} · {link}")
    return "\n".join(lines)
