from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from newsparser.market import pulse, store


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    monkeypatch.setattr(pulse, "_BACKFILL_ATTEMPTED", set())
    store.init_market_db()


NOW = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


# The last bar closes at NOW - 1min: past the BAR_CLOSE_GRACE_S settle window,
# so detect() treats it as complete.
CLOSED = NOW - timedelta(minutes=1)


def _bars(alias: str, closes: list[float], end: datetime = CLOSED) -> list[dict]:
    """Bars laid out so the last one closes exactly at `end`."""
    out = []
    for i, close in enumerate(closes):
        ts = end - timedelta(minutes=pulse.INTERVAL_MINUTES * (len(closes) - i))
        out.append({"instrument": alias, "ts": ts.isoformat(), "open": close,
                    "high": close, "low": close, "close": close, "volume": 0})
    return out


def _calm_then(jump_pct: float, n: int = 300) -> list[float]:
    """A flat-ish series (tiny alternating wiggle) ending in one big move."""
    closes = [100.0]
    for i in range(n):
        closes.append(closes[-1] * (1.001 if i % 2 else 0.999))
    closes.append(closes[-1] * (1 + jump_pct / 100))
    return closes


def _store(alias: str, closes: list[float], end: datetime = CLOSED) -> None:
    store.upsert_intraday(_bars(alias, closes, end), interval=pulse.INTERVAL)


def test_detect_fires_on_outlier_bar():
    _store("KOSPI", _calm_then(5.0))
    hit = pulse.detect("KOSPI", now=NOW)
    assert hit is not None
    assert hit["instrument"] == "KOSPI"
    assert hit["delta_pct"] == pytest.approx(5.0, abs=0.01)
    assert hit["z_score"] > pulse.Z_THRESHOLD


def test_detect_ignores_ordinary_bar():
    _store("KOSPI", _calm_then(0.1))
    assert pulse.detect("KOSPI", now=NOW) is None


def test_detect_needs_minimum_history():
    _store("SPX", [100.0 + i * 0.01 for i in range(pulse.MIN_BARS - 5)])
    assert pulse.detect("SPX", now=NOW) is None


def test_detect_skips_bar_still_forming():
    # Last bar opened 5 minutes ago — inside the 15m interval, so not closed.
    _store("KOSPI", _calm_then(5.0), end=NOW + timedelta(minutes=10))
    assert pulse.detect("KOSPI", now=NOW) is None


def test_detect_skips_stale_bar():
    """After downtime or a weekend the newest closed bar can be hours old —
    alerting on it then would read as a live move with today's headlines."""
    _store("KOSPI", _calm_then(5.0),
           end=NOW - timedelta(minutes=pulse.MAX_ALERT_AGE_MIN + 20))
    assert pulse.detect("KOSPI", now=NOW) is None


def test_detect_ignores_zero_close_glitch_as_latest_bar():
    """A zero-price provider glitch must not fire a ~-100% alert."""
    _store("KOSPI", _calm_then(0.1) + [0.0])
    assert pulse.detect("KOSPI", now=NOW) is None


def test_zero_close_glitch_in_history_does_not_poison_stats():
    """One 0.0 bar mid-series used to inject a -100% return into the rolling
    stats, inflating the p99 floor enough to suppress real alerts."""
    closes = _calm_then(5.0)
    closes[50] = 0.0
    _store("KOSPI", closes)
    hit = pulse.detect("KOSPI", now=NOW)
    assert hit is not None
    assert hit["delta_pct"] == pytest.approx(5.0, abs=0.01)


def test_detect_does_not_refire_recorded_bar():
    _store("KOSPI", _calm_then(5.0))
    hit = pulse.detect("KOSPI", now=NOW)
    assert hit is not None
    store.record_pulse(instrument="KOSPI", interval=pulse.INTERVAL, ts=hit["ts"],
                       delta_pct=hit["delta_pct"], z_score=hit["z_score"],
                       floor_pct=hit["floor_pct"], guids=["g1"])
    assert pulse.detect("KOSPI", now=NOW) is None


def test_floor_suppresses_statistically_odd_but_tiny_move():
    """A dead-flat series makes any wiggle a huge z-score; the p99 floor is what
    keeps thin overnight FX sessions from alerting on 0.01% moves."""
    closes = [100.0] * 300 + [100.001]
    _store("USDJPY", closes)
    hit = pulse.detect("USDJPY", now=NOW)
    assert hit is None


def test_check_records_and_builds_message():
    _store("KOSPI", _calm_then(5.0))
    with patch.object(pulse, "refresh", return_value=0), \
         patch.object(pulse.headlines, "candidates", return_value=[]), \
         patch.object(pulse.headlines, "select", return_value=[]):
        msgs = pulse.check(aliases=["KOSPI"], now=NOW)
    assert len(msgs) == 1
    assert "KOSPI" in msgs[0]
    assert "연관 헤드라인 없음" in msgs[0]
    # A second pass must stay silent even though the bar is unchanged.
    with patch.object(pulse, "refresh", return_value=0):
        assert pulse.check(aliases=["KOSPI"], now=NOW) == []


def test_check_attaches_selected_headlines():
    _store("KOSPI", _calm_then(-5.0))
    picks = [{"guid": "g1", "title": "한은 총재 금리 발언", "source": "연합인포맥스"}]
    with patch.object(pulse, "refresh", return_value=0), \
         patch.object(pulse.headlines, "candidates", return_value=picks), \
         patch.object(pulse.headlines, "select", return_value=picks):
        msgs = pulse.check(aliases=["KOSPI"], now=NOW)
    assert "한은 총재 금리 발언" in msgs[0]
    assert "연합인포맥스" in msgs[0]
    assert "📉" in msgs[0]
    rows = store.get_intraday_tail("KOSPI", pulse.INTERVAL, 1)
    assert store.pulse_exists("KOSPI", pulse.INTERVAL, rows[0]["ts"])


def test_check_isolates_per_instrument_failure():
    _store("KOSPI", _calm_then(5.0))
    with patch.object(pulse, "refresh", return_value=0), \
         patch.object(pulse, "detect", side_effect=[RuntimeError("boom"), None]):
        assert pulse.check(aliases=["SPX", "KOSPI"], now=NOW) == []


def test_tnx_delta_rendered_in_percentage_points():
    hit = {"instrument": "TNX", "ts": NOW.isoformat(), "delta_pct": -3.2,
           "z_score": 3.5, "floor_pct": 0.3, "prev_close": 4.20, "close": 4.06}
    msg = pulse.build_message(hit, [])
    assert "-0.14%p" in msg
    assert "-3.20%" not in msg


def test_refresh_stores_batch_under_the_right_interval():
    bars = {"SPX": _bars("SPX", [100.0, 101.0])}
    with patch.object(pulse.fetcher, "fetch_intraday_batch", return_value=bars):
        assert pulse.refresh(["SPX"]) == 2
    assert len(store.get_intraday_tail("SPX", pulse.INTERVAL, 10)) == 2
    assert store.get_intraday_tail("SPX", "1h", 10) == []


def test_refresh_backfills_only_thin_instruments():
    """The 60d pull must not include instruments that already hold a full floor
    window — they get the regular 5d fetch."""
    _store("KOSPI", [100.0] * (pulse.FLOOR_BARS + 1))
    with patch.object(pulse.fetcher, "fetch_intraday_batch", return_value={}) as fib:
        pulse.refresh(["KOSPI", "SPX"])
    calls = {c.kwargs["period"]: c.kwargs["aliases"] for c in fib.call_args_list}
    assert calls == {pulse.BACKFILL_PERIOD: ["SPX"],
                     pulse.FETCH_PERIOD: ["KOSPI"]}


def test_refresh_attempts_backfill_only_once_per_process():
    """A symbol yfinance never returns (rename/delisting) stays below
    FLOOR_BARS forever; without the attempt memory every 300s tick would re-run
    the full 60d download."""
    with patch.object(pulse.fetcher, "fetch_intraday_batch", return_value={}) as fib:
        pulse.refresh(["SPX"])
        pulse.refresh(["SPX"])
    periods = [c.kwargs["period"] for c in fib.call_args_list]
    assert periods == [pulse.BACKFILL_PERIOD, pulse.FETCH_PERIOD]
