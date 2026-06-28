"""Tolerant markdown-table cell parsing shared by the ignore list and source
loaders. A data row is ``| a | b | c |``; a separator row is ``|---|:--|``."""


def split_row(line: str) -> list[str]:
    """Split a markdown table row into stripped cells.

    Drops the leading and trailing pipe before splitting so empty cells survive.
    """
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def is_separator(cells: list[str]) -> bool:
    """True if every cell is a non-empty run of ``-``/``:`` (a ``|---|:--|`` row)."""
    return all(set(c) <= set("-:") and c for c in cells)
