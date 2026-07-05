`$ARGUMENTS`를 공백으로 구분된 두 토큰으로 파싱한다: slot(예: `2026-05-08-12`), category(예: `tech` 또는 `markets`).

## 카테고리 컨텍스트

**tech**: AI 활용·신규 AI 정보·일반 컴퓨터 기술. 시장 영향·일반 산업 뉴스는 markets 사이클에서 처리하므로 다루지 마.

**markets**: 시장·매크로·정책·지정학·일반 산업. AI 회사 실적·주가 영향처럼 시장 관점이면 여기서 다뤄도 됨.

## 사용자 관심사

`workspace/me/interests_{category}.md`를 읽고 중요도 점수 가중치에 반영한다. interest_weight가 높은 주제일수록 더 깊게 분석한다.

## 무시 목록

Read `workspace/me/ignore.md`. 표의 모든 `대상`(종류 entity/storyline)을 이번 사이클에서 **완전히 배제**한다:
- 직전 사이클 리포트(아래 작업 2단계)에서 해당 화제를 **이어받아 재서술하지 않는다.**
- 중요도 점수·다이제스트 본문·`## Graph updates` 블록 어디에도 포함하지 않는다.
- 목록이 비어 있으면 무시.

## 시장 스냅샷

입력파일 상단에 `## 시장 스냅샷` 블록이 있다. 보고서의 "새 소식" 첫 단락 또는 lead-in 한 줄에 그 날 시장 상태를 짧게 요약·반영하라. Indicator 엔티티를 라벨링할 때 `canonical_name`은 반드시 다음 별칭 중 하나로 쓴다: `SPX`, `NDX`, `KOSPI`, `USDKRW`, `USDJPY`, `DXY`, `VIX`, `TNX`. 그래프와 가격 DB는 이 별칭으로 연결된다.

## 진위 검증 규칙

리포트에는 **입력파일 기사에서 직접 확인되는 사실만** 넣는다:

- 새 소식·이어지는 흐름의 모든 주장(사건, 수치, 날짜, 발언)은 입력파일의 특정 기사(A-인덱스)로 추적 가능해야 한다. `출처:` 필드에는 그 근거 기사의 실제 매체명을 쓴다.
- 기사에 없는 디테일을 기억이나 일반 지식으로 보충하지 않는다. 직전 사이클 리포트의 내용은 맥락 연결(델타, 스레딩)에만 쓰고, 이번 입력에 근거 기사가 없는 주장을 새 사실처럼 재기술하지 않는다.
- 단일 출처의 루머·미확인 보도는 헤드라인이나 본문에 `(미확인)`을 명시하고 conf를 0.5 이하로 낮춘다. 진위가 불확실한 내용은 `## Graph updates`에 넣지 않는다 — 그래프에는 근거 기사(src:)가 확실한 관계만 추가한다.
- 추론·전망을 쓸 경우 사실과 구분되게 "~로 보임", "~가능성" 등으로 표시한다.

## 작업

1. `workspace/input/{category}/{slot}-input.md`를 읽는다.
2. `workspace/cycles/{category}/`에서 가장 최근 파일을 읽어 직전 맥락으로 삼는다(없으면 생략). **무시 목록의 대상이 직전 리포트에 등장하더라도 이어받지 말 것** (위 "무시 목록" 참조).
3. 수집된 기사 전체를 분석한다:
   - 교차 출처 중복 제거: 여러 출처에 실린 같은 사건 → 병합.
   - 델타: 진짜 새로운 것과 기존 흐름의 연속을 구분.
   - 인과 스레딩: 이전 사이클과 연결한다("Story X의 세 번째 업데이트").
   - 중요도 점수: 0.0–1.0, 객관적 기준만. 0.8 이상은 실제로 시장을 움직이는 사건에만 부여한다.
   - 각 관계에 대해 그 주장을 뒷받침하는 입력파일 내 기사 인덱스(`A001`, `A002` 등)를 `src:` 세그먼트로 표기한다. 예: `[conf:0.85, impact:0.7, src:A001,A007]`.
4. 전체 리포트(한국어 다이제스트 + 그래프 블록)를 `workspace/cycles/{category}/{slot}.md`에 쓴다.
5. 실행: `.venv/bin/python newsparser/scripts/apply_graph.py {category} {slot}`
6. 실행: `.venv/bin/python newsparser/scripts/mark_processed.py {category} {slot}`
7. 텔레그램 메시지는 이제 Python(`run_cycle.py`)이 리포트 파일에서 직접 렌더하므로, **별도의 stdout 요약을 출력할 필요가 없다.** 리포트 `.md`만 위 형식대로 정확히 작성하면 된다 (특히 각 항목의 `• (중요도 0.NN) 헤드라인`을 정확한 형식으로).

## 문체 규칙

리포트 본문은 문어체로 작성한다. 구어 표현(~해요, ~거든요, ~네요, ~인데요)은 사용하지 않는다.
문장 말미는 명사형 종결(~함, ~됨) 또는 서술형 종결(~다, ~이다, ~하였다)로 통일한다.
헤드라인은 핵심 사실만 명사구로 압축한다.

## 리포트 파일 형식

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

유효 Label: Company, Person, Institution, Event, Indicator, Market, Sector, Policy
유효 Predicate: INFLUENCES, MEMBER_OF, COMPETES_WITH, ANNOUNCED, IMPACTS, CONTRADICTS, FOLLOWS_UP

Event canonical_name 규칙: `{핵심 주체} {핵심 행위 명사} {날짜}` 한 가지 꼴로만 짓는다.
조사·수식어·서술형 금지, 대표 동사 하나로 통일(발표/공개/배포/출시 중 하나만).
날짜는 **연-월(`YYYY-MM`)을 기본**으로 한다 — 방문·정상회담·협상·회의처럼 여러 날
걸치거나 사이클마다 날짜 추정이 갈릴 수 있는 사건은 반드시 연-월만 쓴다(일자를 붙이면
같은 사건이 날짜별로 파편화됨). 한 날짜에 확정된 단발 사건이고 같은 달 다른 사건과
구분이 꼭 필요할 때만 `YYYY-MM-DD`를 쓴다.
같은 사건은 늘 같은 이름이 나와야 한다 — 예: `시진핑 방북 2026-06`, `Claude Fable 5 출시 2026-06`
(O), `Claude Fable 5·Mythos 5 발표` / `Mythos 5 Project Glasswing 배포`처럼
서술이 갈리는 이름 (X).

다이제스트 섹션에 보고할 내용이 없으면 `• 없음`이라고 쓴다. 비어 있는 그래프 항목은 생략한다.
