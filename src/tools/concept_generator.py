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
    exclude_concepts: list[str] | None = None,
    similar_apps: list[dict] | None = None,
    retry_strategy: str = "fresh",
) -> dict[str, Any]:
    """
    수집된 밈 트렌드와 IT 트렌드를 교차 조합하여 macOS 앱 컨셉 후보를 생성한다.

    스코프 제약 (반드시 지킬 계약):
    - 반드시 "단일 기능 macOS 유틸리티"만 생성한다 — 메뉴바 앱 / Dock 앱 / 단축키 유틸 /
      위젯 처럼 "하나의 일을 가볍게 잘하는" 도구. 레퍼런스: RunCat(메뉴바 꾸미기 유틸),
      Rectangle(단축키 창 정렬 유틸).
    - 다음은 절대 생성하지 않는다: 플랫폼, 노코드/비주얼 빌더, 봇 프레임워크,
      단계별로 직접 만들며 배우는 학습앱. (복잡한 조립/자동화 플랫폼 금지)

    재생성(루프백) 정책 — 단계적 전략 (retry_strategy):
    - "fresh"   : 최초 생성. 주어진 트렌드 조합으로 새 컨셉.
    - "twist"   : 1차 유사 앱 발견 후. **트렌드 조합(meme×it)은 유지**하고 similar_apps와
                  핵심 가치가 겹치지 않게 기능 각도를 비틀어 차별화한다. (트렌드 폐기 X)
    - "pivot"   : 비틀어도 계속 유사 앱이 나올 때. 같은 조합을 고집하지 말고 **트렌드 조합
                  자체를 바꿔** 완전히 새로운 컨셉을 생성한다. (agent가 다른 meme/it 키워드를 넘긴다)
    - exclude_concepts에는 이미 만든 내 컨셉명을 넣어 같은 안의 반복을 막는다.
    - 동일 조합으로 3회 이상 막히면 호출을 중단한다(루프 탈출).

    Args:
        meme_trend: 밈 트렌드 키워드 (예: "카피바라 밈")
        it_trend: IT 트렌드 키워드 (예: "MCP 핫함")
        exclude_concepts: 이번 세션에서 이미 만든 내 컨셉명 목록 (같은 안 반복 방지)
        similar_apps: app_existence_checker가 찾은 유사 앱 목록(name/description). 이들과
                      가치가 겹치지 않게 차별화하는 근거로 쓴다. (배제가 아니라 차별화)
        retry_strategy: "fresh"(최초) / "twist"(조합 유지+비틀기) / "pivot"(조합 교체+완전 새 컨셉)

    Returns:
        ok: 성공 여부
        data: app_name, description, core_feature, target_os, app_form, concept_basis, scope_check
        error: 실패 시 에러 정보
    """
    exclude_str = ""
    if exclude_concepts:
        exclude_str = (
            f"\n[이미 시도한 내 컨셉 — 같은 안을 반복하지 말 것]: {', '.join(exclude_concepts)}"
        )

    similar_str = ""
    if similar_apps:
        lines = []
        for a in similar_apps[:5]:
            name = a.get("name", "")
            desc = a.get("description", "")
            lines.append(f"  · {name}: {desc}")
        similar_str = (
            "\n[이미 존재하는 유사 앱]\n" + "\n".join(lines)
        )

    # 재시도 전략별 지시 (단계적: 비틀기 → 안 되면 조합 교체)
    if retry_strategy == "twist":
        strategy_str = (
            "\n[재생성 전략: TWIST] 위 트렌드 조합(밈×IT)은 그대로 유지하라. 다만 위 유사 앱들과 "
            "'핵심 가치'가 겹치지 않도록 기능의 각도를 비틀거나 새로운 쓸모를 더해 차별화하라. "
            "트렌드를 폐기하지 말 것. 단, 억지로 비틀어 어색해지면 안 된다 — 자연스러운 차별화가 "
            "어렵다고 판단되면 차라리 솔직히 약한 컨셉임을 description에 드러내라."
        )
    elif retry_strategy == "pivot":
        strategy_str = (
            "\n[재생성 전략: PIVOT] 같은 트렌드 조합을 비틀어도 계속 기존 앱과 겹쳤다. 이제 "
            "현재 조합을 고집하지 말고 **완전히 새로운 컨셉**을 만들어라. 위에 주어진 meme/it가 "
            "이미 포화 상태라면, 그 트렌드의 다른 측면을 쓰거나 더 참신한 각도로 접근하라. "
            "'억지로 트는 것'보다 '새로 잘 만든 것'이 낫다."
        )
    else:  # fresh
        strategy_str = ""

    prompt = f"""당신은 창의적인 macOS 앱 아이디어를 생성하는 전문가입니다.
아래 두 트렌드를 교차 조합하여 "귀엽고 하찮지만 실용적인" macOS 앱 컨셉을 하나 생성하세요.

밈 트렌드: {meme_trend}
IT 트렌드: {it_trend}
{exclude_str}{similar_str}{strategy_str}

스코프 (반드시 지킬 것):
- 반드시 "단일 기능 macOS 유틸리티"여야 합니다. 형태는 메뉴바 앱 / Dock 앱 / 단축키 유틸 / 위젯 중 하나.
  → "하나의 일을 가볍게 잘하는" 작은 앱. RunCat(CPU 부하에 따라 뛰는 메뉴바 고양이)이
    "캐릭터가 상태에 반응한다"는 점에서 가장 가까운 결이다.
- 다음은 절대 만들지 마세요(스코프 이탈):
  · 플랫폼 / 대형 앱
  · 노코드·비주얼 빌더 / 드래그앤드롭 조립기
  · 봇 프레임워크 / 인터프리터 / 자동화 플랫폼
  · 단계별로 직접 만들며 배우는 학습앱 / 튜토리얼 앱

규칙:
- macOS 전용 앱이어야 합니다.
- 바이브코딩으로 1~3일 안에 구현 가능한 수준이어야 합니다.
- **핵심 감성: "귀여운 캐릭터/마스코트가 사용자의 행동·상태에 실시간으로 반응하는" 앱.**
  기능을 대놓고 내세우지 말고 감성·유머로 감싸세요.
- **통과 기준 (둘 중 적어도 하나가 압도적이어야 한다):**
  ① **실용성** — 사용자에게 실질적 쓸모(정보 제공 / 알림 / 행동 교정 / 기록·동기부여)를 준다.
     · 예: 거북목 되면 거북이 등장(자세 교정), 제스처로 투두 입력해 잠금화면 상주(할 일 관리),
       GitHub 커밋 쌓일수록 메뉴바 식물이 자람(기록·동기부여).
  ② **트렌드 폭발력** — 실용성이 거의 없어도, 압도적으로 핫한 트렌드라 "그것만으로 쓰고 싶은" 것.
     · 예: 클로드 코드로 타자 칠 때 옆에서 춤추는 카피바라 — 실용성은 0에 가깝지만
       "전 세계가 쓰는 클로드 코드"라는 트렌드 폭발력이 그걸 압도한다.
  → **둘 다 약하면 탈락.** (예: "Rust 터미널 빠를수록 카피바라가 온천에 앉음" = 실용성 0 +
     트렌드도 평범 → 그냥 장식, 만들지 말 것.) 반응이 장식에 그치지 않도록, 실용이든 트렌드든
     "이걸 왜 깔아서 쓰는가"에 명확히 답할 수 있어야 한다.
- **타깃은 한쪽이 아니다**: 완전한 개발자도, 취미 개발자도, 일반 맥 사용자도 모두
  "어 이거 귀엽고 쓸만한데?" 또는 "이 트렌드 앱 갖고 싶다"며 .dmg로 깔고 싶어질 만한 것.
- **기술 트렌드도 훌륭한 소재다 — 단 "재밌게 풀어야" 한다.** GitHub·HackerNews가 물어오는
  진짜 기술 핫함(새 언어/도구/AI 등)을 버리지 마라. 그걸 캐릭터·반응·유머로 풀면 최고의 소재다.
   · 나쁜 예(박제): "MCP 서버 설정을 시각화" — 기술을 날것으로 박아 일반인이 못 씀
   · 좋은 예(재해석): "GitHub 커밋이 쌓일수록 메뉴바 식물이 자란다", "빌드 도는 동안
     강아지가 공을 물어오고 빌드 끝나면 갖다준다"
  → 기술을 "기능으로 박제"하지 말고 "캐릭터의 반응으로 번역"하라. 그러면 개발자는 디테일에
    공감하고, 일반인은 귀여움으로 즐긴다.
- 밈의 감성은 캐릭터·반응 연출에, IT 트렌드(기술이든 서비스든)는 그 캐릭터가 반응하는
  "맥락/트리거"로 자연스럽게 엮으세요.

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
