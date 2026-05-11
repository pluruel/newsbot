"""Manual smoke test for newsparser/market/fetcher.py.

Run after fetcher changes to confirm yfinance still returns sensible data
for each tracked alias. Not exercised by CI.

Usage:
    .venv/bin/python scripts/smoke_market.py
"""
from datetime import date, datetime, timedelta, timezone
import sys

from newsparser.market import fetcher


def main() -> int:
    today = date.today()
    yesterday = today - timedelta(days=1)
    rc = 0
    for alias in fetcher.TICKERS:
        bars = fetcher.fetch_daily(alias, yesterday - timedelta(days=5), today)
        if bars:
            print(f"  ✓ {alias}: {len(bars)} daily bars, latest close={bars[-1]['close']}")
        else:
            print(f"  ✗ {alias}: no daily bars returned")
            rc = 1

    print("Intraday smoke for SPX (last 2 hours UTC):")
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(hours=2)
    bars = fetcher.fetch_intraday_hourly("SPX", start_utc, end_utc)
    print(f"  SPX intraday: {len(bars)} bars")

    return rc


if __name__ == "__main__":
    sys.exit(main())
