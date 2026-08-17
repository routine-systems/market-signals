#!/usr/bin/env python3
"""
Daily potentials — cross-tab of daily screeners.

Scrapes (daily backtests):
  cp-ich-trend-bounce-dly       primary  (the daily potentials universe + history)
  cp-ich-trend-bounce-dly-fil   filter   (stricter subset → gold highlight dot)
  cp-pb                         signal   (price breakout)
  cp-mq                         signal   (Minervini Quotient)

Builds a versioned local state payload: tickers on the selected day
ranked by cross-tab score = in dly (always) + also in PB + also in MQ (1–3), so a
ticker in all three ranks highest; plus a consistency dot-grid over the last N days.
Run 2–3× a day.

    python3 daily.py                 # scrape all four and store state
    python3 daily.py --no-scrape     # refresh derived state without scraping
"""

import argparse
import json
import sys
import time
from pathlib import Path

from selenium.webdriver.support.ui import WebDriverWait

from scrape import build_driver, download_backtest_csv, extract_scanlink, scanlink_only, polite_pause
import chartink_payloads as bd
import sectors_lib
from signal_paths import SIGNAL_STATE_ROOT

HERE = Path(__file__).resolve().parent
DATA_DIR = SIGNAL_STATE_ROOT
STORE = DATA_DIR / "history_daily.json"
TMP = DATA_DIR / ".dly_tmp"
BASE = "https://chartink.com/screener/"

PRIMARY = "cp-ich-trend-bounce-dly"
FILTER = "cp-ich-trend-bounce-dly-fil"
SIGNALS = [("pb", "PB", "Price breakout", "cp-pb"),
           ("mq", "MQ", "Minervini Quotient", "cp-mq")]


def fetch_csv(driver, slug, timeout):
    """Load a screener, return (csv_path_in_TMP, scanlink, timeframe)."""
    driver.get(BASE + slug)
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(6)
    scanlink, timeframe = extract_scanlink(driver.page_source)
    for p in TMP.glob("*.csv"):
        p.unlink()
    got = download_backtest_csv(driver, TMP, timeout, set())
    return got, scanlink, timeframe


def scrape_all(pause, headless, timeout):
    TMP.mkdir(parents=True, exist_ok=True)
    driver = build_driver(TMP, headless)
    try:
        print(f"[1] primary {PRIMARY}")
        csv, scanlink, timeframe = fetch_csv(driver, PRIMARY, timeout)
        h = bd.build_history(csv, BASE + PRIMARY, scanlink, timeframe or "daily")
        h["timeframe"] = timeframe or "daily"
        h["mode"] = "daily"
        csv.unlink()
        print(f"    {len(h['weeks'])} days ({h['weeks'][0]['week']}..{h['weeks'][-1]['week']}); scanlink {scanlink}")

        polite_pause(pause)
        print(f"[2] filter {FILTER}")
        csv, _, _ = fetch_csv(driver, FILTER, timeout)
        bd.attach_filter(h, csv, BASE + FILTER)
        csv.unlink()

        h["signals"] = []
        for i, (key, label, name, slug) in enumerate(SIGNALS, 3):
            polite_pause(pause)
            print(f"[{i}] signal {slug} ({label})")
            csv, _, _ = fetch_csv(driver, slug, timeout)
            mem = bd.parse_membership(csv)
            csv.unlink()
            h["signals"].append({"key": key, "label": label, "name": name,
                                 "url": BASE + slug, "weeks": mem})
            last = max(mem) if mem else "—"
            print(f"    {len(mem)} days; latest {last} → {len(mem.get(last, [])) if mem else 0} symbols")

        # keep only the latest 90 trading days (no archive needed)
        keep = set(w["week"] for w in h["weeks"][-90:])
        h["weeks"] = [w for w in h["weeks"] if w["week"] in keep]
        if h.get("filter"):
            h["filter"]["weeks"] = {d: v for d, v in h["filter"]["weeks"].items() if d in keep}
        for s in h.get("signals", []):
            s["weeks"] = {d: v for d, v in s["weeks"].items() if d in keep}
        print(f"    capped to {len(h['weeks'])} days")
        return h
    finally:
        driver.quit()
        try:
            for p in TMP.glob("*"):
                p.unlink()
            TMP.rmdir()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily potentials cross-tab dashboard")
    ap.add_argument("--pause", type=float, default=30.0, help="Max random gap (s) between screener pulls; 0 disables")
    ap.add_argument("--no-scrape", action="store_true", help="Refresh derived stored state")
    ap.add_argument("--refresh", action="store_true", help="Only re-extract the scanlink and rebuild (no download)")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.no_scrape or args.refresh:
        if not STORE.exists():
            print("❌ No stored history — run a full scrape first.", file=sys.stderr)
            return 1
        h = json.loads(STORE.read_text())
        if args.refresh:                       # cheap fix for an expired scanlink
            res = scanlink_only(BASE + PRIMARY, headless=not args.show)
            h["scanlink"] = res["scanlink"]
            h["timeframe"] = res.get("timeframe") or h.get("timeframe", "daily")
    else:
        h = scrape_all(args.pause, not args.show, args.timeout)

    # cross-reference: which of these daily tickers are also in the WEEKLY sheet?
    wk = DATA_DIR / "history.json"
    if wk.exists():
        w = json.loads(wk.read_text())
        h["cross"] = {"label": "W", "name": "weekly", "url": w.get("source_url"),
                      "weeks": {x["week"]: sorted({t["symbol"] for t in x["tickers"]})
                                for x in w.get("weeks", [])}}
        print(f"↔ cross: weekly sheet has {len(h['cross']['weeks'])} weeks")

    syms = {t["symbol"] for wk in h.get("weeks", []) for t in wk["tickers"]}
    rot = sectors_lib.rotation_for(syms)
    if rot:
        h["rotation"] = rot
        print(f"◉ rotation: {len(rot['of'])}/{len(syms)} tickers mapped to sectors")
    bd.atomic_json(h, STORE)
    print(json.dumps({"path": str(STORE), "periods": len(h.get("weeks", []))}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
