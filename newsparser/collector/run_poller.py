"""Entry point: run the RSS polling loop indefinitely."""
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

from newsparser.store.sqlite import init_db, get_recent, mark_alerted
from newsparser.collector.sources import load_sources
from newsparser.collector.poller import poll_all
from newsparser.collector.alert import detect_convergence, detect_spike
from newsparser.market import pulse
from newsparser.market.store import init_market_db
from newsparser.bot.sender import send_long_message, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# python-telegram-bot이 HTTP 요청 URL(토큰 포함)을 INFO로 출력하므로 억제
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# 300s, not 600s: the volatility alerts below judge 15-minute bars, and the
# headlines that explain a move have to be in the DB by the time the bar closes.
# Measured cost of one pass is ~6s (20 feeds in 5.8s + ~0.1s per new article),
# and halving the interval does not change the body-scraping total — only the
# RSS fetches double.
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
SPIKE_COOLDOWN_HOURS = 1
# The spike baseline decays per *tick*, so its wall-clock memory depends on the
# poll interval — derive the per-tick α from a fixed half-life instead of
# hard-coding it. α=0.3 was tuned at the original 600s cadence (half-life
# ≈19.4min ≈ this constant); keeping α fixed while halving the interval would
# halve the half-life, letting the baseline absorb a ~35min ramp fast enough to
# suppress the spike alert it used to fire.
BASELINE_HALFLIFE_S = 1200
BASELINE_ALPHA = 1 - 0.5 ** (POLL_INTERVAL / BASELINE_HALFLIFE_S)
BASELINE: dict[str, float] = {}  # accumulated at runtime
_spike_alerted_at: dict[str, datetime] = {}
MARKET_PULSE_ENABLED = os.environ.get("MARKET_PULSE", "1") != "0"


def _send(msg: str) -> None:
    """Send without a parse mode: every alert this loop composes (breaking,
    spike, pulse) embeds verbatim article titles, and a single `<` or `&` in
    one makes Telegram reject the whole message as broken HTML. None of them
    use markup, so plain text is both safer and sufficient."""
    try:
        send_message(msg, parse_mode=None)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


def _send_plain(msg: str) -> None:
    """Like _send, but chunked past Telegram's 4096-char limit — pulse alerts
    can carry several full headlines."""
    try:
        send_long_message(msg)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


def _market_pulse() -> None:
    """Fire volatility alerts for any 15m bar that just closed unusually.

    Lives in the poller rather than in a bots/*/bot.py cron for two reasons: it
    runs right after new articles land, so the headline window is as fresh as it
    can be; and every cron bot goes through the JobManager, whose recent-job
    list caps at 10 (jobs.py:26) — a 5-minute bot would evict the cycle/weekly
    history that job_status exists to show.
    """
    for msg in pulse.check():
        logger.warning("Market pulse: %s", msg.splitlines()[0])
        _send_plain(msg)


def run() -> None:
    init_db()
    if MARKET_PULSE_ENABLED:
        init_market_db()
    sources = load_sources()
    logger.info("Loaded %d sources. Poll interval: %ds. Market pulse: %s",
                len(sources), POLL_INTERVAL, "on" if MARKET_PULSE_ENABLED else "off")

    while True:
        new_articles = poll_all(sources)
        if new_articles:
            logger.info("Fetched %d new articles", len(new_articles))

        recent = get_recent(minutes=60)

        # 크로스소스 수렴 감지 (이미 알림 발송한 기사 제외)
        unalerted = [a for a in recent if not a["alerted"]]
        clusters = detect_convergence(unalerted)
        for cluster in clusters:
            titles = " / ".join(a["title"][:40] for a in cluster[:3])
            sources_str = ", ".join(sorted({a["source"] for a in cluster}))
            msg = f"⚡ Breaking ({sources_str})\n{titles}"
            logger.warning("Breaking detected: %s", titles)
            _send(msg)
            for a in cluster:
                mark_alerted(a["guid"])

        # 볼륨 스파이크 감지
        spiking = detect_spike(recent, BASELINE)
        cooldown = timedelta(hours=SPIKE_COOLDOWN_HOURS)
        now = datetime.now(timezone.utc)
        for source in spiking:
            if now - _spike_alerted_at.get(source, datetime.min.replace(tzinfo=timezone.utc)) < cooldown:
                continue
            source_articles = [a for a in recent if a["source"] == source]
            titles = "\n".join(f"· {a['title'][:60]}" for a in source_articles[:3])
            msg = f"📈 Volume spike: {source}\n{titles}"
            logger.warning("Spike: %s", source)
            _send(msg)
            _spike_alerted_at[source] = now

        # 시장 변동성 알림 (기사 수집 직후 — 헤드라인 창이 가장 신선한 시점)
        if MARKET_PULSE_ENABLED:
            try:
                _market_pulse()
            except Exception as exc:
                logger.error("Market pulse failed: %s", exc, exc_info=True)

        # BASELINE 업데이트 (지수 이동 평균, 반감기 BASELINE_HALFLIFE_S)
        counts: dict[str, float] = defaultdict(float)
        for a in recent:
            counts[a["source"]] += 1
        for source, count in counts.items():
            if source in BASELINE:
                BASELINE[source] = (BASELINE[source] * (1 - BASELINE_ALPHA)
                                    + count * BASELINE_ALPHA)
            else:
                BASELINE[source] = count

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
