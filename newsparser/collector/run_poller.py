"""Entry point: run the RSS polling loop indefinitely."""
import logging
import os
import time
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

from newsparser.store.sqlite import init_db, get_recent
from newsparser.collector.sources import load_sources
from newsparser.collector.poller import poll_all
from newsparser.collector.alert import detect_convergence, detect_spike
from newsparser.bot.sender import send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "600"))
BASELINE: dict[str, float] = {}  # accumulated at runtime


def run() -> None:
    init_db()
    sources = load_sources()
    logger.info("Loaded %d sources. Poll interval: %ds", len(sources), POLL_INTERVAL)

    while True:
        new_articles = poll_all(sources)
        if new_articles:
            logger.info("Fetched %d new articles", len(new_articles))

        recent = get_recent(minutes=60)

        # 크로스소스 수렴 감지
        clusters = detect_convergence(recent)
        for cluster in clusters:
            titles = " / ".join(a["title"][:40] for a in cluster[:3])
            sources_str = ", ".join(sorted({a["source"] for a in cluster}))
            msg = f"⚡ Breaking ({sources_str})\n{titles}"
            logger.warning("Breaking detected: %s", titles)
            send_message(msg)

        # 볼륨 스파이크 감지
        spiking = detect_spike(recent, BASELINE)
        for source in spiking:
            msg = f"📈 Volume spike: {source}"
            logger.warning("Spike: %s", source)
            send_message(msg)

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
