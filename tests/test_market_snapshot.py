from datetime import date

import pytest

from newsparser.market import store, snapshot


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    store.init_market_db()


def _bar(alias, d, close):
    return {"instrument": alias, "date": d,
            "open": close, "high": close, "low": close, "close": close, "volume": 1}


def test_snapshot_has_header_and_table():
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 5208.0),
        _bar("SPX", "2026-05-08", 5230.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    assert "## 시장 스냅샷" in text
    assert "S&P 500" in text
    assert "5,230" in text or "5230" in text


def test_snapshot_computes_pct_change():
    store.upsert_daily([
        _bar("USDKRW", "2026-05-07", 1370.0),
        _bar("USDKRW", "2026-05-08", 1369.20),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    # (1369.20 - 1370.0) / 1370.0 * 100 ≈ -0.06%
    # Allow the renderer to round; just assert a negative sign and "USD/KRW"
    assert "USD/KRW" in text
    line = next(l for l in text.splitlines() if "USD/KRW" in l)
    assert "-0.0" in line or "-0.1" in line


def test_snapshot_handles_missing_instrument():
    # Only SPX has data; the rest should render as 결측
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 100.0),
        _bar("SPX", "2026-05-08", 102.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 9))
    assert "결측" in text  # at least one missing row rendered
    assert "S&P 500" in text  # SPX row still present


def test_snapshot_uses_most_recent_trading_day_le_at():
    # at=2026-05-15, but latest data is 2026-05-08; should still render
    store.upsert_daily([
        _bar("SPX", "2026-05-07", 100.0),
        _bar("SPX", "2026-05-08", 102.0),
    ])
    text = snapshot.build_snapshot_block(date(2026, 5, 15))
    assert "2026-05-08" in text
