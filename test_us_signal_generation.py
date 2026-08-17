#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

from test_weekly_features import weekly_fixture
from us_signal_generation import build_candidates
from weekly_features import add_weekly_features


def _inputs(earnings_date: str | None):
    features = add_weekly_features(weekly_fixture())
    features.loc[features.index[-1], "wkly"] = True
    features.loc[features.index[-1], "wkly_fil"] = True
    liquidity = pd.DataFrame(
        [
            {
                "symbol": "TEST",
                "latest_close": 189.0,
                "median_turnover_20": 10_000_000.0,
                "turnover_sessions": 20,
            }
        ]
    )
    master = pd.DataFrame(
        [
            {
                "symbol": "TEST",
                "name": "Test",
                "exchange": "NYSE",
                "asset_type": "stock",
                "eligible_initial": True,
            }
        ]
    )
    earnings = pd.DataFrame(
        []
        if earnings_date is None
        else [
            {
                "symbol": "TEST",
                "next_earnings_date": earnings_date,
                "earnings_time": "post-market",
            }
        ],
        columns=["symbol", "next_earnings_date", "earnings_time"],
    )
    return features, liquidity, master, earnings


def test_signal_candidate_is_blocked_inside_earnings_window():
    features, liquidity, master, earnings = _inputs("2025-09-22")
    result, _ = build_candidates("weekly", features, liquidity, master, earnings)
    assert not result.loc[0, "earnings_clear"]
    assert not result.loc[0, "eligible_for_shortlist"]


def test_signal_candidate_without_known_event_is_fail_closed():
    result, _ = build_candidates("weekly", *_inputs(None))
    assert result.loc[0, "earnings_status"] == "unknown"
    assert not result.loc[0, "earnings_clear"]
    assert not result.loc[0, "eligible_for_shortlist"]


def test_etf_does_not_require_an_earnings_event():
    features, liquidity, master, earnings = _inputs(None)
    master.loc[0, "asset_type"] = "etf"
    result, _ = build_candidates("weekly", features, liquidity, master, earnings)
    assert result.loc[0, "earnings_status"] == "not_applicable"
    assert result.loc[0, "earnings_clear"]
