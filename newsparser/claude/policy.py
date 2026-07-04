"""Per-task tool policies for headless claude runs.

Principle: tools follow input taint, not model tier. Any run whose prompt or
input files contain scraped web content (article bodies) or content derived
from it (cycle reports, graph entity names) gets an explicit allowlist under
permission_mode="default" — tool calls outside the list are auto-denied (the
model sees an error result and continues; denials surface via runner logging
and jobs.json). Trusted-input runs (tracker: telegram gated to ALLOWED_CHAT_ID)
keep broad permissions at their call site.

See plan-tool-policy.md for the full call-site table.
"""

# reflect/weekly read cycle reports (news-derived): file tools only, no shell.
TAINTED_FILE_TOOLS: list[str] = [
    "Read", "Write", "Edit", "Grep", "Glob", "TodoWrite",
]

# cycle.md instructs exactly two shell commands — whitelist them and nothing else.
CYCLE_TOOLS: list[str] = TAINTED_FILE_TOOLS + [
    "Bash(.venv/bin/python newsparser/scripts/apply_graph.py:*)",
    "Bash(.venv/bin/python newsparser/scripts/mark_processed.py:*)",
]
