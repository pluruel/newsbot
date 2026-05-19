# newsparser/scripts/run_reflect.py
import asyncio
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


async def main(date: str | None = None) -> None:
    if date is None:
        date = datetime.now(_KST).strftime("%Y-%m-%d")
    ensure_workspace()
    try:
        result = await run_claude(f"/reflect {date}")
        if result.text.strip():
            await asyncio.to_thread(send_long_message, f"[REFLECT] {result.text}")
    except Exception as exc:
        logger.error("Reflect failed: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
