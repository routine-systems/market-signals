#!/usr/bin/env python3
"""Price-and-volume US translations of the PB, MQ, and KuBra signal families."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


MIN_US_MARKET_CAP = 300_000_000.0
MIN_US_DOLLAR_TURNOVER = 5_000_000.0
MQ_VWAP_SOURCE = "typical_price_proxy"


def _crossed_above(left: pd.Series, right: pd.Series, symbols: pd.Series) -> pd.Series:
    previous_left = left.groupby(symbols, sort=False).shift(1)
    previous_right = right.groupby(symbols, sort=False).shift(1)
    return left.gt(right) & previous_left.le(previous_right)


def _rolling_count(values: pd.Series, symbols: pd.Series, window: int) -> pd.Series:
    return values.groupby(symbols, sort=False).transform(
        lambda series: series.rolling(window, min_periods=window).sum()
    )


def _wma(values: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(weights.sum())
    return values.rolling(length, min_periods=length).apply(
        lambda window: float(np.dot(window, weights) / denominator), raw=True
    )


def _hma(values: pd.Series, length: int) -> pd.Series:
    half = max(1, length // 2)
    root = max(1, int(math.sqrt(length)))
    raw = 2 * _wma(values, half) - _wma(values, length)
    return _wma(raw, root)


def parabolic_sar(group: pd.DataFrame, step: float = 0.02, maximum: float = 0.2) -> pd.Series:
    """Return a deterministic Wilder-style PSAR for one ordered instrument."""
    high = group["high"].to_numpy(dtype=float)
    low = group["low"].to_numpy(dtype=float)
    close = group["close"].to_numpy(dtype=float)
    result = np.full(len(group), np.nan, dtype=float)
    if len(group) < 2:
        return pd.Series(result, index=group.index)
    bullish = close[1] >= close[0]
    result[0] = low[0] if bullish else high[0]
    extreme = high[0] if bullish else low[0]
    acceleration = step
    for position in range(1, len(group)):
        candidate = result[position - 1] + acceleration * (extreme - result[position - 1])
        if bullish:
            candidate = min(candidate, low[position - 1])
            if position > 1:
                candidate = min(candidate, low[position - 2])
            if low[position] < candidate:
                bullish = False
                candidate = extreme
                extreme = low[position]
                acceleration = step
            elif high[position] > extreme:
                extreme = high[position]
                acceleration = min(maximum, acceleration + step)
        else:
            candidate = max(candidate, high[position - 1])
            if position > 1:
                candidate = max(candidate, high[position - 2])
            if high[position] > candidate:
                bullish = True
                candidate = extreme
                extreme = high[position]
                acceleration = step
            elif low[position] < extreme:
                extreme = low[position]
                acceleration = min(maximum, acceleration + step)
        result[position] = candidate
    return pd.Series(result, index=group.index)


def attach_weekly_references(daily: pd.DataFrame) -> pd.DataFrame:
    """Attach prior completed-week PSAR and literal 52-weeks-ago OHLC references."""
    frame = daily.sort_values(["symbol", "signal_date"]).copy()
    frame["week_end"] = frame["signal_date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    weekly = (
        frame.groupby(["symbol", "week_end"], as_index=False, sort=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
        .sort_values(["symbol", "week_end"])
    )
    weekly["psar"] = np.nan
    for _, instrument in weekly.groupby("symbol", sort=False):
        weekly.loc[instrument.index, "psar"] = parabolic_sar(instrument).to_numpy()
    grouped = weekly.groupby("symbol", sort=False)
    weekly["psar_prior"] = grouped["psar"].shift(1)
    previous = weekly[
        ["symbol", "week_end", "close", "psar", "psar_prior"]
    ].rename(
        columns={
            "week_end": "previous_week_end",
            "close": "previous_week_close",
            "psar": "previous_week_psar",
            "psar_prior": "two_weeks_ago_psar",
        }
    )
    frame["previous_week_end"] = frame["week_end"] - pd.Timedelta(days=7)
    frame = frame.merge(
        previous,
        on=["symbol", "previous_week_end"],
        how="left",
        validate="many_to_one",
    )
    references = weekly[["symbol", "week_end", "low", "high"]].rename(
        columns={
            "week_end": "reference_week_end",
            "low": "week_low_52_ago",
            "high": "week_high_52_ago",
        }
    )
    frame["reference_week_end"] = frame["week_end"] - pd.Timedelta(days=364)
    return frame.merge(
        references,
        on=["symbol", "reference_week_end"],
        how="left",
        validate="many_to_one",
    ).sort_values(["symbol", "signal_date"]).reset_index(drop=True)


def add_us_daily_signals(
    daily: pd.DataFrame,
    *,
    minimum_market_cap: float = MIN_US_MARKET_CAP,
    minimum_dollar_turnover: float = MIN_US_DOLLAR_TURNOVER,
) -> pd.DataFrame:
    """Add PB, MQ-proxy, and KuBra flags using current and earlier observations only."""
    required = {"symbol", "signal_date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(daily.columns))
    if missing:
        raise ValueError(f"daily signal input missing columns: {', '.join(missing)}")
    frame = attach_weekly_references(daily)
    grouped = frame.groupby("symbol", sort=False, group_keys=False)
    symbols = frame["symbol"]
    prior_close = grouped["close"].shift(1)
    prior_volume = grouped["volume"].shift(1)
    volume_sma30 = grouped["volume"].transform(
        lambda values: values.rolling(30, min_periods=30).mean()
    )
    market_cap = frame.get("market_cap", pd.Series(np.nan, index=frame.index)).astype(float)
    market_cap_ok = market_cap.ge(minimum_market_cap)
    weekly_psar_cross = (
        frame["close"].gt(frame["previous_week_psar"])
        & frame["previous_week_close"].le(frame["two_weeks_ago_psar"])
    )
    frame["pb_weekly_psar_cross"] = weekly_psar_cross
    frame["pb_volume_3x"] = frame["volume"].gt(3 * volume_sma30)
    frame["pb"] = (
        weekly_psar_cross
        & frame["close"].gt(prior_close)
        & frame["pb_volume_3x"]
        & frame["volume"].gt(prior_volume)
        & market_cap_ok
    )

    for length in (20, 50, 150, 200):
        frame[f"sma_close_{length}"] = grouped["close"].transform(
            lambda values, window=length: values.rolling(window, min_periods=window).mean()
        )
    for length in (4, 34, 77):
        source = "high" if length == 4 else "close"
        frame[f"ema_{source}_{length}"] = grouped[source].transform(
            lambda values, span=length: values.ewm(span=span, adjust=False).mean()
        )
    frame["hma_close_15"] = grouped["close"].transform(lambda values: _hma(values, 15))
    sma_close_40 = grouped["close"].transform(
        lambda values: values.rolling(40, min_periods=40).mean()
    )
    frame["al"] = sma_close_40.groupby(symbols, sort=False).transform(
        lambda values: values.rolling(41, min_periods=41).mean()
    )
    if "vwap" in frame and frame["vwap"].notna().any():
        vwap = frame["vwap"].astype(float)
        vwap_source = "reported_daily_vwap"
    else:
        vwap = (frame["high"] + frame["low"] + frame["close"]) / 3
        vwap_source = MQ_VWAP_SOURCE
    frame["vwap_proxy"] = vwap
    frame["sma_vwap_proxy_30"] = vwap.groupby(symbols, sort=False).transform(
        lambda values: values.rolling(30, min_periods=30).mean()
    )

    high_9 = grouped["high"].transform(lambda values: values.rolling(9, min_periods=9).max())
    low_9 = grouped["low"].transform(lambda values: values.rolling(9, min_periods=9).min())
    high_26 = grouped["high"].transform(lambda values: values.rolling(26, min_periods=26).max())
    low_26 = grouped["low"].transform(lambda values: values.rolling(26, min_periods=26).min())
    high_52 = grouped["high"].transform(lambda values: values.rolling(52, min_periods=52).max())
    low_52 = grouped["low"].transform(lambda values: values.rolling(52, min_periods=52).min())
    span_a = ((high_9 + low_9) / 2 + (high_26 + low_26) / 2) / 2
    span_b = (high_52 + low_52) / 2
    frame["cloud_top"] = pd.concat(
        [
            span_a.groupby(symbols, sort=False).shift(26),
            span_b.groupby(symbols, sort=False).shift(26),
        ],
        axis=1,
    ).max(axis=1)

    cross_sma20 = _crossed_above(frame["close"], frame["sma_close_20"], symbols)
    cross_hma15 = _crossed_above(frame["close"], frame["hma_close_15"], symbols)
    cross_cloud = _crossed_above(frame["close"], frame["cloud_top"], symbols)
    hma_count_10_shift2 = _rolling_count(cross_hma15.astype(int), symbols, 10).groupby(
        symbols, sort=False
    ).shift(2)
    cloud_count_5 = _rolling_count(cross_cloud.astype(int), symbols, 5)
    interactions = pd.Series(False, index=frame.index)
    for offset in (0, 1, 2):
        close_at = frame["close"].groupby(symbols, sort=False).shift(offset)
        low_at = frame["low"].groupby(symbols, sort=False).shift(offset)
        al_at = frame["al"].groupby(symbols, sort=False).shift(offset)
        interactions |= close_at.gt(al_at) & low_at.lt(al_at)

    trend_template = (
        frame["close"].gt(frame["sma_close_150"])
        & frame["close"].gt(frame["sma_close_200"])
        & frame["sma_close_150"].gt(frame["sma_close_200"])
        & frame["sma_close_200"].gt(frame["sma_close_200"].groupby(symbols).shift(10))
        & frame["sma_close_200"].groupby(symbols).shift(10).gt(
            frame["sma_close_200"].groupby(symbols).shift(20)
        )
        & frame["sma_close_200"].groupby(symbols).shift(20).gt(
            frame["sma_close_200"].groupby(symbols).shift(30)
        )
        & frame["sma_close_50"].gt(frame["sma_close_150"])
        & frame["sma_close_50"].gt(frame["sma_close_200"])
        & frame["close"].gt(1.3 * frame["week_low_52_ago"])
        & frame["close"].gt(0.75 * frame["week_high_52_ago"])
        & cross_sma20
        & (frame["close"] * frame["volume"]).gt(minimum_dollar_turnover)
    )
    breakout = (
        frame["close"].gt(frame["ema_high_4"])
        & prior_close.lt(frame["ema_high_4"].groupby(symbols).shift(1))
        & frame["close"].groupby(symbols).shift(2).lt(
            frame["ema_high_4"].groupby(symbols).shift(2)
        )
        & frame["close"].groupby(symbols).shift(3).lt(
            frame["ema_high_4"].groupby(symbols).shift(3)
        )
        & frame["close"].gt(frame["sma_vwap_proxy_30"])
        & market_cap_ok
        & hma_count_10_shift2.gt(0)
    )
    recent_ema_interactions = pd.Series(True, index=frame.index)
    for length in (34, 77):
        high_above = frame["high"].gt(frame[f"ema_close_{length}"])
        low_below = frame["low"].lt(frame[f"ema_close_{length}"])
        recent_ema_interactions &= _rolling_count(high_above.astype(int), symbols, 5).gt(0)
        recent_ema_interactions &= _rolling_count(low_below.astype(int), symbols, 5).gt(0)
    frame["mq_trend_template"] = trend_template
    frame["mq_breakout"] = breakout
    frame["mq_al_interaction"] = interactions
    frame["mq_cloud_recent"] = cloud_count_5.ge(1)
    frame["mq_ema_interaction"] = recent_ema_interactions
    frame["mq"] = (
        market_cap_ok
        & frame["mq_trend_template"]
        & frame["mq_breakout"]
        & frame["mq_al_interaction"]
        & frame["mq_cloud_recent"]
        & frame["mq_ema_interaction"]
    )
    frame["mq_vwap_source"] = vwap_source

    frame["kubra_bull"] = (
        frame["ema_close_34"].gt(frame["ema_close_77"])
        & frame["close"].gt(frame["ema_close_34"])
        & frame["volume"].gt(1.5 * volume_sma30)
    )
    return frame
