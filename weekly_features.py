"""Stable completed-week indicators promoted from the US research harness."""

from __future__ import annotations

import pandas as pd


def _ema(values: pd.Series, span: int) -> pd.Series:
    return values.ewm(span=span, adjust=False, min_periods=span).mean()


def add_weekly_features(weekly: pd.DataFrame) -> pd.DataFrame:
    """Add point-in-time WKLY and WKLY_FIL memberships without future bars."""
    frame = weekly.sort_values(["symbol", "signal_date"]).copy()
    grouped = frame.groupby("symbol", sort=False, group_keys=False)

    for span in (10, 12, 20, 26, 34):
        frame[f"ema_{span}"] = grouped["close"].transform(lambda values, n=span: _ema(values, n))

    frame["macd_line"] = frame["ema_12"] - frame["ema_26"]
    frame["macd_signal"] = frame.groupby("symbol", sort=False)["macd_line"].transform(
        lambda values: _ema(values, 9)
    )
    frame["macd_hist"] = frame["macd_line"] - frame["macd_signal"]

    high_9 = grouped["high"].transform(lambda values: values.rolling(9, min_periods=9).max())
    low_9 = grouped["low"].transform(lambda values: values.rolling(9, min_periods=9).min())
    high_26 = grouped["high"].transform(lambda values: values.rolling(26, min_periods=26).max())
    low_26 = grouped["low"].transform(lambda values: values.rolling(26, min_periods=26).min())
    high_52 = grouped["high"].transform(lambda values: values.rolling(52, min_periods=52).max())
    low_52 = grouped["low"].transform(lambda values: values.rolling(52, min_periods=52).min())
    conversion = (high_9 + low_9) / 2
    base = (high_26 + low_26) / 2
    span_a = ((conversion + base) / 2).groupby(frame["symbol"]).shift(26)
    span_b = ((high_52 + low_52) / 2).groupby(frame["symbol"]).shift(26)
    frame["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1, skipna=False)

    previous_low = grouped["low"].shift(1)
    previous_close = grouped["close"].shift(1)
    previous_ema_10 = grouped["ema_10"].shift(1)
    previous_cloud = grouped["cloud_top"].shift(1)
    ema_bounce = frame["close"].gt(frame["ema_10"]) & previous_low.lt(previous_ema_10)
    cloud_bounce = frame["close"].gt(frame["cloud_top"]) & previous_close.lt(previous_cloud)
    trend = frame["ema_10"].gt(frame["ema_34"]) & frame["ema_20"].gt(frame["ema_34"])
    frame["wkly"] = trend & (ema_bounce | cloud_bounce)

    previous_hist = grouped["macd_hist"].shift(1)
    second_previous_hist = grouped["macd_hist"].shift(2)
    macd_filter = (
        frame["macd_hist"].gt(previous_hist)
        & previous_hist.gt(second_previous_hist)
        & frame["macd_line"].gt(0)
        & frame["macd_hist"].gt(0)
    )
    frame["wkly_fil"] = frame["wkly"] & macd_filter
    frame["wkly_fil_count_5"] = frame.groupby("symbol", sort=False)["wkly_fil"].transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    )
    frame["wkly_fil_3of5"] = frame["wkly_fil_count_5"].ge(3) & frame["wkly_fil"]
    prior_persistence = frame.groupby("symbol", sort=False)["wkly_fil_count_5"].shift(1).ge(3)
    frame["wkly_fil_3of5_trigger"] = frame["wkly_fil_3of5"] & ~prior_persistence
    frame["return_13w"] = grouped["close"].pct_change(13, fill_method=None)
    frame["return_26w"] = grouped["close"].pct_change(26, fill_method=None)
    return frame
