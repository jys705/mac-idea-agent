import os
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

# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def concept_generator(
    meme_trend: str,
    it_trend: str,
    exclude_concepts: list[str] | None = None
) -> dict[str, Any]:
    """
    수집된 밈 트렌드와 IT 트렌드를 교차 조합하여 macOS 앱 컨셉 후보를 생성한다.
    app_existence_checker에서 유사 앱이 발견된 경우 exclude_concepts에 해당 앱명을 넣어 재호출한다.
    동일 조합으로 3회 이상 재호출 시 호출을 중단해야 한다.

    Args:
        meme_trend: 밈 트렌드 키워드 (예: "카피바라 밈")
        it_trend: IT 트렌드 키워드 (예: "MCP 핫함")
        exclude_concepts: 이미 유사 앱이 존재해서 제외할 컨셉명 목록

    Returns:
        ok: 성공 여부
        data: app_name, description, core_feature, target_os, concept_basis
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

규칙:
- macOS 전용 앱이어야 합니다 (메뉴바 앱, Dock 앱, 알림 앱 등)
- 바이브코딩으로 1~3일 안에 구현 가능한 수준이어야 합니다
- 개발자나 IT 종사자가 실제로 쓰고 싶어할 만한 실용성이 있어야 합니다
- 반드시 밈의 감성과 IT 트렌드의 실용성이 결합되어야 합니다

아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요:
{{
  "app_name": "앱 이름 (영문, 재치있게)",
  "description": "한 줄 설명 (한국어, 50자 이내)",
  "core_feature": "핵심 기능 1가지 (한국어, 30자 이내)",
  "target_os": "macOS",
  "concept_basis": {{
    "meme": "{meme_trend}",
    "it": "{it_trend}"
  }}
}}"""

    try:
        response = _llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()

        # JSON 파싱
        import json
        # 코드블록 제거
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        
        data = json.loads(content.strip())

        return {
            "ok": True,
            "data": {
                **data,
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