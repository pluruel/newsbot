# tests/test_cron_state.py
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from newsparser.bots import Cron
from newsparser.bots.core import cron_state

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture(autouse=True)
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "ws"))


def _ts(y, m, d, hh, mm) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=KST).timestamp()


# --- record/load round-trip -------------------------------------------------

def test_last_run_none_when_unrecorded():
    assert cron_state.last_run("market_daily") is None


def test_record_run_round_trip():
    cron_state.record_run("market_daily", when=_ts(2026, 8, 1, 7, 30))
    assert cron_state.last_run("market_daily") == _ts(2026, 8, 1, 7, 30)


def test_record_run_defaults_to_now():
    before = time.time()
    cron_state.record_run("market_daily")
    assert before <= cron_state.last_run("market_daily") <= time.time()


def test_record_run_keeps_other_bots():
    cron_state.record_run("market_daily", when=_ts(2026, 8, 1, 7, 30))
    cron_state.record_run("weekly", when=_ts(2026, 8, 1, 9, 0))
    assert cron_state.load() == {
        "market_daily": _ts(2026, 8, 1, 7, 30),
        "weekly": _ts(2026, 8, 1, 9, 0),
    }


def test_load_tolerates_corrupt_state_file():
    cron_state.record_run("market_daily")
    (cron_state._path()).write_text("{not json")
    assert cron_state.load() == {}


def test_load_drops_non_numeric_entries():
    cron_state._path().parent.mkdir(parents=True, exist_ok=True)
    cron_state._path().write_text('{"a": 1.0, "b": "nope"}')
    assert cron_state.load() == {"a": 1.0}


# --- missed_fire ------------------------------------------------------------

DAILY_0730 = Cron("30 7 * * *", tz="Asia/Seoul")


def test_never_run_counts_as_missed():
    assert cron_state.missed_fire(DAILY_0730, None, now=_ts(2026, 8, 2, 9, 0))


def test_missed_when_dispatcher_was_down_across_fire():
    # ran 07/30 07:30, back up 08/02 09:00 -> 07/31, 08/01, 08/02 fires lost
    assert cron_state.missed_fire(
        DAILY_0730, _ts(2026, 7, 30, 7, 30), now=_ts(2026, 8, 2, 9, 0))


def test_not_missed_when_already_ran_today():
    # ran at today's 07:30; next fire is tomorrow -> nothing to catch up
    assert not cron_state.missed_fire(
        DAILY_0730, _ts(2026, 8, 2, 7, 30), now=_ts(2026, 8, 2, 9, 0))


def test_not_missed_before_todays_fire_time():
    # ran yesterday 07:30, now 06:00 today -> today's fire has not come due yet
    assert not cron_state.missed_fire(
        DAILY_0730, _ts(2026, 8, 1, 7, 30), now=_ts(2026, 8, 2, 6, 0))


def test_missed_exactly_at_fire_time_is_inclusive():
    assert cron_state.missed_fire(
        DAILY_0730, _ts(2026, 8, 1, 7, 30), now=_ts(2026, 8, 2, 7, 30))


def test_restart_within_the_same_minute_is_not_missed():
    # guards the +1s offset: a fire we just ran must not re-report as missed
    ran = _ts(2026, 8, 2, 7, 30)
    assert not cron_state.missed_fire(DAILY_0730, ran, now=ran + 5)


def test_tz_is_honoured():
    # 07:30 KST == 22:30 UTC previous day; under a UTC schedule the same
    # wall-clock window straddles a different fire time
    utc_cron = Cron("30 7 * * *", tz="UTC")
    last = _ts(2026, 8, 2, 7, 30)          # 2026-08-01 22:30 UTC
    now = _ts(2026, 8, 2, 12, 0)           # 2026-08-02 03:00 UTC
    assert not cron_state.missed_fire(utc_cron, last, now=now)
    assert not cron_state.missed_fire(DAILY_0730, last, now=now)


def test_bad_schedule_is_not_reported_as_missed():
    assert not cron_state.missed_fire(Cron("not a cron"), None, now=_ts(2026, 8, 2, 9, 0))
