from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from newsparser.market import store
import newsparser.scripts.fetch_market_daily as script


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))


def _bar(alias: str, d: str, close: float) -> dict:
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def test_backfill_when_empty_db_calls_fetch_daily_for_every_alias():
    calls: list[tuple[str, date, date]] = []

    def fake_fetch(alias, start, end):
        calls.append((alias, start, end))
        return [_bar(alias, "2026-05-08", 100.0)]

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    aliases = sorted({c[0] for c in calls})
    assert aliases == sorted(["SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"])
    # Backfill window should be 5 years
    for _, start, _ in calls:
        assert (date.today() - start).days >= 365 * 5 - 1


def test_incremental_when_existing_uses_last_date_plus_one():
    store.init_market_db()
    store.upsert_daily([_bar("SPX", "2026-05-08", 5230.0)])

    captured = {}

    def fake_fetch(alias, start, end):
        if alias == "SPX":
            captured["start"] = start
        return []

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    assert captured["start"] == date(2026, 5, 9)


def test_one_alias_failure_doesnt_stop_others():
    counts: dict[str, int] = {}

    def fake_fetch(alias, start, end):
        counts[alias] = counts.get(alias, 0) + 1
        if alias == "SPX":
            raise RuntimeError("boom")
        return []

    with patch("newsparser.scripts.fetch_market_daily.fetcher.fetch_daily",
               side_effect=fake_fetch):
        script.main()

    # All 8 aliases were attempted, even though SPX raised
    assert set(counts.keys()) >= {"SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"}
