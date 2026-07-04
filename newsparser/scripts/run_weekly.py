# newsparser/scripts/run_weekly.py
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from newsparser.bot.sender import send_long_message
from newsparser.claude.runner import run_claude
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)
_KST = ZoneInfo("Asia/Seoul")


def main(date: str | None = None) -> None:
    if date is None:
        date = datetime.now(_KST).strftime("%Y-%m-%d")
    ensure_workspace()
    # Failures propagate to the JobManager, which notifies ❌ (or 🛑 on kill) —
    # swallowing them here would make the job look successful.
    stdout = run_claude(f"/weekly {date}")
    if stdout.strip():
        send_long_message(f"[WEEKLY] {stdout}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
