#!/usr/bin/env python3
"""
Daily market-breadth tracker.

For each market-trend screener, downloads its BACKTEST CSV (daily Date,Symbol rows),
computes the count of stocks per day, and merges those counts into a rolling 9-month
state payload. The raw CSVs are parsed and deleted.

    python3 market.py                 # scrape all screeners and store state
    python3 market.py --no-scrape     # inspect existing stored counts
    python3 market.py --pause 6       # longer gap between downloads
"""

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from selenium.webdriver.support.ui import WebDriverWait

from scrape import build_driver, download_backtest_csv, polite_pause
from chartink_payloads import iso_date as _iso
from sectors_lib import LEVELS, SECTOR_STORE, load_sector_map
from signal_paths import SIGNAL_STATE_ROOT

HERE = Path(__file__).resolve().parent
DATA_DIR = SIGNAL_STATE_ROOT
STORE = DATA_DIR / "market_counts.json"
TMP = DATA_DIR / ".mkt_tmp"
CAP_DAYS = 275  # ~9 months

SCREENERS = [
    ("cp-tsi-mkt-total", "Total market"),
    ("cp-tsi-mkt-nifty", "Nifty"),
    ("cp-tsi-mkt-nifty500", "Nifty 500"),
    ("cp-tsi-mkt-futures", "Futures"),
    ("cp-tsi-mkt-indices", "Indices"),
    ("cp-tsi-mkt-mid-smallcap", "Mid / Small cap"),
    ("cp-tsi-mkt-banknifty", "BankNifty"),
    ("cp-stage-2", "Stage-2"),
    ("cp-cmo", "CMO"),
]

# McClellan Oscillator inputs — advances / declines daily counts (not shown as grid cards)
MCL_ADV = "cp-mcl-adv"
MCL_DEC = "cp-mcl-dec"

# --- Sector-rotation page (4th page): a WEEKLY screener + a finer sector map -----------------
# LEVELS / SECTOR_STORE / load_sector_map are shared in sectors_lib (imported above).
SECTOR_SLUG = "cp-cmo-wkly"
CAP_WEEKS = 156                                       # ~3 years of weekly rows


def counts_from_csv(path: Path):
    """Backtest CSV -> { 'YYYY-MM-DD': unique-symbol-count } per day."""
    reader = csv.DictReader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    fields = {(k or "").lower().strip(): k for k in (reader.fieldnames or [])}
    dcol, scol = fields.get("date"), fields.get("symbol")
    if not dcol or not scol:
        raise RuntimeError(f"CSV missing Date/Symbol: {reader.fieldnames}")
    per = defaultdict(set)
    for row in reader:
        sym = (row.get(scol) or "").strip()
        if not sym:
            continue
        iso = _iso(row.get(dcol) or "")
        if iso:
            per[iso].add(sym)
    return {d: len(s) for d, s in per.items()}


def load_store():
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {"screeners": [], "counts": {}, "updated_at": None}


def _ema(vals, n):
    a = 2.0 / (n + 1)
    e = None
    out = []
    for v in vals:
        e = v if e is None else a * v + (1 - a) * e
        out.append(e)
    return out


def compute_mcclellan(adv: dict, dec: dict):
    """Ratio-adjusted McClellan Oscillator from advances/declines daily counts:
    RANA = 1000·(adv−dec)/(adv+dec); Oscillator = EMA19(RANA) − EMA39(RANA)."""
    days = sorted(set(adv) & set(dec))
    if len(days) < 2:
        return {"days": [], "osc": [], "net": []}
    # Degenerate input: if the advances and declines screeners return identical
    # counts every day, adv−dec ≡ 0 → a flat, meaningless oscillator. Suppress it
    # (the chart self-hides) until the two screeners actually differ.
    if all(adv.get(d) == dec.get(d) for d in days):
        return {"days": [], "osc": [], "net": [], "degenerate": True}
    net = []
    for d in days:
        a, c = adv.get(d, 0), dec.get(d, 0)
        tot = a + c
        net.append(1000.0 * (a - c) / tot if tot else 0.0)
    e19, e39 = _ema(net, 19), _ema(net, 39)
    osc = [round(e19[i] - e39[i], 1) for i in range(len(days))]
    return {"days": days, "osc": osc, "net": [round(x, 1) for x in net]}


def membership_from_csv(path: Path):
    """Weekly backtest CSV -> { 'YYYY-MM-DD': set(symbols) } per week."""
    reader = csv.DictReader(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    fields = {(k or "").lower().strip(): k for k in (reader.fieldnames or [])}
    dcol, scol = fields.get("date"), fields.get("symbol")
    if not dcol or not scol:
        raise RuntimeError(f"CSV missing Date/Symbol: {reader.fieldnames}")
    per = defaultdict(set)
    for row in reader:
        sym = (row.get(scol) or "").strip().upper()
        iso = _iso(row.get(dcol) or "")
        if sym and iso:
            per[iso].add(sym)
    return per


def build_sector_store(per: dict, smap: dict):
    """Per-week stock counts per group, at each classification level, aligned to a week list."""
    weeks = sorted(per)[-CAP_WEEKS:]
    counts = {lvl: defaultdict(lambda: [0] * len(weeks)) for lvl, _ in LEVELS}
    totals, mapped, allsyms = [], set(), set()
    for wi, w in enumerate(weeks):
        syms = per[w]
        totals.append(len(syms))
        allsyms |= syms
        for lvl, _ in LEVELS:
            seen = defaultdict(int)
            for s in syms:
                g = smap.get(s)
                seen[(g[lvl] if g else "Unclassified")] += 1
                if g:
                    mapped.add(s)
            for g, c in seen.items():
                counts[lvl][g][wi] = c
    return {
        "screener": SECTOR_SLUG,
        "url": f"https://chartink.com/screener/{SECTOR_SLUG}",
        "levels": LEVELS,
        "weeks": weeks,
        "counts": {lvl: dict(counts[lvl]) for lvl, _ in LEVELS},
        "totals": totals,
        "coverage": {"mapped": len(mapped & allsyms), "total": len(allsyms)},
    }


def cap(counts: dict):
    cutoff = (date.today() - timedelta(days=CAP_DAYS)).isoformat()
    return {d: c for d, c in counts.items() if d >= cutoff}


def scrape_all(pause: float, headless: bool, timeout: int):
    TMP.mkdir(parents=True, exist_ok=True)
    store = load_store()
    store["screeners"] = [{"slug": s, "name": n} for s, n in SCREENERS]
    sector_store = None
    driver = build_driver(TMP, headless)
    try:
        for i, (slug, name) in enumerate(SCREENERS, 1):
            url = f"https://chartink.com/screener/{slug}"
            print(f"[{i}/{len(SCREENERS)}] {name} ({slug})")
            try:
                for p in TMP.glob("*.csv"):
                    p.unlink()
                driver.get(url)
                WebDriverWait(driver, timeout).until(
                    lambda d: d.execute_script("return document.readyState") == "complete")
                time.sleep(6)
                got = download_backtest_csv(driver, TMP, timeout, set())
                new = counts_from_csv(got)
                got.unlink()
                merged = store["counts"].get(slug, {})
                merged.update(new)
                store["counts"][slug] = cap(merged)
                days = sorted(store["counts"][slug])
                print(f"    {len(new)} days downloaded; stored {len(days)} "
                      f"({days[0]}..{days[-1]}); latest count {store['counts'][slug][days[-1]]}")
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠ skipped: {e}")
            polite_pause(pause)

        # McClellan Oscillator inputs — advances / declines daily counts.
        # Chartink's backtest table loads asynchronously: right after navigating to the
        # second slug it can still hold the first slug's backtest, so the download comes
        # back byte-identical (adv==dec → a flat, meaningless oscillator). Guard: if a
        # slug's CSV matches the previously-scraped slug exactly, hard-reload and retry
        # with a longer settle before giving up.
        prev_hash = None
        for slug in (MCL_ADV, MCL_DEC):
            print(f"[mcl] {slug}")
            try:
                got = None
                for attempt in range(3):
                    for p in TMP.glob("*.csv"):
                        p.unlink()
                    driver.get(f"https://chartink.com/screener/{slug}")
                    WebDriverWait(driver, timeout).until(
                        lambda d: d.execute_script("return document.readyState") == "complete")
                    time.sleep(8 + attempt * 5)
                    cand = download_backtest_csv(driver, TMP, timeout, set())
                    h = hashlib.sha1(cand.read_bytes()).hexdigest()
                    if h != prev_hash:
                        got, prev_hash = cand, h
                        break
                    print(f"    ↻ identical to previous slug (stale backtest); retry {attempt + 1}/3")
                    cand.unlink()
                if got is None:
                    raise RuntimeError("only a stale (duplicate) backtest after 3 tries")
                new = counts_from_csv(got)
                got.unlink()
                merged = store["counts"].get(slug, {})
                merged.update(new)
                store["counts"][slug] = cap(merged)
                days = sorted(store["counts"][slug])
                print(f"    {len(new)} days; stored {len(days)}; latest {store['counts'][slug][days[-1]]}")
            except Exception as e:  # noqa: BLE001
                print(f"    ⚠ {slug} skipped: {e}")
            polite_pause(pause)

        # 4th page: the weekly sector-rotation screener, mapped to a finer sector classification
        print(f"[sectors] {SECTOR_SLUG} (weekly) + Downloads/data.csv classification")
        try:
            for p in TMP.glob("*.csv"):
                p.unlink()
            driver.get(f"https://chartink.com/screener/{SECTOR_SLUG}")
            WebDriverWait(driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete")
            time.sleep(6)
            got = download_backtest_csv(driver, TMP, timeout, set())
            per = membership_from_csv(got)
            got.unlink()
            smap = load_sector_map()
            sector_store = build_sector_store(per, smap)
            sector_store["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            SECTOR_STORE.write_text(json.dumps(sector_store))
            cov = sector_store["coverage"]
            print(f"    {len(per)} weeks; latest {sector_store['totals'][-1]} stocks; "
                  f"{len(sector_store['counts']['sector'])} sectors / "
                  f"{len(sector_store['counts']['basic'])} basic-industries; "
                  f"mapped {cov['mapped']}/{cov['total']}"
                  + ("" if smap else "  (⚠ no sector map found)"))
        except Exception as e:  # noqa: BLE001
            print(f"    ⚠ sector page skipped: {e}")
    finally:
        driver.quit()
        try:
            for p in TMP.glob("*"):
                p.unlink()
            TMP.rmdir()
        except OSError:
            pass
    store["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    STORE.write_text(json.dumps(store))
    return store, sector_store


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily market-breadth counts dashboard")
    ap.add_argument("--pause", type=float, default=30.0, help="Max random gap (s) between screener pulls; 0 disables")
    ap.add_argument("--no-scrape", action="store_true", help="Use stored counts only")
    ap.add_argument("--show", action="store_true", help="Show the browser window")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.no_scrape:
        store = load_store()
        sector_store = json.loads(SECTOR_STORE.read_text()) if SECTOR_STORE.exists() else None
    else:
        store, sector_store = scrape_all(args.pause, not args.show, args.timeout)
    if not store.get("screeners"):
        store["screeners"] = [{"slug": s, "name": n} for s, n in SCREENERS]
    print(
        json.dumps(
            {
                "market_state": str(STORE),
                "sector_state": str(SECTOR_STORE) if sector_store else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
