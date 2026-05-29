# mac-idea-agent

> **하찮은 맥앱 아이디어 브리핑 에이전트**
> 글로벌 밈 트렌드 × IT 트렌드를 교차 분석해 "귀엽고 하찮지만 실용적인" macOS 앱 아이디어를 자동으로 브리핑합니다.

7주차 AI 에이전트 구현 과제 — 6주차 설계서(`design_v2.md`)의 ReAct + Workflow/Agent 분리 구조를 LangGraph로 실제 구현한 결과물입니다.

---

## 1. 개요

매일 "오늘 뭐 만들지?"를 고민하는 1인 바이브코더(`이주임` 페르소나)를 타깃으로 하는 LangGraph 기반 ReAct 에이전트입니다.

**왜 단일 LLM 호출이 아니라 Agent인가**
- 학습 데이터에 없는 **오늘**의 Reddit·YouTube·HackerNews·GitHub 트렌드는 실시간 Tool 없이는 알 수 없음
- 트렌드 수집 → 컨셉 생성 → 유사 앱 검색 → 난이도 판단의 **고정 파이프라인은 Workflow**, 그 사이의 예외 분기(유사 앱 발견 시 루프백, 일부 소스 실패 시 진행 여부 등)만 **Agent의 동적 판단**이 필요

---

## 2. 아키텍처 — Workflow vs Agent 구간 분리

design_v2.md 멘토 피드백 반영: 정상 흐름은 Workflow가 처리, 예외·판단 분기점에만 Agent의 자율성을 제한적으로 부여.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[WORKFLOW — 고정 파이프라인 (정상 흐름)]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1. trend_scanner          → 트렌드 수집
Step 2. concept_generator      → 후보 컨셉 생성
Step 3. app_existence_checker  → 유사 앱 검색
Step 4. feasibility_checker    → 난이도 판단

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[AGENT — 예외 발생 시에만 개입]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
판단 1. similar_app_found=True       → concept_generator 재호출 (루프백)
판단 2. 트렌드 소스 partial_failure  → 나머지 소스만으로 계속 진행 여부
판단 3. difficulty_limit_exceeded    → 후보 컨셉 교체 여부
판단 4. loop_count >= 3              → 루프 탈출 + fallback
```

이 분리 원칙은 `src/prompts/system_prompt.py`에 [WORKFLOW] / [AGENT] 라벨로 명시되어 있고, LangGraph `create_react_agent` 위에서 system prompt 가이드로 동작합니다.

### Tool 명세

| Tool | API | 담당 구간 | 역할 |
|------|-----|-----------|------|
| `trend_scanner` | HackerNews·GitHub 실제 API + Reddit·YouTube Mock | WORKFLOW (정상) / AGENT (소스 일부 실패) | 밈·IT 트렌드 수집 |
| `concept_generator` | claude-sonnet-4-6 (temperature=0.9) | WORKFLOW (정상) / AGENT (루프백) | 트렌드 교차 조합 → 앱 컨셉 |
| `app_existence_checker` | iTunes Search API + GitHub Search API | WORKFLOW (정상) / AGENT (threshold 조정) | 유사 앱 존재 여부 |
| `feasibility_checker` | claude-haiku-4-5 (temperature=0.3) | WORKFLOW (정상) / AGENT (난이도 초과 시 후보 교체) | 구현 난이도 + 추천 스택 |

---

## 3. 설치 및 실행

### 패키지 설치

```bash
pip install -r requirements.txt
```

### 환경 변수

```bash
cp .env.example .env
# .env에서 ANTHROPIC_API_KEY 입력
```

### 실행

```bash
# 기본 (밈 + IT)
python -m src.main

# IT 트렌드 중심 + 3일 이내
python -m src.main --focus IT --difficulty 3days

# 유사 앱 발견 시 자동 루프백
python -m src.main --query "아무도 안 만든 거 찾아줘" --exclude-existing

# 결과 JSON 저장
python -m src.main --output result.json
```

### CLI 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--query`, `-q` | "오늘 뭐 만들면 재밌을까?" | 자연어 요청 |
| `--focus`, `-f` | `both` | `meme` / `IT` / `both` |
| `--difficulty`, `-d` | 제한 없음 | `1day` / `3days` / `1week` |
| `--exclude-existing`, `-e` | False | 유사 앱 발견 시 자동 루프백 |
| `--output`, `-o` | 터미널 출력 | 결과 저장 경로 |

---

## 4. 출력 스키마

```json
{
  "today_brief": {
    "meme_trend": ["트렌드1", "트렌드2"],
    "it_trend": ["트렌드1", "트렌드2"],
    "concepts": [
      {
        "app_name": "앱 이름",
        "description": "한 줄 설명",
        "core_feature": "핵심 기능",
        "similar_app_exists": false,
        "difficulty": "2~3days",
        "stack": ["Swift", "SwiftUI", "AppKit"]
      }
    ]
  },
  "metadata": {
    "used_tools": ["trend_scanner", "concept_generator", "app_existence_checker", "feasibility_checker"],
    "loop_count": 0,
    "failure_type": null,
    "fallback_action": null,
    "sources": {
      "meme": ["reddit_mock", "youtube_mock"],
      "it": ["hackernews", "github"]
    }
  }
}
```

---

## 5. 케이스 검증 — 설계서 vs 실제 실행 결과 매핑

design_v2.md §8의 4개 성공 판정 케이스 중 **2개를 실제 실행으로 검증**, **1개는 실패 사례로 종료 조건이 발동된 것을 확인**했습니다.

| design_v2.md 케이스 | examples/ 파일 | 검증 상태 |
|---------------------|----------------|-----------|
| 케이스 1 — IT 중심 요청 | [`case1_it_focus.json`](./examples/case1_it_focus.json) | ✅ 정상 동작 |
| 케이스 2 — 유사 앱 발견 후 재생성 (`exclude_existing=true`) | [`case2_both_exclude_existing.json`](./examples/case2_both_exclude_existing.json) | ✅ 정상 동작 |
| 케이스 3 — 트렌드 API 일부 실패 | (직접 검증 미실행) | ⚠️ Reddit·YouTube가 Mock이라 실패 시뮬레이션 미실시 |
| 케이스 4 — 유사 앱 3회 발견 후 fallback | [`case_failure_recursion_limit.json`](./examples/case_failure_recursion_limit.json) | ⚠️ 루프 탈출 발동했으나 설계와 다른 경로(아래 §7 참고) |

### 케이스 1 — IT 중심 요청 검증

```
입력: --focus IT
expected_tool_sequence:
  trend_scanner(type="IT") → concept_generator → app_existence_checker → feasibility_checker
expected_stop_reason: "정상 종료"
```

실제 결과 ([`case1_it_focus.json`](./examples/case1_it_focus.json)):
- `metadata.used_tools` = 4개 Tool 모두 호출됨 ✅
- `today_brief.meme_trend` = `[]` (밈 스킵 확인) ✅
- `today_brief.it_trend` = HackerNews·GitHub 기반 5개 키워드 ✅
- `metadata.sources.it` = `["hackernews", "github"]` ✅
- `loop_count: 0`, `failure_type: null` ✅

### 케이스 2 — 공백 탐색형 검증

```
입력: --exclude-existing
expected_stop_reason: "정상 종료"
```

실제 결과 ([`case2_both_exclude_existing.json`](./examples/case2_both_exclude_existing.json)):
- `concepts[*].similar_app_exists: false` (모든 컨셉 공백 확인됨) ✅
- 밈 + IT 통합 트렌드 수집 확인 ✅
- 이번 실행에선 1차 컨셉 생성에서 바로 공백이 확인되어 `loop_count: 0` (루프백 미발동, 정상 흐름)

---

## 6. 프로젝트 구조

```
mac-idea-agent/
├── src/
│   ├── agent.py                       # LangGraph create_react_agent 래퍼
│   ├── main.py                        # CLI 진입점
│   ├── tools/
│   │   ├── trend_scanner.py           # HackerNews·GitHub 실제 API + Reddit·YouTube Mock
│   │   ├── concept_generator.py       # claude-sonnet-4-6 기반 컨셉 생성
│   │   ├── app_existence_checker.py   # iTunes·GitHub 유사 앱 검색
│   │   └── feasibility_checker.py     # claude-haiku 기반 난이도 판단
│   └── prompts/
│       └── system_prompt.py           # ReAct 행동 원칙 + WORKFLOW/AGENT 라벨링 + 출력 스키마 강제
├── examples/
│   ├── case1_it_focus.json                    # 케이스 1 정상 동작
│   ├── case2_both_exclude_existing.json       # 케이스 2 정상 동작
│   └── case_failure_recursion_limit.json      # 종료 조건 발동 (실패 사례)
├── .env.example
├── requirements.txt
└── README.md
```

---

## 7. 현재 구현 범위 및 알려진 제한

### 구현 완료

- Tool 4개 동작 확인 (`trend_scanner`, `concept_generator`, `app_existence_checker`, `feasibility_checker`)
- LangGraph `create_react_agent` 기반 ReAct Loop
- system prompt에 WORKFLOW vs AGENT 구간 명시적 라벨링
- `similar_app_found=True` → concept_generator 루프백 발동 가능 (case 2 시나리오)
- partial_failure 처리 (HackerNews/GitHub 응답 일부 실패 시 나머지로 계속 진행)
- CLI 인터페이스 + JSON 파일 출력
- 출력 스키마 강제 (system prompt에서 순수 JSON 응답 명시)

### 알려진 제한

#### 1. `--difficulty 1day` + 루프백 조합 → recursion_limit 도달

`exclude_existing=true` 또는 `difficulty_limit=1day`처럼 제약이 강한 입력에서 1일 이내 구현 가능한 컨셉이 희소하면, **설계서 §5의 "동일 조합 3회 시 루프 탈출"이 발동되기 전에 LangGraph 기본 `recursion_limit=15`에 먼저 걸려** `failure_type: "agent_error"`로 종료됩니다.

실제 발동 사례: [`examples/case_failure_recursion_limit.json`](./examples/case_failure_recursion_limit.json)

```json
{
  "today_brief": null,
  "metadata": {
    "used_tools": [],
    "loop_count": 0,
    "failure_type": "agent_error",
    "fallback_action": null,
    "error": "Recursion limit of 15 reached without hitting a stop condition."
  }
}
```

해결 방향:
- `recursion_limit`을 design_v2.md 설계대로 25~30으로 상향
- 또는 agent.py에 동일 컨셉 조합 카운터를 명시적으로 주입해 LangGraph recursion 한도 전에 자체 루프 탈출 → `fallback_action: "차별화 포인트 제안"` 반환

#### 2. Reddit · YouTube 실 API 미연동

설계서 §7에서는 Reddit OAuth2 + YouTube Data API v3 실 API 사용을 명시했으나, 현재는 Mock 데이터로 동작합니다 (`metadata.sources.meme: ["reddit_mock", "youtube_mock"]`). 그 결과 케이스 3(트렌드 API 일부 실패) 시나리오를 실 API로 재현하지 못했습니다.

#### 3. 입력 가드레일 미구현

설계서 §9에서 보안 확장 포인트로 명시한 프롬프트 인젝션 감지 레이어("ignore previous instructions"류 패턴 필터링)는 미구현 상태입니다. 케이스 5(악성 입력) 검증 미수행.

#### 4. LangSmith 트레이싱 미연동

`.env`의 `LANGCHAIN_TRACING_V2=false` 상태. 8주차 관측성 실습에서 `true`로 전환 예정.

---

## 8. 다음 단계 (8주차 관측성 + 보안 보강)

- LangSmith 트레이싱 활성화 (`LANGCHAIN_TRACING_V2=true`) → ReAct 루프 단계별 추적
- Reddit OAuth2 / YouTube Data API v3 실 API 전환 + 케이스 3 재현
- recursion_limit 상향 + 자체 루프 카운터 추가로 케이스 4 fallback 정상화
- 입력 가드레일 미들웨어 추가 (인젝션 패턴 필터링)
