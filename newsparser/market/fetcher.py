import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

TICKERS: dict[str, str] = {
    "SPX":    "^GSPC",
    "NDX":    "^IXIC",
    "KOSPI":  "^KS11",
    "USDKRW": "KRW=X",
    "USDJPY": "JPY=X",
    "DXY":    "DX-Y.NYB",
    "VIX":    "^VIX",
    "TNX":    "^TNX",
}

_RETRIES = 3
_BACKOFF_BASE = 1.0   # seconds


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _call_with_retry(fn, label: str) -> Any:
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == _RETRIES - 1:
                break
            wait = _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.25)
            logger.warning("%s attempt %d failed (%s); sleeping %.2fs",
                           label, attempt + 1, exc, wait)
            _sleep(wait)
    logger.warning("%s gave up after %d attempts: %s", label, _RETRIES, last_exc)
    return None


def _df_to_daily_bars(alias: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for idx, row in df.iterrows():
        dt = idx.date() if hasattr(idx, "date") else idx
        bars.append({
            "instrument": alias,
            "date": dt.isoformat() if isinstance(dt, date) else str(dt),
            "open": round(float(row["Open"]), 4) if pd.notna(row["Open"]) else None,
            "high": round(float(row["High"]), 4) if pd.notna(row["High"]) else None,
            "low":  round(float(row["Low"]),  4) if pd.notna(row["Low"])  else None,
            "close": round(float(row["Close"]), 4) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        })
    return bars


def _df_to_intraday_bars(alias: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for idx, row in df.iterrows():
        ts = idx
        if not isinstance(ts, datetime):
            ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        bars.append({
            "instrument": alias,
            "ts": ts.isoformat(),
            "open": round(float(row["Open"]), 4) if pd.notna(row["Open"]) else None,
            "high": round(float(row["High"]), 4) if pd.notna(row["High"]) else None,
            "low":  round(float(row["Low"]),  4) if pd.notna(row["Low"])  else None,
            "close": round(float(row["Close"]), 4) if pd.notna(row["Close"]) else None,
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
        })
    return bars


def fetch_daily(alias: str, start: date, end: date) -> list[dict]:
    symbol = TICKERS.get(alias)
    if symbol is None:
        logger.warning("Unknown alias: %s", alias)
        return []

    # yfinance's `end` is exclusive; bump by one day so `end` is included.
    end_excl = end + timedelta(days=1)

    def call() -> pd.DataFrame:
        return yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=end_excl.isoformat(),
            interval="1d",
            auto_adjust=False,
        )

    df = _call_with_retry(call, f"fetch_daily {alias}")
    return _df_to_daily_bars(alias, df)


def fetch_intraday_hourly(alias: str, start: datetime, end: datetime) -> list[dict]:
    symbol = TICKERS.get(alias)
    if symbol is None:
        logger.warning("Unknown alias: %s", alias)
        return []

    def call() -> pd.DataFrame:
        return yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1h",
            auto_adjust=False,
        )

    df = _call_with_retry(call, f"fetch_intraday_hourly {alias}")
    return _df_to_intraday_bars(alias, df)
