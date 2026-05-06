"""Entry point: run the RSS polling loop indefinitely."""
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from newsparser.store.sqlite import init_db, get_recent, mark_alerted
from newsparser.collector.sources import load_sources
from newsparser.collector.poller import poll_all
from newsparser.collector.alert import detect_convergence, detect_spike
from newsparser.bot.sender import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# python-telegram-bot이 HTTP 요청 URL(토큰 포함)을 INFO로 출력하므로 억제
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "600"))
SPIKE_COOLDOWN_HOURS = 1
BASELINE: dict[str, float] = {}  # accumulated at runtime
_spike_alerted_at: dict[str, datetime] = {}


def _send(msg: str) -> None:
    try:
        send_message(msg)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


def run() -> None:
    init_db()
    sources = load_sources()
    logger.info("Loaded %d sources. Poll interval: %ds", len(sources), POLL_INTERVAL)

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
        now = datetime.utcnow()
        for source in spiking:
            if now - _spike_alerted_at.get(source, datetime.min) < cooldown:
                continue
            source_articles = [a for a in recent if a["source"] == source]
            titles = "\n".join(f"· {a['title'][:60]}" for a in source_articles[:3])
            msg = f"📈 Volume spike: {source}\n{titles}"
            logger.warning("Spike: %s", source)
            _send(msg)
            _spike_alerted_at[source] = now

        # BASELINE 업데이트 (지수 이동 평균, α=0.3)
        counts: dict[str, float] = defaultdict(float)
        for a in recent:
            counts[a["source"]] += 1
        for source, count in counts.items():
            if source in BASELINE:
                BASELINE[source] = BASELINE[source] * 0.7 + count * 0.3
            else:
                BASELINE[source] = count

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
