> Note: Python prepends a `## 카테고리` block to this prompt at runtime, declaring the current category and its scope. Treat that block as the source of truth for which category you're processing.

## /cycle task

1. Read the input file (path appended below).
2. Read the most recent file in `workspace/cycles/` for prior context.
3. Analyze all collected articles:
   - Cross-source dedup: same event from multiple sources → merge.
   - Delta: what is genuinely new vs continuation.
   - Causal threading: link to prior cycles ("third update on Story X").
   - Importance scoring: 0.0–1.0, objective only (no personalization). Reserve 0.8+ for genuine market-moving events.
4. Output the report to stdout in two parts:
   - **Top — Korean digest.** Plain text per the system Style rules. This is what gets sent to Telegram.
   - **Bottom — graph block.** Machine-parseable. The `## Graph updates`, `### Entities`, `### Relations` headers and the `NEW | ... | ...` line shape are required exactly as shown.

### 문체 규칙

리포트 본문은 문어체로 작성한다. 구어 표현(~해요, ~거든요, ~네요, ~인데요)은 사용하지 않는다.
문장 말미는 명사형 종결(~함, ~됨) 또는 서술형 종결(~다, ~이다, ~하였다)로 통일한다.
헤드라인은 핵심 사실만 명사구로 압축한다.

### Output format

```
사이클 YYYY-MM-DD HH:00 KST

새 소식
• (중요도 0.NN) 한 줄 헤드라인. 본문 2–4문장.
  엔티티: ... / 출처: ...
• (중요도 0.NN) ...

이어지는 흐름
• (중요도 0.NN) 직전 사이클 대비 새로운 점 한 줄. 필요 시 본문.

조용한 영역
• YYYY-MM-DD-HH 사이클에 예상됐으나 관측되지 않은 ...

오픈 스레드
• ...

## Graph updates
### Entities
- NEW | {Label} | {canonical_name} | aliases: [{alias1}, {alias2}] | metadata: {key: "val"}
- UPDATE | {Label} | {canonical_name}

### Relations
- NEW | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}]--> {object} | {predicate_text}
- UPDATE | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}]--> {object}
```

Valid Labels: Company, Person, Institution, Event, Indicator, Market, Sector, Policy
Valid Predicates: INFLUENCES, MEMBER_OF, COMPETES_WITH, ANNOUNCED, IMPACTS, CONTRADICTS, FOLLOWS_UP

If a digest section has nothing to report, write `• 없음`. In the graph block, omit empty entries — never fabricate.
