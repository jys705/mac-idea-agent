# mac-idea-agent

밈 트렌드 × IT 트렌드를 실시간 교차 분석해서 macOS 앱 아이디어를 브리핑하는 ReAct 에이전트.
컨셉을 확정하면 개발 킥오프 문서(`SPEC.md` / `BRIEF.md`)를 자동 생성한다.

## Features

- 밈/IT 트렌드 실시간 수집 (HackerNews, GitHub, Reddit, YouTube, r/productivity)
- 밈×IT 교차 조합으로 단일 유틸 앱 컨셉 생성
- iTunes Search(Mac App Store) + GitHub 임베딩 기반 유사 앱 검사
- self-critique 2축 채점 (실용성 / 트렌드 폭발력)
- 추천 1위 + 밀린 후보 이력 제시 후 사용자 선택
- 확정 컨셉의 `SPEC.md`(개발용) / `BRIEF.md`(기획용) 생성
- LangSmith + 로컬 JSON trace, 입력/외부 인젝션 가드레일

## Architecture

자율성을 모호한 판단 지점에만 배치. 정상 흐름은 코드, 분기 판단만 LLM, 최종 선택은 사용자.

```
WORKFLOW (code)   trend_scanner → (generate → check → critic) ×3 → rank → feasibility(.md)
AGENT (LLM)       트렌드 충분성 → 재탐색 / 소스 일부 실패 → 진행 (횟수 상한은 코드)
HUMAN (1회)       추천 1위 + 이력 → [수락 / 다른 후보 / 패스]
```

## Tools

| Tool | 역할 | 소스 |
|------|------|------|
| `trend_scanner` | 트렌드 수집 + 충분성 판단 | HackerNews / GitHub / meme-api / YouTube / r-productivity |
| `concept_generator` | 단일 유틸 앱 컨셉 생성 | Claude (Sonnet) |
| `app_existence_checker` | 유사 앱 의미 유사도 판정 | iTunes Search + GitHub, Gemini embedding |
| `concept_critic` | self-critique 채점 | Claude (Haiku) |
| `feasibility_checker` | SPEC.md / BRIEF.md 생성 | Claude (Haiku) |

## Stack

- LangGraph (`create_react_agent`, ReAct)
- Anthropic Claude (Sonnet / Haiku)
- Google Gemini embedding (`gemini-embedding-001`)
- LangSmith, requests, pydantic

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY 필수

python -m src.main -q "오늘 뭐 만들면 재밌을까?"
python -m src.main -q "개발자 감성으로" -f IT
pytest
```

`GOOGLE_API_KEY` 없으면 유사도 판정이 글자 매칭으로 fallback. `YOUTUBE_API_KEY`, `LANGCHAIN_API_KEY`는 선택.

## Layout

```
src/
  agent.py          ReAct 루프, 추천 산정, 최종 선택, trace
  main.py           CLI 진입점
  prompts/          시스템 프롬프트
  tools/            Tool 5종 + 임베딩
  observability.py  로컬 trace
tests/              단위 테스트
examples/           케이스별 실행 결과
security-tests/     인젝션 Before/After
output/{app}/       생성된 SPEC.md / BRIEF.md
```

## Roadmap

- CLI UX 개선: 현재 플래그 기반 입력을 대화형/프리셋으로 단순화
- 1인 사용자용 웹 서비스 배포 (`run_agent`는 입력 함수 주입 구조라 웹 전환 대비됨)
- IT 트렌드/뉴스 데일리 파악 용도로 활용 (trend_scanner 단독 분리)
- 장기 메모리: 과거 아이디어와 비교(아이디어 진화 추적)
- Multi-Agent(수집/검증 분리): 트렌드 소스 확장 시 조건부 도입
