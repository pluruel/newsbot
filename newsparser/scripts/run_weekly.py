# newsparser/scripts/run_weekly.py
import json
import logging
from datetime import date as _date, datetime, timedelta
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

logger = logging.getLogger(__name__)
_KST = ZoneInfo("Asia/Seoul")
_DEMAND_DAYS = 7
_CATEGORIES = ("tech", "markets")

# The full briefing goes to the file; Telegram only gets the headlines.
WEEKLY_SCHEMA = {
    "type": "object",
    "properties": {
        "briefing": {"type": "string"},
        "headlines": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["briefing", "headlines"],
    "additionalProperties": False,
}


def _build_weekly_prompt(date: str, reports: list[tuple[str, str]], demand: str) -> str:
    """User message for the weekly call. The block names are the contract with
    prompts/weekly.md — the model has no tools, so everything it may use is here."""
    report_blocks = format_report_blocks(reports)
    return (
        f"기준일 {date}\n"
        f"\n"
        f"<reports>\n{report_blocks}\n</reports>\n"
        f"\n"
        f"<demand>\n{demand}\n</demand>"
    )


def main(date: str | None = None) -> None:
    if date is None:
        date = datetime.now(_KST).strftime("%Y-%m-%d")
    workspace = ensure_workspace()
    end = _date.fromisoformat(date)
    start = end - timedelta(days=_DEMAND_DAYS - 1)
    reports = reports_between(workspace, _CATEGORIES, start, end)
    # With nothing to summarize the model would invent a briefing from the
    # demand digest alone — send nothing rather than that.
    if not reports:
        logger.warning("weekly %s: no reports between %s and %s — skipping",
                       date, start, end)
        return
    # What the user actually asked about this week, for the "관심 주제" section
    # (the call has no MCP/DB access — see scheduler/demand.py).
    demand = build_demand_digest(date, _DEMAND_DAYS)
    prompt = _build_weekly_prompt(date, reports, demand)

    # Failures propagate to the JobManager, which notifies ❌ (or 🛑 on kill) —
    # swallowing them here would make the job look successful.
    raw = ask_claude(prompt, system_prompt("weekly"),
                     timeout=1500, output_schema=WEEKLY_SCHEMA)
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise ClaudeError(f"weekly: invalid JSON from model: {raw[:200]!r}") from exc

    # The model has no tools: Python writes the file.
    out = workspace / "cycles" / "weekly" / f"{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result["briefing"], encoding="utf-8")

    headlines = [h.strip().lstrip("•").strip() for h in result["headlines"]]
    headlines = [h for h in headlines if h]
    if headlines:
        send_long_message("[WEEKLY] " + "\n".join("• " + h for h in headlines))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
