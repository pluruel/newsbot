"""External ignore list: entity names and free-text storylines the bot should
exclude from cycle analysis, the graph, and Telegram.

Stored as a markdown table in ``workspace/me/ignore.md`` (human- and
bot-editable, same tier as manifesto/interests), parsed tolerantly like
``collector/sources.py``. No database — the file is the only persistent state.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from newsparser._mdtable import is_separator, parse_rows, split_row
from newsparser.paths import workspace_dir

VALID_KINDS = ("entity", "storyline")


@dataclass
class IgnoreEntry:
    kind: str            # "entity" | "storyline"
    target: str
    added: date | None = None
    note: str = ""


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def _matches_target(target: str, haystack: str) -> bool:
    """True if casefolded ``target`` occurs in casefolded ``haystack``.

    ASCII targets match on whole-token boundaries so short ones like ``ai`` /
    ``meta`` don't hit ``openai`` / ``metaverse`` (or ``오픈ai``); targets that
    contain non-ASCII (e.g. Korean) keep plain substring matching, since Korean
    attaches particles without a word boundary."""
    if target.isascii():
        return re.search(rf"(?<!\w){re.escape(target)}(?!\w)", haystack) is not None
    return target in haystack


class IgnoreList:
    def __init__(self, entries: list[IgnoreEntry]):
        self.entries = entries

    def entity_names(self) -> set[str]:
        return {e.target.casefold() for e in self.entries
                if e.kind == "entity" and e.target}

    def storylines(self) -> list[str]:
        return [e.target for e in self.entries
                if e.kind == "storyline" and e.target]

    def _all_targets_cf(self) -> list[str]:
        return [e.target.casefold() for e in self.entries if e.target]

    def matches(self, text: str) -> bool:
        """True if any ignore target (entity or storyline) matches ``text``.
        Deterministic backstop for the Telegram render; semantic storyline
        exclusion is the cycle.md (SOFT) instruction's job. See
        ``_matches_target`` for the word-boundary vs substring rule."""
        if not text:
            return False
        t = text.casefold()
        return any(_matches_target(target, t) for target in self._all_targets_cf())

    def matches_entity(self, name: str, aliases: list[str]) -> bool:
        """True if any entity-kind target matches the entity name or one of its
        aliases. Used to drop graph entities/relations. See ``_matches_target``
        for the word-boundary vs substring rule."""
        targets = self.entity_names()
        if not targets:
            return False
        haystacks = [name.casefold()] + [a.casefold() for a in aliases]
        return any(_matches_target(target, h) for target in targets for h in haystacks)


def _workspace(workspace: Path | str | None = None) -> Path:
    if workspace is not None:
        return Path(workspace)
    return workspace_dir()


def load_ignore(workspace: Path | str | None = None) -> IgnoreList:
    path = _workspace(workspace) / "me" / "ignore.md"
    if not path.exists():
        return IgnoreList([])

    entries: list[IgnoreEntry] = []
    for row in parse_rows(path.read_text(encoding="utf-8")):
        kind = (row.get("종류") or row.get("kind") or "").strip().lower()
        target = (row.get("대상") or row.get("target") or "").strip()
        if kind not in VALID_KINDS or not target:
            continue
        entries.append(IgnoreEntry(
            kind=kind,
            target=target,
            added=_parse_date(row.get("추가일") or row.get("added") or ""),
            note=(row.get("메모") or row.get("note") or "").strip(),
        ))
    return IgnoreList(entries)


def format_list(ignore: IgnoreList, today: date) -> str:
    if not ignore.entries:
        return "무시 목록이 비어 있음"
    lines = [f"무시 목록 ({len(ignore.entries)}건)"]
    for e in ignore.entries:
        if e.added is None:
            age = "추가일 미상"
        else:
            age = f"{(today - e.added).days}일 경과"
        lines.append(f"• [{e.kind}] {e.target} — {age}")
    return "\n".join(lines)


# --- writers -------------------------------------------------------------
# The bot edits this list on the user's behalf ("무시: X"), and it used to do so
# with the Edit tool on the raw markdown table. That put three things in the
# model's hands that belong in code: the `종류` value (an out-of-vocabulary one
# makes load_ignore skip the row *silently*, so the user is told it was ignored
# while nothing filters), the KST date stamp, and the table syntax itself.
# These writers are what the read_ignore/add_ignore/remove_ignore MCP tools call.

_KST = ZoneInfo("Asia/Seoul")

_HEADER_LINE = "| 종류 | 대상 | 추가일 | 메모 |"
_SEPARATOR_LINE = "|------|------|--------|------|"
_TARGET_KEYS = ("대상", "target")


def ignore_path(workspace: Path | str | None = None) -> Path:
    """Location of the ignore table."""
    return _workspace(workspace) / "me" / "ignore.md"


def _clean_cell(value: str) -> str:
    """Collapse whitespace and reject the pipe, which would split the cell."""
    cleaned = " ".join((value or "").split())
    if "|" in cleaned:
        raise ValueError("'|' 문자는 표를 깨뜨려서 쓸 수 없다")
    return cleaned


def _target_column(header_cells: list[str]) -> int:
    """Index of the target column, so a reordered/renamed header still works."""
    lowered = [h.strip().lower() for h in header_cells]
    for key in _TARGET_KEYS:
        if key in lowered:
            return lowered.index(key)
    return 1  # 종류 | 대상 | ... — the documented order


def add_entry(kind: str, target: str, note: str = "",
              workspace: Path | str | None = None,
              today: date | None = None) -> IgnoreEntry:
    """Append one row to the ignore table. Returns the entry that was written.

    Raises ValueError on an unknown kind, a blank target, or a duplicate —
    callers surface the message to the user rather than reporting success.
    """
    kind = (kind or "").strip().lower()
    if kind not in VALID_KINDS:
        raise ValueError(f"종류는 {' 또는 '.join(VALID_KINDS)} 여야 한다 (받은 값: {kind or '빈 값'})")
    target = _clean_cell(target)
    if not target:
        raise ValueError("대상이 비어 있다")
    note = _clean_cell(note)

    if any(e.target.casefold() == target.casefold()
           for e in load_ignore(workspace).entries):
        raise ValueError(f"이미 무시 목록에 있다: {target}")

    stamp = today or datetime.now(_KST).date()
    entry = IgnoreEntry(kind=kind, target=target, added=stamp, note=note)
    row = f"| {kind} | {target} | {stamp.isoformat()} | {note} |"

    path = ignore_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    last_table_line = max(
        (i for i, line in enumerate(lines) if line.strip().startswith("|")),
        default=None,
    )
    if last_table_line is None:
        # No table yet (or no file) — create one, keeping any prose above it.
        if lines and lines[-1].strip():
            lines.append("")
        lines += [_HEADER_LINE, _SEPARATOR_LINE, row]
    else:
        lines.insert(last_table_line + 1, row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entry


def remove_entry(target: str, workspace: Path | str | None = None) -> int:
    """Drop every row whose target equals ``target`` (casefolded). Returns the
    number of rows removed; 0 means nothing matched."""
    wanted = _clean_cell(target).casefold()
    if not wanted:
        raise ValueError("대상이 비어 있다")

    path = ignore_path(workspace)
    if not path.exists():
        return 0

    kept: list[str] = []
    removed = 0
    header: list[str] | None = None
    col = 1
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            kept.append(line)
            continue
        cells = split_row(line)
        if header is None:
            header = cells
            col = _target_column(cells)
            kept.append(line)
            continue
        if is_separator(cells):
            kept.append(line)
            continue
        cell = cells[col].strip() if col < len(cells) else ""
        if cell.casefold() == wanted:
            removed += 1
            continue
        kept.append(line)

    if removed:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def main() -> None:
    print(format_list(load_ignore(), date.today()))


if __name__ == "__main__":
    main()
