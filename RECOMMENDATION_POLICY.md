# Recommendation policy

This file is the executable recommendation specification. It contains only components that
currently define eligibility, rank, vetoes, execution, or output behavior.

Historical experiments and inactive components live in
[`notes/zero_weight_research.md`](zero_weight_research.md). They do not participate in this
policy.

## Active cadence

Effective 2026-08-10, automatic runs generate recommendations from completed weekly bars.
Daily market data, signal artifacts, and matured outcome fields continue to refresh after each
market session. Generate a daily shortlist only after an explicit request:

```bash
python3 combined_recommendation.py --timeframe daily
python3 us_combined_recommendation.py --timeframe daily
```

The default remains `--timeframe weekly` in both markets.

## Required recommendation sequence

1. Freeze data at the latest fully closed bar on the requested timeframe.
2. Generate the primary signal universe.
3. Calculate consolidation state on that timeframe.
4. Suppress new entries on consolidation bars and identify eligible consolidation exits.
5. Apply liquidity, rotation, portfolio-capacity, and duplicate-position rules.
6. Rank mechanical survivors using the market-specific score.
7. Refresh scheduled-event data for the bounded survivor pool.
8. Apply the event-proximity gate and backfill in score order.
9. Apply the official opening-gap rule.
10. Present the remaining prospects and preserve every exclusion reason.
11. Add every presented symbol to the canonical TradingView watchlist.

## TradingView recommendation watchlist

Use `10 - Codex Recos` for every presented recommendation or prospect. Add exchange-qualified
identifiers such as `NYSE:AME` and `NSE:AARTIIND`.

## Scheduled-event gate

Apply this gate after mechanical ranking and before presenting an entry.

1. Check scheduled earnings or financial results.
2. Check other material dated events, including investor meetings, corporate actions,
   shareholder votes, regulatory decisions, and known index changes.
3. Prefer a current primary exchange filing or company investor-relations source.
4. Exclude entries during the five trading sessions before a material event.
5. Exclude entries during the two trading sessions after the event.
6. Extend the post-event window when price has not established a stable range.
7. Record event type, date, source, lookup time, distance in sessions, and exclusion reason.
8. Treat missing or inconclusive calendar data as `event_status_unknown` and withhold entry.

Candidate output must retain `next_event`, `event_date`, `sessions_to_event`, `event_source`,
and `event_gate_status`.

`india_event_calendar.py` uses official NSE board-meeting and corporate-action endpoints.
`us_event_calendar.py` uses the maintained Nasdaq earnings, ex-dividend, and split feeds.

## India policy

### Active strategy roles

| Strategy | Role |
|---|---|
| `WKLY_FIL` | Primary weekly trend and persistence universe. |
| `WKLY` | Weekly trend foundation below `WKLY_FIL`. |
| `PB` | Highest-weight daily standalone component for explicit daily requests. |
| `DLY_FIL` | Filtered daily trend structure. |
| `DLY` | Daily trend structure below `DLY_FIL`. |
| `MQ` | Lowest-weight daily confluence component. It cannot bypass another gate. |
| Industry rotation | Mandatory India gate. |
| Basic Industry rotation | Additional India rank evidence. |
| Twin HA | Timing confirmation after a primary signal exists. |
| Consolidation | Entry veto on active bars and rank evidence on the first exit bar. |

### India composite score

The score is additive and deterministic. Break ties by 20-session median turnover descending,
then symbol ascending.

- Daily base: `PB +10`, `DLY_FIL +6`, `DLY +3`, `MQ +1`, and `+0.5` per additional same-day
  signal.
- Daily timing: Twin HA body trigger `+3`, body-aligned state `+1`, and consolidation exit `+2`.
- Weekly base: `WKLY_FIL +10`, `WKLY +3`, trailing-five occurrence count `+0` to `+5`, and a
  new three-of-five threshold crossing `+2`.
- Weekly timing: Twin HA wick trigger `+3`, wick-aligned state `+1`, and consolidation exit `+1`.
- Both timeframes: Industry rising `+2` and Basic Industry rising `+0.5`.

Require NSE `EQ`, median 20-session turnover of at least ₹5 crore, rising Industry rotation,
no active consolidation, and a clear scheduled-event gate. Keep every pre-open row at
`opening_gate_status=pending_open` until an official opening print passes the ±2% rule.

### India weekly signal

Generate `WKLY` when all conditions hold:

1. Weekly EMA(10) is above EMA(34).
2. Weekly EMA(20) is above EMA(34).
3. Price either closes back above EMA(10) after the previous weekly low crossed below EMA(10),
   or closes above the Ichimoku cloud after the previous close was below it.

Generate `WKLY_FIL` by adding:

1. Positive MACD line.
2. Positive MACD histogram.
3. Current MACD histogram above the prior value.
4. Prior MACD histogram above the value from two weeks earlier.

Define persistence as a current `WKLY_FIL` signal with at least three `WKLY_FIL` appearances in
the trailing five completed weekly observations. The pilot uses the first threshold crossing.

### India weekly-pilot runbook

1. Freeze the latest completed weekly observation.
2. Start with NSE `EQ` instruments at their first three-of-five `WKLY_FIL` threshold crossing.
3. Require a valid signal close and ₹5-crore point-in-time median turnover.
4. Require rising NSE Industry rotation.
5. Rank by `signal_count + volume_ratio_20 / 100` descending.
6. Break ties by median turnover descending, then symbol ascending.
7. Apply the scheduled-event gate to every survivor.
8. Accept an official next-session opening print only within ±2% of signal close.
9. For the ₹1 lakh pilot, allow five positions and risk at most 1% per position.
10. Include 0.25% entry and exit costs in sizing.
11. Use an 8% initial stop below executed average entry.
12. Use the close of the 50th capital-market session including entry as the time exit.
13. Do not average down.
14. Preserve every gate and exclusion field.

Run and inspect:

```bash
python3 weekly_pilot.py
python3 weekly_pilot.py --opening-prices path/to/opening_prices.csv
pytest -q test_weekly_pilot.py test_india_event_calendar.py test_risk_exit_research.py
```

The authoritative outputs are `reports/weekly_pilot_candidates.csv` and
`reports/weekly_pilot_plan.md`. A zero-candidate result means hold cash.

## Consolidation gate

Calculate both detectors independently on closed bars for the active timeframe.

### EMA-angle detector

- Source: `ohlc4`.
- Trend line: EMA(27).
- Normalized angle: `degrees(atan((ema27 - ema27[1]) / ATR(14)))`.
- Set `ema_angle_consolidation=true` when absolute angle is at most 2 degrees.

### ADX/DI-compression detector

- ADX and directional-movement length: 14.
- Weak-ADX threshold: 14.
- Smooth custom positive DM, negative DM, and true range with RMA(14).
- Set `adx_di_consolidation=true` when:
  - ADX is below 14;
  - the prior custom `DI+` and `DI-` difference is below 5;
  - current custom `DI+` is below 25; and
  - current custom `DI-` is below 25.

Normalize the supplied `ta.dmi(atr_length, atr_length)` call to
`ta.dmi(adx_length, adx_length)`. The custom DI series drive compression. Built-in `ta.dmi`
supplies ADX.

### Consolidation behavior

1. Set `in_consolidation = ema_angle_consolidation or adx_di_consolidation`.
2. Suppress new entries when `in_consolidation=true`.
3. Count consecutive consolidation bars independently by timeframe.
4. Reset the live count on the first non-consolidation bar.
5. Set `consolidation_exit=true` when the prior bar was consolidating and the current bar is not.
6. Preserve the prior count as `completed_consolidation_bars` on the exit bar.
7. Apply every other liquidity, rotation, event, capacity, and opening gate to an exit signal.

No minimum consolidation duration is part of the policy.

## Twin Smoothed Heikin Ashi confirmation

1. Construct the fast candle with EMA(8) before and after recursive Heikin Ashi construction.
2. Construct the slow candle with EMA(20) before and after recursive Heikin Ashi construction.
3. Require fast HA close at or above fast HA open.
4. Require slow HA close at or above slow HA open.
5. Define `body` as real close above all four fast and slow HA open and close values.
6. Define `wick` as real close above both fast and slow HA high values.
7. Set the trigger only when the full state changes from false to true.
8. Label the trigger `reversal` when either prior candle was bearish.
9. Label it `continuation` when both prior candles were bullish and price recrosses the boundary.
10. Use body confirmation for daily selection and wick confirmation for weekly selection.
11. Never allow Twin HA to create eligibility or bypass a gate.

The source imports `wallneradam/TAExt/8`. Compare the local translation with TradingView before
claiming line-for-line formula parity.

## US policy

US output remains a research watchlist. Set `actionable_entry=false` until the maintained US
capital-deployment gate changes.

### Active US universes

| Strategy | Role |
|---|---|
| `DLY_FIL` | Primary daily universe for explicit daily requests. |
| `DLY` | Daily trend universe below `DLY_FIL`. |
| `WKLY_FIL` | Primary weekly universe. |
| `WKLY` | Weekly trend foundation. |
| `WKLY_FIL` three-of-five | Weekly persistence rank evidence. |

### US composite

1. Start with instruments marked `eligible_initial` in the current US symbol master.
2. Require `stock`.
3. Require latest close of at least $5.
4. Require 20 turnover observations.
5. Require median 20-session dollar turnover of at least $5 million.
6. Require point-in-time market capitalization of at least $300 million.
7. Require current `DLY` or `DLY_FIL` for daily selection.
8. Require current `WKLY_FIL` for weekly selection.
9. Suppress the active timeframe when it is in consolidation.
10. Compute `rank_score` as the equal-weight sum of percentile ranks for:
    - 13-week return relative to SPY;
    - 26-week return relative to SPY;
    - log-transformed 20-session median dollar turnover; and
    - MACD histogram.
11. Add `DLY_FIL +6` and `DLY +3` for daily ranking.
12. Add `WKLY_FIL +10`, `WKLY +3`, occurrence count `+0` to `+5`, and fresh three-of-five
    crossing `+2` for weekly ranking.
13. Break ties by median dollar turnover descending, then symbol ascending.
14. Refresh official events for the top ten eligible rows per timeframe.
15. Treat any event request failure as `event_status_unknown` for every requested symbol.
16. Exclude dated events from two XNYS sessions before entry through five sessions after entry.
17. Require the official next open within ±2% of signal close.
18. Use `pending_open` before that print.
19. Calculate cost-aware 1%-risk sizing with an 8% stop and five-position capacity.
20. Use 0.10% cost per side.
21. Use 10 sessions for daily planning and 50 sessions for weekly planning.
22. Keep `actionable_entry=false` regardless of operational gates.

The maintained implementation is `us_combined_recommendation.py`. Point-in-time shares, sectors,
delisted histories, and liquidity come from `us_point_in_time_research.py`.

### US operational coverage

Run `us_parity_audit.py` after the composite. Blocking checks cover current OHLCV, required
signal columns, official-event refresh, and fail-closed composite state. The US event feed covers
earnings, ex-dividends, and splits. Investor meetings, regulatory decisions, and index changes
remain outside the automated feed.

## Decision log and forward outcomes

The first row for a `market + timeframe + signal_date + symbol` decision ID is immutable.
Preserve presented rows and mechanical survivors before the bounded event-refresh budget.

Forward outcomes use the first official session open on or after `planned_entry_date`. Record:

- actual entry date and open;
- opening gap and ±2% gate result;
- current return and benchmark-relative return;
- maximum favorable and adverse excursion; and
- 1, 5, 10, 20, and 50-session returns when mature.

India uses `NIFTYBEES`. The US uses `SPY`.

Mark a non-presented row as an opportunity miss when its forward return reaches 10%. This label
does not change score or retrospectively promote the row.

Run or rebuild the ledger with:

```bash
python3 recommendation_forward_test.py
pytest -q test_recommendation_forward_test.py
```

The page is `recommendations.html`. The decision and outcome stores are
`data/forward_test/recommendation_decisions.parquet` and
`data/forward_test/recommendation_outcomes.parquet`.

## Labels and final output

- `prospect`: mechanically selected candidate.
- `actionable_entry`: prospect that also passes event, opening, capacity, and sizing gates.
- `manual_request`: symbol added at the user's direction without a mechanical-policy claim.

For every output:

1. Use the most recent fully closed observation.
2. Record cutoff, signal date, market, exchange-qualified symbol, and selection mode.
3. Return fewer than the requested count when fewer rows survive.
4. Preserve every exclusion reason and gate status.
5. Add every presented prospect to `10 - Codex Recos`.

## Active reproduction commands

```bash
python3 combined_recommendation.py
python3 us_signal_generation.py --timeframe all --asof YYYY-MM-DD
python3 us_combined_recommendation.py
python3 us_parity_audit.py
python3 weekly_pilot.py
pytest -q test_combined_recommendation.py test_us_combined_recommendation.py \
  test_us_signal_generation.py test_us_parity_audit.py \
  test_weekly_pilot.py test_strategy_suite.py
```
