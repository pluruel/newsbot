from datetime import datetime, timezone
import pytest

from newsparser.market import store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _bar(alias, d, close):
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def _call(tool, **kwargs):
    """Call a FastMCP-decorated tool. FastMCP wraps the callable on `.fn`
    in newer versions; older versions just return the underlying function."""
    fn = getattr(tool, "fn", None) or getattr(tool, "callable", None) or tool
    return fn(**kwargs)


def test_market_query_returns_table_for_daily():
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 5208.0),
        _bar("SPX", "2026-05-08", 5230.0),
    ])
    from newsparser.mcp_server import market_query
    out = _call(market_query, instruments=["SPX"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "SPX" in out
    assert "5230" in out or "5,230" in out
    assert "2026-05-07" in out and "2026-05-08" in out


def test_market_query_handles_no_data():
    from newsparser.mcp_server import market_query
    out = _call(market_query, instruments=["SPX"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "no data" in out.lower()


def test_market_query_unknown_alias():
    from newsparser.mcp_server import market_query
    out = _call(market_query, instruments=["XYZ"], start="2026-05-07", end="2026-05-08", freq="1d")
    assert "unknown" in out.lower() or "XYZ" in out


def test_market_query_hourly_uses_intraday_table():
    ts = datetime(2026, 5, 9, 3, 0, tzinfo=timezone.utc).isoformat()
    store.upsert_intraday([{"instrument": "SPX", "ts": ts,
                            "open": 5230.0, "high": 5232.0, "low": 5228.0,
                            "close": 5230.5, "volume": 1000}])
    from newsparser.mcp_server import market_query
    out = _call(market_query, instruments=["SPX"], start="2026-05-09", end="2026-05-09", freq="1h")
    assert "5230.5" in out or "5,230.5" in out
