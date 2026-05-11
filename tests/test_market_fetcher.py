from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from newsparser.market import fetcher


def _df_daily(rows):
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(df.pop("date"))
    return df


def _df_intraday(rows):
    df = pd.DataFrame(rows)
    df.index = pd.DatetimeIndex(df.pop("ts"))
    return df


def test_tickers_dict_has_all_eight_aliases():
    expected = {"SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"}
    assert set(fetcher.TICKERS.keys()) == expected


def test_fetch_daily_calls_yfinance_and_converts():
    df = _df_daily([
        {"date": "2026-05-07", "Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100},
        {"date": "2026-05-08", "Open": 1.5, "High": 3.0, "Low": 1.0, "Close": 2.0, "Volume": 200},
    ])
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker) as ticker_cls:
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    ticker_cls.assert_called_once_with("^GSPC")
    fake_ticker.history.assert_called_once()
    assert [b["date"] for b in bars] == ["2026-05-07", "2026-05-08"]
    assert bars[0]["instrument"] == "SPX"
    assert bars[1]["close"] == 2.0


def test_fetch_daily_empty_dataframe_returns_empty_list():
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker):
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    assert bars == []


def test_fetch_daily_retries_then_returns_empty():
    fake_ticker = MagicMock()
    fake_ticker.history.side_effect = RuntimeError("yfinance down")
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker), \
         patch.object(fetcher, "_sleep") as sleep_:
        bars = fetcher.fetch_daily("SPX", date(2026, 5, 7), date(2026, 5, 8))
    assert bars == []
    assert fake_ticker.history.call_count == 3
    assert sleep_.call_count >= 2


def test_fetch_intraday_hourly_converts_to_utc_iso():
    df = _df_intraday([
        {"ts": pd.Timestamp("2026-05-09 02:00:00", tz="UTC"),
         "Open": 5230.0, "High": 5231.0, "Low": 5229.0, "Close": 5230.5, "Volume": 1000},
        {"ts": pd.Timestamp("2026-05-09 03:00:00", tz="UTC"),
         "Open": 5230.5, "High": 5232.0, "Low": 5228.0, "Close": 5228.3, "Volume": 1100},
    ])
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = df
    with patch.object(fetcher.yf, "Ticker", return_value=fake_ticker):
        bars = fetcher.fetch_intraday_hourly(
            "SPX",
            datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 9, 4, 0, tzinfo=timezone.utc),
        )
    assert bars[0]["ts"] == "2026-05-09T02:00:00+00:00"
    assert bars[1]["close"] == 5228.3
