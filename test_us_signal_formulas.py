import numpy as np
import pandas as pd

import us_signal_formulas as subject


def daily_fixture(rows: int = 500) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    close = np.concatenate(
        [np.linspace(100, 60, 350), np.linspace(60, 140, rows - 350)]
    )
    return pd.DataFrame(
        {
            "symbol": "TEST",
            "signal_date": dates,
            "open": close - 0.2,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000.0,
            "market_cap": 1_000_000_000.0,
        }
    )


def test_pb_requires_weekly_psar_cross_and_three_times_volume() -> None:
    daily = daily_fixture()
    daily.loc[354, "volume"] = 4_000_000.0
    result = subject.add_us_daily_signals(daily)
    assert result.loc[354, "pb"]
    assert not result.loc[353, "pb"]


def test_weekly_reference_is_literal_fifty_two_weeks_ago() -> None:
    dates = pd.date_range("2023-01-06", periods=60, freq="W-FRI")
    close = np.arange(60, dtype=float) + 100
    daily = pd.DataFrame(
        {
            "symbol": "TEST",
            "signal_date": dates,
            "open": close,
            "high": close + 2,
            "low": close - 3,
            "close": close,
            "volume": 1_000_000.0,
        }
    )
    result = subject.attach_weekly_references(daily)
    assert result.loc[52, "week_low_52_ago"] == daily.loc[0, "low"]
    assert result.loc[52, "week_high_52_ago"] == daily.loc[0, "high"]


def test_future_row_does_not_change_prior_signal_features() -> None:
    future = daily_fixture(500)
    base = future.iloc[:-1].copy()
    original = subject.add_us_daily_signals(base).iloc[-1]
    future.loc[499, ["open", "high", "low", "close", "volume"]] = [
        500,
        550,
        450,
        525,
        20_000_000,
    ]
    prior = subject.add_us_daily_signals(future).iloc[-2]
    for column in (
        "pb",
        "mq",
        "kubra_bull",
        "sma_close_20",
        "sma_close_200",
        "hma_close_15",
        "cloud_top",
        "al",
    ):
        assert original[column] == prior[column]


def test_mq_records_non_parity_vwap_source() -> None:
    result = subject.add_us_daily_signals(daily_fixture())
    assert result["mq_vwap_source"].eq("typical_price_proxy").all()
    assert result["mq"].dtype == bool


def test_mq_uses_reported_daily_vwap_when_present() -> None:
    daily = daily_fixture()
    daily["vwap"] = (daily["open"] + daily["close"]) / 2
    result = subject.add_us_daily_signals(daily)
    assert result["mq_vwap_source"].eq("reported_daily_vwap").all()
    assert result["vwap_proxy"].equals(daily["vwap"])
