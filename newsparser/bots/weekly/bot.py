from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_weekly import main as _run_weekly

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    date = datetime.now(_KST).strftime("%Y-%m-%d")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /weekly 시작: {date}")
    await _run_weekly(date)
    if ctx.message:
        await ctx.telegram.send("✅ Weekly 완료")


BOT = Bot(
    name="weekly",
    triggers=[
        Cron("0 9 * * 1", tz="Asia/Seoul"),
        TelegramMatch(r"^/weekly\b"),
    ],
    run=run,
)
