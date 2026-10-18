# newsparser/scripts/run_reflect.py
import json
import logging
import re
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from newsparser.bot.sender import send_long_message
from newsparser.claude.api import ask_claude
from newsparser.claude.runner import ClaudeError
from newsparser.prompts import system_prompt
from newsparser.scheduler.demand import build_demand_digest
from newsparser.scheduler.reports import format_report_blocks, reports_between
from newsparser.scheduler.workspace import ensure_workspace
from newsparser import triage

logger = logging.getLogger(__name__)
_KST = ZoneInfo("Asia/Seoul")
_DEMAND_DAYS = 14
_CATEGORIES = ("tech", "markets")
_RESULT_KEYS = ("interests_tech", "interests_markets",
                "triage_weights_tech", "triage_weights_markets", "changes")

# A profile is a markdown table; a reply without a single table row is a
# truncated or prose answer that would wipe the user's profile if written.
_TABLE_ROW = re.compile(r"^\|.*\|\s*$", re.MULTILINE)


def reflect_schema() -> dict:
    """Output schema for the reflect call. The weight objects list every bucket
    explicitly, so the model can neither omit nor invent one."""
    def weights(category: str) -> dict:
        names = triage.bucket_names(category)
        return {
            "type": "object",
            "properties": {name: {"type": "number"} for name in names},
            "required": names,
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "interests_tech": {"type": "string"},
            "interests_markets": {"type": "string"},
            "triage_weights_tech": weights("tech"),
            "triage_weights_markets": weights("markets"),
            "changes": {"type": "array", "items": {"type": "string"}},
        },
        "required": list(_RESULT_KEYS),
        "additionalProperties": False,
    }


def _build_reflect_prompt(date: str, profiles: dict[str, str],
                          reports: list[tuple[str, str]], demand: str) -> str:
    """User message for the reflect call. The block names are the contract with
    prompts/reflect.md — the model has no tools, so everything it may use is here."""
    axis = json.dumps(triage.axis_dict(), ensure_ascii=False, indent=2)
    return (
        f"기준일 {date}\n"
        f"\n"
        f"<interests_tech>\n{profiles['tech']}\n</interests_tech>\n"
        f"\n"
        f"<interests_markets>\n{profiles['markets']}\n</interests_markets>\n"
        f"\n"
        f"<reports>\n{format_report_blocks(reports)}\n</reports>\n"
        f"\n"
        f"<demand>\n{demand}\n</demand>\n"
        f"\n"
        f"<bucket_axis>\n{axis}\n</bucket_axis>"
    )


def _parse_result(raw: str) -> dict:
    """Parse and shape-check the reply before anything is written."""
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise ClaudeError(f"reflect: invalid JSON from model: {raw[:200]!r}") from exc
    if not isinstance(result, dict) or any(k not in result for k in _RESULT_KEYS):
        raise ClaudeError(f"reflect: invalid output shape from model: {raw[:200]!r}")
    if not isinstance(result["changes"], list):
        raise ClaudeError(f"reflect: invalid 'changes' from model: {raw[:200]!r}")
    return result


def _write_profile(path: Path, category: str, content) -> None:
    if not isinstance(content, str) or not content.strip() or not _TABLE_ROW.search(content):
        logger.warning("reflect: %s profile empty or not a table; keeping %s", category, path)
        return
    path.write_text(content, encoding="utf-8")


def _write_weights(category: str, raw) -> None:
    try:
        weights = triage.sanitize_weights(category, raw)
    except ValueError as exc:
        logger.warning("reflect: invalid %s triage weights (%s); keeping existing file", category, exc)
        return
    path = triage.weights_path(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")


def main(date: str | None = None) -> None:
    if date is None:
        date = datetime.now(_KST).strftime("%Y-%m-%d")
    workspace = ensure_workspace()
    me = workspace / "me"
    profile_paths = {cat: me / f"interests_{cat}.md" for cat in _CATEGORIES}
    profiles = {cat: path.read_text(encoding="utf-8") for cat, path in profile_paths.items()}
    end = _date.fromisoformat(date)
    start = end - timedelta(days=_DEMAND_DAYS - 1)
    reports = reports_between(workspace, _CATEGORIES, start, end)
    # What the user actually asked about in this period, as the demand signal
    # (the call has no MCP/DB access — see scheduler/demand.py).
    demand = build_demand_digest(date, _DEMAND_DAYS)
    prompt = _build_reflect_prompt(date, profiles, reports, demand)

    # Failures propagate to the JobManager, which notifies ❌ (or 🛑 on kill) —
    # swallowing them here would make the job look successful.
    raw = ask_claude(prompt, system_prompt("reflect"),
                     timeout=1500, output_schema=reflect_schema())
    result = _parse_result(raw)

    # The model has no tools: Python writes the files. Each profile and each
    # weights file is independent — one bad piece must not discard the rest.
    for cat in _CATEGORIES:
        _write_profile(profile_paths[cat], cat, result[f"interests_{cat}"])
        _write_weights(cat, result[f"triage_weights_{cat}"])

    changes = [str(c) for c in result["changes"]]
    send_long_message("[REFLECT] " + ("\n".join(changes) if changes else "변경 없음"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
