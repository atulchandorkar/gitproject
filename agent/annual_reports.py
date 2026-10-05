"""AI implementations disclosed in banks' own annual reports (a separate source type, tagged to the bank).

Per bank and fiscal year (the last two, as the reports appear):
  1. find the annual / integrated report PDF on the bank's own site (config override, the investor-relations
     pages linked from the homepage, then a DuckDuckGo search restricted to the bank's domain);
  2. read the PDF and keep only the passages that mention AI (free, local);
  3. Claude Haiku extracts concrete AI implementations / initiatives / investments / outcomes, each with the
     exact quote and page;
  4. checks: the quote must appear word for word on that page of the PDF, every number must be on that page,
     and the independent fact-check must confirm the claims against the page text.
Each disclosure becomes a card in the bank's news, marked "Annual Report <year> · p. N" with a link to that page.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Callable

from pydantic import BaseModel

import newsrooms
import verify

YEARS_BACK = 2                 # fiscal years to cover (matches the 2-year news history)
MAX_PDF_BYTES = 80_000_000
PASSAGE_CHARS = 30_000         # AI-related text sent to Claude per report
MAX_ITEMS_PER_REPORT = 10
RECHECK_DAYS = 14              # look again for a report that is not out yet
RUN_BUDGET_S = 30 * 60         # time cap per daily run; remaining banks continue next run

AI_RE = re.compile(
    r"\b(ai|a\.i\.|genai|gen ai|generative|artificial intelligence|machine[- ]learning|llms?|large language models?|"
    r"agentic|copilots?|chatgpt|gpt|chatbots?|virtual assistants?|robotic process automation|rpa|"
    r"natural language processing|nlp|computer vision|predictive (?:model|analytics)\w*|intelligent automation)\b"
    r"|الذكاء الاصطناعي|ذكاء اصطناعي|الذكاء الإصطناعي|التعلم الآلي|المساعد الافتراضي|الأتمتة الذكية", re.I)
IR_LINK_RE = re.compile(r"investor|annual[-_ ]?reports?|financial[-_ ]?(?:reports?|statements|information)|"
                        r"reports?[-_ ]and[-_ ]presentations|publications|علاقات المستثمرين|المستثمرين|التقارير", re.I)
AR_RE = re.compile(r"annual[-_ %20]*report|integrated[-_ %20]*report|التقرير السنوي|التقرير المتكامل|"
                   r"\bAR[-_ ]?20\d\d|20\d\d[-_ ]?AR\b", re.I)
SKIP_RE = re.compile(r"sustainab|esg|governance[-_ ]report|corporate[-_ ]governance|pillar|basel|"
                     r"financial[-_ ]statements|interim|quarter|q[1-4]\b|press|presentation|summary|highlights|"
                     r"zakat|shariah[-_ ]report|remuneration|الاستدامة|الحوكمة|القوائم المالية|المرحلية", re.I)
YEAR_RE = re.compile(r"(?<!\d)(20[12]\d)(?!\d)")


class Disclosure(BaseModel):
    title: str            # clear English headline of the AI implementation, max ~110 chars
    summary: str          # two short English sentences, only what the quote/passage states
    category: str         # one of the dashboard themes
    topics: list[str]     # 1-3 themes, including the main one
    tags: list[str]       # 1-4 short AI tags
    partners: list[str]   # technology partners named in the passage
    impact: str           # measurable outcome stated in the passage, else ""
    page: int             # page number given in the passage marker [p.N]
    quote: str            # the exact sentence(s) from the passage, copied character for character


class Extracted(BaseModel):
    items: list[Disclosure]


EXTRACT_PROMPT = """You read passages from {bank}'s {report} (each passage starts with its page marker [p.N]) \
for a dashboard that tracks banks' AI implementations.

Extract each CONCRETE AI implementation, initiative, investment, partnership, programme or measurable outcome of \
{bank} itself (AI/GenAI/ML models, assistants, chatbots, copilots, AI for fraud, credit, KYC, operations, AI \
strategy or governance set up, AI training, AI-related awards). Skip: generic statements ("AI will transform \
banking"), risk-factor boilerplate, market commentary, other companies' activities, and plain digital or IT \
projects without AI. Merge repeated mentions of the same initiative into one item. At most {max_items} items, most \
significant first.

For each item: title (clear English headline starting with "{short}"), summary (two short English sentences, only \
what the passage states), category and topics from the theme list, tags, partners named, impact (a measurable \
outcome stated in the passage, else ""), page (from the [p.N] marker) and quote (the exact sentence(s) from the \
passage that state it, copied character for character, Arabic stays Arabic). Never add facts, numbers or names that \
are not in the passage. Return no items if there is nothing concrete.

Themes:
{themes}"""


# --------------------------------------------------------------------------- #
# Finding the report
# --------------------------------------------------------------------------- #
def fetch_bytes(url: str, limit: int = MAX_PDF_BYTES, timeout: int = 60) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={**newsrooms.HEADERS, "Accept": "application/pdf,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
        if len(data) > limit:
            raise ValueError("file too large")
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data, dict(resp.headers)


def report_year(url: str, text: str) -> int | None:
    """Fiscal year of a report link, from its anchor text or file name."""
    for src in (text, urllib.parse.unquote(url.rsplit("/", 1)[-1]), urllib.parse.unquote(url)):
        years = [int(y) for y in YEAR_RE.findall(src or "")]
        if years:
            return max(years)
    return None


def pdf_links(page_url: str, domain: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(annual-report PDF links, further IR pages) found on one page of the bank's site."""
    try:
        final, raw = newsrooms.fetch(page_url, timeout=25)
    except Exception:
        return [], []
    links = newsrooms.parse(raw, final).links
    pdfs, pages = [], []
    for url, text in links:
        text = re.sub(r"\s+", " ", text or "").strip()
        if not url.startswith("http"):
            continue
        host_ok = newsrooms.same_site(url, domain) or "cdn" in url or "blob" in url or "amazonaws" in url
        blob = f"{text} {urllib.parse.unquote(url)}"
        if re.search(r"\.pdf($|\?)", url, re.I) and host_ok and AR_RE.search(blob) and not SKIP_RE.search(blob):
            pdfs.append((url, text))
        elif newsrooms.same_site(url, domain) and (IR_LINK_RE.search(blob) or AR_RE.search(blob)) \
                and not re.search(r"\.(pdf|jpg|png|zip|xlsx?)($|\?)", url, re.I):
            pages.append(url)
    return pdfs, list(dict.fromkeys(pages))


def ddg_pdfs(bank: dict, year: int) -> list[tuple[str, str]]:
    """Fallback: search the bank's own domain for the report PDF."""
    q = f'site:{bank["domain"]} "annual report" {year} pdf'
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
    try:
        _, raw = newsrooms.fetch(url, timeout=20)
    except Exception:
        return []
    out = []
    for m in re.finditer(r'href="[^"]*?uddg=([^"&]+)[^"]*"[^>]*>(.*?)</a>', raw, re.S):
        link = urllib.parse.unquote(m.group(1))
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        if newsrooms.same_site(link, bank["domain"]) and re.search(r"\.pdf($|\?)", link, re.I) \
                and AR_RE.search(f"{text} {link}") and not SKIP_RE.search(f"{text} {link}"):
            out.append((link, text))
    return out


def find_reports(bank: dict, cached_pages: list[str]) -> tuple[dict[int, str], list[str]]:
    """{fiscal year: pdf url} for the bank, plus the IR pages that worked (cached for next time)."""
    domain = bank["domain"]
    seeds = list(bank.get("annual_reports_pages", [])) + list(cached_pages)
    if not seeds:
        seeds = [f"https://www.{domain}/", f"https://www.{domain}/en", f"https://{domain}/"]
    found: list[tuple[str, str]] = []
    useful: list[str] = []
    queue, seen = list(dict.fromkeys(seeds)), set()
    while queue and len(seen) < 12:                       # homepage → IR page → annual-reports page
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        pdfs, more = pdf_links(page, domain)
        if pdfs:
            found += pdfs
            useful.append(page)
        ranked = sorted(more, key=lambda u: (not AR_RE.search(u), not re.search(r"investor|المستثمرين", u, re.I)))
        queue += [u for u in ranked[:6] if u not in seen]
    by_year: dict[int, list[tuple[str, str]]] = {}
    for url, text in found + [(u, "") for u in bank.get("annual_reports", [])]:
        y = report_year(url, text)
        if y:
            by_year.setdefault(y, []).append((url, text))
    # prefer the English edition
    pick = {y: sorted(c, key=lambda c: (bool(re.search(r"[؀-ۿ]|/ar/|[-_]ar[-_.]|arabic", c[0] + c[1], re.I)),
                                        len(c[0])))[0][0] for y, c in by_year.items()}
    return pick, useful


# --------------------------------------------------------------------------- #
# Reading the report
# --------------------------------------------------------------------------- #
def read_pdf(data: bytes) -> tuple[list[str], str | None]:
    """(text of each page, publication date from the PDF metadata if present)."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    date = None
    try:
        md = reader.metadata
        d = md and (md.creation_date or md.modification_date)
        date = d.date().isoformat() if d else None
    except Exception:
        pass
    return pages, date


def passages(pages: list[str]) -> str:
    """Only the AI-related sentences (with one sentence of context), tagged with their page number."""
    out, total = [], 0
    for n, text in enumerate(pages, start=1):
        text = re.sub(r"\s+", " ", text)
        sents = re.split(r"(?<=[.!?؟])\s+", text)
        keep = set()
        for k, s in enumerate(sents):
            if AI_RE.search(s):
                keep.update({k - 1, k, k + 1})
        if not keep:
            continue
        chunk = " ".join(sents[k] for k in sorted(keep) if 0 <= k < len(sents))
        block = f"[p.{n}] {chunk}"
        if total + len(block) > PASSAGE_CHARS:
            block = block[: max(0, PASSAGE_CHARS - total)]
        out.append(block)
        total += len(block)
        if total >= PASSAGE_CHARS:
            break
    return "\n\n".join(out)


def _squash(s: str) -> str:
    return re.sub(r"\W+", "", (s or "").lower())


def locate(quote: str, pages: list[str], hint: int) -> int | None:
    """Page (1-based) on which the quote appears word for word (spacing/hyphenation ignored)."""
    q = _squash(quote)
    if len(q) < 30:
        return None
    order = [hint] + [n for n in range(1, len(pages) + 1) if n != hint]
    for n in order:
        if 1 <= n <= len(pages) and q in _squash(pages[n - 1]):
            return n
    return None


# --------------------------------------------------------------------------- #
# Collector
# --------------------------------------------------------------------------- #
class AnnualReports:
    def __init__(self, banks: dict, state: dict, checker: verify.FactChecker, themes: dict[str, str],
                 today: dt.date, now_iso: Callable[[], str]):
        self.banks, self.checker, self.themes, self.today, self.now_iso = banks, checker, themes, today, now_iso
        self.state = state.setdefault("annual_reports", {})

    def years(self) -> list[int]:
        latest = self.today.year - 1 if self.today.month >= 3 else self.today.year - 2
        return [latest - k for k in range(YEARS_BACK)]

    def due(self, bid: str) -> bool:
        st = self.state.get(bid) or {}
        missing = [y for y in self.years() if str(y) not in st.get("done", {})]
        last = st.get("checked")
        return bool(missing) and (not last or (self.today - dt.date.fromisoformat(last)).days >= RECHECK_DAYS)

    def extract(self, bank: dict, year: int, pages: list[str]) -> list[Disclosure]:
        text = passages(pages)
        if not text:
            return []
        report = f"Annual Report {year}"
        system = EXTRACT_PROMPT.format(bank=bank["name"], short=bank["short"], report=report,
                                       max_items=MAX_ITEMS_PER_REPORT,
                                       themes="\n".join(f"- {k}: {v}" for k, v in self.themes.items()))
        out = self.checker.ask(system, f"Passages:\n\n{text}", Extracted, 8000)
        return list(out.items)[:MAX_ITEMS_PER_REPORT] if out else []

    def verify(self, bank: dict, year: int, url: str, pages: list[str], date: str,
               found: list[Disclosure]) -> tuple[list[dict], list[dict]]:
        staged, rejected = [], []
        for d in found:
            page = locate(d.quote, pages, d.page)
            base = {"bank_id": bank["id"], "date": date, "title": d.title, "source_url": url,
                    "source": f"{bank['short']} Annual Report {year}"}
            if not page:
                rejected.append({**base, "reason": "quote not found word for word in the report"})
                continue
            ev = re.sub(r"\s+", " ", pages[page - 1])
            pos = max(0, _squash_pos(ev, d.quote) - 2500)
            ev = ev[pos:pos + 7000]
            impact = d.impact if verify.numbers_supported(d.impact, ev) else ""
            summary = d.summary if verify.numbers_supported(d.summary, ev) else ""
            title = d.title if verify.numbers_supported(d.title, ev) else ""
            if not title:
                rejected.append({**base, "reason": "headline has numbers that are not on the page"})
                continue
            cats = [c for c in d.topics if c in self.themes]
            cat = d.category if d.category in self.themes else (cats[0] if cats else "AI Adoption in Operations")
            src = f"{url}#page={page}"
            item = {
                "id": hashlib.sha1(f"ar|{bank['id']}|{url}|{_squash(d.quote)[:80]}".encode()).hexdigest()[:12],
                "bank_id": bank["id"], "country": bank["country"], "date": date,
                "title": title.strip(), "summary": summary.strip(), "category": cat,
                "topics": list(dict.fromkeys([cat, *cats]))[:3], "tags": [t for t in d.tags if t.strip()][:4],
                "partners": [p for p in d.partners if p.strip()], "impact": impact.strip(),
                "source_url": src, "source_name": f"{bank['short']} Annual Report {year}",
                "source_title": "", "sources": [{"url": src, "name": f"{bank['short']} Annual Report {year}"}],
                "language": "en", "added_at": self.now_iso(), "source_type": "annual_report",
                "report": {"year": year, "url": url, "page": page, "quote": d.quote.strip()},
            }
            staged.append((item, ev))
        published = []
        for s in range(0, len(staged), 6):          # independent fact-check against the page text
            chunk = staged[s:s + 6]
            rows = [{"i": n, "bank": bank["name"], "evidence_type": f"page {it['report']['page']} of the bank's own "
                     f"annual report {year} (\"the Bank\", \"we\" and \"our\" refer to {bank['name']})",
                     "evidence": ev, "claims": {"title": it["title"], "summary": it["summary"],
                                                "impact": it["impact"], "partners": it["partners"]}}
                    for n, (it, ev) in enumerate(chunk)]
            results = self.checker.check(rows)
            for n, (it, ev) in enumerate(chunk):
                c = results.get(n)
                if c is None or not c.bank_correct or not c.bank_own_ai_activity or not c.title_supported:
                    rejected.append({"bank_id": bank["id"], "date": date, "title": it["title"], "source_url": url,
                                     "source": it["source_name"],
                                     "reason": "fact-check: " + ((c.reason if c else "") or "not supported by the page")})
                    continue
                if not c.summary_supported:
                    it["summary"] = ""
                if not c.impact_supported:
                    it["impact"] = ""
                if not c.partners_supported:
                    it["partners"] = []
                if len(it["summary"]) < 40:
                    it["summary"] = (f"{bank['name']} reports in its {year} annual report (p. {it['report']['page']}): "
                                     f"“{it['report']['quote'][:220].rstrip()}{'…' if len(it['report']['quote']) > 220 else ''}”")
                it["verification"] = {"level": "official", "evidence": "annual report", "checked": self.today.isoformat()}
                published.append(it)
        return published, rejected

    def export(self, path) -> None:
        """Report list for the dashboard (each bank's annual report PDFs, linked to the bank's own site)."""
        out = {bid: sorted(({"year": int(y), **d} for y, d in st.get("done", {}).items()), key=lambda r: -r["year"])
               for bid, st in self.state.items() if st.get("done")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)

    def run(self, rejected_log: list[dict]) -> list[dict]:
        start = time.monotonic()
        added: list[dict] = []
        todo = [b for b in self.banks.values() if self.due(b["id"])]
        print(f"Annual reports: {len(todo)} banks to check (years {', '.join(map(str, self.years()))}) …")
        for bank in todo:
            if time.monotonic() - start > RUN_BUDGET_S:
                print("  annual reports: time budget used, the rest continue next run")
                break
            st = self.state.setdefault(bank["id"], {"done": {}})
            try:
                reports, useful = find_reports(bank, st.get("pages", []))
            except Exception as exc:
                print(f"  ! {bank['short']}: report search failed ({exc!r})", file=sys.stderr)
                reports, useful = {}, []
            if useful:
                st["pages"] = useful[:3]
            for y in self.years():
                if str(y) in st["done"]:
                    continue
                url = reports.get(y) or next((u for u, _ in ddg_pdfs(bank, y)), None)
                if not url:
                    continue
                try:
                    data, headers = fetch_bytes(url)
                    if not data.startswith(b"%PDF"):
                        raise ValueError("not a PDF")
                    pages, pdf_date = read_pdf(data)
                except Exception as exc:
                    print(f"  ! {bank['short']} {y}: report not readable ({exc.__class__.__name__}: {exc})", file=sys.stderr)
                    continue
                date = pdf_date if pdf_date and pdf_date[:4] >= str(y) else None
                if not date and headers.get("Last-Modified"):
                    try:
                        import email.utils
                        lm = email.utils.parsedate_to_datetime(headers["Last-Modified"]).date().isoformat()
                        date = lm if lm[:4] >= str(y) else None
                    except (TypeError, ValueError):
                        date = None
                date = min(date or f"{y + 1}-03-31", self.today.isoformat())
                try:
                    found = self.extract(bank, y, pages)
                    items, rejected = self.verify(bank, y, url, pages, date, found)
                except Exception as exc:
                    if exc.__class__.__name__ == "FatalAPIError":
                        raise
                    print(f"  ! {bank['short']} {y}: extraction failed, will retry ({exc!r})", file=sys.stderr)
                    continue
                for r in rejected:
                    rejected_log.append({"checked": self.today.isoformat(), **r})
                st["done"][str(y)] = {"url": url, "pages": len(pages), "items": len(items), "date": date}
                added += items
                print(f"  {bank['short']} {y}: {len(pages)} pages, {len(found)} AI disclosures found, "
                      f"{len(items)} verified")
            st["checked"] = self.today.isoformat()
        return added


def _squash_pos(text: str, quote: str) -> int:
    """Approximate character position of the quote in the (whitespace-normalised) page text."""
    words = re.findall(r"\w+", quote)[:4]
    if not words:
        return 0
    m = re.search(r"\W+".join(map(re.escape, words)), text, re.I)
    return m.start() if m else 0
