import asyncio
import os

from telegram import Bot

TELEGRAM_LIMIT = 4096
CHUNK_SIZE = 4000  # safety margin under 4096


def send_message(text: str) -> None:
    """Send a message to the configured chat with HTML parse mode. Blocks until sent."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    async def _send() -> None:
        async with Bot(token) as bot:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    asyncio.run(_send())


def _chunk(text: str, size: int) -> list[str]:
    """Split text into <=size chunks, preferring line boundaries."""
    chunks: list[str] = []
    remaining = text
    while len(remaining) > size:
        cut = remaining.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def send_long_message(text: str) -> None:
    """Send plain text, chunking past Telegram's 4096-char limit. Blocks until all sent."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    parts = _chunk(text, CHUNK_SIZE) or [""]

    async def _send() -> None:
        async with Bot(token) as bot:
            for part in parts:
                await bot.send_message(chat_id=chat_id, text=part)

    asyncio.run(_send())
