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
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from pydantic import BaseModel

import newsrooms
import verify

YEARS_BACK = 2                 # fiscal years to cover (matches the 2-year news history)
MAX_PDF_BYTES = 160_000_000   # some integrated reports are 100 MB+
PASSAGE_CHARS = 30_000         # AI-related text sent to Claude per report
MAX_ITEMS_PER_REPORT = 20     # keep every concrete AI item a report discloses (up to this many)
MAX_FAILS = 5                  # a report that could not be downloaded/read is retried daily, up to this many runs
RECHECK_DAYS = 14              # look again for a report that is not out yet
COUNTRY_ORDER = ["QA", "AE", "SA", "KW", "OM", "BH"]
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
projects without AI. Merge repeated mentions of the same initiative into one item. Include EVERY concrete AI item you \
find (up to {max_items}), not only the most significant ones, ordered by significance.

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
def _quote_url(url: str) -> str:
    """Encode spaces and other unsafe characters in a link as written on the bank's page."""
    parts = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._"),
                                                  query=urllib.parse.quote(parts.query, safe="=&%/:+,;?@~-._")))


def fetch_bytes(url: str, limit: int = MAX_PDF_BYTES, timeout: int = 60) -> tuple[bytes, dict]:
    url = _quote_url(url)
    parts = urllib.parse.urlsplit(url)
    base = {**newsrooms.HEADERS, "Accept": "application/pdf,*/*"}
    # some bank sites refuse files requested without a page of their own as referrer (403)
    browser = {**base, "Referer": f"{parts.scheme}://{parts.netloc}/", "Accept-Language": "en-US,en;q=0.9",
               "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"}
    for n, headers in enumerate((base, browser)):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("file too large")
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            if n or exc.code not in (401, 403, 406, 429):
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


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
    configured = {}   # links set in config/banks.json: {"year": 2025, "url": …} (or a plain URL with the year in it)
    for entry in bank.get("annual_reports", []):
        if isinstance(entry, dict):
            configured[int(entry["year"])] = entry["url"]
        else:
            found.append((entry, ""))
    for url, text in found:
        y = report_year(url, text)
        if y:
            by_year.setdefault(y, []).append((url, text))
    # prefer the English edition
    pick = {y: sorted(c, key=lambda c: (bool(re.search(r"[؀-ۿ]|/ar/|[-_]ar[-_.]|arabic", c[0] + c[1], re.I)),
                                        len(c[0])))[0][0] for y, c in by_year.items()}
    pick.update(configured)          # a link checked by hand wins over one found automatically
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
        self.root_state = state
        self.replaced: set[tuple[str, int]] = set()
        self.state = state.setdefault("annual_reports", {})

    def years(self) -> list[int]:
        latest = self.today.year - 1 if self.today.month >= 3 else self.today.year - 2
        return [latest - k for k in range(YEARS_BACK)]

    def _todo_years(self, st: dict) -> list[int]:
        done = st.get("done", {})
        return [y for y in self.years() if str(y) not in done or done[str(y)].get("redo")]

    @staticmethod
    def _config_sig(bank: dict) -> str:
        return json.dumps([bank.get("annual_reports", []), bank.get("annual_reports_pages", [])], sort_keys=True)

    def due(self, bid: str) -> bool:
        st = self.state.get(bid) or {}
        todo = self._todo_years(st)
        if not todo:
            return False
        redo = any(st.get("done", {}).get(str(y), {}).get("redo") for y in todo)
        # a report we found but could not read (site busy, refused, …) is tried again next run
        retry = any(f["n"] < MAX_FAILS for y, f in st.get("failed", {}).items() if int(y) in todo)
        new_links = st.get("config") != self._config_sig(self.banks[bid])   # links added/changed in the config
        last = st.get("checked")
        return redo or retry or new_links or not last or (self.today - dt.date.fromisoformat(last)).days >= RECHECK_DAYS

    def _priority(self, bank: dict) -> tuple:
        """Reports not read yet come before re-reads; then banks with hand-checked links, featured bank, country order."""
        st = self.state.get(bank["id"]) or {}
        done = st.get("done", {})
        new = any(str(y) not in done for y in self._todo_years(st))
        hand = bool(bank.get("annual_reports") or bank.get("annual_reports_pages"))   # links checked by hand
        return (not new, not hand, not bank.get("featured"), COUNTRY_ORDER.index(bank["country"])
                if bank["country"] in COUNTRY_ORDER else len(COUNTRY_ORDER))

    def retry_missing_now(self) -> None:
        """One-time: reports that failed before failures were tracked are looked for again on the next run."""
        done = self.root_state.setdefault("migrations", [])
        if "ar-retry-v1" in done:
            return
        for st in self.state.values():
            if self._todo_years(st):
                st.pop("checked", None)
        done.append("ar-retry-v1")

    def mark_all_for_reread(self) -> None:
        """One-time: reports read under the old 'top 10 items' rule are read again to keep every AI item.
        Their items are replaced only once the new reading has succeeded (no gap on the dashboard)."""
        done = self.root_state.setdefault("migrations", [])
        if "ar-all-items-v1" in done:
            return
        n = 0
        for st in self.state.values():
            for d in st.get("done", {}).values():
                d["redo"] = True
                n += 1
        print(f"Annual reports: {n} reports will be read again to keep every AI item")
        done.append("ar-all-items-v1")

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

    def repair_merged(self, news_items: list[dict]) -> list[dict]:
        """One-time: an earlier duplicate rule merged some different disclosures from the same report. Reports that
        now show fewer items than were verified are read again (their items are replaced by a fresh extraction)."""
        done = self.root_state.setdefault("migrations", [])
        if "ar-unmerge-v1" in done:
            return news_items
        shown: dict[tuple, int] = {}
        for i in news_items:
            if i.get("source_type") == "annual_report":
                k = (i["bank_id"], str(i["report"]["year"]))
                shown[k] = shown.get(k, 0) + 1
        redo = [(bid, y) for bid, st in self.state.items()
                for y, d in st.get("done", {}).items() if shown.get((bid, y), 0) < d.get("items", 0)]
        for bid, y in redo:
            self.state[bid]["done"].pop(y, None)
            self.state[bid]["checked"] = None
        keep = [i for i in news_items if not (i.get("source_type") == "annual_report"
                                              and (i["bank_id"], str(i["report"]["year"])) in redo)]
        if redo:
            print(f"Annual reports: re-reading {len(redo)} reports where items had been merged: "
                  + ", ".join(f"{b} {y}" for b, y in redo))
        done.append("ar-unmerge-v1")
        return keep

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
        todo = sorted((b for b in self.banks.values() if self.due(b["id"])), key=self._priority)
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
            for y in self._todo_years(st):
                url = reports.get(y) or next((u for u, _ in ddg_pdfs(bank, y)), None)
                if not url:
                    print(f"  · {bank['short']} {y}: no report PDF found yet")
                    continue
                try:
                    data, headers = fetch_bytes(url)
                    if not data.startswith(b"%PDF"):
                        raise ValueError("not a PDF")
                    pages, pdf_date = read_pdf(data)
                except Exception as exc:
                    f = st.setdefault("failed", {}).setdefault(str(y), {"n": 0})
                    f.update(n=f["n"] + 1, url=url, error=f"{exc.__class__.__name__}: {exc}"[:200])
                    print(f"  ! {bank['short']} {y}: report not readable ({f['error']}); attempt {f['n']}/{MAX_FAILS}",
                          file=sys.stderr)
                    continue
                st.get("failed", {}).pop(str(y), None)
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
                if st["done"].get(str(y), {}).get("redo"):
                    self.replaced.add((bank["id"], y))     # tracker drops this report's old items
                st["done"][str(y)] = {"url": url, "pages": len(pages), "items": len(items), "date": date}
                added += items
                print(f"  {bank['short']} {y}: {len(pages)} pages, {len(found)} AI disclosures found, "
                      f"{len(items)} verified")
            st["checked"] = self.today.isoformat()
            st["config"] = self._config_sig(bank)
        return added


def _squash_pos(text: str, quote: str) -> int:
    """Approximate character position of the quote in the (whitespace-normalised) page text."""
    words = re.findall(r"\w+", quote)[:4]
    if not words:
        return 0
    m = re.search(r"\W+".join(map(re.escape, words)), text, re.I)
    return m.start() if m else 0
