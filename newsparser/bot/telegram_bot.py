"""Telegram bot: receive messages and route to dispatcher/tracker."""
import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

from newsparser.bot.dispatcher import classify_message, MessageType
from newsparser.bot.tracker import run_tracker
from newsparser.scheduler.cycle import run_cycle

logger = logging.getLogger(__name__)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)

    allowed = os.environ.get("ALLOWED_CHAT_ID")
    if allowed and chat_id != allowed:
        logger.warning("Unauthorized chat_id %s — ignoring", chat_id)
        return

    msg_type = classify_message(text)

    logger.info("Message [%s] from %s: %s", msg_type.value, chat_id, text[:60])

    kst = ZoneInfo("Asia/Seoul")

    if msg_type == MessageType.SLASH_CYCLE:
        slot = datetime.now(kst).strftime("%Y-%m-%d-%H")
        await update.message.reply_text(f"⚙️ /cycle 시작: {slot}")
        await asyncio.to_thread(run_cycle, slot)
        await update.message.reply_text("✅ Cycle 완료")

    elif msg_type == MessageType.SLASH_WEEKLY:
        await update.message.reply_text("⚙️ /weekly — 미구현 (Plan 5)")

    elif msg_type == MessageType.SLASH_REFLECT:
        await update.message.reply_text("⚙️ /reflect — 미구현 (Plan 5)")

    else:
        await update.message.reply_text("🔍 분석 중...")
        try:
            answer = await asyncio.to_thread(run_tracker, chat_id=chat_id, query=text)
        except Exception as e:
            logger.exception("Tracker failed for query: %s", text[:60])
            await update.message.reply_text(f"❌ 오류: {e}")
            return
        await update.message.reply_text(answer[:4096])


def start() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))
    logger.info("Bot polling started")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start()
