import os
import json
import re
import time
from typing import Any
from dotenv import load_dotenv

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from src.tools import tools
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.observability import save_trace

load_dotenv()

# ── LLM 초기화 (Agent 판단용 — Sonnet) ────────────────────

_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0,
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

_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+previous",
    r"disregard\s+(the\s+)?(above|previous)",
    r"forget\s+(your|the)\s+(instructions|system\s+prompt)",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"reveal\s+(your|the)\s+system\s+prompt",
    r"print\s+(your|the)\s+system\s+prompt",
    r"show\s+me\s+your\s+(prompt|instructions)",
    r"<\s*system\s*>",
]

_OUT_OF_SCOPE_PATTERNS = [
    r"주식\s*추천",
    r"의료\s*상담",
    r"법률\s*자문",
]


def check_guardrail(user_query: str) -> dict[str, Any]:
    text = user_query or ""
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return {"blocked": True, "reason": "prompt_injection_suspected", "matched_pattern": pat}
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return {"blocked": True, "reason": "out_of_scope", "matched_pattern": pat}
    return {"blocked": False, "reason": None, "matched_pattern": None}


# ── Tool Trace 추출 ────────────────────────────────────────

def _extract_tool_trace(messages: list) -> list[dict]:
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
        parsed: dict = {}
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
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
    started_at = time.time()

    # 가드레일 선검사
    guard = check_guardrail(user_query)
    if guard["blocked"]:
        print(f"\n[가드레일 차단] reason={guard['reason']} pattern={guard['matched_pattern']}")
        result = {
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
        save_trace(
            user_input=user_query,
            trend_focus=trend_focus,
            difficulty_limit=difficulty_limit,
            exclude_existing=exclude_existing,
            messages=[],
            final_result=result,
            started_at=started_at,
            stop_reason="guardrail_blocked",
            guardrail_blocked=True,
        )
        return result

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

        try:
            if "```json" in final_message:
                json_str = final_message.split("```json")[1].split("```")[0].strip()
            elif "```" in final_message:
                json_str = final_message.split("```")[1].split("```")[0].strip()
            else:
                json_str = final_message.strip()

            parsed = json.loads(json_str)
            parsed.setdefault("metadata", {})
            parsed["metadata"]["tool_trace"] = tool_trace

            save_trace(
                user_input=user_query,
                trend_focus=trend_focus,
                difficulty_limit=difficulty_limit,
                exclude_existing=exclude_existing,
                messages=messages,
                final_result=parsed,
                started_at=started_at,
                stop_reason="final_answer",
            )
            return parsed

        except json.JSONDecodeError:
            result = {
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
            save_trace(
                user_input=user_query,
                trend_focus=trend_focus,
                difficulty_limit=difficulty_limit,
                exclude_existing=exclude_existing,
                messages=messages,
                final_result=result,
                started_at=started_at,
                stop_reason="json_parse_error",
            )
            return result

    except Exception as e:
        print(f"[Agent 오류] {e}")
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []
        result = {
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
        save_trace(
            user_input=user_query,
            trend_focus=trend_focus,
            difficulty_limit=difficulty_limit,
            exclude_existing=exclude_existing,
            messages=accumulated_messages,
            final_result=result,
            started_at=started_at,
            stop_reason="agent_error",
        )
        return result