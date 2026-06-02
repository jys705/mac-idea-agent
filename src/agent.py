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

def _extract_key_result(tool_name: str, data: dict) -> dict | None:
    """
    8주차 피드백 반영: 각 Tool의 핵심 관찰 결과(observation)를 요약한다.
    "단순히 기능이 호출됐다"가 아니라 "결과를 보고 다음 판단이 어떻게 바뀌었는지"
    파악할 수 있도록 핵심 필드만 추출한다.
    """
    if not isinstance(data, dict):
        return None

    if tool_name == "trend_scanner":
        meme = [t.get("keyword") for t in data.get("meme_trends", []) if t.get("keyword")]
        it = [t.get("keyword") for t in data.get("it_trends", []) if t.get("keyword")]
        partial = data.get("partial_failure", [])
        return {
            "top_meme_keywords": meme[:3],
            "top_it_keywords": it[:3],
            "partial_failure": partial,
        }

    if tool_name == "concept_generator":
        return {
            "app_name": data.get("app_name"),
            "description": data.get("description"),
            "concept_basis": data.get("concept_basis"),
        }

    if tool_name == "app_existence_checker":
        similar = data.get("similar_apps", [])
        return {
            "similar_app_found": data.get("similar_app_found"),
            "similar_app_names": [a.get("name") for a in similar if a.get("name")],
            "next_action": "concept_generator 재호출 (루프백)" if data.get("similar_app_found") else "feasibility_checker 진행",
        }

    if tool_name == "feasibility_checker":
        return {
            "difficulty": data.get("difficulty"),
            "stack": data.get("stack"),
            "difficulty_limit_exceeded": data.get("difficulty_limit_exceeded"),
            "vibe_coding_tip": data.get("vibe_coding_tip"),
        }

    return None


def _extract_tool_trace(messages: list) -> list[dict]:
    """
    LangGraph 실행 messages에서 tool_call → tool_result 쌍을 추출한다.
    각 step에 key_result(핵심 관찰 결과)를 추가하여
    다음 판단이 어떻게 바뀌었는지 추적 가능하게 한다.
    """
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

        # 8주차 피드백: 핵심 관찰 결과 추출
        key_result = _extract_key_result(tool_name, data) if isinstance(data, dict) else None

        trace.append({
            "step": step,
            "tool": tool_name,
            "ok": ok,
            "key_result": key_result,           # ← 8주차 추가
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
        error_str = str(e)
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []

        # 7주차 피드백 반영: recursion_limit 발동 시 사용자 이해 가능한 fallback 응답
        if "Recursion limit" in error_str or "recursion_limit" in error_str.lower():
            print(f"[루프 제한 도달] {error_str}")
            concepts_so_far = []
            for t in partial_trace:
                if t["tool"] == "concept_generator" and t.get("key_result"):
                    name = t["key_result"].get("app_name")
                    desc = t["key_result"].get("description")
                    if name:
                        concepts_so_far.append({"app_name": name, "description": desc})

            result = {
                "today_brief": {
                    "meme_trend": [],
                    "it_trend": [],
                    "concepts": concepts_so_far,
                    "notice": "유사 앱 탐색 반복으로 최대 단계에 도달했습니다. 조건을 완화하거나 다른 트렌드로 재시도해주세요.",
                },
                "metadata": {
                    "used_tools": list(dict.fromkeys(t["tool"] for t in partial_trace)),
                    "loop_count": sum(1 for t in partial_trace if t["tool"] == "concept_generator"),
                    "failure_type": "loop_overflow",
                    "fallback_action": "부분 결과 반환 — 루프 제한 도달. exclude_existing 해제 또는 difficulty_limit 완화 권장",
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
                stop_reason="loop_overflow",
            )
        else:
            print(f"[Agent 오류] {error_str}")
            result = {
                "today_brief": None,
                "metadata": {
                    "used_tools": [t["tool"] for t in partial_trace],
                    "loop_count": 0,
                    "failure_type": "agent_error",
                    "fallback_action": None,
                    "error": error_str,
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