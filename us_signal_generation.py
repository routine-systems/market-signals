#!/usr/bin/env python3
"""Generate local US daily and weekly Chartink-formula signal artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from signal_paths import MARKET_DATA_ROOT, SIGNAL_ARTIFACT_ROOT
from us_signal_formulas import add_us_daily_signals
from weekly_features import add_weekly_features


HERE = Path(__file__).resolve().parent
BAR_GLOB = str(MARKET_DATA_ROOT / "us" / "ohlcv" / "bucket=*" / "part.parquet")
SYMBOL_PATH = MARKET_DATA_ROOT / "us" / "symbol_master.parquet"
EARNINGS_PATH = MARKET_DATA_ROOT / "us" / "alpha_vantage" / "metadata.sqlite3"
IDENTIFIER_PATH = MARKET_DATA_ROOT / "us" / "sec" / "security_identifiers.parquet"
SHARES_PATH = MARKET_DATA_ROOT / "us" / "sec" / "shares_as_filed.parquet"
SIGNAL_ROOT = SIGNAL_ARTIFACT_ROOT / "us"
LATEST_PATH = SIGNAL_ROOT / "latest.json"
MIN_PRICE = 5.0
MIN_MEDIAN_TURNOVER_20 = 5_000_000.0
EARNINGS_BLACKOUT_DAYS = 7


def _atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    os.replace(temporary, path)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def load_period_bars(
    connection: duckdb.DuckDBPyConnection,
    timeframe: str,
    asof: date,
) -> pd.DataFrame:
    if timeframe not in {"daily", "weekly"}:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    warmup_days = 550 if timeframe == "daily" else 2300
    start = pd.Timestamp(asof) - pd.Timedelta(days=warmup_days)
    adjusted = """
        WITH adjusted AS (
            SELECT
                symbol,
                date,
                COALESCE(adj_close, close) / NULLIF(close, 0) AS factor,
                open,
                high,
                low,
                COALESCE(adj_close, close) AS adjusted_close,
                volume
            FROM read_parquet(?, union_by_name=true)
            WHERE date BETWEEN ? AND ?
        )
    """
    if timeframe == "daily":
        query = adjusted + """
            SELECT
                symbol,
                date AS week_start,
                date AS signal_date,
                open * factor AS open,
                high * factor AS high,
                low * factor AS low,
                adjusted_close AS close,
                volume
            FROM adjusted
            ORDER BY symbol, signal_date
        """
    else:
        query = adjusted + """
            SELECT
                symbol,
                CAST(date_trunc('week', date) AS DATE) AS week_start,
                MAX(date) AS signal_date,
                arg_min(open * factor, date) AS open,
                MAX(high * factor) AS high,
                MIN(low * factor) AS low,
                arg_max(adjusted_close, date) AS close,
                SUM(volume) AS volume
            FROM adjusted
            GROUP BY symbol, date_trunc('week', date)
            ORDER BY symbol, week_start
        """
    frame = connection.execute(query, [BAR_GLOB, start, pd.Timestamp(asof)]).fetchdf()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"])
    frame["week_start"] = pd.to_datetime(frame["week_start"])
    return frame


def latest_liquidity(
    connection: duckdb.DuckDBPyConnection,
    asof: date,
) -> pd.DataFrame:
    start = pd.Timestamp(asof) - pd.Timedelta(days=60)
    query = """
        WITH recent AS (
            SELECT
                symbol,
                date,
                COALESCE(adj_close, close) AS adjusted_close,
                COALESCE(adj_close, close) * volume AS dollar_turnover,
                row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS recency
            FROM read_parquet(?, union_by_name=true)
            WHERE date BETWEEN ? AND ?
        )
        SELECT
            symbol,
            arg_max(adjusted_close, date) AS latest_close,
            median(dollar_turnover) FILTER (WHERE recency <= 20) AS median_turnover_20,
            COUNT(*) FILTER (WHERE recency <= 20) AS turnover_sessions
        FROM recent
        WHERE recency <= 20
        GROUP BY symbol
    """
    return connection.execute(query, [BAR_GLOB, start, pd.Timestamp(asof)]).fetchdf()


def latest_market_caps(asof: date, liquidity: pd.DataFrame) -> pd.DataFrame:
    """Calculate current point-in-time market caps from SEC-available share facts."""
    identifiers = pd.read_parquet(IDENTIFIER_PATH)
    identifiers = identifiers[
        identifiers["requested_state"].eq("active")
        & identifiers["current_master_eligible"]
        & identifiers["cik"].notna()
    ].sort_values(["symbol", "cik_match_method"]).drop_duplicates("symbol")
    shares = pd.read_parquet(SHARES_PATH)
    shares = shares[
        shares["available_date"].notna()
        & pd.to_datetime(shares["available_date"]).le(pd.Timestamp(asof))
        & shares["shares_outstanding"].gt(0)
    ].copy()
    shares = shares.sort_values(
        ["cik", "available_date", "fact_end", "accession"]
    ).drop_duplicates("cik", keep="last")
    frame = identifiers[["symbol", "cik", "cik_match_method"]].merge(
        shares[["cik", "shares_outstanding", "available_date", "fact_end"]],
        on="cik",
        how="left",
        validate="many_to_one",
    )
    frame = frame.merge(
        liquidity[["symbol", "latest_close"]],
        on="symbol",
        how="left",
        validate="one_to_one",
    )
    frame["market_cap"] = frame["shares_outstanding"] * frame["latest_close"]
    return frame[
        [
            "symbol", "cik", "cik_match_method", "shares_outstanding",
            "available_date", "fact_end", "market_cap",
        ]
    ]


def upcoming_earnings(asof: date, database: Path = EARNINGS_PATH) -> pd.DataFrame:
    columns = ["symbol", "next_earnings_date", "earnings_time"]
    if not database.exists():
        return pd.DataFrame(columns=columns)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT symbol, report_date, source_row_json
            FROM earnings_calendar
            WHERE report_date >= ?
            ORDER BY symbol, report_date
            """,
            [asof.isoformat()],
        ).fetchall()
    first: dict[str, tuple[str, str | None]] = {}
    for symbol, report_date, source_json in rows:
        if symbol in first:
            continue
        try:
            time_of_day = json.loads(source_json).get("timeOfTheDay")
        except (TypeError, json.JSONDecodeError):
            time_of_day = None
        first[str(symbol)] = (str(report_date), time_of_day)
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "next_earnings_date": report_date,
                "earnings_time": time_of_day,
            }
            for symbol, (report_date, time_of_day) in first.items()
        ],
        columns=columns,
    )


def build_candidates(
    timeframe: str,
    features: pd.DataFrame,
    liquidity: pd.DataFrame,
    master: pd.DataFrame,
    earnings: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    if features.empty:
        raise RuntimeError(f"no {timeframe} features are available")
    signal_date = pd.Timestamp(features["signal_date"].max())
    latest = features[features["signal_date"].eq(signal_date)].copy()
    if timeframe == "daily":
        for column in ("pb", "mq", "kubra_bull"):
            if column not in latest:
                latest[column] = False
        candidates = latest[
            latest[["wkly", "pb", "mq", "kubra_bull"]].any(axis=1)
        ].copy()
    else:
        candidates = latest[latest["wkly"]].copy()
    candidates = candidates.merge(liquidity, on="symbol", how="left")
    master_columns = ["symbol", "name", "exchange", "asset_type", "eligible_initial"]
    candidates = candidates.merge(
        master[master_columns].drop_duplicates("symbol"), on="symbol", how="left"
    )
    candidates = candidates.merge(earnings, on="symbol", how="left")
    candidates["next_earnings_date"] = pd.to_datetime(
        candidates["next_earnings_date"], errors="coerce"
    )
    candidates["days_to_earnings"] = (
        candidates["next_earnings_date"] - signal_date.normalize()
    ).dt.days
    candidates["earnings_known"] = candidates["next_earnings_date"].notna()
    candidates["earnings_status"] = np.select(
        [
            candidates["asset_type"].eq("etf"),
            candidates["earnings_known"],
        ],
        ["not_applicable", "known"],
        default="unknown",
    )
    candidates["earnings_clear"] = (
        candidates["asset_type"].eq("etf")
        | (
            candidates["earnings_known"]
            & candidates["days_to_earnings"].gt(EARNINGS_BLACKOUT_DAYS)
        )
    )
    candidates["liquidity_clear"] = (
        candidates["eligible_initial"].fillna(False)
        & candidates["latest_close"].ge(MIN_PRICE)
        & candidates["median_turnover_20"].ge(MIN_MEDIAN_TURNOVER_20)
        & candidates["turnover_sessions"].ge(20)
    )
    candidates["eligible_for_shortlist"] = (
        candidates["wkly_fil"]
        & candidates["liquidity_clear"]
        & candidates["earnings_clear"]
    )
    benchmark = latest[latest["symbol"].eq("SPY")]
    benchmark_13 = benchmark["return_13w"].iloc[0] if len(benchmark) else np.nan
    benchmark_26 = benchmark["return_26w"].iloc[0] if len(benchmark) else np.nan
    candidates["relative_13p_vs_spy"] = candidates["return_13w"] - benchmark_13
    candidates["relative_26p_vs_spy"] = candidates["return_26w"] - benchmark_26
    if len(candidates):
        candidates["rank_score"] = (
            candidates["relative_13p_vs_spy"].rank(pct=True)
            + candidates["relative_26p_vs_spy"].rank(pct=True)
            + np.log1p(candidates["median_turnover_20"]).rank(pct=True)
            + candidates["macd_hist"].rank(pct=True)
        )
    else:
        candidates["rank_score"] = pd.Series(dtype=float)
    prefix = "dly" if timeframe == "daily" else "wkly"
    candidates = candidates.rename(
        columns={
            "wkly": f"{prefix}",
            "wkly_fil": f"{prefix}_fil",
            "wkly_fil_3of5": f"{prefix}_fil_3of5",
            "wkly_fil_count_5": f"{prefix}_fil_count_5",
            "wkly_fil_3of5_trigger": f"{prefix}_fil_3of5_trigger",
            "return_13w": "return_13_periods",
            "return_26w": "return_26_periods",
        }
    )
    if timeframe == "daily":
        candidates["eligible_for_shortlist"] = (
            candidates["dly_fil"]
            & candidates["liquidity_clear"]
            & candidates["earnings_clear"]
        )
    else:
        candidates["pb"] = False
        candidates["mq"] = False
        candidates["kubra_bull"] = False
        candidates["mq_vwap_source"] = pd.NA
    if "market_cap" not in candidates:
        candidates["market_cap"] = np.nan
    for column in ("cik", "available_date", "fact_end"):
        if column not in candidates:
            candidates[column] = pd.NA
    candidates["timeframe"] = timeframe
    candidates["formula"] = "ema_or_cloud_bounce_with_trend"
    columns = [
        "signal_date",
        "timeframe",
        "symbol",
        "name",
        "exchange",
        "asset_type",
        "cik",
        "available_date",
        "fact_end",
        prefix,
        f"{prefix}_fil",
        f"{prefix}_fil_3of5",
        f"{prefix}_fil_count_5",
        f"{prefix}_fil_3of5_trigger",
        "pb",
        "mq",
        "kubra_bull",
        "mq_vwap_source",
        "latest_close",
        "market_cap",
        "median_turnover_20",
        "turnover_sessions",
        "return_13_periods",
        "return_26_periods",
        "relative_13p_vs_spy",
        "relative_26p_vs_spy",
        "macd_hist",
        "rank_score",
        "next_earnings_date",
        "earnings_time",
        "days_to_earnings",
        "earnings_known",
        "earnings_status",
        "earnings_clear",
        "liquidity_clear",
        "eligible_for_shortlist",
        "formula",
    ]
    candidates = candidates.sort_values(
        ["eligible_for_shortlist", f"{prefix}_fil_3of5", "rank_score"],
        ascending=[False, False, False],
    )[columns].reset_index(drop=True)
    regime = {
        "signal_date": signal_date.date().isoformat(),
        "spy_present": bool(len(benchmark)),
        "spy_trend_positive": bool(
            len(benchmark)
            and benchmark["close"].iloc[0]
            > benchmark["ema_10"].iloc[0]
            > benchmark["ema_20"].iloc[0]
            > benchmark["ema_34"].iloc[0]
        ),
    }
    return candidates, regime


def artifact_path(timeframe: str, signal_date: date) -> Path:
    return SIGNAL_ROOT / timeframe / f"asof={signal_date.isoformat()}" / "signals.parquet"


def generate(timeframe: str, asof: date) -> dict:
    connection = duckdb.connect()
    bars = load_period_bars(connection, timeframe, asof)
    liquidity = latest_liquidity(connection, asof)
    market_caps = latest_market_caps(asof, liquidity)
    bars = bars.merge(
        market_caps[["symbol", "cik", "market_cap", "available_date", "fact_end"]],
        on="symbol",
        how="left",
        validate="many_to_one",
    )
    features = add_weekly_features(bars)
    if timeframe == "daily":
        features = add_us_daily_signals(features)
    master = pd.read_parquet(SYMBOL_PATH)
    earnings = upcoming_earnings(asof)
    candidates, regime = build_candidates(timeframe, features, liquidity, master, earnings)
    signal_date = pd.Timestamp(features["signal_date"].max()).date()
    path = artifact_path(timeframe, signal_date)
    _atomic_parquet(candidates, path)
    metadata_path = path.with_name("metadata.json")
    payload = {
        "timeframe": timeframe,
        "requested_asof": asof.isoformat(),
        "signal_date": signal_date.isoformat(),
        "formula_candidates": len(candidates),
        "filtered_candidates": int(candidates[f"{'dly' if timeframe == 'daily' else 'wkly'}_fil"].sum()),
        "shortlist_eligible": int(candidates["eligible_for_shortlist"].sum()),
        "earnings_blackout_days": EARNINGS_BLACKOUT_DAYS,
        "regime": regime,
        "path": str(path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(payload, metadata_path)
    latest = json.loads(LATEST_PATH.read_text()) if LATEST_PATH.exists() else {}
    latest[timeframe] = {
        "signal_date": signal_date.isoformat(),
        "signals": str(path),
        "metadata": str(metadata_path),
    }
    latest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(latest, LATEST_PATH)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", choices=("daily", "weekly", "all"), required=True)
    parser.add_argument("--asof", type=date.fromisoformat, required=True)
    arguments = parser.parse_args()
    timeframes = ("daily", "weekly") if arguments.timeframe == "all" else (arguments.timeframe,)
    print(
        json.dumps(
            [generate(timeframe, arguments.asof) for timeframe in timeframes],
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
