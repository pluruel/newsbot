"""Entry point: run APScheduler with all recurring jobs."""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from newsparser.scheduler.cycle import run_cycle
from newsparser.scheduler.morning import run_morning

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# KST = UTC+9
TIMEZONE = "Asia/Seoul"


def _cycle_job() -> None:
    slot = datetime.now().strftime("%Y-%m-%d-%H")
    logger.info("Starting cycle: %s", slot)
    run_cycle(slot)


def _morning_job() -> None:
    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info("Starting morning brief: %s", date_str)
    run_morning(date_str)


def start() -> None:
    scheduler = BlockingScheduler(timezone=TIMEZONE)

    # 00:00, 06:00, 12:00, 18:00 KST
    scheduler.add_job(_cycle_job, CronTrigger(hour="0,6,12,18", minute=0, timezone=TIMEZONE))

    # 07:00 KST
    scheduler.add_job(_morning_job, CronTrigger(hour=7, minute=0, timezone=TIMEZONE))

    logger.info("Scheduler started. Jobs: %s", [j.id for j in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    start()
