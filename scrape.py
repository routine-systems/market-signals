#!/usr/bin/env python3
"""
Chartink screener backtest scraper.

Drives headless Chrome to a screener page and downloads the **BACKTEST HISTORY**
CSV (the "Download → CSV" menu below the results table), which contains ~3 years
of weekly scan membership: columns Date, Symbol, Marketcapname, Sector — one row
per stock per weekly scan date. This is the source of truth for the appearance
ranking (a single download backfills the whole history).

Also extracts the rotating per-screener "scanlink" hash (for in-scan chart links).

Saves (overwritten each run — the backtest is a full replacement, not incremental):
    data/<slug>_backtest_latest.csv
    data/<slug>_latest.meta.json      { scanlink, timeframe, scraped_at, url }

Usage:
    python3 scrape.py                       # default screener below
    python3 scrape.py --url <chartink-url>  # any screener
    python3 scrape.py --show                # watch the browser (non-headless)
"""

import argparse
import json
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from signal_paths import SIGNAL_STATE_ROOT

DEFAULT_URL = "https://chartink.com/screener/cp-ich-trend-bounce-wkly"
HERE = Path(__file__).resolve().parent
DATA_DIR = SIGNAL_STATE_ROOT


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1] or "screener"


def polite_pause(max_s: float = 30.0, min_s: float = 2.0):
    """Sleep a RANDOM gap between screener pulls — looks less like a bot to Chartink.
    Default 2–30s (owner's request). Pass max_s=0 to disable (e.g. fast local runs)."""
    if max_s <= 0:
        return
    d = random.uniform(min(min_s, max_s), max_s)
    print(f"⏳ pause {d:.1f}s")
    time.sleep(d)


def build_driver(download_dir: Path, headless: bool) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1400")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    # Pin the browser to IST so Chartink renders backtest dates the same everywhere — on a
    # UTC runner (GitHub Actions) an IST-midnight timestamp would otherwise render as the
    # previous day "6:30 pm", shifting/breaking the daily date keys.
    try:
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Kolkata"})
    except Exception:  # noqa: BLE001
        pass
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def extract_scanlink(page_source: str):
    m = re.search(r"scanlink:([0-9a-f]{16,})", page_source)
    scanlink = m.group(1) if m else None
    mt = re.search(r"scan_link=scanlink:[0-9a-f]+&(?:amp;)?timeframe=([a-z0-9]+)", page_source)
    timeframe = mt.group(1) if mt else "weekly"
    return scanlink, timeframe


def _newest_csv(dir_: Path, exclude: set):
    fresh = [p for p in dir_.glob("*.csv") if p.name not in exclude]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def download_backtest_csv(driver, dest_dir: Path, timeout: int = 45, before=None) -> Path:
    """Open BACKTEST HISTORY → Download → CSV; return the downloaded file path.

    `before` = set of pre-existing *.csv names in dest_dir, used to detect the fresh
    file. The menu opens only on a REAL click (JS .click() doesn't fire it), and its
    CSV item sits BELOW the Download button — the STOCKS toolbar CSV button also has
    text 'CSV' and class 'w-full' but is far above, so we select strictly on y > dl_y.
    """
    if before is None:
        before = {p.name for p in dest_dir.glob("*.csv")}
    dl_btns = [b for b in driver.find_elements(
        By.XPATH, "//button[.//span[normalize-space(text())='Download']]") if b.is_displayed()]
    if not dl_btns:
        raise RuntimeError("Backtest Download button not found")
    dl = sorted(dl_btns, key=lambda b: b.location["y"])[-1]
    dl_y = dl.location["y"]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dl)
    time.sleep(1)

    def open_and_find():
        for _ in range(8):
            time.sleep(0.5)
            cands = [e for e in driver.find_elements(By.XPATH, "//button[normalize-space(.)='CSV']")
                     if e.is_displayed() and e.location["y"] > dl_y]
            if cands:
                return sorted(cands, key=lambda e: e.location["y"])[-1]
        return None

    try:
        dl.click()
    except Exception:
        driver.execute_script("arguments[0].click();", dl)
    target = open_and_find()
    if target is None:                       # retry the open via JS click
        driver.execute_script("arguments[0].click();", dl)
        target = open_and_find()
    if target is None:
        raise RuntimeError("Backtest CSV menu item not found")
    try:
        target.click()
    except Exception:
        driver.execute_script("arguments[0].click();", target)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        got = _newest_csv(dest_dir, before)
        if got and got.stat().st_size > 200:
            s1 = got.stat().st_size
            time.sleep(1)
            if got.stat().st_size == s1:
                return got
    raise RuntimeError("Backtest CSV download timed out")


def scanlink_only(url: str, headless: bool = True, timeout: int = 40) -> dict:
    """Load the screener page, re-extract the (rotating) scanlink, update the sidecar
    meta, and return it — WITHOUT downloading the backtest. Cheap fix for expired links."""
    slug = slug_from_url(url)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    driver = build_driver(DATA_DIR, headless)
    try:
        print(f"🔗 {url}")
        driver.get(url)
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(4)
        scanlink, timeframe = extract_scanlink(driver.page_source)
        mp = DATA_DIR / f"{slug}_latest.meta.json"
        m = json.loads(mp.read_text()) if mp.exists() else {}
        m.update({"scanlink": scanlink, "timeframe": timeframe,
                  "scanlink_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": url})
        mp.write_text(json.dumps(m, indent=2))
        print(f"🔑 refreshed scanlink: {scanlink or 'NOT FOUND'}")
        return {"scanlink": scanlink, "timeframe": timeframe}
    finally:
        driver.quit()


def scrape(url: str, headless: bool = True, timeout: int = 45) -> dict:
    slug = slug_from_url(url)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in DATA_DIR.glob("*.csv")}

    driver = build_driver(DATA_DIR, headless)
    try:
        print(f"🔗 {url}")
        driver.get(url)
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(6)  # let the results + backtest sections render
        print(f"📄 {driver.title}")

        scanlink, timeframe = extract_scanlink(driver.page_source)
        print(f"🔑 scanlink: {scanlink or 'NOT FOUND'} · timeframe: {timeframe}")

        got = download_backtest_csv(driver, DATA_DIR, timeout, before)
        print("👆 Backtest → CSV")

        # Each backtest download is a FULL 3-year replacement (not incremental), so we
        # overwrite one file — no per-run snapshots to accumulate. Keeps data/ tidy.
        latest = DATA_DIR / f"{slug}_backtest_latest.csv"
        shutil.move(str(got), str(latest))

        meta = {
            "scanlink": scanlink,
            "timeframe": timeframe,
            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "url": url,
        }
        meta_path = DATA_DIR / f"{slug}_latest.meta.json"
        meta_path.write_text(json.dumps(meta, indent=2))

        rows = max(0, len(latest.read_text(errors="replace").splitlines()) - 1)
        print(f"✅ {rows} backtest rows → {latest.name}  (+ {meta_path.name})")
        return {"csv": latest, "meta": meta_path, **meta}
    finally:
        driver.quit()


def main() -> int:
    ap = argparse.ArgumentParser(description="Chartink screener backtest CSV + scanlink scraper")
    ap.add_argument("--url", default=DEFAULT_URL, help="Chartink screener URL")
    ap.add_argument("--show", action="store_true", help="Show the browser window")
    ap.add_argument("--timeout", type=int, default=45)
    args = ap.parse_args()
    try:
        scrape(args.url, headless=not args.show, timeout=args.timeout)
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"❌ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
