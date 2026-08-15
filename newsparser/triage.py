"""Haiku-backed article triage: coarse bucket + salience, scored against
weekly weights.

The bucket axis is fixed here in code. Per-bucket weights are runtime state
the weekly /reflect job writes to ``workspace/me/triage_weights_{category}.json``
— they are never committed. Missing or unreadable weights fall back to 1.0
per bucket, so ranking degrades to pure salience order and the pipeline never
depends on reflect having run.

Scoring is deliberately split: Haiku judges (bucket, salience) only; the final
score ``weight × salience`` is computed in Python at selection time. The
prompt therefore never changes when weights do, weight updates apply
retroactively to everything already triaged, and a cut can always be
decomposed into "which bucket" vs "how salient" vs "what weight".

Run ``python3 -m newsparser.triage`` to print the bucket axis as JSON — the
/reflect spec reads it from there so the axis has a single source of truth.
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from newsparser.claude.haiku import ask_haiku
from newsparser.claude.runner import ClaudeError
from newsparser.paths import workspace_dir
from newsparser.store.sqlite import article_ts

logger = logging.getLogger(__name__)

# Bucket names are prompt-facing tokens: short, no whitespace, unique across
# categories (the name alone resolves the category). Descriptions exist for
# the Haiku prompt — keep them one line.
BUCKETS: dict[str, tuple[tuple[str, str], ...]] = {
    "markets": (
        ("통화정책", "중앙은행 결정·금리 경로 (Fed·FOMC, 한국은행, ECB·BOJ)"),
        ("채권금리", "국채 금리·수익률곡선·발행/입찰 (미국채, KTB, JGB)"),
        ("물가지표", "인플레이션·고용·성장 등 거시지표 (PCE·CPI, 수출입, GDP)"),
        ("한국증시", "KOSPI·수급·연기금/국민연금·외국인·레버리지/빚투"),
        ("환율", "FX (원달러, 엔, DXY, 캐리 트레이드)"),
        ("지정학통상", "지정학 리스크·무역·관세·제재 (중동, 미중, 미 행정부 정책)"),
        ("에너지원자재", "유가·가스·금속 등 원자재 가격/수급"),
        ("반도체AI산업", "반도체·AI 산업의 시장 영향 (메모리/HBM 가격, 공급망, 산업정책)"),
        ("기업이벤트", "시장 영향이 있는 대형 IPO·M&A·실적·크레딧 이벤트"),
        ("부동산가계", "부동산 정책·가계부채·주담대 (개별 매물/단지 시세 기사는 노이즈)"),
        ("글로벌매크로", "그 외 해외 경제·EM·유럽/중국 경기"),
        ("기타경제", "위 어디에도 없지만 경제·금융 관련성이 있는 기사"),
    ),
    "tech": (
        ("AI릴리스", "Claude·OpenAI·Gemini 등 모델/API 공식 릴리스·변경사항"),
        ("AI에이전트도구", "코딩 에이전트·개발도구 경쟁·MCP 생태계·에이전트 보안"),
        ("보안취약점", "CVE·공급망 침해·익스플로잇·인증/암호화 결함"),
        ("AI정책안전", "AI 규제·수출통제·안전/정렬 연구"),
        ("하드웨어", "GPU·메모리 가격/수급, 칩·부품, CUDA 등 인프라"),
        ("플랫폼OS", "OS·플랫폼·ML 프레임워크 업데이트 (Apple, Linux, PyTorch)"),
        ("로봇물리AI", "로봇·피지컬 AI"),
        ("AI비즈니스", "기술 내용 없는 AI 기업 비즈니스 기사 (펀딩·점유율·M&A)"),
        ("기타기술", "위 어디에도 없지만 기술 관련성이 있는 기사"),
    ),
}

# Category-independent: an article that is neither economy nor tech
# (entertainment, sports, lifestyle, local PR, general politics, health...).
NOISE_BUCKET = "노이즈"

# Where an unknown bucket name from the model lands when we at least know the
# category — keeps the row scored instead of retrying it forever.
FALLBACK_BUCKETS = {"markets": "기타경제", "tech": "기타기술"}

_BUCKET_CATEGORY: dict[str, str] = {
    name: cat for cat, defs in BUCKETS.items() for name, _ in defs
}

# Score assigned to rows that never got triaged (Haiku outage, parse failure).
# Fail-open: they stay in the queue at mid rank instead of being dropped.
DEFAULT_SCORE = 0.5

# Articles scoring below this at selection time are retired untriaged-into-cycle
# (recorded, searchable, absorbed by dedup — just never analyzed).
THRESHOLD = float(os.environ.get("TRIAGE_THRESHOLD", "0.2"))

_MAX_TOKENS = 16
_BODY_EXCERPT_CHARS = 500

_SYSTEM_PROMPT = (
    "You are a news triage classifier. Reply with exactly one line: "
    "'<bucket> <salience>' where bucket is one of the given names and "
    "salience is a number between 0 and 1. No explanations."
)


def _bucket_lines() -> str:
    lines = []
    for cat, defs in BUCKETS.items():
        lines.append(f"[{cat}]")
        lines.extend(f"- {name}: {desc}" for name, desc in defs)
    lines.append(f"[공통]\n- {NOISE_BUCKET}: 경제·기술과 무관 (연예·스포츠·라이프스타일·지역행사·일반 정치·건강 등)")
    return "\n".join(lines)


_PROMPT = (
    "다음 기사를 아래 대분류 중 하나로 분류하고, 그 분야 안에서의 사건 비중(salience)을 매겨.\n\n"
    "대분류:\n{buckets}\n\n"
    "salience 기준:\n"
    "- 0.9~1.0: 해당 분야 전체를 움직이는 사건 (정책 결정, 사상 최초/최대, 위기)\n"
    "- 0.6~0.8: 유의미한 새 전개\n"
    "- 0.3~0.5: 루틴 보도, 점진적 후속 기사\n"
    "- 0.0~0.2: 지엽적·사소한 소식\n\n"
    "응답은 정확히 '대분류 숫자' 한 줄 (예: 한국증시 0.7).\n\n"
    "제목: {title}\n"
    "본문 (앞 {n}자): {body}"
)

_RESPONSE_RE = re.compile(r"([A-Za-z가-힣0-9]+)\s+([01]?(?:\.\d+)?)\s*$")


@dataclass
class TriageResult:
    category: str
    bucket: str
    salience: float


def _parse_response(raw: str, category_hint: str | None) -> TriageResult | None:
    m = _RESPONSE_RE.search((raw or "").strip())
    if not m:
        return None
    bucket, salience_s = m.group(1), m.group(2)
    try:
        salience = min(1.0, max(0.0, float(salience_s)))
    except ValueError:
        return None
    if bucket == NOISE_BUCKET:
        # Noise still needs a category so it flows through the per-category
        # cut path instead of sitting category-NULL forever.
        return TriageResult(category_hint or "markets", bucket, salience)
    if bucket in _BUCKET_CATEGORY:
        return TriageResult(_BUCKET_CATEGORY[bucket], bucket, salience)
    if category_hint in FALLBACK_BUCKETS:
        return TriageResult(category_hint, FALLBACK_BUCKETS[category_hint], salience)
    return None


def triage_article(
    title: str, body: str | None, category_hint: str | None = None
) -> TriageResult | None:
    """One Haiku call → (category, bucket, salience). None on failure — the
    caller leaves the row untriaged and a later pass (or the cycle backstop)
    retries it; selection scores it DEFAULT_SCORE meanwhile."""
    prompt = _PROMPT.format(
        buckets=_bucket_lines(),
        title=title,
        n=_BODY_EXCERPT_CHARS,
        body=(body or "")[:_BODY_EXCERPT_CHARS],
    )
    try:
        raw = ask_haiku(prompt, _SYSTEM_PROMPT, _MAX_TOKENS, timeout=15,
                        usage_tag="triage")
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("triage_article failed (%s); leaving untriaged", exc)
        return None
    result = _parse_response(raw, category_hint)
    if result is None:
        logger.warning("triage_article unparseable reply %r; leaving untriaged", raw)
    return result


def weights_path(category: str) -> Path:
    return workspace_dir() / "me" / f"triage_weights_{category}.json"


def load_weights(category: str) -> dict[str, float]:
    """Reflect-written bucket weights; {} (→ every bucket 1.0) when absent
    or unreadable, so a broken file can't stall a cycle."""
    path = weights_path(category)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            str(k): min(1.0, max(0.0, float(v)))
            for k, v in data.items()
            if isinstance(v, (int, float))
        }
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("triage weights unreadable at %s: %s", path, exc)
        return {}


def article_score(row: dict, weights: dict[str, float]) -> float:
    bucket = row.get("bucket")
    salience = row.get("salience")
    if bucket is None or salience is None:
        return DEFAULT_SCORE
    return weights.get(bucket, 1.0) * float(salience)


def select(
    articles: list[dict],
    weights: dict[str, float],
    cap: int,
    threshold: float = THRESHOLD,
) -> tuple[list[dict], list[dict], int]:
    """Split candidates into (selected, cut, n_passed).

    ``cut`` scored below ``threshold`` — retire immediately. Of the rest
    (``n_passed``), the top ``cap`` by (score desc, published desc) are
    ``selected``; the remainder stays pending and competes again next cycle.
    """
    scored = [(article_score(a, weights), a) for a in articles]
    cut = [a for s, a in scored if s < threshold]
    passed = [(s, a) for s, a in scored if s >= threshold]
    # Score desc, ties broken by recency (newest first) via stable sort.
    passed.sort(key=lambda sa: article_ts(sa[1]), reverse=True)
    passed.sort(key=lambda sa: sa[0], reverse=True)
    selected = [a for _, a in passed[:cap]]
    return selected, cut, len(passed)


def axis_dict() -> dict:
    """The bucket axis in the shape the /reflect spec consumes (run_reflect
    snapshots it to workspace/me/triage-buckets.json — the reflect run has no
    Bash, so it can't ask this module directly)."""
    return {
        "buckets": {cat: [name for name, _ in defs] for cat, defs in BUCKETS.items()},
        "noise_bucket": NOISE_BUCKET,
        "weights_files": {cat: str(weights_path(cat)) for cat in BUCKETS},
    }


def write_axis_snapshot(workspace: Path) -> Path:
    path = workspace / "me" / "triage-buckets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(axis_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    print(json.dumps(axis_dict(), ensure_ascii=False, indent=2))
