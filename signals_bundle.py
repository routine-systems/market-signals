#!/usr/bin/env python3
"""Produce the versioned artifact consumed by market-screener-web."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import sectors_lib
from signal_paths import CLASSIFICATION_PATH, SIGNAL_ARTIFACT_ROOT, SIGNAL_STATE_ROOT


DEFAULT_OUTPUT = SIGNAL_ARTIFACT_ROOT / "signals-bundle.v1.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest(values) -> str | None:
    items = [str(value) for value in values if value]
    return max(items) if items else None


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1)
    current = None
    output = []
    for value in values:
        current = value if current is None else alpha * value + (1 - alpha) * current
        output.append(current)
    return output


def compute_mcclellan(advances: dict, declines: dict) -> dict:
    days = sorted(set(advances) & set(declines))
    if len(days) < 2:
        return {"days": [], "osc": [], "net": []}
    if all(advances.get(day) == declines.get(day) for day in days):
        return {"days": [], "osc": [], "net": [], "degenerate": True}
    net = []
    for day in days:
        advancing = advances.get(day, 0)
        declining = declines.get(day, 0)
        total = advancing + declining
        net.append(1000.0 * (advancing - declining) / total if total else 0.0)
    ema_19 = _ema(net, 19)
    ema_39 = _ema(net, 39)
    return {
        "days": days,
        "osc": [round(ema_19[index] - ema_39[index], 1) for index in range(len(days))],
        "net": [round(value, 1) for value in net],
    }


def _recommendations(path: Path | None, generated_at: str) -> dict:
    if path and path.exists():
        payload = _read(path)
        if not isinstance(payload.get("rows"), list) or not isinstance(payload.get("summary"), dict):
            raise ValueError("recommendation payload requires rows and summary")
        payload.setdefault("generated_at", generated_at)
        return payload
    return {
        "generated_at": generated_at,
        "summary": {"presented": 0, "near_misses": 0, "tracked": 0, "opportunity_misses": 0},
        "rows": [],
    }


def _commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def build_bundle(
    state_root: Path = SIGNAL_STATE_ROOT,
    recommendation_path: Path | None = None,
) -> dict:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    weekly = _read(state_root / "history.json")
    daily = _read(state_root / "history_daily.json")
    breadth = _read(state_root / "market_counts.json")
    sectors = _read(state_root / "sector_weekly.json")
    weekly_dates = [item.get("week") for item in weekly.get("weeks", [])]
    daily_dates = [item.get("week") for item in daily.get("weeks", [])]
    breadth_dates = [day for counts in breadth.get("counts", {}).values() for day in counts]
    sector_dates = sectors.get("weeks", [])

    breadth = dict(breadth)
    breadth["cap_days"] = 274
    breadth["mcclellan"] = compute_mcclellan(
        breadth.get("counts", {}).get("cp-mcl-adv", {}),
        breadth.get("counts", {}).get("cp-mcl-dec", {}),
    )
    sectors = dict(sectors)
    sectors["cap_weeks"] = 40
    sectors["parents"] = sectors_lib.group_parents(sectors_lib.load_sector_map())

    if "cross" not in daily:
        daily["cross"] = {
            "label": "W",
            "name": "weekly",
            "url": weekly.get("source_url"),
            "weeks": {
                item["week"]: sorted(
                    {ticker["symbol"] for ticker in item.get("tickers", [])}
                )
                for item in weekly.get("weeks", [])
            },
        }
    recommendation_page = _recommendations(recommendation_path, generated_at)
    cutoff = _latest(weekly_dates + daily_dates + breadth_dates + list(sector_dates))
    return {
        "schema_version": "1.0",
        "producer_commit": _commit(),
        "generated_at_utc": generated_at,
        "data_cutoff": {"IN": cutoff},
        "markets": ["IN"],
        "timeframes": ["daily", "weekly"],
        "pages": {
            "weekly": {"default_window": 8, "payload": weekly},
            "daily": {"payload": daily},
            "market": {"payload": breadth},
            "sectors": {"payload": sectors},
            "recommendations": {"payload": recommendation_page},
        },
        "source_freshness": {
            "weekly": {"as_of": _latest(weekly_dates)},
            "daily": {"as_of": _latest(daily_dates)},
            "market": {"as_of": _latest(breadth_dates)},
            "sectors": {"as_of": _latest(sector_dates)},
        },
        "event_gate_status": {},
        "artifacts": [],
    }


def write_bundle(bundle: dict, output: Path = DEFAULT_OUTPUT) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=SIGNAL_STATE_ROOT)
    parser.add_argument("--recommendations", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    bundle = build_bundle(args.state_root, args.recommendations)
    checksum = write_bundle(bundle, args.output)
    print(json.dumps({"path": str(args.output), "sha256": checksum}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
