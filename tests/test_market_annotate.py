from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from newsparser.claude.output_parser import RelationUpdate
from newsparser.market import store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _rel(predicate="IMPACTS", obj="SPX"):
    return RelationUpdate(op="NEW", subject="Fed", predicate=predicate,
                          obj=obj, confidence=0.85, impact_score=0.7)


def test_annotate_skips_non_tracked_alias():
    from newsparser.market import annotate
    with patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel(obj="OpenAI")], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_skips_non_impacts_predicate():
    from newsparser.market import annotate
    with patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel(predicate="ANNOUNCED")], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_uses_intraday_when_available():
    """Slot 12:00 KST = 03:00 UTC. Bars at 02:00 UTC and 03:00 UTC."""
    from newsparser.market import annotate
    before_ts = datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc).isoformat()
    after_ts  = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    bars = [
        {"instrument": "SPX", "ts": before_ts, "open": 0, "high": 0, "low": 0, "close": 100.0, "volume": 0},
        {"instrument": "SPX", "ts": after_ts,  "open": 0, "high": 0, "low": 0, "close": 99.0,  "volume": 0},
    ]

    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=bars), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")

    assert n == 1
    call_kwargs = cy.call_args.kwargs
    assert call_kwargs["window_literal"] == "[-60m, +60m]"
    assert abs(call_kwargs["delta_pct"] - (-1.0)) < 1e-6  # (99-100)/100*100


def test_annotate_falls_back_to_daily_when_intraday_empty():
    from newsparser.market import annotate
    # No intraday data; seed daily values
    store.upsert_daily([
        {"instrument": "SPX", "date": "2026-05-08",
         "open": 0, "high": 0, "low": 0, "close": 100.0, "volume": 0},
        {"instrument": "SPX", "date": "2026-05-09",
         "open": 0, "high": 0, "low": 0, "close": 101.5, "volume": 0},
    ])

    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=[]), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")

    assert n == 1
    call_kwargs = cy.call_args.kwargs
    assert call_kwargs["window_literal"] == "daily"
    assert abs(call_kwargs["delta_pct"] - 1.5) < 1e-6


def test_annotate_skips_when_both_intraday_and_daily_empty():
    from newsparser.market import annotate
    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", return_value=[]), \
         patch.object(annotate, "_apply_annotation_cypher") as cy:
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")
    assert n == 0
    assert cy.call_count == 0


def test_annotate_never_raises():
    from newsparser.market import annotate
    def boom(*a, **kw):
        raise RuntimeError("kaboom")
    with patch("newsparser.market.annotate.fetcher.fetch_intraday_hourly", side_effect=boom):
        # Should swallow the exception per-relation; never propagate
        n = annotate.maybe_annotate_impacts([_rel()], "2026-05-09-12", "markets")
    assert n == 0
