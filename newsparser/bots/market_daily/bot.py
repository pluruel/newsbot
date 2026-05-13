from newsparser.bots import Bot, Cron, Context
from newsparser.scripts.fetch_market_daily import main as _run_market


async def run(ctx: Context) -> None:
    await ctx.run_in_thread(_run_market)


BOT = Bot(
    name="market_daily",
    triggers=[Cron("30 7 * * *", tz="Asia/Seoul")],
    run=run,
)
