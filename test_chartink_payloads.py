import tempfile
from pathlib import Path

import chartink_payloads


def test_backtest_parser_normalises_dates_and_membership():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "screen.csv"
        path.write_text(
            "Date,Symbol,Marketcapname,Sector\n07-08-2026,AAA,Smallcap,Industrials\n07-08-2026,BBB,Midcap,Finance\n"
        )
        history = chartink_payloads.build_history(
            path, "https://chartink.com/screener/example", "hash", "weekly"
        )
        assert history["weeks"][0]["week"] == "2026-08-03"
        assert chartink_payloads.parse_membership(path) == {
            "2026-08-07": ["AAA", "BBB"]
        }


def test_holiday_week_keeps_primary_filter_sector_and_daily_aligned(tmp_path):
    from market import membership_from_csv, counts_from_csv, build_sector_store
    path = tmp_path / "holiday.csv"
    path.write_text("Date,Symbol,Sector\n15-09-2026,AAA,Finance\n16-09-2026,BBB,Finance\n")
    weekly = chartink_payloads.build_history(path, "https://chartink.com/screener/weekly")
    chartink_payloads.attach_filter(weekly, path, "https://chartink.com/screener/weekly-fil")
    assert [row["week"] for row in weekly["weeks"]] == ["2026-09-14"]
    assert weekly["filter"]["weeks"] == {"2026-09-14": ["AAA", "BBB"]}
    assert membership_from_csv(path) == {"2026-09-14": {"AAA", "BBB"}}
    assert build_sector_store(membership_from_csv(path), {})["weeks"] == ["2026-09-14"]
    daily = chartink_payloads.build_history(path, "https://chartink.com/screener/daily", timeframe="daily")
    chartink_payloads.attach_filter(daily, path, "https://chartink.com/screener/daily-fil")
    assert [row["week"] for row in daily["weeks"]] == ["2026-09-15", "2026-09-16"]
    assert daily["filter"]["weeks"] == {"2026-09-15": ["AAA"], "2026-09-16": ["BBB"]}
    assert counts_from_csv(path) == {"2026-09-15": 1, "2026-09-16": 1}
