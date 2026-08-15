from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, Context
from newsparser.scripts.run_cycle import main as _run_cycle

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    slot = datetime.now(_KST).strftime("%Y-%m-%d-%H")
    await ctx.run_in_thread(_run_cycle, slot)
    if ctx.telegram.chat_id:
        await ctx.telegram.send("✅ Cycle 완료")


BOT = Bot(
    name="cycle",
    triggers=[Cron("0 0,3,6,9,12,15,18,21 * * *", tz="Asia/Seoul")],
    run=run,
    background=True,
)
