from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_cycle import main as _run_cycle

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    slot = datetime.now(_KST).strftime("%Y-%m-%d-%H")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /cycle 시작: {slot}")
    await ctx.run_in_thread(_run_cycle, slot)
    if ctx.message:
        await ctx.telegram.send("✅ Cycle 완료")


BOT = Bot(
    name="cycle",
    triggers=[
        Cron("0 12,18,0,6 * * *", tz="Asia/Seoul"),
        TelegramMatch(r"^/cycle\b"),
    ],
    run=run,
)
