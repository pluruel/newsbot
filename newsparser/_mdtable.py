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


def parse_rows(text: str) -> list[dict[str, str]]:
    """Parse a markdown table body into a list of ``{header: cell}`` dicts.

    The first pipe row is the header (keys lowercased); the separator row is
    skipped; short rows are right-padded with empty cells; lines that are not
    table rows (don't start with ``|``) are ignored. Shared by ``ignore.py`` and
    ``collector/sources.py`` so the tolerant row-iteration lives in one place.
    """
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
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
        rows.append(dict(zip(header, cells)))
    return rows
