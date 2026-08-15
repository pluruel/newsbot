"""Entry point: run the RSS polling loop indefinitely."""
import logging
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

from newsparser.store.sqlite import (
    init_db, get_recent, get_untriaged, hourly_counts, mark_alerted, update_triage,
)
from newsparser.triage import triage_article
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
# Seeded at startup from per-source medians so a restart doesn't reset every
# source to detect_spike's 5.0 default — busy sources tripped false spikes
# during the ~20min the EMA needed to converge.
BASELINE_SEED_DAYS = 14
# Triage runs last in each poll pass (alerts must not wait behind Haiku calls)
# and is bounded twice: by row count, and by wall-clock — 120 rows at the
# usual ~1.5s/call fits a 300s interval, but a degraded API at the 15s call
# timeout would otherwise hold one pass for 30 minutes. Leftovers just wait
# for the next pass; the cycle-time backstop catches anything still untriaged.
TRIAGE_MAX_PER_PASS = int(os.environ.get("TRIAGE_MAX_PER_PASS", "120"))
TRIAGE_TIME_BUDGET_S = int(os.environ.get("TRIAGE_TIME_BUDGET_S", "180"))
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


def _seed_baseline() -> None:
    """현재 시각(UTC 시간대 버킷) 기준, 소스별 최근 N일 기사 수의 중앙값으로 BASELINE을 시딩한다.

    시간대별 중앙값인 이유: 뉴스 유입은 시간대 편차가 커서 전체 평균으로 시딩하면
    한산한 시간대 재시작 시 과대평가되고, 평균은 과거 스파이크에 오염된다.
    """
    hour = datetime.now(timezone.utc).strftime("%H")
    by_source: dict[str, list[int]] = defaultdict(list)
    for r in hourly_counts(BASELINE_SEED_DAYS):
        if r["hour"] == hour:
            by_source[r["source"]].append(r["n"])
    for source, counts in by_source.items():
        counts += [0] * (BASELINE_SEED_DAYS - len(counts))  # 기사 없던 날 = 0
        # floor 1.0: median 0이면 임계가 0이 되어 기사 1건에도 스파이크가 뜬다
        BASELINE[source] = max(statistics.median(counts), 1.0)
    logger.info("Seeded spike baseline for %d sources (hour=%sZ)", len(BASELINE), hour)


def _triage_pass() -> None:
    """Tag untriaged articles with (category, bucket, salience) via Haiku.

    Fail-open at every level: a failed or unparseable call leaves the row
    bucket-NULL (retried next pass, scored DEFAULT_SCORE at selection), and
    the caller wraps the whole pass so triage can never take down ingest."""
    rows = get_untriaged(limit=TRIAGE_MAX_PER_PASS)
    if not rows:
        return
    deadline = time.monotonic() + TRIAGE_TIME_BUDGET_S
    done = 0
    for row in rows:
        if time.monotonic() > deadline:
            logger.warning("triage pass hit %ds budget after %d/%d rows",
                           TRIAGE_TIME_BUDGET_S, done, len(rows))
            break
        result = triage_article(row["title"], row["body"], category_hint=row["category"])
        if result is not None:
            update_triage(row["guid"], result.category, result.bucket, result.salience)
        done += 1
    logger.info("Triage pass: %d/%d rows tagged", done, len(rows))


def run() -> None:
    init_db()
    _seed_baseline()
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

        # 트리아지 태깅 (알림 처리 뒤 — Haiku 지연이 breaking 감지를 막지 않게)
        try:
            _triage_pass()
        except Exception as exc:
            logger.error("Triage pass failed: %s", exc, exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
