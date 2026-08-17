#!/usr/bin/env python3
"""Acquire the weekly Chartink screen and store its signal payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chartink_payloads as payloads
import scrape
from signal_paths import SIGNAL_STATE_ROOT


DEFAULT_URL = "https://chartink.com/screener/cp-ich-trend-bounce-wkly"
HISTORY = SIGNAL_STATE_ROOT / "history.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--filter-url")
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.refresh:
        if not HISTORY.exists():
            raise RuntimeError(f"weekly state does not exist: {HISTORY}")
        history = json.loads(HISTORY.read_text())
        metadata = scrape.scanlink_only(args.url, headless=not args.show)
        history["scanlink"] = metadata["scanlink"]
        history["timeframe"] = metadata.get("timeframe") or history.get("timeframe", "weekly")
        payloads.atomic_json(history, HISTORY)
        return 0

    result = scrape.scrape(args.url, headless=not args.show)
    history = payloads.build_history(
        result["csv"], args.url, result.get("scanlink"), result.get("timeframe")
    )
    if not args.no_filter:
        filter_url = args.filter_url or f"{args.url}-fil"
        scrape.polite_pause()
        filtered = scrape.scrape(filter_url, headless=not args.show)
        payloads.attach_filter(history, filtered["csv"], filter_url)
    payloads.atomic_json(history, HISTORY)
    print(json.dumps({"path": str(HISTORY), "periods": len(history["weeks"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
