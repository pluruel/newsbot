# Newsparser

Personal market intelligence system. Collects and analyzes news, posts cycle reports to Telegram, and answers queries against a knowledge graph.

---

## Telegram Bot

Just send a message. Claude uses the knowledge graph and recent cycle reports to answer.

### Queries

```
How is Samsung doing lately?
What's the impact of Fed rate decisions on semiconductors?
Compare recent trends for Nvidia and AMD
```

### Interest weights

Tracked for query weighting and graph traversal.

```
Lower my AI interest weight
Add semiconductors theme with interest 0.8
Show my current interests
Show weight comparison        ← configured weights vs actual query frequency
```

### Manifesto

How the system understands your perspective — reflected in tracker tone.

```
Show my manifesto
I'm a semiconductor investor focused on risk — update accordingly
```

### Reset

```
Clear interest events         ← resets weight estimation baseline
Clear conversation history
```

---

## Automated jobs

| Schedule | Action |
|----------|--------|
| Hourly | Collect news |
| 00/06/12/18 KST | Generate cycle report → update graph → post to Telegram |

---

## Architecture

```
Telegram message
    └─ tracker → Claude (sonnet) + MCP
                     ├─ graph_query          3-hop knowledge graph lookup
                     ├─ read_cycle_reports   recent cycle reports
                     ├─ get_interest_weights configured vs estimated comparison
                     ├─ read/write_interests update interest profile
                     ├─ read/write_manifesto update perspective
                     └─ clear_*             reset logs and history
```

---

## Running

```bash
cp .env.example .env
./run.sh
```

Required env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NEO4J_PASSWORD`
