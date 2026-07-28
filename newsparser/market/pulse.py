"""Intraday volatility alerts: flag an unusual bar, attach the headlines that
might explain it.

Thresholding is two-sided on purpose, calibrated against 60 days of real 15m
bars for all eight instruments:

* **z-score alone over-fires on 24h instruments.** USDKRW/USDJPY/DXY print ~85
  bars a day against an index's ~25, and thin overnight sessions make a 0.03%
  move look extreme relative to its neighbours. z>3.0 alone fires 7.2×/day
  across the eight, over half of it FX noise.
* **An absolute floor alone misses regime shifts** — a fixed percentage that
  suits a calm week screams all day in a volatile one.

Requiring both (z > Z_THRESHOLD *and* |return| ≥ the instrument's rolling p99)
lands at ~3.4 alerts/day across all eight. A 60-minute cooldown was measured too
and only removed a further 0.3/day — the p99 floor already collapses the
clustering that a cooldown exists to suppress — so it is deliberately omitted.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from newsparser.market import fetcher, headlines, store
from newsparser.market.snapshot import DISPLAY, _fmt_close

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

INTERVAL = "15m"
INTERVAL_MINUTES = 15
Z_THRESHOLD = 3.0
FLOOR_QUANTILE = 0.99
# Trailing bars for the z-score baseline and for the absolute floor. The floor
# needs the longer window: a p99 estimated from 100 samples is one order
# statistic and jumps around.
BASELINE_BARS = 100
FLOOR_BARS = 500
# How much history to pull each refresh. 5d comfortably re-covers a weekend gap
# while staying far inside yfinance's ~60-day limit for sub-hourly bars.
FETCH_PERIOD = "5d"
# Cold start: 5d only yields ~120 bars for an index, and a p99 estimated from
# 120 samples is a noisy order statistic — the floor would sit wherever that
# window's worst bar happened to land. Pull the provider's full sub-hourly
# history once so the floor starts out well-estimated. 60d is yfinance's limit
# for intervals under 1h.
BACKFILL_PERIOD = "60d"
# A bar is only judged once it has closed. The grace period keeps a bar that
# closed moments ago from being read while the provider is still filling it.
BAR_CLOSE_GRACE_S = 30
# A bar is only alertable while it is still news. After poller downtime, a
# weekend, or a first-deploy backfill the newest closed bar can be hours or
# days old; alerting on it then reads as a live move (the message shows HH:MM
# with no date) and attaches headlines from the entire gap.
MAX_ALERT_AGE_MIN = 60
# Cold start: with fewer bars than this the rolling statistics are meaningless.
MIN_BARS = BASELINE_BARS + 1


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile. Avoids a numpy import on the poller's hot path."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


# Aliases whose BACKFILL_PERIOD pull has been attempted this process. One shot
# each: a symbol yfinance no longer serves (Yahoo does rename tickers) would
# otherwise stay under FLOOR_BARS forever and drag the poll loop back into the
# 60d download every tick. If the one attempt fails transiently the alias still
# gets FETCH_PERIOD refreshes each tick — the floor is just noisier until
# enough bars accumulate (or the process restarts and retries the backfill).
_BACKFILL_ATTEMPTED: set[str] = set()


def refresh(aliases: list[str] | None = None) -> int:
    """Pull the latest bars for every instrument in one request and store them.

    An instrument still short of a full floor window — in practice the first
    run after a deploy, or one offline long enough to age out of the store —
    gets one widened BACKFILL_PERIOD pull; everything else fetches
    FETCH_PERIOD.
    """
    aliases = aliases or list(fetcher.TICKERS)
    thin = [a for a in aliases
            if a not in _BACKFILL_ATTEMPTED
            and store.count_intraday(a, INTERVAL) < FLOOR_BARS]
    total = 0
    if thin:
        logger.info("pulse: backfilling %s (%s) — %d instrument(s) below %d bars",
                    INTERVAL, BACKFILL_PERIOD, len(thin), FLOOR_BARS)
        _BACKFILL_ATTEMPTED.update(thin)
        batch = fetcher.fetch_intraday_batch(INTERVAL, period=BACKFILL_PERIOD,
                                             aliases=thin)
        for bars in batch.values():
            total += store.upsert_intraday(bars, interval=INTERVAL)
    fresh = [a for a in aliases if a not in thin]
    if fresh:
        batch = fetcher.fetch_intraday_batch(INTERVAL, period=FETCH_PERIOD,
                                             aliases=fresh)
        for bars in batch.values():
            total += store.upsert_intraday(bars, interval=INTERVAL)
    return total


def _closed_bars(alias: str, now: datetime) -> list[dict]:
    """Stored bars for `alias` that have finished forming, oldest-first.

    yfinance returns the in-progress bar too, and its close is wherever the
    price happens to sit mid-interval — judging it would fire on a move that
    has not happened yet and then re-fire when the bar actually closes.

    Non-positive closes are provider glitches (Yahoo emits occasional zero-price
    rows — yfinance's repair= flag exists for them), not prices: kept, a single
    0.0 close would fire a nonsense ~-100% alert and then poison the p99 floor
    for the next ~FLOOR_BARS bars.
    """
    cutoff = now - timedelta(minutes=INTERVAL_MINUTES,
                             seconds=BAR_CLOSE_GRACE_S)
    bars = store.get_intraday_tail(alias, INTERVAL, FLOOR_BARS + 1)
    return [b for b in bars
            if b["close"] is not None and b["close"] > 0
            and datetime.fromisoformat(b["ts"]) <= cutoff]


def detect(alias: str, now: datetime | None = None) -> dict | None:
    """Return the alert payload for `alias`'s newest closed bar, or None.

    None covers every non-event: not enough history, a flat bar, a market that
    has not printed since the last check, a bar that already alerted, and a
    newest bar too old to be news (MAX_ALERT_AGE_MIN).
    """
    now = now or datetime.now(timezone.utc)
    bars = _closed_bars(alias, now)
    if len(bars) < MIN_BARS:
        return None

    latest = bars[-1]
    bar_end = datetime.fromisoformat(latest["ts"]) + timedelta(minutes=INTERVAL_MINUTES)
    if now - bar_end > timedelta(minutes=MAX_ALERT_AGE_MIN):
        return None
    if store.pulse_exists(alias, INTERVAL, latest["ts"]):
        return None

    # _closed_bars guarantees positive closes, so every adjacent pair yields a
    # return and returns[-1] is always the latest bar's move.
    closes = [b["close"] for b in bars]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100
               for i in range(1, len(closes))]

    delta = returns[-1]
    history = [abs(r) for r in returns[:-1]]
    baseline = history[-BASELINE_BARS:]
    sd = _stdev(baseline)
    if sd == 0:
        return None
    z = (abs(delta) - _mean(baseline)) / sd
    floor = _quantile(history[-FLOOR_BARS:], FLOOR_QUANTILE)

    if z <= Z_THRESHOLD or abs(delta) < floor:
        return None

    return {
        "instrument": alias,
        "ts": latest["ts"],
        "delta_pct": delta,
        "z_score": z,
        "floor_pct": floor,
        "prev_close": closes[-2],
        "close": latest["close"],
    }


def _delta_label(alias: str, delta: float, prev_close: float, close: float) -> str:
    """TNX quotes a yield, so a percentage change of it reads as nonsense
    ("금리 -3.2%"). Show it the way snapshot.py does: percentage points."""
    if alias == "TNX":
        diff = close - prev_close
        return f"{diff:+.2f}%p"
    return f"{delta:+.2f}%"


def _window_label(bar_start: datetime) -> str:
    start_kst = bar_start.astimezone(_KST)
    end_kst = start_kst + timedelta(minutes=INTERVAL_MINUTES)
    return f"{start_kst:%H:%M}~{end_kst:%H:%M}"


def build_message(hit: dict, picks: list[dict]) -> str:
    alias = hit["instrument"]
    bar_start = datetime.fromisoformat(hit["ts"])
    delta = _delta_label(alias, hit["delta_pct"], hit["prev_close"], hit["close"])
    arrow = "📈" if hit["delta_pct"] > 0 else "📉"

    lines = [
        f"{arrow} {DISPLAY.get(alias, alias)} {INTERVAL_MINUTES}분 {delta} (z={hit['z_score']:.1f})",
        f"{_fmt_close(alias, hit['prev_close'])} → {_fmt_close(alias, hit['close'])}"
        f" · {_window_label(bar_start)} KST",
    ]
    if picks:
        lines += ["", "연관 가능"]
        for a in picks:
            lines.append(f"· {a['title']}")
            lines.append(f"  {a['source']}")
    else:
        lines += ["", "연관 헤드라인 없음"]
    return "\n".join(lines)


def check(aliases: list[str] | None = None, now: datetime | None = None) -> list[str]:
    """One full pass: refresh bars, detect, attach headlines, record.

    Returns the messages to deliver. Recording happens here rather than at the
    send site so a Telegram failure cannot replay the same alert on the next
    tick. Per-instrument errors are contained — one bad symbol must not stop the
    other seven.
    """
    now = now or datetime.now(timezone.utc)
    try:
        refresh(aliases)
    except Exception as exc:
        logger.warning("pulse refresh failed: %s", exc)
        return []

    messages: list[str] = []
    for alias in (aliases or list(fetcher.TICKERS)):
        try:
            hit = detect(alias, now=now)
            if hit is None:
                continue
            bar_start = datetime.fromisoformat(hit["ts"])
            delta = _delta_label(alias, hit["delta_pct"], hit["prev_close"], hit["close"])
            picks = headlines.select(
                DISPLAY.get(alias, alias), f"{INTERVAL_MINUTES}분", delta,
                _window_label(bar_start), headlines.candidates(bar_start, now=now),
            )
            store.record_pulse(
                instrument=alias, interval=INTERVAL, ts=hit["ts"],
                delta_pct=hit["delta_pct"], z_score=hit["z_score"],
                floor_pct=hit["floor_pct"], guids=[a["guid"] for a in picks],
            )
            logger.info("pulse: %s %s delta=%.3f%% z=%.2f picks=%d",
                        alias, hit["ts"], hit["delta_pct"], hit["z_score"], len(picks))
            messages.append(build_message(hit, picks))
        except Exception as exc:
            logger.warning("pulse check failed for %s: %s", alias, exc)
    return messages
