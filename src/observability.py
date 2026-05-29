"""
8주차 Observability 모듈
- LangSmith 자동 트레이싱과 별개로 로컬 JSON 로그 저장
- latency, token 사용량, tool_trace 집계
- 민감정보 마스킹
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# traces/ 저장 경로
TRACES_DIR = Path(__file__).parent.parent / "traces"
TRACES_DIR.mkdir(exist_ok=True)


# ── 민감정보 마스킹 ────────────────────────────────────────

_MASK_KEYS = {"api_key", "token", "password", "secret", "authorization"}

def _mask_sensitive(obj: Any) -> Any:
    """딕셔너리/리스트를 순회하며 민감 필드를 마스킹"""
    if isinstance(obj, dict):
        return {
            k: "***MASKED***" if k.lower() in _MASK_KEYS else _mask_sensitive(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_sensitive(i) for i in obj]
    return obj


# ── Tool Trace 추출 ────────────────────────────────────────

def extract_tool_trace(messages: list) -> list[dict]:
    """
    LangGraph messages에서 tool_call → tool_result 쌍을 추출하여
    step별 trace 리스트로 변환
    """
    trace = []
    step = 0

    for msg in messages:
        msg_type = getattr(msg, "type", None)

        # AI 메시지에서 tool_calls 추출
        if msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", [])
            usage = getattr(msg, "usage_metadata", {}) or {}
            for tc in tool_calls:
                step += 1
                trace.append({
                    "step": step,
                    "tool": tc.get("name", "unknown"),
                    "arguments": _mask_sensitive(tc.get("args", {})),
                    "result": None,       # tool 결과는 다음 메시지에서 채움
                    "ok": None,
                    "error_code": None,
                    "latency_ms": None,   # LangSmith에서 추출 가능, 로컬은 None
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                })

        # Tool 메시지에서 결과 추출
        elif msg_type == "tool":
            tool_call_id = getattr(msg, "tool_call_id", None)
            content = getattr(msg, "content", "")
            status = getattr(msg, "status", "success")

            # 매칭되는 step 찾아서 결과 채우기
            try:
                parsed = json.loads(content) if isinstance(content, str) else content
                ok = parsed.get("ok", True)
                error_code = parsed.get("error", {}).get("code") if not ok else None
            except (json.JSONDecodeError, AttributeError):
                ok = status == "success"
                error_code = None

            # 가장 최근 result=None인 step에 채우기
            for t in reversed(trace):
                if t["result"] is None:
                    t["result"] = "success" if ok else "error"
                    t["ok"] = ok
                    t["error_code"] = error_code
                    break

    return trace


# ── 토큰 집계 ──────────────────────────────────────────────

def aggregate_tokens(messages: list) -> dict:
    """전체 messages에서 token 사용량 합산"""
    total_input = 0
    total_output = 0

    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "estimated_cost_usd": round(
            (total_input * 0.000003) + (total_output * 0.000015), 6
        )  # claude-sonnet-4-6 기준 근사값
    }


# ── 메인 Trace 저장 함수 ───────────────────────────────────

def save_trace(
    user_input: str,
    trend_focus: str,
    difficulty_limit: str | None,
    exclude_existing: bool,
    messages: list,
    final_result: dict,
    started_at: float,
    stop_reason: str = "unknown",
    guardrail_blocked: bool = False,
) -> str:
    """
    실행 결과를 traces/{trace_id}.json으로 저장하고 trace_id 반환

    민감정보:
    - ANTHROPIC_API_KEY 등 환경변수는 저장하지 않음
    - user_input은 그대로 저장 (개인정보 미포함 도메인)
    """
    trace_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    total_latency_ms = round((time.time() - started_at) * 1000)

    tool_trace = extract_tool_trace(messages)
    token_usage = aggregate_tokens(messages)

    trace = {
        "trace_id": trace_id,
        "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
        "total_latency_ms": total_latency_ms,

        # Request
        "request": {
            "user_input": user_input,
            "trend_focus": trend_focus,
            "difficulty_limit": difficulty_limit,
            "exclude_existing": exclude_existing,
        },

        # Prompt version (민감정보 아님 — 공개 가능한 설정값)
        "prompt_version": "system_prompt_v2",
        "model": {
            "agent": "claude-sonnet-4-6",
            "concept_generator": "claude-sonnet-4-6 (temperature=0.9)",
            "feasibility_checker": "claude-haiku-4-5-20251001 (temperature=0.3)",
        },

        # Steps
        "steps": tool_trace,
        "step_count": len(tool_trace),

        # Output
        "stop_reason": stop_reason,
        "guardrail_blocked": guardrail_blocked,
        "final_answer": {
            "today_brief": final_result.get("today_brief"),
            "metadata": final_result.get("metadata"),
        },

        # Metrics
        "metrics": {
            **token_usage,
            "total_latency_ms": total_latency_ms,
            "tool_error_count": sum(1 for t in tool_trace if not t.get("ok", True)),
        },

        # 민감정보 처리 기록
        "privacy": {
            "masked_fields": list(_MASK_KEYS),
            "excluded_fields": ["ANTHROPIC_API_KEY", "LANGCHAIN_API_KEY", "env_vars"],
        }
    }

    path = TRACES_DIR / f"{trace_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)

    print(f"\n[Trace 저장] {path}")
    print(f"  총 latency: {total_latency_ms}ms | 토큰: {token_usage['total_tokens']} | Tool 호출: {len(tool_trace)}회")

    return trace_id