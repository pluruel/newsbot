from newsparser.bots import Bot, Cron, Context
from newsparser.scripts.fetch_market_daily import main as _run_market


async def run(ctx: Context) -> None:
    await ctx.run_in_thread(_run_market)
    if ctx.telegram.chat_id:
        await ctx.telegram.send("✅ Market daily 완료")


BOT = Bot(
    name="market_daily",
    # catchup: fetch_market_daily resumes from latest_daily_date, so a run that
    # was missed while the dispatcher was down backfills the gap on restart
    # instead of leaving a hole until the next 07:30.
    triggers=[Cron("30 7 * * *", tz="Asia/Seoul", catchup=True)],
    run=run,
)
