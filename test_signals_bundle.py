import json
import tempfile
from pathlib import Path

import signals_bundle


def test_build_bundle_emits_five_page_contract_with_eight_week_default():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "history.json").write_text(
            json.dumps(
                {
                    "source_url": "https://example.test/weekly",
                    "weeks": [{"week": "2026-08-07", "tickers": [{"symbol": "ONE"}]}],
                }
            )
        )
        (root / "history_daily.json").write_text(
            json.dumps(
                {
                    "source_url": "https://example.test/daily",
                    "weeks": [{"week": "2026-08-11", "tickers": [{"symbol": "ONE"}]}],
                }
            )
        )
        (root / "market_counts.json").write_text(
            json.dumps(
                {
                    "counts": {
                        "cp-mcl-adv": {"2026-08-10": 100, "2026-08-11": 120},
                        "cp-mcl-dec": {"2026-08-10": 80, "2026-08-11": 70},
                    }
                }
            )
        )
        (root / "sector_weekly.json").write_text(
            json.dumps(
                {
                    "weeks": ["2026-08-07"],
                    "levels": [["sector", "Sector"]],
                    "counts": {
                        "sector": {"Test Sector": [1, 2, 3]},
                        "industry": {"Test Industry": [1, 2, 3]},
                        "basic": {"Test Basic": [1, 2, 3]},
                    },
                    "totals": [1],
                }
            )
        )
        classification = root / "classification.csv"
        classification.write_text(
            "Symbol,Sector,Industry,Basic Industry\n"
            "ONE,Test Sector,Test Industry,Test Basic\n"
        )
        bundle = signals_bundle.build_bundle(
            root, classification_path=classification
        )
        assert bundle["schema_version"] == "1.0"
        assert set(bundle["pages"]) == {
            "weekly",
            "daily",
            "market",
            "sectors",
            "recommendations",
        }
        assert bundle["pages"]["weekly"]["default_window"] == 8
        assert bundle["data_cutoff"]["IN"] == "2026-08-11"
        assert bundle["pages"]["market"]["payload"]["mcclellan"]["days"]
        assert bundle["pages"]["sectors"]["payload"]["parents"] == {
            "industry": {"Test Industry": "Test Sector"},
            "basic": {"Test Basic": "Test Sector"},
        }
        weekly = bundle["pages"]["weekly"]["payload"]
        daily = bundle["pages"]["daily"]["payload"]
        assert weekly["cross"]["weeks"] == {"2026-08-11": ["ONE"]}
        assert daily["cross"]["weeks"] == {"2026-08-07": ["ONE"]}
        assert weekly["rotation"]["of"]["ONE"] == [
            "Test Sector",
            "Test Industry",
            "Test Basic",
        ]
        assert daily["rotation"]["of"]["ONE"] == [
            "Test Sector",
            "Test Industry",
            "Test Basic",
        ]
