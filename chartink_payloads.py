"""Pure Chartink backtest parsing and page-payload construction."""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def iso_date(value: str) -> str:
    parts = (value or "").strip().split()
    source = parts[0] if parts else ""
    for format_string in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(source, format_string).date().isoformat()
        except ValueError:
            continue
    return source


def parse_backtest(csv_path: Path) -> list[dict]:
    reader = csv.DictReader(
        csv_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    )
    fields = {(field or "").lower().strip(): field for field in (reader.fieldnames or [])}

    def column(*names: str) -> str | None:
        return next((fields[name] for name in names if name in fields), None)

    date_column = column("date")
    symbol_column = column("symbol")
    market_cap_column = column("marketcapname", "marketcap", "market cap")
    sector_column = column("sector")
    if not date_column or not symbol_column:
        raise RuntimeError(f"Backtest CSV missing Date/Symbol columns: {reader.fieldnames}")
    by_period: dict[str, list[dict]] = defaultdict(list)
    for row in reader:
        symbol = (row.get(symbol_column) or "").strip()
        period = iso_date(row.get(date_column) or "")
        if symbol and period:
            by_period[period].append(
                {
                    "symbol": symbol,
                    "sector": (row.get(sector_column) or "").strip() if sector_column else "",
                    "marketcap": (row.get(market_cap_column) or "").strip()
                    if market_cap_column
                    else "",
                }
            )
    return [{"week": period, "tickers": by_period[period]} for period in sorted(by_period)]


def build_history(
    csv_path: Path,
    url: str,
    scanlink: str | None = None,
    timeframe: str | None = None,
) -> dict:
    periods = parse_backtest(csv_path)
    if not periods:
        raise RuntimeError(f"No rows parsed from {csv_path}")
    return {
        "screener": url.rstrip("/").split("/")[-1],
        "source_url": url,
        "scanlink": scanlink,
        "timeframe": timeframe or "weekly",
        "weeks": periods,
    }


def parse_membership(csv_path: Path) -> dict[str, list[str]]:
    return {
        item["week"]: sorted({ticker["symbol"] for ticker in item["tickers"]})
        for item in parse_backtest(csv_path)
    }


def attach_filter(history: dict, filter_csv: Path, url: str) -> dict:
    history["filter"] = {
        "screener": url.rstrip("/").split("/")[-1],
        "url": url,
        "weeks": parse_membership(filter_csv),
    }
    return history


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)
