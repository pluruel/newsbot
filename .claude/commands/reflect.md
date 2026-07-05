Parse `$ARGUMENTS` as the date string (e.g. `2026-05-08`).

## Task

1. Read current interest profiles: `workspace/me/interests_tech.md` and `workspace/me/interests_markets.md`.
2. List and read cycle reports from the past 14 days in both `workspace/cycles/tech/` and `workspace/cycles/markets/`.
3. Read `workspace/me/interest-demand.md` if present — the user's actual questions this period (the demand signal). Cycles are the supply signal; this is what the user actually cares about.
4. For each profile, update interest_weight and familiarity_weight from both signals:
   - Increase interest_weight for themes that had 0.7+ importance items in cycles.
   - Increase familiarity_weight for themes that appeared in 3+ cycles.
   - Increase interest_weight for themes the user repeatedly asked about in interest-demand.md, even if cycle coverage was low — demand can lead supply.
   - Add new rows for themes that appeared prominently (in cycles OR user questions) but aren't tracked yet.
   - Decrease weights for themes that were predicted but didn't appear (오픈 스레드 entries that resolved silently) AND the user never asked about.
5. Overwrite both files using the same markdown table format.
6. Print one line per changed theme to stdout — `테마명 ↑/↓ (3단어 이내 사유)` 형식, 산문 없이. 변경 없으면 `변경 없음` 한 줄.
