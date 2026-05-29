import os
import json
from typing import Any
from dotenv import load_dotenv

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage

from src.tools import tools
from src.prompts.system_prompt import SYSTEM_PROMPT

load_dotenv()

# ── LLM 초기화 (Agent 판단용 — Sonnet) ────────────────────

_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0,  # Agent 판단은 일관성이 중요
    max_tokens=4096,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
)

# ── Agent 생성 ─────────────────────────────────────────────

agent = create_react_agent(
    model=_llm,
    tools=tools,
    prompt=SYSTEM_PROMPT,
)

# ── 실행 함수 ──────────────────────────────────────────────

def run_agent(
    user_query: str,
    trend_focus: str = "both",
    difficulty_limit: str | None = None,
    exclude_existing: bool = False,
) -> dict[str, Any]:
    """
    Agent를 실행하고 최종 브리핑 결과를 반환한다.

    Args:
        user_query: 사용자 자연어 요청
        trend_focus: "meme" / "IT" / "both"
        difficulty_limit: "1day" / "3days" / "1week" / None
        exclude_existing: True 시 유사 앱 있으면 자동 루프백

    Returns:
        today_brief + metadata 구조의 딕셔너리
    """
    # 사용자 입력 구성
    input_context = f"""사용자 요청: {user_query}

설정값:
- trend_focus: {trend_focus}
- difficulty_limit: {difficulty_limit if difficulty_limit else "제한 없음"}
- exclude_existing: {exclude_existing}

위 설정에 맞게 트렌드를 수집하고 맥앱 아이디어를 브리핑해주세요."""

    print(f"\n{'='*50}")
    print(f"[Agent 시작] {user_query}")
    print(f"{'='*50}")

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=input_context)]},
            config={"recursion_limit": 15}  # max_steps=15
        )

        # 최종 메시지 추출
        final_message = result["messages"][-1].content

        print(f"\n[Agent 완료]")
        print(f"총 메시지 수: {len(result['messages'])}")

        # JSON 파싱 시도
        try:
            if "```json" in final_message:
                json_str = final_message.split("```json")[1].split("```")[0].strip()
            elif "```" in final_message:
                json_str = final_message.split("```")[1].split("```")[0].strip()
            else:
                json_str = final_message.strip()

            parsed = json.loads(json_str)
            return parsed

        except json.JSONDecodeError:
            # JSON 파싱 실패 시 원문 반환
            return {
                "today_brief": None,
                "metadata": {
                    "used_tools": [],
                    "loop_count": 0,
                    "failure_type": "json_parse_error",
                    "fallback_action": "raw_response",
                    "raw_response": final_message
                }
            }

    except Exception as e:
        print(f"[Agent 오류] {e}")
        return {
            "today_brief": None,
            "metadata": {
                "used_tools": [],
                "loop_count": 0,
                "failure_type": "agent_error",
                "fallback_action": None,
                "error": str(e)
            }
        }