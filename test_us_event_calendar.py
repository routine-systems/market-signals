from datetime import date

import pandas as pd

import us_event_calendar as subject


def payload(rows, *, dividend=False):
    data = {"calendar": {"rows": rows}} if dividend else {"rows": rows}
    return {"status": {"rCode": 200}, "data": data}


def test_parse_three_nasdaq_event_types() -> None:
    fetched = "2026-08-10T00:00:00+00:00"
    earnings = subject.parse_payload(
        payload([{"symbol": "PH", "name": "Parker", "time": "time-after-hours"}]),
        "earnings",
        date(2026, 8, 10),
        fetched,
    )
    dividends = subject.parse_payload(
        payload(
            [{"symbol": "PH", "companyName": "Parker", "dividend_Ex_Date": "8/11/2026"}],
            dividend=True,
        ),
        "dividend",
        date(2026, 8, 11),
        fetched,
    )
    splits = subject.parse_payload(
        payload([{"symbol": "PH", "name": "Parker", "executionDate": "8/12/2026", "ratio": "2 : 1"}]),
        "split",
        date(2026, 8, 12),
        fetched,
    )
    assert earnings.loc[0, "event_date"] == pd.Timestamp("2026-08-10")
    assert dividends.loc[0, "event_type"] == "dividend"
    assert splits.loc[0, "event_type"] == "split"


def test_market_sessions_exclude_weekend() -> None:
    sessions = subject.market_sessions(date(2026, 8, 7), date(2026, 8, 10))
    assert sessions == [date(2026, 8, 7), date(2026, 8, 10)]


def test_blackout_bounds_and_session_distance() -> None:
    start, end = subject.blackout_bounds(date(2026, 8, 10))
    assert start == date(2026, 8, 6)
    assert end == date(2026, 8, 17)
    assert subject.session_distance(date(2026, 8, 10), date(2026, 8, 17)) == 5
    assert subject.session_distance(date(2026, 8, 10), date(2026, 8, 6)) == -2


class FakeResponse:
    def __init__(self, body, fail=False):
        self.body = body
        self.fail = fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("calendar unavailable")

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, fail_splits=False):
        self.fail_splits = fail_splits

    def get(self, url, params, timeout):
        if url.endswith("splits"):
            return FakeResponse(payload([]), fail=self.fail_splits)
        if url.endswith("dividends"):
            return FakeResponse(payload([], dividend=True))
        return FakeResponse(
            payload([{"symbol": "PH", "name": "Parker", "time": "time-after-hours"}])
        )


def test_refresh_is_fail_closed_when_one_calendar_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(subject, "EVENT_PATH", tmp_path / "events.parquet")
    monkeypatch.setattr(subject, "REFRESH_PATH", tmp_path / "refresh.json")
    result = subject.refresh_symbols(
        ["PH"],
        date(2026, 8, 10),
        date(2026, 8, 10),
        session=FakeSession(fail_splits=True),
    )
    assert result.status_by_symbol == {"PH": "unknown"}
    assert len(result.failures) == 1
    assert (tmp_path / "events.parquet").exists()


def test_refresh_marks_covered_symbols_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(subject, "EVENT_PATH", tmp_path / "events.parquet")
    monkeypatch.setattr(subject, "REFRESH_PATH", tmp_path / "refresh.json")
    result = subject.refresh_symbols(
        ["PH"],
        date(2026, 8, 10),
        date(2026, 8, 10),
        session=FakeSession(),
    )
    assert result.status_by_symbol == {"PH": "success"}
    assert result.events["event_type"].tolist() == ["earnings"]
