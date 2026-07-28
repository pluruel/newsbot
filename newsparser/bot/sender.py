import asyncio
import logging
import os

from telegram import Bot
from telegram.error import NetworkError
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096
CHUNK_SIZE = 4000  # safety margin under 4096
SEND_RETRIES = 3


def _make_bot(token: str) -> Bot:
    return Bot(token, request=HTTPXRequest(
        connect_timeout=10, read_timeout=30, write_timeout=30, pool_timeout=10))


async def _send_with_retry(bot: Bot, chat_id: str, text: str, **kwargs) -> None:
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return
        except NetworkError:
            if attempt == SEND_RETRIES:
                raise
            logger.warning("send_message failed (attempt %d/%d), retrying", attempt, SEND_RETRIES)
            await asyncio.sleep(2 * attempt)


def send_message(text: str, parse_mode: str | None = "HTML") -> None:
    """Send a message to the configured chat. Blocks until sent.

    parse_mode="HTML" suits text we compose; pass None for text carrying
    verbatim scraped content — one unescaped `<` or `&` in a headline and
    Telegram rejects the whole message with 400 Can't parse entities.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    async def _send() -> None:
        async with _make_bot(token) as bot:
            await _send_with_retry(bot, chat_id, text, parse_mode=parse_mode)

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
        async with _make_bot(token) as bot:
            for part in parts:
                await _send_with_retry(bot, chat_id, part)

    asyncio.run(_send())
