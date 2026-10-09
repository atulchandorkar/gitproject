#!/usr/bin/env python3
"""Ask Jev (typesafe/jev-1.13) typed questions through OpenRouter's Decisions route.

Usage:
  jev.py REQUEST.json            # {"state": ..., "questions": {...}}
  jev.py REQUEST.json --dry-run  # print the payload that would leave the machine, send nothing

The OpenRouter key is read from the OPENROUTER_API_KEY environment variable, or
failing that from ~/.config/openrouter/key (a file only you can read). If neither
exists, the request goes out without one, for a cloud environment whose network
secret for openrouter.ai adds it in transit. The key is never printed or logged.

Output (stdout, JSON): answers, model, provider, usage (incl. cost in USD), elapsed_ms.
On failure: exits 1 and prints the HTTP status and the exact response body, or the
exact network exception.
"""
import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
KEY_FILE = Path.home() / ".config" / "openrouter" / "key"


def load_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    if KEY_FILE.exists():
        mode = KEY_FILE.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            sys.exit(f"error: {KEY_FILE} is readable by other users; run: chmod 600 {KEY_FILE}")
        return KEY_FILE.read_text().strip()
    # No local key: in a Claude Code cloud environment with an openrouter.ai network secret,
    # the agent proxy attaches the Authorization header after the request leaves the machine.
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("request", help="JSON file with 'state' and 'questions' (optional 'model')")
    parser.add_argument("--model", help=f"override model (default {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="print the payload and exit without sending")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text())
    payload = {
        "model": args.model or request.get("model") or DEFAULT_MODEL,
        "state": request["state"],
        "questions": request["questions"],
    }
    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    headers = {"Content-Type": "application/json"}
    key = load_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        print(f"HTTP {e.code} {e.reason} after {elapsed_ms} ms", file=sys.stderr)
        print(e.read().decode(errors="replace"), file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        print(f"network error after {elapsed_ms} ms: {e!r}", file=sys.stderr)
        sys.exit(1)
    elapsed_ms = round((time.perf_counter() - start) * 1000)

    data = json.loads(body)
    print(json.dumps({
        "answers": data.get("answers"),
        "model": data.get("model"),
        "provider": data.get("provider"),
        "usage": data.get("usage"),
        "elapsed_ms": elapsed_ms,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
