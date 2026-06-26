Parse `$ARGUMENTS` as two space-separated tokens: slot (e.g. `2026-05-08-12`) and category (e.g. `tech` or `markets`).

## 카테고리 컨텍스트

**tech**: AI 활용·신규 AI 정보·일반 컴퓨터 기술. 시장 영향·일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마.

**markets**: 시장·매크로·정책·지정학·일반 산업. AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨.

## 사용자 관심사

Read `workspace/me/interests_{category}.md` and use it to weight importance scoring. Higher interest_weight topics deserve more analysis depth.

## 시장 스냅샷

입력파일 상단에 `## 시장 스냅샷` 블록이 있다. 보고서의 "새 소식" 첫 단락 또는 lead-in 한 줄에 그 날 시장 상태를 짧게 요약·반영하라. Indicator 엔티티를 라벨링할 때 `canonical_name`은 반드시 다음 별칭 중 하나로 쓴다: `SPX`, `NDX`, `KOSPI`, `USDKRW`, `USDJPY`, `DXY`, `VIX`, `TNX`. 그래프와 가격 DB는 이 별칭으로 연결된다.

## Task

1. Read `workspace/input/{category}/{slot}-input.md`.
2. Read the most recent file in `workspace/cycles/{category}/` for prior context (skip if none exist).
3. Analyze all collected articles:
   - Cross-source dedup: same event from multiple sources → merge.
   - Delta: what is genuinely new vs continuation.
   - Causal threading: link to prior cycles ("third update on Story X").
   - Importance scoring: 0.0–1.0, objective only. Reserve 0.8+ for genuine market-moving events.
   - 각 관계에 대해 그 주장을 뒷받침하는 입력파일 내 기사 인덱스(`A001`, `A002` 등)를 `src:` 세그먼트로 표기한다. 예: `[conf:0.85, impact:0.7, src:A001,A007]`.
4. Write the full report (Korean digest + graph block) to `workspace/cycles/{category}/{slot}.md`.
5. Run: `.venv/bin/python newsparser/scripts/apply_graph.py {category} {slot}`
6. Run: `.venv/bin/python newsparser/scripts/mark_processed.py {category} {slot}`
7. 마지막으로 텔레그램 전송용 키워드 요약을 **stdout(최종 메시지)** 으로 출력한다. 형식은 아래 "텔레그램 전송용 요약 (stdout)"을 따른다. 이것이 텔레그램으로 전송되는 유일한 출력이며, 리포트 파일에는 넣지 않는다.

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
- NEW | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}, src:A001,A007]--> {object} | {predicate_text}
- UPDATE | {subject} --{PREDICATE}[conf:{0.NN}, impact:{0.NN}, src:A003]--> {object}
```

Valid Labels: Company, Person, Institution, Event, Indicator, Market, Sector, Policy
Valid Predicates: INFLUENCES, MEMBER_OF, COMPETES_WITH, ANNOUNCED, IMPACTS, CONTRADICTS, FOLLOWS_UP

If a digest section has nothing to report, write `• 없음`. Omit empty graph entries.

## 텔레그램 전송용 요약 (stdout)

리포트 파일을 쓰고 위 스크립트를 모두 실행한 뒤, **마지막 출력(stdout)** 으로 텔레그램 전송용 키워드 요약만 출력한다. 텔레그램에는 이 stdout 요약만 전송되고, 리포트 .md 파일에는 위 전체 다이제스트가 그대로 남아 /weekly·/reflect·그래프 맥락에 쓰인다 (요약은 파일에 넣지 않는다). 섹션 구조(새 소식/이어지는 흐름/조용한 영역/오픈 스레드)는 유지하되 각 항목은 `• (0.NN) 한 줄 헤드라인` 한 줄로만 — 본문 문장·엔티티·출처는 넣지 않는다. 내용이 없으면 `• 없음`.

```
YYYY-MM-DD HH:00 KST

새 소식
• (0.NN) 한 줄 헤드라인
• (0.NN) ...

이어지는 흐름
• (0.NN) ...

조용한 영역
• 없음

오픈 스레드
• ...
```
