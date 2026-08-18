"""Vertex AI (Gemini) path — used only when a chat message carries a YouTube link.

Claude cannot watch a video; Gemini takes a YouTube URL directly as a content
part, so that one case routes here instead of through the tracker. Everything
else in the system stays on the Claude paths.

Auth is a GCP service-account key at ``gcp-key.json`` in the project root
(``GCP_KEY_FILE`` overrides). No key, no YouTube analysis — the caller reports
the error rather than silently answering without having seen the video.
"""
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent

DEFAULT_MODEL = "gemini-3.7-flash"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
# Vertex serves the Gemini 3 family from the multi-region "global" endpoint.
_DEFAULT_LOCATION = "global"
# TelegramSender truncates at 4096 chars, so an over-long summary loses its tail
# with no error. Ask for less than that rather than relying on the model.
_MAX_CHARS = 3000

_client: Any | None = None
_client_lock = threading.Lock()

# Both answer paths (this one and the tracker's Claude prompt) send their text
# straight to Telegram, which renders no markdown. One constant so the two
# paths cannot drift into different voices.
PLAIN_KOREAN_STYLE = (
    "답변은 사용자에게 존댓말로 쓴다. 반말·평어체는 쓰지 않는다.\n"
    "형식은 평문 대화체 문단으로만 한다. 마크다운 금지: 헤더(#), "
    "볼드(**), 불릿(-/*), 표, 수평선(---) 모두 쓰지 않는다. "
    "섹션 구분은 빈 줄로만 한다."
)

# Normalising every link shape to one watch URL means youtu.be / shorts / live
# links work without betting on the API accepting each variant. Ids are exactly
# 11 chars of [A-Za-z0-9_-]; anchoring on that length keeps trailing punctuation
# ("…watch?v=abc123DEF45.") out of the id and lets "watch?app=desktop&v=ID" match.
_YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:\S*?&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
    r"(?:[?&]\S*)?"
)


def find_youtube_url(text: str) -> tuple[str, str] | None:
    """(watch URL, the rest of the message) for the first YouTube link, else None.

    This is the whole routing decision for the chat path. The remainder is what
    the user typed around the link ("3분대 발언 위주로"), which steers the summary.
    """
    match = _YOUTUBE_RE.search(text)
    if match is None:
        return None
    url = f"https://www.youtube.com/watch?v={match.group(1)}"
    rest = " ".join((text[: match.start()] + " " + text[match.end():]).split())
    return url, rest.strip(" ()[]<>")


class GeminiError(RuntimeError):
    pass


def key_path() -> Path:
    """Service-account key location — ``$GCP_KEY_FILE`` or ``gcp-key.json``."""
    override = os.environ.get("GCP_KEY_FILE")
    return Path(override) if override else _PROJECT_ROOT / "gcp-key.json"


def credentials_available() -> bool:
    return key_path().is_file()


def _build_client() -> Any:
    # Imported lazily: without this, a host that never uses the YouTube path
    # would still need google-genai installed just to import the tracker bot.
    try:
        from google.genai import Client
        from google.oauth2 import service_account
    except ImportError as exc:
        raise GeminiError(f"google-genai가 설치되어 있지 않습니다 ({exc})") from exc

    path = key_path()
    if not path.is_file():
        raise GeminiError(f"GCP 키 파일이 없습니다: {path}")

    try:
        project_id = json.loads(path.read_text()).get("project_id")
    except (OSError, json.JSONDecodeError) as exc:
        raise GeminiError(f"GCP 키 파일을 읽을 수 없습니다: {exc}") from exc

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or project_id
    if not project:
        raise GeminiError(f"GCP 키에 project_id가 없습니다: {path}")

    try:
        credentials = service_account.Credentials.from_service_account_file(
            str(path), scopes=_SCOPES
        )
    except Exception as exc:
        raise GeminiError(f"GCP 자격증명 로드 실패: {exc}") from exc

    return Client(
        vertexai=True,
        credentials=credentials,
        project=project,
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", _DEFAULT_LOCATION),
    )


def _get_client() -> Any:
    """Built once and shared across PTB worker threads, as in claude/haiku.py."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = _build_client()
    return _client


def reset_client() -> None:
    """Drop the cached client so the next call re-reads key file and environment."""
    global _client
    with _client_lock:
        _client = None


_SYSTEM_PROMPT = (
    "너는 시장·기술 뉴스를 다루는 개인 인텔리전스 어시스턴트다. "
    "사용자가 보낸 유튜브 영상을 직접 보고 내용을 정리해 전달한다.\n\n"
    "근거 규칙:\n"
    "- 영상에서 실제로 보고 들은 내용만 쓴다. 일반 지식이나 배경 추측으로 빈틈을 메우지 않는다.\n"
    "- 수치, 티커, 날짜, 고유명사, 발언은 영상에 나온 그대로 정확히 옮긴다. "
    "임의로 반올림하지 않는다.\n"
    "- 영어 발화는 자연스러운 한국어로 옮기되 티커와 영어 고유명사는 원문 그대로 둔다.\n"
    "- 화자의 주장과 네 해석을 섞지 않는다. 해석을 덧붙일 때는 그것이 해석임을 밝힌다.\n\n"
    f"길이는 {_MAX_CHARS}자 이내로 맞춘다.\n\n"
    f"{PLAIN_KOREAN_STYLE}"
)


def summarize_youtube(url: str, instruction: str = "", timeout: float = 300.0) -> str:
    """Watch `url` and return a plain-text Korean summary.

    `instruction` is whatever the user typed around the link — it steers the
    summary ("3분대 발언 위주로"). Empty means a general summary.

    Uses the SDK's interactions API, whose VideoContent takes a YouTube watch
    URL directly (no mime type, no upload). Public videos only — the model
    cannot open private or unlisted ones.

    Raises GeminiError on missing credentials or any API failure.
    """
    from google.genai.interactions import TextContent, VideoContent

    client = _get_client()
    ask = (
        "이 영상의 내용을 정리해줘. 무엇에 대한 영상인지, 화자가 내세우는 핵심 주장과 "
        "결론이 무엇인지, 그 근거로 제시한 수치·사례가 무엇인지 짚어줘."
    )
    if instruction:
        ask += f"\n\n사용자 요청: {instruction}"

    try:
        interaction = client.interactions.create(
            model=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
            input=[TextContent(text=ask), VideoContent(uri=url)],
            system_instruction=_SYSTEM_PROMPT,
            timeout=timeout,
        )
    except Exception as exc:
        raise GeminiError(f"Gemini 호출 실패: {type(exc).__name__}: {exc}") from exc

    text = (interaction.output_text or "").strip()
    if not text:
        # Blocked, or the video was unreachable — either way there is no summary,
        # and returning "" would be delivered to the user as an empty reply.
        detail = interaction.errors or interaction.status
        raise GeminiError(
            f"Gemini가 빈 응답을 반환했습니다 (영상이 비공개·연령제한이거나 응답이 차단됨). "
            f"url={url} detail={detail}"
        )
    return text
