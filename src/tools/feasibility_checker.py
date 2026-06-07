import os
import re
import json
from pathlib import Path
from typing import Any
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

# ── LLM 초기화 ─────────────────────────────────────────────

_llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001",
    temperature=0.3,  # 킥오프 계획은 일관성이 중요하므로 낮게 설정
    # SPEC/BRIEF용 구조화 plan은 필드가 많아 토큰이 크다. 잘리면 JSON이 깨지므로 넉넉히.
    max_tokens=4096,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
)


# ── 유틸: 파일명 슬러그 ────────────────────────────────────

def _slugify(name: str) -> str:
    """앱 이름을 파일시스템 안전 폴더명으로 변환한다."""
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", (name or "").strip())
    slug = slug.strip("_")
    return slug or "app"


# ── 난이도 → SPEC MVP 경계 의미 전환 ──────────────────────

def _difficulty_exceeded(difficulty: str, difficulty_limit: str | None) -> bool:
    if not difficulty_limit:
        return False
    limit_map = {"1day": 1, "3days": 2, "1week": 3}
    difficulty_map = {"1day": 1, "2~3days": 2, "1week": 3}
    limit_val = limit_map.get(difficulty_limit, 99)
    actual_val = difficulty_map.get(difficulty, 3)
    return actual_val > limit_val


# ── 마크다운 렌더링 (순수 함수 — LLM/네트워크 없이 테스트 가능) ──

def _bullets(items: list[str], empty: str = "- (해당 없음)") -> str:
    items = [str(i) for i in (items or []) if str(i).strip()]
    return "\n".join(f"- {i}" for i in items) if items else empty


def render_spec_md(plan: dict) -> str:
    """외부 AI 개발용 SPEC.md를 결정적으로 렌더링한다.

    plan: _generate_plan()이 만든 구조화 dict.
    """
    app_name = plan.get("app_name", "App")
    one_liner = plan.get("one_liner") or plan.get("description", "")
    app_form = plan.get("app_form", "menubar")
    mvp = plan.get("mvp_scope", {}) or {}
    completion = list(plan.get("completion_criteria", []) or [])

    # 배포 형태/서명·공증 완료 기준이 누락되면 기본 경고를 보강한다.
    if not any(".dmg" in str(c) or ".app" in str(c) or "공증" in str(c) or "서명" in str(c)
               for c in completion):
        completion.append(
            ".app 빌드 후 .dmg 배포 시 코드 서명(code signing) + notarization(애플 공증) 필요 "
            "— 미적용 시 Gatekeeper '확인되지 않은 개발자' 경고 (Apple Developer Program 연 $99)"
        )

    kickoff_prompt = plan.get("kickoff_prompt") or (
        f"이 폴더의 SPEC.md를 기준 문서로 읽고 '{app_name}'을(를) 개발해줘. "
        f"단일 기능 macOS 유틸리티({app_form})이며 [고정] 섹션(제품정의·차별점·MVP·완료기준)은 "
        f"그대로 존중하고, [초안] 섹션(스택·파일구조·구현순서)은 plan 모드로 더 나은 안을 제시해 "
        f"나와 조율한 뒤 확정해줘. 조율로 바뀐 결정은 SPEC.md 해당 섹션을 직접 갱신하고, "
        f"이 폴더에 CLAUDE.md가 없으면 확정된 핵심 결정을 요약한 CLAUDE.md를 만들어 줘. "
        f"먼저 plan 모드로 구현 순서를 제시하고 내 승인을 받은 뒤 진행해줘."
    )

    return f"""# {app_name} — 개발 SPEC (외부 AI 개발용)

> 이 문서는 Claude Code / Kiro / Gemini 등 **외부 AI가 개발을 시작하기 위한 기준 문서**다.
> mac-idea-agent가 오늘의 트렌드를 분석해 생성했다. plan 모드 + 이 SPEC.md 조합을 권장한다.
>
> **이 문서를 다루는 규칙 (중요):**
> 1. **[고정] 표시 섹션**(0·0-1·1·6)은 mac-idea-agent만 아는 맥락이다. 트렌드 분석·유사 앱
>    검사·제품 의도의 결과이므로 **임의로 바꾸지 말고 그대로 존중**하라.
> 2. **[초안] 표시 섹션**(2·3·4·5)은 출발점일 뿐이다. plan 모드에서 네가 더 나은 안을
>    제시하고 사용자와 조율한 뒤 확정하라. 완벽한 명세가 아니다.
> 3. 이 SPEC.md는 **살아있는 문서(living document)** 다. plan 단계에서 조율·확정된 결정은
>    채팅으로만 끝내지 말고, 승인 후 실행 단계에서 **이 파일의 해당 섹션을 직접 갱신**하라.
>    (예: 스택을 SwiftUI→AppKit으로 바꾸기로 했으면 §2를 그렇게 고쳐 둔다.)
> 4. 이 폴더에 CLAUDE.md가 없다면, 확정된 핵심 결정을 요약한 CLAUDE.md를 생성해
>    이후 세션에서도 맥락이 유지되게 하라.

## 0. 제품 정의 (스코프 고정) [고정]
- **{app_name}** — {one_liner}
- 형태: **단일 기능 macOS 유틸리티 ({app_form})**. RunCat·Rectangle 같은 "하나의 일을 가볍게 잘하는" 도구.
- 금지: 플랫폼/노코드 빌더/봇 프레임워크/단계별 학습앱으로 확장하지 말 것.

## 0-1. 차별화 포인트 (기존 유사 앱 대비) [고정]
{plan.get("differentiation", "(유사 앱 정보 없음 — 이 앱만의 강점을 개발 중 정의할 것)")}

## 1. MVP 범위 (선 긋기 — "며칠"이 아니라 "여기까지만") [고정]
### 넣을 것
{_bullets(mvp.get("include", []))}
### 뺄 것 (이번 버전에서 제외)
{_bullets(mvp.get("exclude", []))}

## 2. 추천 기술 스택 [초안 — plan 모드에서 확정]
{_bullets(plan.get("stack", []))}

## 3. 추천 파일 구조 [초안 — plan 모드에서 확정]
{_bullets(plan.get("file_structure", []))}

## 4. 구현 순서 [초안 — plan 모드에서 확정]
{_bullets(plan.get("implementation_order", []))}

## 5. 구현 시 까다로운 지점 / 함정 경고 [초안]
> (기존 난이도 판단을 "함정"으로 의미 전환한 부분 — 예상 난이도: {plan.get("difficulty", "미정")})
{_bullets(plan.get("tricky_points", []))}

## 6. 완료 기준 (어디까지 되면 끝인지 + 배포 형태) [고정]
{_bullets(completion)}

## 7. 킥오프 프롬프트 (복붙용)
```
{kickoff_prompt}
```
"""


def render_brief_md(plan: dict) -> str:
    """사람용 BRIEF.md를 결정적으로 렌더링한다."""
    app_name = plan.get("app_name", "App")
    brief = plan.get("brief", {}) or {}
    mon = brief.get("monetization", {}) or {}

    return f"""# {app_name} — 제품 브리핑 (사람용)

> 이 문서는 **사람(기획·의사결정)용**이다. 외부 AI 결과물을 보완·수정할 때
> 이 문서를 참조해 "원래 의도"를 잃지 않게 한다.

## 1. 왜 "하찮지만 실용적"인가 (제품 의도)
{brief.get("why_trivial_useful", "(미정)")}

## 2. 사용자에게 어떻게 비춰지는가 (포지셔닝)
{brief.get("positioning", "(미정)")}

## 3. 사용자가 뭘 편하게 느끼는가 (사용자 가치 / 실용 포인트)
{_bullets(brief.get("user_value", []))}

## 4. 트렌드 근거 (오늘의 밈 × IT 트렌드)
{brief.get("trend_basis", "(미정)")}

## 5. 수익화 방향
- 참조 모델: **{mon.get("model_ref", "RunCat")}** (RunCat=콘텐츠 판매형 / Rectangle=Pro 전환형)
- 근거: {mon.get("rationale", "무료로 설치 장벽을 낮추고 일부만 유료 전환")}
- 무료/유료 경계: {mon.get("free_paid_boundary", "MVP 범위 = 무료, 추가 테마·고급 기능 = 유료 전환 포인트")}
"""


def write_kickoff_docs(plan: dict, output_dir: str = "output") -> dict:
    """plan으로부터 SPEC.md / BRIEF.md를 output_dir/{slug}/ 아래에 쓴다. 경로를 반환."""
    slug = _slugify(plan.get("app_name", "app"))
    target = Path(output_dir) / slug
    target.mkdir(parents=True, exist_ok=True)

    spec_path = target / "SPEC.md"
    brief_path = target / "BRIEF.md"
    spec_path.write_text(render_spec_md(plan), encoding="utf-8")
    brief_path.write_text(render_brief_md(plan), encoding="utf-8")

    return {"spec_path": str(spec_path), "brief_path": str(brief_path)}


# ── LLM 호출: 구조화된 킥오프 계획 생성 ────────────────────

def _generate_plan(
    concept: str,
    description: str,
    core_feature: str,
    meme_trend: str,
    it_trend: str,
    similar_apps: list[dict] | None = None,
) -> dict:
    # 유사 앱 정보(app_existence_checker 결과)를 차별점 근거로 주입한다.
    similar_block = ""
    if similar_apps:
        lines = []
        for a in similar_apps[:5]:
            name = a.get("name", "")
            desc = a.get("description", "")
            score = a.get("similarity_score")
            score_str = f" (유사도 {score})" if score is not None else ""
            lines.append(f"- {name}{score_str}: {desc}")
        similar_block = (
            "\n\n[이미 존재하는 유사 앱 — 반드시 이들과의 차별점을 만들 것]\n"
            + "\n".join(lines)
        )

    prompt = f"""당신은 macOS 앱 개발 경험이 풍부한 시니어 개발자이자 제품 기획자입니다.
아래 "단일 기능 macOS 유틸리티" 아이디어를 외부 AI(Claude Code 등)가 바로 개발에 착수할 수 있도록
개발 킥오프 계획을 만드세요. 절대 거대한 플랫폼/빌더로 키우지 말고 RunCat·Rectangle 수준의
작고 단일 기능에 집중한 유틸리티로 한정하세요.

앱 이름: {concept}
한 줄 설명: {description}
핵심 기능: {core_feature}
밈 트렌드 근거: {meme_trend}
IT 트렌드 근거: {it_trend}{similar_block}

아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요:
{{
  "app_name": "{concept}",
  "one_liner": "한 줄 설명 (한국어)",
  "app_form": "menubar 또는 dock 또는 shortcut 또는 widget 중 하나",
  "difficulty": "1day 또는 2~3days 또는 1week",
  "differentiation": "위 유사 앱들과 무엇이 다른지 / 왜 이걸 따로 쓸 이유가 있는지 (한국어). 유사 앱이 없으면 이 앱만의 핵심 강점.",
  "stack": ["Swift", "SwiftUI", "MenuBarExtra" 등 추천 스택],
  "mvp_scope": {{
    "include": ["첫 버전에 넣을 기능 (선 긋기)"],
    "exclude": ["이번엔 뺄 기능"]
  }},
  "file_structure": ["{concept}/App.swift", "..."],
  "implementation_order": ["1. ...", "2. ...", "3. ..."],
  "tricky_points": ["구현 시 까다로운 지점/함정 (기존 난이도 정보를 여기로 전환)"],
  "completion_criteria": ["어디까지 되면 끝인지", ".app/.dmg 배포·서명·공증 여부"],
  "kickoff_prompt": "외부 AI에 그대로 붙여넣어 시작할 복붙용 프롬프트 한 덩어리",
  "brief": {{
    "why_trivial_useful": "왜 하찮지만 실용적인지 (한국어)",
    "positioning": "사용자에게 어떻게 비춰지는지",
    "user_value": ["사용자가 편하게 느끼는 실용 포인트"],
    "trend_basis": "이 아이디어가 나온 밈 × IT 트렌드 근거",
    "monetization": {{
      "model_ref": "RunCat 또는 Rectangle",
      "rationale": "왜 그 모델인지",
      "free_paid_boundary": "무료/유료 경계"
    }}
  }}
}}"""
    response = _llm.invoke([HumanMessage(content=prompt)])
    return _parse_plan_json(response.content)


def _parse_plan_json(content: str) -> dict:
    """LLM 응답에서 plan JSON을 견고하게 추출한다(코드펜스/잡설 방어)."""
    content = (content or "").strip()
    if "```" in content:
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 앞뒤 잡설 제거: 가장 바깥 {...} 블록만 파싱 시도
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def feasibility_checker(
    concept: str,
    core_feature: str,
    description: str = "",
    meme_trend: str = "",
    it_trend: str = "",
    similar_apps: list[dict] | None = None,
    difficulty_limit: str | None = None,
    output_dir: str = "output",
) -> dict[str, Any]:
    """
    확정된 앱 컨셉으로 "어떻게 시작하면 돼?"를 답하는 개발 킥오프 산출물을 생성한다.
    output/{app_name}/ 아래에 SPEC.md(외부 AI 개발용)와 BRIEF.md(사람용) 두 파일을 만든다.
    app_existence_checker에서 유사 앱 없음이 확인된(또는 사용자가 진행을 확인한) 후 마지막으로 호출된다.
    기존 "며칠 걸려?" 난이도 정보는 버리지 않고 SPEC.md의 MVP 경계·까다로운 지점으로 의미를 전환한다.
    난이도가 difficulty_limit을 초과하면 difficulty_limit_exceeded=True를 반환한다.

    Args:
        concept: 앱 이름 (output 폴더명 및 SPEC/BRIEF 제목)
        core_feature: 핵심 기능 설명
        description: 한 줄 설명 (SPEC/BRIEF 본문에 활용)
        meme_trend: 밈 트렌드 근거 (BRIEF.md 트렌드 근거)
        it_trend: IT 트렌드 근거 (BRIEF.md 트렌드 근거)
        similar_apps: app_existence_checker가 찾은 유사 앱 목록 (name/description/similarity_score).
                      SPEC.md의 "차별화 포인트" 섹션 근거로 사용된다. 유사 앱이 없으면 생략 가능.
        difficulty_limit: 사용자가 원하는 최대 구현 기간. "1day" / "3days" / "1week" / None
        output_dir: 산출물 루트 디렉토리 (기본 "output")

    Returns:
        ok: 성공 여부
        data: app_name, spec_path, brief_path, difficulty, differentiation, stack,
              difficulty_limit_exceeded, mvp_scope, tricky_points, monetization_ref
        error: 실패 시 에러 정보
    """
    try:
        plan = _generate_plan(concept, description, core_feature, meme_trend, it_trend, similar_apps)
        paths = write_kickoff_docs(plan, output_dir=output_dir)

        difficulty = plan.get("difficulty", "2~3days")
        exceeded = _difficulty_exceeded(difficulty, difficulty_limit)
        mon = (plan.get("brief", {}) or {}).get("monetization", {}) or {}

        return {
            "ok": True,
            "data": {
                "app_name": plan.get("app_name", concept),
                "spec_path": paths["spec_path"],
                "brief_path": paths["brief_path"],
                "difficulty": difficulty,
                "differentiation": plan.get("differentiation"),
                "stack": plan.get("stack", []),
                "difficulty_limit_exceeded": exceeded,
                "mvp_scope": plan.get("mvp_scope", {}),
                "tricky_points": plan.get("tricky_points", []),
                "monetization_ref": mon.get("model_ref"),
                "vibe_coding_tip": (plan.get("tricky_points") or [None])[0],
                "source_provenance": {
                    "data_source": "llm_inference + file_write",
                    "model": "claude-haiku-4-5-20251001",
                    "temperature": 0.3,
                    "artifacts": [paths["spec_path"], paths["brief_path"]],
                },
            },
            "error": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "FEASIBILITY_UNKNOWN",
                "message": str(e),
                "fallback_action": "킥오프 문서 생성 실패 — 난이도 미정으로 브리핑 출력, 사용자 직접 판단 위임"
            }
        }
