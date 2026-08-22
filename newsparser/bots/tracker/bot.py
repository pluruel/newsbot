import logging
import subprocess

from newsparser.bots import Bot, TelegramMatch, Context
from newsparser.bot.tracker import run_tracker, run_youtube
from newsparser.gemini import GeminiError, find_youtube_url

logger = logging.getLogger(__name__)


async def run(ctx: Context) -> None:
    if ctx.message is None:
        return

    text = ctx.message.text.strip()
    chat_id = str(ctx.message.chat_id)

    if text == "/rebuild":
        await ctx.telegram.send("🔨 이미지 빌드 시작. 잠시 후 재연결됩니다.")
        _docker_rebuild()
        return

    link = find_youtube_url(text)
    if link is not None:
        url, instruction = link
        try:
            answer = await ctx.run_in_thread(
                run_youtube, chat_id=chat_id, query=text,
                url=url, instruction=instruction,
            )
        except GeminiError as exc:
            # No fallback to the tracker: Claude cannot watch the video, so a
            # Claude answer here would be about the link, not its contents.
            logger.warning("youtube summary failed (%s): %s", url, exc)
            answer = f"유튜브 분석에 실패했습니다.\n\n{exc}"
    else:
        answer = await ctx.run_in_thread(run_tracker, chat_id=chat_id, query=text)
    await ctx.telegram.send(answer)


def _docker_rebuild() -> None:
    # start_new_session=True ensures the build survives this container being replaced
    subprocess.Popen(
        ["docker", "compose", "up", "-d", "--build", "dispatcher"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


BOT = Bot(
    name="tracker",
    triggers=[TelegramMatch(r".*")],
    run=run,
)
