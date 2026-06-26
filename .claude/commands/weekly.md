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
5. Print ONLY 3–5 keyword headlines to stdout — one line each, `• 한 줄 핵심` 형식, 본문 문장 없이. This is forwarded to Telegram; the full briefing stays in the file.
