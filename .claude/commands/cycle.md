Parse `$ARGUMENTS` as two space-separated tokens: slot (e.g. `2026-05-08-12`) and category (e.g. `tech` or `markets`).

## 카테고리 컨텍스트

**tech**: AI 활용·신규 AI 정보·일반 컴퓨터 기술. 시장 영향·일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마.

**markets**: 시장·매크로·정책·지정학·일반 산업. AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨.

## 사용자 관심사

Read `workspace/me/interests_{category}.md` and use it to weight importance scoring. Higher interest_weight topics deserve more analysis depth.

## Task

1. Read `workspace/input/{category}/{slot}-input.md`.
2. Read the most recent file in `workspace/cycles/{category}/` for prior context (skip if none exist).
3. Analyze all collected articles:
   - Cross-source dedup: same event from multiple sources → merge.
   - Delta: what is genuinely new vs continuation.
   - Causal threading: link to prior cycles ("third update on Story X").
   - Importance scoring: 0.0–1.0, objective only. Reserve 0.8+ for genuine market-moving events.
4. Write the full report (Korean digest + graph block) to `workspace/cycles/{category}/{slot}.md`.
5. Run: `.venv/bin/python newsparser/scripts/apply_graph.py {category} {slot}`
6. Run: `.venv/bin/python newsparser/scripts/mark_processed.py {category} {slot}`

## 문체 규칙

리포트 본문은 문어체로 작성한다. 구어 표현(~해요, ~거든요, ~네요, ~인데요)은 사용하지 않는다.
문장 말미는 명사형 종결(~함, ~됨) 또는 서술형 종결(~다, ~이다, ~하였다)로 통일한다.
헤드라인은 핵심 사실만 명사구로 압축한다.

## Report file format

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
- NEW | {Label} | {canonical_name} | aliases: [{alias1}, {alias2}]
- UPDATE | {Label} | {canonical_name}

### Relations
- NEW | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}]--> {object} | {predicate_text}
- UPDATE | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}]--> {object}
```

Valid Labels: Company, Person, Institution, Event, Indicator, Market, Sector, Policy
Valid Predicates: INFLUENCES, MEMBER_OF, COMPETES_WITH, ANNOUNCED, IMPACTS, CONTRADICTS, FOLLOWS_UP

If a digest section has nothing to report, write `• 없음`. Omit empty graph entries.
