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
        assert history["weeks"][0]["week"] == "2026-08-07"
        assert chartink_payloads.parse_membership(path) == {
            "2026-08-07": ["AAA", "BBB"]
        }
