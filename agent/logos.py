"""Download each bank's logo into data/logos/ so the dashboard can show it without hotlinking.

Order of preference: a "logo" URL in config/banks.json, the bank site's apple-touch-icon or
largest <link rel="icon">, the bank's official logo on Wikidata (Wikimedia Commons), the logo in its
Wikipedia infobox (EN/AR), the header logo <img> on its homepage, then Google's and DuckDuckGo's
favicon services. Every file is checked by its bytes (real PNG/JPEG/SVG/…, at least 48 px), and saved
files that fail the check are replaced. Tiny or generic images are rejected so the next
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
MIN_PIXELS = 48          # smaller raster images are favicons/placeholders, not proper logos
FALLBACK_PIXELS = 16     # a small favicon is still better than initials, until a proper logo is found
SMALL_RETRY_DAYS = 7     # banks with only a small icon look for a proper logo again weekly
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


def sniff(data: bytes) -> str | None:
    """Real image type from the file's first bytes (servers sometimes send HTML/JS with an image name)."""
    head = data[:2048]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if b"<svg" in head.lower() and b"<html" not in head.lower():
        return "svg"
    return None


def usable(data: bytes, min_px: int = MIN_PIXELS) -> str | None:
    """Image type if the bytes are a real image of at least `min_px` pixels; else None."""
    ext = sniff(data)
    if not ext or len(data) < (MIN_BYTES if min_px >= MIN_PIXELS else 100):
        return None
    px = _pixels(data, ext)
    return None if px is not None and px < min_px else ext


def _download(url: str, headers: dict | None = None, min_px: int = MIN_PIXELS) -> tuple[bytes, str] | None:
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "image/*,*/*;q=0.5", "Accept-Encoding": "identity",
                                                   **(headers or {})})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = (resp.headers.get_content_type() or "").lower()
            data = resp.read(2_000_000)
    except Exception:
        return None
    ext = usable(data, min_px)   # judged by the bytes, not the server's content type or the file name
    return (data, ext) if ext else None


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
        if score or b.get("any_org"):   # publishers (consultancies, vendors, regulators) need not be banks
            ranked.append((score, files[-1]))  # newest logo is usually listed last
    ranked.sort(key=lambda r: -r[0])
    return [f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(f.replace(' ', '_'))}?width=256"
            for _, f in ranked[:2]]


def wikipedia_logo_urls(b: dict) -> list[str]:
    """The logo in the bank's Wikipedia infobox (English, then Arabic), only if the article title matches the bank."""
    norm = lambda t: re.sub(r"[^\w]+", " ", (t or "").lower()).replace(" plc", "").replace(" group", "").strip()
    wanted = {norm(n) for n in [b["name"], b["short"], b.get("name_ar", ""), *b.get("aliases", [])] if n and len(n) > 2}
    out = []
    for lang, titles in (("en", [b["name"], *b.get("aliases", [])]), ("ar", [b.get("name_ar", "")])):
        titles = [t for t in titles if t and len(t) > 3][:4]
        if not titles:
            continue
        url = (f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "prop": "pageimages", "piprop": "original", "redirects": 1, "format": "json",
             "titles": "|".join(titles)}))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=WIKI_UA), timeout=20) as resp:
                pages = json.loads(resp.read()).get("query", {}).get("pages", {})
        except Exception:
            continue
        for pg in pages.values():
            src = (pg.get("original") or {}).get("source", "")
            if src and norm(pg.get("title")) in wanted and re.search(r"logo|emblem|شعار|\.svg", src, re.I):
                out.append(src)
    return out


def _header_logo_candidates(domain: str) -> list[str]:
    """<img> tags on the bank's homepage whose file, alt, class or id says 'logo' (usually the header logo)."""
    for home in (f"https://www.{domain}/", f"https://{domain}/", f"https://www.{domain}/en"):
        try:
            final, html_text = fetch(home)
            break
        except Exception:
            continue
    else:
        return []
    scored = []
    for tag in re.findall(r"<img\b[^>]*>", html_text, re.I)[:300]:
        a = {k.lower(): v for k, v in ATTR.findall(tag)}
        src = a.get("src") or a.get("data-src") or ""
        blob = " ".join([src, a.get("alt", ""), a.get("class", ""), a.get("id", "")]).lower()
        if not src or "logo" not in blob and "شعار" not in blob or re.search(r"partner|award|footer|visa|master|app-?store|google-?play", blob):
            continue
        url = urllib.parse.urljoin(final, src)
        score = (3 if url.lower().split("?")[0].endswith(".svg") else 2 if url.lower().split("?")[0].endswith(".png") else 1)
        scored.append((score, url))
    return [u for _, u in sorted(scored, key=lambda s: -s[0])][:4]


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
        lambda: wikipedia_logo_urls(b),
        lambda: _header_logo_candidates(b["domain"]),
        lambda: [f"https://www.google.com/s2/favicons?domain={b['domain']}&sz=256",
                 f"https://icons.duckduckgo.com/ip3/{b['domain']}.ico"],
    ]
    fallback = None
    for source in sources:  # only look further when the earlier source gave nothing usable
        try:
            urls = source()
        except Exception:
            continue
        for url in urls:
            got = _download(url, WIKI_UA if "wiki" in url else None, FALLBACK_PIXELS)
            if not got:
                continue
            if usable(got[0]):            # a proper logo
                return got[0], got[1], url
            fallback = fallback or (got[0], got[1], url)
    return fallback                       # small icon, better than initials


def refresh(banks: list[dict], data_dir: Path, today: dt.date, force: bool = False,
            index_name: str = "logos.json", label: str = "banks") -> None:
    out_dir = data_dir / "logos"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_file = data_dir / index_name
    index = json.loads(index_file.read_text()) if index_file.exists() else {}

    def valid_file(entry: dict, min_px: int = FALLBACK_PIXELS) -> bool:
        f = data_dir / entry["file"]
        return f.exists() and usable(f.read_bytes(), min_px) is not None

    def due(b: dict) -> bool:
        entry = index.get(b["id"])
        if force or not entry or not entry.get("file") or not valid_file(entry):   # missing or broken
            return True
        age = (today - dt.date.fromisoformat(entry["checked"])).days
        return age >= REFRESH_DAYS or (age >= SMALL_RETRY_DAYS and not valid_file(entry, MIN_PIXELS))

    todo = [b for b in banks if due(b)]
    if not todo:
        return
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for b, got in zip(todo, pool.map(fetch_logo, todo)):
            entry = index.get(b["id"], {})
            entry["checked"] = today.isoformat()
            old_ok = entry.get("file") and valid_file(entry)
            if got and (not old_ok or usable(got[0]) or not valid_file(entry, MIN_PIXELS)):
                data, ext, src = got
                for old in out_dir.glob(f"{b['id']}.*"):
                    old.unlink()
                (out_dir / f"{b['id']}.{ext}").write_bytes(data)
                entry.update(file=f"logos/{b['id']}.{ext}", source=src)
            elif entry.get("file") and not valid_file(entry):
                (data_dir / entry["file"]).unlink(missing_ok=True)   # never serve a broken image
                entry.pop("file", None)
                entry.pop("source", None)
            index[b["id"]] = entry
    index_file.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    small = [b["short"] for b in banks if index.get(b["id"], {}).get("file") and not valid_file(index[b["id"]], MIN_PIXELS)]
    if small:
        print(f"  logos: only a small icon so far for {', '.join(small)}")
    have = sum(1 for e in index.values() if e.get("file"))
    missing = [b["short"] for b in banks if not index.get(b["id"], {}).get("file")]
    more = f" (+{len(missing) - 25} more)" if len(missing) > 25 else ""
    print(f"  logos: {have}/{len(banks)} {label} have a logo" + (f"; missing: {', '.join(missing[:25])}{more}" if missing else ""))


def publisher_id(name: str) -> str:
    return "pub-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def refresh_publishers(publishers: list[dict], data_dir: Path, today: dt.date) -> None:
    """Logos of the Sector Insights publishers and outlets (BCG, Accenture, Reuters …), same checks as bank logos."""
    orgs = [{**p, "id": publisher_id(p["name"]), "any_org": True} for p in publishers if p.get("domain")]
    refresh(orgs, data_dir, today, index_name="publisher_logos.json", label="publishers")


if __name__ == "__main__":  # python agent/logos.py  → refresh all logos now
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config" / "banks.json").read_text())
    refresh(cfg["banks"], root / "data", dt.date.today(), force="--force" in sys.argv)
