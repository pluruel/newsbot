Parse `$ARGUMENTS` as the date string (e.g. `2026-05-08`).

## Task

1. List files in `workspace/cycles/tech/` and `workspace/cycles/markets/` — find all reports from the past 7 days (filename starts with date in YYYY-MM-DD format, compare to the provided date).
2. Read each of those report files (omit `## Graph updates` blocks — you only need the Korean digest sections).
3. Synthesize a weekly briefing in Korean:
   - Lead with the 3–5 most important developments across both categories.
   - Group by theme, not by category.
   - Include a "시장 vs 기술" synthesis section if notable cross-category patterns emerged.
   - End with "다음 주 주목할 점" — 2–3 forward-looking threads.
4. Write the briefing to `workspace/cycles/weekly/{date}.md`.
5. Print a short summary (3–5 bullet points) to stdout — this is forwarded to Telegram.
