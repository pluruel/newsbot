import logging
import os
import re
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.triggers.cron import CronTrigger

from newsparser.bots.core.context import Context, TelegramSender
from newsparser.bots.core.jobs import JobManager
from newsparser.bots.core.registry import BotRegistry
from newsparser.bots.core.types import Bot, Cron

load_dotenv()
logger = logging.getLogger(__name__)

_WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
registry = BotRegistry()
jobs = JobManager(_WORKSPACE)


def _make_ctx(bot_name: str, ptb_bot, chat_id: str | None = None, message=None) -> Context:
    return Context(
        bot_name=bot_name,
        workspace=_WORKSPACE,
        telegram=TelegramSender(
            bot=ptb_bot,
            chat_id=chat_id,
            alert_chat_id=os.environ.get("TELEGRAM_ALERT_CHAT_ID", ""),
        ),
        message=message,
    )


async def _run_with_guard(bot: Bot, ctx: Context) -> None:
    try:
        await bot.run(ctx)
    except Exception:
        tb = traceback.format_exc()
        logger.exception("Bot %s failed", bot.name)
        try:
            await ctx.telegram.send(f"❌ {bot.name} 실패\n{tb[-1500:]}")
        except Exception:
            pass


async def _handle_message(update: Update, ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)

    allowed = os.environ.get("ALLOWED_CHAT_ID")
    if allowed and chat_id != allowed:
        logger.warning("Unauthorized chat_id %s — ignoring", chat_id)
        return

    matched: Bot | None = None
    for bot, trigger in registry.telegram_bots():
        if re.search(trigger.pattern, text):
            matched = bot
            break

    if matched is None:
        return

    ctx = _make_ctx(matched.name, ptb_ctx.bot, chat_id=chat_id, message=update.message)
    if matched.background:
        if jobs.start(matched, ctx, trigger="telegram") is None:
            running = jobs.running_for(matched.name)
            elapsed = int(time.time() - running.started_at) // 60 if running else 0
            await ctx.telegram.send(f"⚠️ {matched.name} 이미 실행 중 ({elapsed}분 경과)")
        return
    await _run_with_guard(matched, ctx)


async def _handle_reload(update: Update, ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    allowed = os.environ.get("ALLOWED_CHAT_ID")
    chat_id = str(update.message.chat_id)
    if allowed and chat_id != allowed:
        logger.warning("Unauthorized /reload attempt from %s", chat_id)
        return
    before = set(registry.names())
    job_queue = ptb_ctx.application.job_queue
    registry.load()
    after = set(registry.names())
    # Remove jobs for bots no longer in registry
    for job in job_queue.jobs():
        if job.name in before and job.name not in after:
            job.schedule_removal()
    # Register only new bots' cron jobs
    new_bots = after - before
    for bot, trigger in registry.cron_bots():
        if bot.name in new_bots:
            from apscheduler.triggers.cron import CronTrigger
            job_queue.run_custom(
                callback=_make_cron_callback(bot),
                job_kwargs={"trigger": CronTrigger.from_crontab(trigger.schedule, timezone=trigger.tz)},
                name=bot.name,
            )
    added = sorted(after - before)
    removed = sorted(before - after)
    lines = [f"✅ Reload 완료 — 활성: {sorted(after)}"]
    if added:
        lines.append(f"추가: {added}")
    if removed:
        lines.append(f"제거: {removed}")
    await update.message.reply_text("\n".join(lines))


async def _poll_job_requests(ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick up job requests written by the MCP start_job tool (separate process)
    and launch them as background jobs."""
    for req in jobs.consume_requests():
        name = req["bot"]
        bot = next((b for b in registry.all() if b.name == name), None)
        chat_id = req.get("chat_id")
        ctx = _make_ctx(name, ptb_ctx.bot, chat_id=chat_id)
        if bot is None:
            logger.warning("Job request for unknown bot %r ignored", name)
            await ctx.telegram.send(f"⚠️ 알 수 없는 작업 요청: {name}")
            continue
        if jobs.start(bot, ctx, trigger="mcp") is None:
            await ctx.telegram.send(f"⚠️ {name} 이미 실행 중 — 요청 무시됨")


def _make_cron_callback(bot: Bot):
    async def _cb(ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = _make_ctx(bot.name, ptb_ctx.bot)
        if jobs.start(bot, ctx, trigger="cron") is None:
            logger.warning("Cron trigger skipped — %s already running", bot.name)
    return _cb


def _register_cron_jobs(app: Application) -> None:
    for bot, trigger in registry.cron_bots():
        app.job_queue.run_custom(
            callback=_make_cron_callback(bot),
            job_kwargs={
                "trigger": CronTrigger.from_crontab(trigger.schedule, timezone=trigger.tz)
            },
            name=bot.name,
        )
        logger.info("Registered cron bot: %s  schedule=%s tz=%s", bot.name, trigger.schedule, trigger.tz)


def start() -> None:
    from newsparser.store.sqlite import init_db
    init_db()
    registry.load()
    logger.info("Loaded bots: %s", registry.names())

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = (
        Application.builder()
        .token(token)
        .read_timeout(30)
        .connect_timeout(10)
        .concurrent_updates(True)
        .build()
    )

    _register_cron_jobs(app)
    app.job_queue.run_repeating(_poll_job_requests, interval=3, first=3, name="job-requests")
    app.add_handler(CommandHandler("reload", _handle_reload))
    app.add_handler(MessageHandler(filters.TEXT, _handle_message))

    logger.info("Dispatcher polling")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start()
