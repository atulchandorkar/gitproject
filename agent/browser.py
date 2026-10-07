"""A real browser (headless Chromium via Playwright) for sites that refuse plain downloads or build their pages
with JavaScript: consultancies' press pages (McKinsey, BCG, PwC, Kearney …), banks' investor-relations pages and
interactive annual reports, and report PDFs behind bot protection (e.g. QNB).

Used only as a fallback after the plain download failed, so a normal day costs no extra time. If Playwright or
Chromium is not installed, every function simply raises and callers carry on as before.
"""

from __future__ import annotations

import atexit
import os
import re
import sys
import time
import urllib.parse

from newsrooms import HEADERS, fetch

# page loads per run and purpose (keeps the daily run short; one purpose can't use up another's share)
MAX_LOADS = {"reports": 60, "sector": 40}
BLOCKED = (401, 403, 406, 429, 503)

_pw = _browser = _ctx = None
_loads: dict[str, int] = {}
_broken = False


class Unavailable(RuntimeError):
    pass


def _context():
    global _pw, _browser, _ctx, _broken
    if _ctx is not None:
        return _ctx
    if _broken:
        raise Unavailable("browser not available")
    try:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        exe = os.environ.get("BROWSER_PATH") or None   # e.g. a pre-installed Chromium
        _browser = _pw.chromium.launch(headless=True, executable_path=exe,
                                       args=["--disable-blink-features=AutomationControlled"])
        _ctx = _browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US",
                                    viewport={"width": 1366, "height": 900}, accept_downloads=True)
        _ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        atexit.register(close)
        return _ctx
    except Exception as exc:
        _broken = True
        print(f"  ! browser could not start ({exc.__class__.__name__}: {str(exc)[:120]})", file=sys.stderr)
        raise Unavailable(str(exc)) from exc


def close() -> None:
    global _pw, _browser, _ctx
    for obj, meth in ((_ctx, "close"), (_browser, "close"), (_pw, "stop")):
        try:
            if obj is not None:
                getattr(obj, meth)()
        except Exception:
            pass
    _pw = _browser = _ctx = None


def _budget(kind: str) -> None:
    if _loads.get(kind, 0) >= MAX_LOADS.get(kind, 20):
        raise Unavailable(f"browser page budget for {kind} used")
    _loads[kind] = _loads.get(kind, 0) + 1


def _open(url: str, kind: str, wait_ms: int = 2500, timeout_ms: int = 45000):
    _budget(kind)
    page = _context().new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(wait_ms)          # let bot checks and client-side rendering finish
        status = resp.status if resp else 0
        if status in BLOCKED or status >= 400:
            raise RuntimeError(f"HTTP {status} in browser")
        return page
    except Exception:
        page.close()
        raise


def page_html(url: str, kind: str) -> tuple[str, str]:
    """(final_url, rendered html) as a browser sees it."""
    page = _open(url, kind)
    try:
        return page.url, page.content()
    finally:
        page.close()


def page_text(url: str, kind: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(final_url, visible text, [(link, link text)]) of the rendered page."""
    page = _open(url, kind)
    try:
        text = page.inner_text("body", timeout=15000)
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href, (e.innerText || e.getAttribute('aria-label') || '').trim()])")
        return page.url, text, [(h.split("#")[0], t) for h, t in links if h.startswith("http")]
    finally:
        page.close()


def fetch_any(url: str, kind: str, timeout: int = 20) -> tuple[str, str]:
    """Plain download first; a real browser when the site refuses robots, times out, or sends an empty JS shell."""
    try:
        final, raw = fetch(url, timeout=timeout)
        body = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", raw, flags=re.S | re.I)
        if len(re.findall(r"<a\s[^>]*href", body, re.I)) >= 5:    # a real page, not an empty JavaScript shell
            return final, raw
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code is not None and code not in BLOCKED:
            raise
    return page_html(url, kind)


def download(url: str, limit: int, kind: str = "reports") -> tuple[bytes, dict]:
    """A file (e.g. a report PDF) fetched with the browser's cookies, after visiting the site's home page
    the way a person would (passes most bot checks that refuse plain downloads)."""
    ctx = _context()
    parts = urllib.parse.urlsplit(url)
    home = f"{parts.scheme}://{parts.netloc}/"
    try:
        _open(home, kind, wait_ms=3000).close()
    except Exception:
        pass
    _budget(kind)
    for attempt in range(2):
        resp = ctx.request.get(url, headers={"Referer": home, "Accept": "application/pdf,*/*"}, timeout=180000)
        if resp.ok:
            body = resp.body()
            if len(body) > limit:
                raise ValueError("file too large")
            return body, dict(resp.headers)
        if attempt or resp.status not in BLOCKED:
            raise RuntimeError(f"HTTP {resp.status} in browser")
        time.sleep(3)
    raise RuntimeError("unreachable")
