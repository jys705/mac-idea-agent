import os
import json
import re
from typing import Any
from dotenv import load_dotenv

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

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


# ── 가드레일 ──────────────────────────────────────────────

# 인젝션 의심 패턴 (대소문자 무시, 부분 일치)
_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+previous",
    r"disregard\s+(the\s+)?(above|previous)",
    r"forget\s+(your|the)\s+(instructions|system\s+prompt)",
    r"you\s+are\s+now\s+(a|an)\s+",  # "you are now a different agent"
    r"reveal\s+(your|the)\s+system\s+prompt",
    r"print\s+(your|the)\s+system\s+prompt",
    r"show\s+me\s+your\s+(prompt|instructions)",
    r"<\s*system\s*>",  # 가짜 system 태그 주입
]

# 도메인 외 의도가 명백한 키워드 (블록까진 아니고 재라우팅 가능 — 우선 차단)
_OUT_OF_SCOPE_PATTERNS = [
    r"주식\s*추천",
    r"의료\s*상담",
    r"법률\s*자문",
]


def check_guardrail(user_query: str) -> dict[str, Any]:
    """
    사용자 입력을 인젝션 / 도메인 외 패턴으로 검사한다.
    Returns: {"blocked": bool, "reason": str | None, "matched_pattern": str | None}
    """
    text = user_query or ""
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return {
                "blocked": True,
                "reason": "prompt_injection_suspected",
                "matched_pattern": pat,
            }
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return {
                "blocked": True,
                "reason": "out_of_scope",
                "matched_pattern": pat,
            }
    return {"blocked": False, "reason": None, "matched_pattern": None}


# ── Tool Trace 추출 ────────────────────────────────────────

def _extract_tool_trace(messages: list) -> list[dict]:
    """
    LangGraph 실행 messages에서 (AIMessage tool_call -> ToolMessage 결과) 쌍을 추출한다.
    각 호출의 출처(data_source, endpoint)와 ok 여부를 짧게 요약한다.
    """
    # tool_call_id → tool_name 매핑
    call_id_to_name: dict[str, str] = {}
    for m in messages:
        if isinstance(m, AIMessage):
            tool_calls = getattr(m, "tool_calls", None) or []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    call_id_to_name[tc.get("id", "")] = tc.get("name", "unknown")
                else:
                    call_id_to_name[getattr(tc, "id", "")] = getattr(tc, "name", "unknown")

    trace: list[dict] = []
    step = 0
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        step += 1
        tool_name = call_id_to_name.get(m.tool_call_id, "unknown")
        content = m.content
        # ToolMessage.content는 str이지만 우리 tool들은 dict를 반환 → str(dict) 형태
        parsed: dict = {}
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
                # python repr 형태일 수도 있음
                try:
                    import ast

                    parsed = ast.literal_eval(content)
                except Exception:
                    parsed = {}
        elif isinstance(content, dict):
            parsed = content

        ok = parsed.get("ok") if isinstance(parsed, dict) else None
        data = parsed.get("data") if isinstance(parsed, dict) else None
        provenance = None
        if isinstance(data, dict):
            provenance = data.get("source_provenance")
        err = parsed.get("error") if isinstance(parsed, dict) else None

        trace.append({
            "step": step,
            "tool": tool_name,
            "ok": ok,
            "source_provenance": provenance,
            "error_code": (err or {}).get("code") if isinstance(err, dict) else None,
        })
    return trace


# ── 실행 함수 ──────────────────────────────────────────────

def run_agent(
    user_query: str,
    trend_focus: str = "both",
    difficulty_limit: str | None = None,
    exclude_existing: bool = False,
) -> dict[str, Any]:
    """
    Agent를 실행하고 최종 브리핑 결과를 반환한다.
    """
    # 가드레일 선검사
    guard = check_guardrail(user_query)
    if guard["blocked"]:
        print(f"\n[가드레일 차단] reason={guard['reason']} pattern={guard['matched_pattern']}")
        return {
            "today_brief": None,
            "metadata": {
                "used_tools": [],
                "loop_count": 0,
                "failure_type": "guardrail_blocked",
                "fallback_action": "요청 거부 — 도메인 외 또는 인젝션 의심",
                "guardrail": guard,
                "tool_trace": [],
            },
        }

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

    accumulated_messages: list = []
    try:
        # stream 모드로 실행하여 매 step의 messages를 누적
        # → recursion_limit 등 예외 발생 시에도 부분 trace를 보존 가능
        for chunk in agent.stream(
            {"messages": [HumanMessage(content=input_context)]},
            config={"recursion_limit": 15},
            stream_mode="values",
        ):
            accumulated_messages = chunk.get("messages", accumulated_messages)

        messages = accumulated_messages
        final_message = messages[-1].content if messages else ""
        tool_trace = _extract_tool_trace(messages)

        print(f"\n[Agent 완료] 총 메시지 수: {len(messages)} / Tool 호출 수: {len(tool_trace)}")

        # JSON 파싱 시도
        try:
            if "```json" in final_message:
                json_str = final_message.split("```json")[1].split("```")[0].strip()
            elif "```" in final_message:
                json_str = final_message.split("```")[1].split("```")[0].strip()
            else:
                json_str = final_message.strip()

            parsed = json.loads(json_str)

            # tool_trace를 metadata에 주입
            parsed.setdefault("metadata", {})
            parsed["metadata"]["tool_trace"] = tool_trace
            return parsed

        except json.JSONDecodeError:
            return {
                "today_brief": None,
                "metadata": {
                    "used_tools": [t["tool"] for t in tool_trace],
                    "loop_count": 0,
                    "failure_type": "json_parse_error",
                    "fallback_action": "raw_response",
                    "raw_response": final_message,
                    "tool_trace": tool_trace,
                },
            }

    except Exception as e:
        print(f"[Agent 오류] {e}")
        # 예외 발생 직전까지 누적된 부분 trace는 보존
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []
        return {
            "today_brief": None,
            "metadata": {
                "used_tools": [t["tool"] for t in partial_trace],
                "loop_count": 0,
                "failure_type": "agent_error",
                "fallback_action": None,
                "error": str(e),
                "tool_trace": partial_trace,
            },
        }
