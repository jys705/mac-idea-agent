import os
import re
import json
from typing import Any
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()

# ── LLM 초기화 ─────────────────────────────────────────────

_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0.9,  # 창의적인 컨셉 생성을 위해 높게 설정
    max_tokens=1024,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
)


# ── 스코프 검증 (Golden Dataset 케이스 7 — 단일 유틸 경계) ──
# design_v2.md 섹션 6 / future-work 기능3:
# 생성 결과가 "단일 기능 macOS 유틸리티"를 벗어나 플랫폼/노코드 빌더/봇 프레임워크/
# 단계별 학습앱으로 새는지 검출한다. 프롬프트 제약을 보조하는 자가 검증 레이어.

ALLOWED_APP_FORMS = {"menubar", "dock", "shortcut", "widget"}

# (라벨, 정규식) — 스코프 이탈 신호
_OUT_OF_SCOPE_PATTERNS: list[tuple[str, str]] = [
    ("platform", r"플랫폼|platform"),
    ("no_code_builder", r"노\s*코드|no[\s-]?code|드래그\s*앤\s*드롭|drag.?and.?drop|"
                        r"비주얼\s*빌더|visual\s+builder|\b빌더\b|\bbuilder\b|조립(?:기|식|하는|해)"),
    ("bot_framework", r"봇\s*프레임워크|bot\s+framework|\b프레임워크\b|\bframework\b|"
                      r"인터프리터|interpreter"),
    ("learning_app", r"배우는|직접\s*만들며|단계별\s*학습|학습\s*앱|학습용|튜토리얼|tutorial|"
                     r"가르치는|teaches?\s+you|learn\s+to\s+build"),
]


def validate_scope(
    app_name: str,
    description: str = "",
    core_feature: str = "",
    app_form: str | None = None,
) -> dict:
    """생성된 컨셉이 단일 기능 macOS 유틸리티 범위 안인지 검증한다.

    Returns: {"is_single_utility": bool, "reason": str, "violation": str|None}
    """
    text = f"{app_name} {description} {core_feature}".lower()
    for label, pattern in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return {
                "is_single_utility": False,
                "violation": label,
                "reason": f"스코프 이탈 신호 감지({label}) — 단일 기능 유틸리티가 아닌 "
                          f"플랫폼/빌더/프레임워크/학습앱 성격으로 보임",
            }

    if app_form and app_form.lower() not in ALLOWED_APP_FORMS:
        return {
            "is_single_utility": False,
            "violation": "unknown_form",
            "reason": f"app_form '{app_form}'이(가) 허용 형태(메뉴바/Dock/단축키/위젯)가 아님",
        }

    return {
        "is_single_utility": True,
        "violation": None,
        "reason": "메뉴바/Dock/단축키/위젯 형태의 단일 기능 유틸리티로 판단",
    }


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def concept_generator(
    meme_trend: str,
    it_trend: str,
    exclude_concepts: list[str] | None = None
) -> dict[str, Any]:
    """
    수집된 밈 트렌드와 IT 트렌드를 교차 조합하여 macOS 앱 컨셉 후보를 생성한다.

    스코프 제약 (반드시 지킬 계약):
    - 반드시 "단일 기능 macOS 유틸리티"만 생성한다 — 메뉴바 앱 / Dock 앱 / 단축키 유틸 /
      위젯 처럼 "하나의 일을 가볍게 잘하는" 도구. 레퍼런스: RunCat(메뉴바 꾸미기 유틸),
      Rectangle(단축키 창 정렬 유틸).
    - 다음은 절대 생성하지 않는다: 플랫폼, 노코드/비주얼 빌더, 봇 프레임워크,
      단계별로 직접 만들며 배우는 학습앱. (복잡한 조립/자동화 플랫폼 금지)

    app_existence_checker에서 유사 앱이 발견된 경우 exclude_concepts에 해당 앱명을 넣어 재호출한다.
    동일 조합으로 3회 이상 재호출 시 호출을 중단해야 한다.

    Args:
        meme_trend: 밈 트렌드 키워드 (예: "카피바라 밈")
        it_trend: IT 트렌드 키워드 (예: "MCP 핫함")
        exclude_concepts: 이미 유사 앱이 존재해서 제외할 컨셉명 목록

    Returns:
        ok: 성공 여부
        data: app_name, description, core_feature, target_os, app_form, concept_basis, scope_check
        error: 실패 시 에러 정보
    """
    exclude_str = ""
    if exclude_concepts:
        exclude_str = f"\n다음 컨셉은 이미 유사한 앱이 존재하므로 절대 사용하지 마세요: {', '.join(exclude_concepts)}"

    prompt = f"""당신은 창의적인 macOS 앱 아이디어를 생성하는 전문가입니다.
아래 두 트렌드를 교차 조합하여 "귀엽고 하찮지만 실용적인" macOS 앱 컨셉을 하나 생성하세요.

밈 트렌드: {meme_trend}
IT 트렌드: {it_trend}
{exclude_str}

스코프 (반드시 지킬 것):
- 반드시 "단일 기능 macOS 유틸리티"여야 합니다. 형태는 메뉴바 앱 / Dock 앱 / 단축키 유틸 / 위젯 중 하나.
  → 레퍼런스: RunCat(메뉴바에서 CPU 부하에 따라 뛰는 고양이), Rectangle(단축키로 창 정렬).
  "하나의 일을 가볍게 잘하는" 작은 도구여야 합니다.
- 다음은 절대 만들지 마세요(스코프 이탈):
  · 플랫폼 / 대형 앱
  · 노코드·비주얼 빌더 / 드래그앤드롭 조립기
  · 봇 프레임워크 / 인터프리터 / 자동화 플랫폼
  · 단계별로 직접 만들며 배우는 학습앱 / 튜토리얼 앱

규칙:
- macOS 전용 앱이어야 합니다.
- 바이브코딩으로 1~3일 안에 구현 가능한 수준이어야 합니다.
- 개발자나 IT 종사자가 실제로 쓰고 싶어할 만한 실용성이 있어야 합니다.
- 반드시 밈의 감성과 IT 트렌드의 실용성이 결합되어야 합니다.

아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요:
{{
  "app_name": "앱 이름 (영문, 재치있게)",
  "description": "한 줄 설명 (한국어, 50자 이내)",
  "core_feature": "핵심 기능 1가지 (한국어, 30자 이내)",
  "target_os": "macOS",
  "app_form": "menubar 또는 dock 또는 shortcut 또는 widget 중 하나",
  "concept_basis": {{
    "meme": "{meme_trend}",
    "it": "{it_trend}"
  }}
}}"""

    try:
        response = _llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()

        # 코드블록 제거
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        data = json.loads(content.strip())

        # 스코프 자가 검증 (단일 유틸 경계)
        scope_check = validate_scope(
            data.get("app_name", ""),
            data.get("description", ""),
            data.get("core_feature", ""),
            data.get("app_form"),
        )

        return {
            "ok": True,
            "data": {
                **data,
                "scope_check": scope_check,
                "source_provenance": {
                    "data_source": "llm_inference",
                    "model": "claude-sonnet-4-6",
                    "temperature": 0.9,
                },
            },
            "error": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "CONCEPT_GENERATION_FAILED",
                "message": str(e),
                "fallback_action": "다른 트렌드 조합으로 재시도 권장"
            }
        }
