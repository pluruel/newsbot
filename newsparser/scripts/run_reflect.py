# newsparser/scripts/run_reflect.py
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from newsparser.bot.sender import send_long_message
from newsparser.claude.policy import TAINTED_FILE_TOOLS
from newsparser.claude.runner import run_claude
from newsparser.scheduler.demand import write_demand_digest
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)
_KST = ZoneInfo("Asia/Seoul")
_DEMAND_DAYS = 14


def main(date: str | None = None) -> None:
    if date is None:
        date = datetime.now(_KST).strftime("%Y-%m-%d")
    workspace = ensure_workspace()
    # Snapshot the user's actual query demand into a file the /reflect spec reads
    # (the run has no MCP/DB access — see scheduler/demand.py).
    write_demand_digest(workspace, date, days=_DEMAND_DAYS)
    # Failures propagate to the JobManager, which notifies ❌ (or 🛑 on kill) —
    # swallowing them here would make the job look successful.
    # Cycle reports are news-derived (taint propagates) — file tools only.
    stdout = run_claude(f"/reflect {date}",
                        allowed_tools=TAINTED_FILE_TOOLS, permission_mode="default")
    if stdout.strip():
        send_long_message(f"[REFLECT] {stdout}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
