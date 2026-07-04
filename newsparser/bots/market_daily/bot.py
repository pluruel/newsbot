from newsparser.bots import Bot, Cron, Context
from newsparser.scripts.fetch_market_daily import main as _run_market


async def run(ctx: Context) -> None:
    await ctx.run_in_thread(_run_market)
    if ctx.telegram.chat_id:
        await ctx.telegram.send("✅ Market daily 완료")


BOT = Bot(
    name="market_daily",
    triggers=[Cron("30 7 * * *", tz="Asia/Seoul")],
    run=run,
)
