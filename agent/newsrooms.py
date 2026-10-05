"""Read banks' own newsroom / media-centre pages (free, no API calls).

For each bank: find its newsroom (config override, cached URL, or auto-discovered from the
homepage), then pull press-release links from it. Dates come from the URL or, for the few
AI-related headlines, from the article page's metadata.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,ar;q=0.8",
    "Accept-Encoding": "gzip",
}
REDISCOVER_DAYS = 30

# Links that look like a newsroom, best first.
NEWSROOM_PATTERNS = [
    (re.compile(r"newsroom|news-room|press[-_ ]?releases?|press[-_ ]?room|media[-_ ]?cent(er|re)|media[-_ ]?room", re.I), 3),
    (re.compile(r"المركز الإعلامي|المركز الاعلامي|البيانات الصحفية|غرفة الأخبار", re.I), 3),
    (re.compile(r"\bnews\b|\bmedia\b|\bpress\b|latest[-_ ]news|الأخبار|أخبار", re.I), 1),
]
ARTICLE_HINT = re.compile(r"news|press|media|article|story|release|stories|/20\d\d/|أخبار", re.I)
SKIP_LINK = re.compile(r"login|log-in|sign-?in|register|careers?|jobs|contact|privacy|terms|cookie|sitemap|"
                       r"facebook|twitter|x\.com|linkedin|instagram|youtube|whatsapp|mailto:|tel:|javascript:|"
                       r"\.pdf$|apply|calculator|branch|atm", re.I)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.rss: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._text = a["href"], []
        elif tag == "link" and (a.get("type") or "").endswith(("rss+xml", "atom+xml")) and a.get("href"):
            self.rss.append(a["href"])

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None


def fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    """Return (final_url, html) or raise."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(3_000_000)
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), raw.decode(charset, errors="replace")


def same_site(url: str, domain: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host == domain or host.endswith("." + domain)


def parse(html_text: str, base: str) -> _Links:
    p = _Links()
    try:
        p.feed(html_text)
    except Exception:
        pass
    p.links = [(urllib.parse.urljoin(base, h).split("#")[0], t) for h, t in p.links]
    p.rss = [urllib.parse.urljoin(base, h) for h in p.rss]
    return p


def article_links(page: _Links, domain: str, newsroom_url: str) -> list[dict]:
    out, seen = [], set()
    for url, text in page.links:
        if (url in seen or url.rstrip("/") == newsroom_url.rstrip("/") or not same_site(url, domain)
                or SKIP_LINK.search(url) or not (25 <= len(text) <= 240) or not ARTICLE_HINT.search(url)):
            continue
        seen.add(url)
        out.append({"title": text, "url": url})
    return out


def discover_newsroom(domain: str) -> str | None:
    for home in (f"https://www.{domain}/", f"https://{domain}/"):
        try:
            final, html_text = fetch(home)
            break
        except Exception:
            continue
    else:
        return None
    page = parse(html_text, final)
    scored: list[tuple[int, str]] = []
    for url, text in page.links:
        if not same_site(url, domain) or SKIP_LINK.search(url):
            continue
        hay = f"{url} {text}"
        for rx, score in NEWSROOM_PATTERNS:
            if rx.search(hay):
                scored.append((score + (1 if "/en" in url.lower() else 0), url))
                break
    for _, url in sorted(set(scored), key=lambda s: -s[0])[:4]:
        try:
            final, html_text = fetch(url)
        except Exception:
            continue
        if len(article_links(parse(html_text, final), domain, final)) >= 3:
            return final
    return None


DATE_IN_URL = re.compile(r"/(20\d\d)[/-](0[1-9]|1[0-2])(?:[/-](0[1-9]|[12]\d|3[01]))?")
META_DATE = re.compile(
    r'(?:article:published_time|datePublished|publish[-_]?date|og:published_time|"dateCreated")'
    r'["\']?\s*(?:content=|:)\s*["\']?(20\d\d-\d\d-\d\d)', re.I)
TEXT_DATE = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(20\d\d)\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _h(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def date_from_url(url: str) -> str | None:
    m = DATE_IN_URL.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}"
    return None


def date_from_article(url: str) -> str | None:
    try:
        _, html_text = fetch(url, timeout=15)
    except Exception:
        return None
    m = META_DATE.search(html_text)
    if m:
        return m.group(1)
    m = TEXT_DATE.search(html_text)
    if m:
        try:
            return dt.date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def scan_bank(b: dict, cached: dict | None, ai_re: re.Pattern, today: dt.date) -> tuple[dict, list[dict]]:
    """Return (newsroom_status, AI-related candidate headlines) for one bank."""
    domain = b["domain"]
    status = dict(cached or {})
    url = b.get("newsroom") or status.get("url")
    stale = not status.get("checked") or (today - dt.date.fromisoformat(status["checked"])).days >= REDISCOVER_DAYS
    if not b.get("newsroom") and (not url or (stale and status.get("status") != "ok")):
        url = discover_newsroom(domain)
        status["checked"] = today.isoformat()
    if not url:
        status.update(url=None, status="not found")
        return status, []
    try:
        final, html_text = fetch(url)
    except Exception as exc:
        status.update(url=url, status=f"unreachable ({type(exc).__name__})")
        return status, []
    links = article_links(parse(html_text, final), domain, final)
    status.update(url=url, status="ok" if links else "no links (page may need JavaScript)", links=len(links))
    if links:
        status.setdefault("checked", today.isoformat())
    first_scan = not (cached or {}).get("scanned")
    known = set((cached or {}).get("known", []))
    status["scanned"] = today.isoformat()
    status["known"] = [_h(l["url"]) for l in links][:400]  # links on the page now, to spot new ones next time

    cands = []
    for link in links:
        if not ai_re.search(link["title"]):
            continue
        date = date_from_url(link["url"]) or date_from_article(link["url"])
        if not date:
            if first_scan or _h(link["url"]) in known:
                continue  # undated and not newly added to the page: could be years old
            date = today.isoformat()  # appeared since the last scan, so it is new
        cands.append({"title": link["title"], "url": link["url"], "source": f"{b['short']} newsroom", "date": date,
                      "language": "ar" if re.search(r"[؀-ۿ]", link["title"]) else "en",
                      "bank_hint": b["id"]})
    return status, cands


def scan_all(banks: list[dict], state: dict, ai_re: re.Pattern, today: dt.date, workers: int = 8) -> list[dict]:
    rooms = state.setdefault("newsrooms", {})
    found: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_bank, b, rooms.get(b["id"]), ai_re, today): b for b in banks}
        for fut in cf.as_completed(futures):
            b = futures[fut]
            try:
                status, cands = fut.result()
            except Exception as exc:
                print(f"  ! newsroom scan failed for {b['short']}: {exc!r}", file=sys.stderr)
                continue
            rooms[b["id"]] = status
            found += cands
    ok = sum(1 for s in rooms.values() if s.get("status") == "ok")
    print(f"  newsrooms: {ok}/{len(banks)} readable, {len(found)} AI-related headlines")
    return found


if __name__ == "__main__":  # quick manual check: python agent/newsrooms.py qnb.com
    import sys as _s
    print(json.dumps({"newsroom": discover_newsroom(_s.argv[1])}, indent=1))
