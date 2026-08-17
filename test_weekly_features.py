import pandas as pd

from weekly_features import add_weekly_features


def weekly_fixture(periods: int = 90) -> pd.DataFrame:
    dates = pd.date_range("2024-01-05", periods=periods, freq="W-FRI")
    close = pd.Series(range(100, 100 + periods), dtype=float)
    return pd.DataFrame(
        {
            "symbol": "TEST",
            "week_start": dates - pd.Timedelta(days=4),
            "signal_date": dates,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


def test_features_do_not_use_future_weeks():
    base = weekly_fixture()
    original = add_weekly_features(base.iloc[:-1]).iloc[-1]
    changed = base.copy()
    changed.loc[changed.index[-1], "close"] = 10_000.0
    changed.loc[changed.index[-1], "high"] = 10_001.0
    prior = add_weekly_features(changed).iloc[-2]
    for column in ("ema_10", "ema_20", "ema_34", "macd_hist", "cloud_top"):
        assert original[column] == prior[column]
