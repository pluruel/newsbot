import asyncio
import os

from telegram import Bot


def send_message(text: str) -> None:
    """Send a message to the configured chat. Blocks until sent."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    async def _send() -> None:
        async with Bot(token) as bot:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    asyncio.run(_send())
