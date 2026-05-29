import os
from typing import Any
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

# ── LLM 초기화 ─────────────────────────────────────────────

_llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001",
    temperature=0.3,  # 난이도 판단은 일관성이 중요하므로 낮게 설정
    max_tokens=1024,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
)

# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def feasibility_checker(
    concept: str,
    core_feature: str,
    difficulty_limit: str | None = None
) -> dict[str, Any]:
    """
    확정된 앱 컨셉을 바이브코딩으로 구현할 때 예상 기간과 추천 기술 스택을 판단한다.
    app_existence_checker에서 유사 앱 없음이 확인된 후 마지막으로 호출된다.
    난이도가 difficulty_limit을 초과하면 difficulty_limit_exceeded=True를 반환한다.

    Args:
        concept: 앱 이름 또는 컨셉
        core_feature: 핵심 기능 설명
        difficulty_limit: 사용자가 원하는 최대 구현 기간. "1day" / "3days" / "1week" / None

    Returns:
        ok: 성공 여부
        data: difficulty, stack, difficulty_limit_exceeded, vibe_coding_tip
        error: 실패 시 에러 정보
    """
    limit_str = f"\n사용자가 원하는 최대 구현 기간: {difficulty_limit}" if difficulty_limit else ""

    prompt = f"""당신은 macOS 앱 개발 경험이 풍부한 시니어 개발자입니다.
아래 앱을 바이브코딩(혼자서 빠르게 구현)으로 만든다면 얼마나 걸릴지 판단해주세요.

앱 이름: {concept}
핵심 기능: {core_feature}
{limit_str}

판단 기준:
- "1day": 단순 UI + 단일 API 호출 수준
- "2~3days": 메뉴바/Dock 앱 + 1~2개 외부 연동
- "1week": 복잡한 상태 관리 또는 여러 API 통합 필요

아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요:
{{
  "difficulty": "1day 또는 2~3days 또는 1week",
  "stack": ["추천 기술 스택 목록 (Swift, SwiftUI, AppKit, MenuBarExtra 등)"],
  "difficulty_limit_exceeded": false,
  "vibe_coding_tip": "구현 시 가장 중요한 팁 1가지 (한국어, 50자 이내)"
}}"""

    try:
        response = _llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()

        import json
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        data = json.loads(content.strip())

        # difficulty_limit 초과 여부 판단
        if difficulty_limit:
            limit_map = {"1day": 1, "3days": 2, "1week": 3}
            difficulty_map = {"1day": 1, "2~3days": 2, "1week": 3}
            limit_val = limit_map.get(difficulty_limit, 99)
            actual_val = difficulty_map.get(data.get("difficulty", "1week"), 3)
            data["difficulty_limit_exceeded"] = actual_val > limit_val

        return {
            "ok": True,
            "data": data,
            "error": None
        }

    except Exception as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "FEASIBILITY_UNKNOWN",
                "message": str(e),
                "fallback_action": "난이도 미정으로 브리핑 출력, 사용자 직접 판단 위임"
            }
        }