"""Download each bank's logo into data/logos/ so the dashboard can show it without hotlinking.

Order of preference: a "logo" URL in config/banks.json, the bank site's apple-touch-icon,
its largest <link rel="icon">, then Google's and DuckDuckGo's favicon services. Refreshed every REFRESH_DAYS.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from newsrooms import HEADERS, fetch, same_site

REFRESH_DAYS = 60
MIN_BYTES = 400
EXT = {"image/png": "png", "image/svg+xml": "svg", "image/jpeg": "jpg", "image/webp": "webp",
       "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/gif": "gif"}
ICON_TAG = re.compile(r"<link\b[^>]*>", re.I)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*["\']([^"\']*)["\']')


def _download(url: str) -> tuple[bytes, str] | None:
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "image/*,*/*;q=0.5", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            data = resp.read(2_000_000)
    except Exception:
        return None
    ext = EXT.get(ctype)
    if not ext:  # some servers send octet-stream; trust the file extension instead
        m = re.search(r"\.(png|svg|jpe?g|webp|ico|gif)(?:\?|$)", url, re.I)
        ext = m and m.group(1).lower().replace("jpeg", "jpg")
    if not ext or len(data) < MIN_BYTES or (ext == "svg" and b"<svg" not in data[:2000].lower()):
        return None
    return data, ext


def _icon_candidates(domain: str) -> list[str]:
    for home in (f"https://www.{domain}/", f"https://{domain}/"):
        try:
            final, html_text = fetch(home)
            break
        except Exception:
            continue
    else:
        return []
    scored: list[tuple[int, str]] = []
    for tag in ICON_TAG.findall(html_text):
        a = {k.lower(): v for k, v in ATTR.findall(tag)}
        rel, href = a.get("rel", "").lower(), a.get("href")
        if not href or "icon" not in rel:
            continue
        size = max((int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))), default=0)
        score = size or 32
        if "apple-touch-icon" in rel:
            score = max(score, 180) + 1000
        elif href.lower().endswith(".svg") or "svg" in a.get("type", ""):
            score += 500
        url = urllib.parse.urljoin(final, href)
        if same_site(url, domain) or "cdn" in url or "static" in url:
            scored.append((score, url))
    urls = [u for _, u in sorted(scored, key=lambda s: -s[0])]
    urls.append(urllib.parse.urljoin(final, "/apple-touch-icon.png"))
    return urls


def fetch_logo(b: dict) -> tuple[bytes, str, str] | None:
    candidates = ([b["logo"]] if b.get("logo") else []) + _icon_candidates(b["domain"]) + [
        f"https://www.google.com/s2/favicons?domain={b['domain']}&sz=256",
        f"https://icons.duckduckgo.com/ip3/{b['domain']}.ico"]
    for url in candidates:
        got = _download(url)
        if got:
            return got[0], got[1], url
    return None


def refresh(banks: list[dict], data_dir: Path, today: dt.date, force: bool = False) -> None:
    out_dir = data_dir / "logos"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_file = data_dir / "logos.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}

    def due(b: dict) -> bool:
        entry = index.get(b["id"])
        return force or not entry or (today - dt.date.fromisoformat(entry["checked"])).days >= REFRESH_DAYS

    todo = [b for b in banks if due(b)]
    if not todo:
        return
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for b, got in zip(todo, pool.map(fetch_logo, todo)):
            entry = index.get(b["id"], {})
            entry["checked"] = today.isoformat()
            if got:
                data, ext, src = got
                for old in out_dir.glob(f"{b['id']}.*"):
                    old.unlink()
                (out_dir / f"{b['id']}.{ext}").write_bytes(data)
                entry.update(file=f"logos/{b['id']}.{ext}", source=src)
            index[b["id"]] = entry
    index_file.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    have = sum(1 for e in index.values() if e.get("file"))
    print(f"  logos: {have}/{len(banks)} banks have a logo")


if __name__ == "__main__":  # python agent/logos.py  → refresh all logos now
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config" / "banks.json").read_text())
    refresh(cfg["banks"], root / "data", dt.date.today(), force="--force" in sys.argv)
