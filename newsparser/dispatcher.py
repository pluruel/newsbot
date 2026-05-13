import logging
import os
import re
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.triggers.cron import CronTrigger

from newsparser.bots.core.context import Context, TelegramSender
from newsparser.bots.core.registry import BotRegistry
from newsparser.bots.core.types import Bot, Cron

load_dotenv()
logger = logging.getLogger(__name__)

_WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
registry = BotRegistry()


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
    await _run_with_guard(matched, ctx)


async def _handle_reload(update: Update, ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    before = set(registry.names())
    job_queue = ptb_ctx.application.job_queue
    for job in job_queue.jobs():
        if job.name in before:
            job.schedule_removal()
    registry.load()
    _register_cron_jobs(ptb_ctx.application)
    after = set(registry.names())
    added = sorted(after - before)
    removed = sorted(before - after)
    lines = [f"✅ Reload 완료 — 활성: {sorted(after)}"]
    if added:
        lines.append(f"추가: {added}")
    if removed:
        lines.append(f"제거: {removed}")
    await update.message.reply_text("\n".join(lines))


def _make_cron_callback(bot: Bot):
    async def _cb(ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = _make_ctx(bot.name, ptb_ctx.bot)
        await _run_with_guard(bot, ctx)
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
        .build()
    )

    _register_cron_jobs(app)
    app.add_handler(CommandHandler("reload", _handle_reload))
    app.add_handler(MessageHandler(filters.TEXT, _handle_message))

    logger.info("Dispatcher polling")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start()
