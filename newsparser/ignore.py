"""External ignore list: entity names and free-text storylines the bot should
exclude from cycle analysis, the graph, and Telegram.

Stored as a markdown table in ``workspace/me/ignore.md`` (human- and
bot-editable, same tier as manifesto/interests), parsed tolerantly like
``collector/sources.py``. No database — the file is the only persistent state.
"""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from newsparser._mdtable import is_separator, split_row

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
        """True if any ignore target (entity or storyline) appears in ``text``
        as a casefold substring. Deterministic backstop for the Telegram render;
        semantic storyline exclusion is the cycle.md (SOFT) instruction's job."""
        if not text:
            return False
        t = text.casefold()
        return any(target in t for target in self._all_targets_cf())

    def matches_entity(self, name: str, aliases: list[str]) -> bool:
        """True if any entity-kind target is a casefold substring of the entity
        name or one of its aliases. Used to drop graph entities/relations."""
        targets = self.entity_names()
        if not targets:
            return False
        haystacks = [name.casefold()] + [a.casefold() for a in aliases]
        return any(target in h for target in targets for h in haystacks)


def _workspace(workspace: Path | str | None = None) -> Path:
    if workspace is not None:
        return Path(workspace)
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def load_ignore(workspace: Path | str | None = None) -> IgnoreList:
    path = _workspace(workspace) / "me" / "ignore.md"
    if not path.exists():
        return IgnoreList([])
    text = path.read_text(encoding="utf-8")

    header: list[str] | None = None
    entries: list[IgnoreEntry] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = split_row(line)
        if header is None:
            header = [h.lower() for h in cells]
            continue
        if is_separator(cells):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        row = dict(zip(header, cells))

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


def main() -> None:
    print(format_list(load_ignore(), date.today()))


if __name__ == "__main__":
    main()
