from datetime import date, timedelta

from newsparser.market import store
from newsparser.market.fetcher import TICKERS

DISPLAY = {
    "SPX":    "S&P 500",
    "NDX":    "NASDAQ",
    "KOSPI":  "KOSPI",
    "USDKRW": "USD/KRW",
    "USDJPY": "USD/JPY",
    "DXY":    "달러인덱스",
    "VIX":    "VIX",
    "TNX":    "미 10Y",
}

# Display order for the snapshot table
ORDER = ["SPX", "NDX", "KOSPI", "USDKRW", "USDJPY", "DXY", "VIX", "TNX"]


def _fmt_close(alias: str, close: float) -> str:
    if alias == "TNX":
        return f"{close:.2f}%"
    if alias in ("USDKRW", "USDJPY", "DXY", "VIX"):
        return f"{close:,.2f}"
    return f"{close:,.2f}"


def _fmt_pct(prev: float, cur: float, alias: str) -> str:
    if prev == 0 or prev is None or cur is None:
        return "—"
    if alias == "TNX":
        # bps change in absolute terms
        diff = cur - prev
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.2f}"
    pct = (cur - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def build_snapshot_block(at: date) -> str:
    # Fetch up to 10 trading days back per instrument so we can pick the latest two
    lookback = at - timedelta(days=14)
    latest_date: str | None = None
    rows_out: list[str] = []

    for alias in ORDER:
        bars = store.get_daily(alias, lookback, at)
        if len(bars) < 1:
            rows_out.append(f"| {DISPLAY[alias]} | — | — (결측) |")
            continue
        if len(bars) == 1:
            cur = bars[-1]
            rows_out.append(f"| {DISPLAY[alias]} | {_fmt_close(alias, cur['close'])} | — (결측) |")
            latest_date = latest_date or cur["date"]
            continue
        prev, cur = bars[-2], bars[-1]
        rows_out.append(
            f"| {DISPLAY[alias]} | {_fmt_close(alias, cur['close'])} "
            f"| {_fmt_pct(prev['close'], cur['close'], alias)} |"
        )
        if latest_date is None or cur["date"] > latest_date:
            latest_date = cur["date"]

    header_date = latest_date or at.isoformat()
    return "\n".join([
        f"## 시장 스냅샷 ({header_date} 기준 종가)",
        "",
        "| 종목 | 종가 | 일변동 |",
        "|---|---|---|",
        *rows_out,
    ])
