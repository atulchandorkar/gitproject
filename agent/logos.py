"""Download each bank's logo into data/logos/ so the dashboard can show it without hotlinking.

Order of preference: a "logo" URL in config/banks.json, the bank site's apple-touch-icon or
largest <link rel="icon">, the bank's official logo on Wikipedia/Wikidata (Wikimedia Commons),
then Google's and DuckDuckGo's favicon services. Tiny or generic images are rejected so the next
source is tried. Banks still without a logo are retried on every run; others every REFRESH_DAYS.
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
MIN_PIXELS = 48          # smaller raster images are favicons/placeholders, not usable logos
WIKI_UA = {"User-Agent": "GCC-Bank-AI-Tracker/1.0 (https://github.com/atulchandorkar/gitproject)"}
EXT = {"image/png": "png", "image/svg+xml": "svg", "image/jpeg": "jpg", "image/webp": "webp",
       "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/gif": "gif"}
ICON_TAG = re.compile(r"<link\b[^>]*>", re.I)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*["\']([^"\']*)["\']')


def _pixels(data: bytes, ext: str) -> int | None:
    """Largest side of a PNG/ICO/GIF image, or None if unknown (SVG, JPEG, WebP are accepted as is)."""
    if ext == "png" and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return max(int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    if ext == "ico" and len(data) >= 22 and data[:4] == b"\x00\x00\x01\x00":
        count = int.from_bytes(data[4:6], "little")
        sizes = [(data[6 + 16 * k] or 256) for k in range(min(count, (len(data) - 6) // 16))]
        return max(sizes, default=None)
    if ext == "gif" and len(data) >= 10:
        return max(int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))
    return None


def _download(url: str, headers: dict | None = None) -> tuple[bytes, str] | None:
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "image/*,*/*;q=0.5", "Accept-Encoding": "identity",
                                                   **(headers or {})})
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
    px = _pixels(data, ext)
    if px is not None and px < MIN_PIXELS:
        return None
    return data, ext


def _wiki_json(params: dict) -> dict:
    url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=WIKI_UA), timeout=20) as resp:
        return json.loads(resp.read())


def wikidata_logo_urls(b: dict) -> list[str]:
    """Official logo files from Wikidata (property P154 'logo image'), rendered by Wikimedia Commons."""
    ids: list[str] = []
    for name in [b["name"], *[a for a in b.get("aliases", []) if len(a) > 4]][:3]:
        try:
            hits = _wiki_json({"action": "wbsearchentities", "search": name, "language": "en",
                               "type": "item", "limit": 5}).get("search", [])
        except Exception:
            continue
        ids += [h["id"] for h in hits if h["id"] not in ids]
    if not ids:
        return []
    try:
        ents = _wiki_json({"action": "wbgetentities", "ids": "|".join(ids[:15]),
                           "props": "claims|descriptions|labels|aliases", "languages": "en|ar"}).get("entities", {})
    except Exception:
        return []
    norm = lambda t: re.sub(r"[^\w]+", " ", t.lower()).replace(" plc", "").replace(" group", "").strip()
    wanted = {norm(n) for n in [b["name"], b["short"], b.get("name_ar", ""), *b.get("aliases", [])] if n and len(n) > 2}
    ranked = []
    for qid in ids[:15]:
        e = ents.get(qid, {})
        names = [v.get("value", "") for lang in ("en", "ar") for v in
                 [e.get("labels", {}).get(lang, {}), *e.get("aliases", {}).get(lang, [])]]
        if not wanted & {norm(n) for n in names if n}:
            continue  # a different organisation (e.g. another bank with a similar name)
        desc = e.get("descriptions", {}).get("en", {}).get("value", "").lower()
        files = [c["mainsnak"].get("datavalue", {}).get("value") for c in e.get("claims", {}).get("P154", [])]
        files = [f for f in files if isinstance(f, str)]
        if not files:
            continue
        # only accept entities that are clearly a bank / financial institution
        score = (2 if "bank" in desc or "مصرف" in desc or "بنك" in desc else
                 1 if any(w in desc for w in ("financ", "lender", "monetary", "islamic")) else 0)
        if score:
            ranked.append((score, files[-1]))  # newest logo is usually listed last
    ranked.sort(key=lambda r: -r[0])
    return [f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(f.replace(' ', '_'))}?width=256"
            for _, f in ranked[:2]]


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
    sources = [
        lambda: [b["logo"]] if b.get("logo") else [],
        lambda: _icon_candidates(b["domain"]),
        lambda: wikidata_logo_urls(b),
        lambda: [f"https://www.google.com/s2/favicons?domain={b['domain']}&sz=256",
                 f"https://icons.duckduckgo.com/ip3/{b['domain']}.ico"],
    ]
    for source in sources:  # only look further when the earlier source gave nothing usable
        for url in source():
            got = _download(url, WIKI_UA if "wikimedia.org" in url else None)
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
        return (force or not entry or not entry.get("file")
                or (today - dt.date.fromisoformat(entry["checked"])).days >= REFRESH_DAYS)

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
    missing = [b["short"] for b in banks if not index.get(b["id"], {}).get("file")]
    print(f"  logos: {have}/{len(banks)} banks have a logo" + (f"; missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":  # python agent/logos.py  → refresh all logos now
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config" / "banks.json").read_text())
    refresh(cfg["banks"], root / "data", dt.date.today(), force="--force" in sys.argv)
