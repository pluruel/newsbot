import subprocess

from newsparser.bots import Bot, TelegramMatch, Context
from newsparser.bot.tracker import run_tracker


async def run(ctx: Context) -> None:
    if ctx.message is None:
        return

    text = ctx.message.text.strip()
    chat_id = str(ctx.message.chat_id)

    if text == "/rebuild":
        await ctx.telegram.send("🔨 이미지 빌드 시작. 잠시 후 재연결됩니다.")
        _docker_rebuild()
        return

    await ctx.telegram.send("🔍 분석 중...")
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
