from pathlib import Path

from newsparser.paths import workspace_dir
from newsparser.store.sqlite import get_unprocessed


def build_input_file(slot: str, category: str,
                     articles: list[dict] | None = None) -> Path:
    """Write input.md for Claude from `articles`, falling back to every
    unprocessed article for `category`. Returns the file path. Each article gets
    an [A001]-style index and an explicit GUID line so Claude can cite source
    articles via `src:A001,A007` in graph block relations.

    run_cycle.py passes the list it already claimed (and wrote {slot}-guids.txt
    from) rather than letting this re-query: a second get_unprocessed() can pick
    up articles the poller inserted in between, which would index articles that
    the guids file never marks processed.
    """
    workspace = workspace_dir()
    if articles is None:
        articles = get_unprocessed(category=category)

    lines = [
        f"# Input {slot} KST [{category}]",
        f"## Collected Articles ({len(articles)} total)",
    ]
    for i, a in enumerate(articles, start=1):
        index = f"A{i:03d}"
        body = (a["body"] or "").replace("\n", "\n  ")
        lines += [
            f"\n### [{index}] [{a['source']}] {a['title']}",
            f"- URL: {a['url']}",
            f"- GUID: {a['guid']}",
            f"- Published: {a['published'] or 'unknown'}",
            f"- Body:\n  {body}",
        ]

    path = workspace / "input" / category / f"{slot}-input.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
