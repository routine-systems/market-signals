#!/usr/bin/env python3
"""
Shared sector helpers — the classification map and the weekly "rotation status".

Kept dependency-free (stdlib only) so market.py, build_dashboard.py and daily.py can
all import it without import cycles. The weekly sector screener (cp-cmo-wkly) is scraped
by market.py into data/sector_weekly.json; this module turns that store + the Symbol→
sector map into a per-ticker rotation status (is the ticker's sector gaining interest?).
"""

import csv
import json
from pathlib import Path

from signal_paths import CLASSIFICATION_PATH, SIGNAL_STATE_ROOT

HERE = Path(__file__).resolve().parent
DATA_DIR = SIGNAL_STATE_ROOT
DATA_CSV = CLASSIFICATION_PATH
SECTOR_MAP = CLASSIFICATION_PATH
SECTOR_STORE = DATA_DIR / "sector_weekly.json"

LEVELS = [("sector", "Sector"), ("industry", "Industry"), ("basic", "Basic Industry")]
ROTATION_WINDOW = 13                                    # weeks used to judge rotation


def load_sector_map(source=None):
    """Read symbol classifications from the configured market-data artifact."""
    candidates = (Path(source),) if source is not None else (DATA_CSV, SECTOR_MAP)
    src = next((candidate for candidate in candidates if candidate.exists()), None)
    if src is None:
        return {}
    raw = src.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(raw.splitlines())
    fields = {(k or "").lower().strip(): k for k in (reader.fieldnames or [])}

    def col(*names):
        for n in names:
            if n in fields:
                return fields[n]
        return None

    csym = col("symbol")
    csec = col("sector")
    cind = col("industry")
    cbas = col("basic industry", "basicindustry", "basic")
    smap = {}
    for row in reader:
        sym = (row.get(csym) or "").strip().upper()
        if not sym:
            continue
        smap[sym] = {
            "sector": (row.get(csec) or "").strip() or "Unclassified",
            "industry": (row.get(cind) or "").strip() or "Unclassified",
            "basic": (row.get(cbas) or "").strip() or "Unclassified",
        }
    return smap


def group_parents(smap=None):
    """Sub-group -> parent sector, for the quadrant's within-sector filter.
    { 'industry': {industry: sector}, 'basic': {basic: sector} }."""
    smap = smap if smap is not None else load_sector_map()
    parents = {"industry": {}, "basic": {}}
    for g in smap.values():
        parents["industry"].setdefault(g["industry"], g["sector"])
        parents["basic"].setdefault(g["basic"], g["sector"])
    return parents


def _slope(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2
    my = sum(vals) / n
    num = den = 0.0
    for i, v in enumerate(vals):
        num += (i - mx) * (v - my)
        den += (i - mx) * (i - mx)
    return num / den if den else 0.0


def _sign(vals):
    """+1 rising / -1 cooling / 0 flat — matches the sectors page's slope test."""
    if len(vals) < 3:
        d = (vals[-1] - vals[0]) if len(vals) >= 2 else 0
        return 1 if d > 0 else (-1 if d < 0 else 0)
    sl = _slope(vals)
    mean = sum(vals) / len(vals)
    eps = max(0.04, mean * 0.015)
    return 1 if sl > eps else (-1 if sl < -eps else 0)


def compute_status(sector_store, window=ROTATION_WINDOW):
    """{level: {group: +1/-1/0}} over the last `window` weeks of the store."""
    out = {}
    for lvl, _ in LEVELS:
        gm = {}
        for g, arr in (sector_store.get("counts", {}).get(lvl, {}) or {}).items():
            w = arr[-window:] if window else arr
            gm[g] = _sign(w)
        out[lvl] = gm
    return out


def rotation_for(symbols, window=ROTATION_WINDOW):
    """Per-ticker rotation payload for the weekly/daily pages, or None if no sector data yet.
    { window, updated_at, levels, levelNames, status:{level:{group:sign}}, of:{sym:[s,i,b]} }."""
    if not SECTOR_STORE.exists():
        return None
    try:
        store = json.loads(SECTOR_STORE.read_text())
    except (OSError, ValueError):
        return None
    smap = load_sector_map()
    if not smap or not store.get("weeks"):
        return None
    status = compute_status(store, window)
    of = {}
    for s in set(symbols):
        g = smap.get((s or "").strip().upper())
        if g:
            of[s] = [g["sector"], g["industry"], g["basic"]]
    return {
        "window": window,
        "updated_at": store.get("updated_at"),
        "levels": [lvl for lvl, _ in LEVELS],
        "levelNames": {lvl: name for lvl, name in LEVELS},
        "status": status,
        "of": of,
    }
