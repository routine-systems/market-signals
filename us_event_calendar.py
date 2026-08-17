#!/usr/bin/env python3
"""Fail-closed Nasdaq event calendar for US recommendation gates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
import requests

from signal_paths import SIGNAL_STATE_ROOT


STORE_ROOT = SIGNAL_STATE_ROOT / "us_events"
EVENT_PATH = STORE_ROOT / "candidate_events.parquet"
REFRESH_PATH = STORE_ROOT / "latest_refresh.json"
NASDAQ_API = "https://api.nasdaq.com/api/calendar"
NASDAQ_PAGE = "https://www.nasdaq.com/market-activity"
EVENT_TYPES = ("earnings", "dividend", "split")


@dataclass(frozen=True)
class RefreshResult:
    events: pd.DataFrame
    status_by_symbol: dict[str, str]
    fetched_at_utc: str
    failures: tuple[str, ...]


def empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol", "event_type", "event_date", "subject", "description",
            "source", "source_url", "source_row_json", "fetched_at_utc",
        ]
    )


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": f"{NASDAQ_PAGE}/earnings",
        }
    )
    return session


def _rows(payload: dict, event_type: str) -> list[dict]:
    data = payload.get("data") or {}
    if event_type == "dividend":
        return ((data.get("calendar") or {}).get("rows") or [])
    return data.get("rows") or []


def parse_payload(
    payload: dict,
    event_type: str,
    requested_date: date,
    fetched_at_utc: str,
) -> pd.DataFrame:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event type: {event_type}")
    status = payload.get("status") or {}
    if status.get("rCode") != 200:
        raise RuntimeError(f"Nasdaq {event_type} status is not 200")
    records = []
    for row in _rows(payload, event_type):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if event_type == "earnings":
            event_date = pd.Timestamp(requested_date)
            subject = f"Earnings: {row.get('name') or symbol}"
            description = f"{row.get('time') or 'time-not-supplied'}; {row.get('fiscalQuarterEnding') or ''}"
            source = "Nasdaq earnings calendar (Zacks)"
            source_url = f"{NASDAQ_PAGE}/earnings?date={requested_date.isoformat()}"
        elif event_type == "dividend":
            event_date = pd.to_datetime(row.get("dividend_Ex_Date"), errors="coerce")
            subject = f"Ex-dividend: {row.get('companyName') or symbol}"
            description = f"rate={row.get('dividend_Rate')}; payment={row.get('payment_Date')}"
            source = "Nasdaq dividend calendar (Quotemedia)"
            source_url = f"{NASDAQ_PAGE}/dividends?date={requested_date.isoformat()}"
        else:
            event_date = pd.to_datetime(row.get("executionDate"), errors="coerce")
            subject = f"Stock split: {row.get('name') or symbol}"
            description = f"ratio={row.get('ratio')}"
            source = "Nasdaq stock-splits calendar"
            source_url = f"{NASDAQ_PAGE}/stock-splits"
        if pd.isna(event_date):
            continue
        records.append(
            {
                "symbol": symbol,
                "event_type": event_type,
                "event_date": pd.Timestamp(event_date).normalize(),
                "subject": subject,
                "description": description,
                "source": source,
                "source_url": source_url,
                "source_row_json": json.dumps(row, sort_keys=True, default=str),
                "fetched_at_utc": fetched_at_utc,
            }
        )
    return pd.DataFrame(records, columns=empty_events().columns)


def fetch_day(
    session: requests.Session,
    event_type: str,
    session_date: date,
    fetched_at_utc: str,
) -> pd.DataFrame:
    response = session.get(
        f"{NASDAQ_API}/{event_type}s" if event_type != "earnings" else f"{NASDAQ_API}/earnings",
        params={"date": session_date.isoformat()},
        timeout=30,
    )
    response.raise_for_status()
    return parse_payload(response.json(), event_type, session_date, fetched_at_utc)


def market_sessions(start: date, end: date) -> list[date]:
    calendar = xcals.get_calendar("XNYS")
    return [
        pd.Timestamp(value).date()
        for value in calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    ]


def session_distance(anchor: date | pd.Timestamp, event: date | pd.Timestamp) -> int:
    """Return XNYS-session distance from anchor to an event session."""
    anchor_date = pd.Timestamp(anchor).normalize()
    event_date = pd.Timestamp(event).normalize()
    if anchor_date == event_date:
        return 0
    earlier, later = sorted([anchor_date, event_date])
    count = len(market_sessions(earlier.date(), later.date())) - 1
    return count if event_date > anchor_date else -count


def blackout_bounds(
    entry_date: date | pd.Timestamp,
    before_sessions: int = 2,
    after_sessions: int = 5,
) -> tuple[date, date]:
    """Return inclusive calendar bounds around an XNYS entry session."""
    calendar = xcals.get_calendar("XNYS")
    anchor = pd.Timestamp(entry_date)
    session = calendar.date_to_session(anchor, direction="none")
    start = session
    end = session
    for _ in range(before_sessions):
        start = calendar.previous_session(start)
    for _ in range(after_sessions):
        end = calendar.next_session(end)
    return pd.Timestamp(start).date(), pd.Timestamp(end).date()


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    os.replace(temporary, path)


def refresh_symbols(
    symbols: list[str],
    start: date,
    end: date,
    session: requests.Session | None = None,
) -> RefreshResult:
    requested = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    fetched_at_utc = datetime.now(timezone.utc).isoformat()
    client = session or new_session()
    frames = []
    failures: list[str] = []
    requested_sessions = market_sessions(start, end)
    for session_date in requested_sessions:
        for event_type in EVENT_TYPES:
            try:
                frames.append(fetch_day(client, event_type, session_date, fetched_at_utc))
            except Exception as exc:
                failures.append(
                    f"{event_type}:{session_date.isoformat()}:{type(exc).__name__}:{exc}"
                )
    populated = [frame for frame in frames if not frame.empty]
    events = pd.concat(populated, ignore_index=True) if populated else empty_events()
    if not events.empty:
        events = events[
            events["symbol"].isin(requested)
            & events["event_date"].between(pd.Timestamp(start), pd.Timestamp(end))
        ].drop_duplicates(["symbol", "event_type", "event_date", "subject"])
        events = events.sort_values(["symbol", "event_date", "event_type"])
    status = "unknown" if failures else "success"
    status_by_symbol = {symbol: status for symbol in requested}
    _atomic_parquet(events, EVENT_PATH)
    _atomic_json(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sessions": len(requested_sessions),
            "symbols": requested,
            "event_rows": len(events),
            "status": status,
            "failures": failures,
            "fetched_at_utc": fetched_at_utc,
        },
        REFRESH_PATH,
    )
    return RefreshResult(events, status_by_symbol, fetched_at_utc, tuple(failures))
