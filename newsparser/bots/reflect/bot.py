from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_reflect import main as _run_reflect

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    date = datetime.now(_KST).strftime("%Y-%m-%d")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /reflect 시작: {date}")
    await ctx.run_in_thread(_run_reflect, date)
    if ctx.message:
        await ctx.telegram.send("✅ Reflect 완료")


BOT = Bot(
    name="reflect",
    triggers=[
        Cron("0 21 * * 0", tz="Asia/Seoul"),
        TelegramMatch(r"^/reflect\b"),
    ],
    run=run,
)
