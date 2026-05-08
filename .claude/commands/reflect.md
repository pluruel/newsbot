Parse `$ARGUMENTS` as the date string (e.g. `2026-05-08`).

## Task

1. Read current interest profiles: `workspace/me/interests_tech.md` and `workspace/me/interests_markets.md`.
2. List and read cycle reports from the past 14 days in both `workspace/cycles/tech/` and `workspace/cycles/markets/`.
3. For each profile, update interest_weight and familiarity_weight based on what appeared frequently and with high importance in recent cycles:
   - Increase interest_weight for themes that had 0.7+ importance items.
   - Increase familiarity_weight for themes that appeared in 3+ cycles.
   - Add new rows for themes that appeared prominently but aren't tracked yet.
   - Decrease weights for themes that were predicted but didn't appear (오픈 스레드 entries that resolved silently).
4. Overwrite both files using the same markdown table format.
5. Print a compact diff summary to stdout (which themes changed and by how much).
