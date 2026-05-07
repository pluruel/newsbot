"""Entry point: run APScheduler with all recurring jobs."""
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from newsparser.scheduler.cycle import run_cycle
from newsparser.store.sqlite import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# KST = UTC+9
TIMEZONE = "Asia/Seoul"
KST = ZoneInfo(TIMEZONE)


def _cycle_job() -> None:
    slot = datetime.now(KST).strftime("%Y-%m-%d-%H")
    logger.info("Starting cycle: %s", slot)
    run_cycle(slot)


def start() -> None:
    init_db()
    scheduler = BlockingScheduler(timezone=TIMEZONE)

    # 00:00, 06:00, 12:00, 18:00 KST
    scheduler.add_job(_cycle_job, CronTrigger(hour="0,6,12,18", minute=0, timezone=TIMEZONE))

    logger.info("Scheduler started. Jobs: %s", [j.id for j in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    start()
