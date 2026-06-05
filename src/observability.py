"""
8~9주차 Observability 모듈
- LangSmith 자동 트레이싱 + 로컬 JSON 로그 저장
- latency, token, cache token 집계
- 민감정보 마스킹
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACES_DIR = Path(__file__).parent.parent / "traces"
TRACES_DIR.mkdir(exist_ok=True)

_MASK_KEYS = {"api_key", "token", "password", "secret", "authorization"}

def _mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: "***MASKED***" if k.lower() in _MASK_KEYS else _mask_sensitive(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_sensitive(i) for i in obj]
    return obj


def extract_tool_trace(messages: list) -> list[dict]:
    from langchain_core.messages import AIMessage, ToolMessage
    import json as _json

    trace = []
    step = 0
    call_id_to_name: dict[str, str] = {}

    for m in messages:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                cid = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                name = tc.get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                call_id_to_name[cid] = name

    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        step += 1
        tool_name = call_id_to_name.get(m.tool_call_id, "unknown")
        content = m.content
        parsed = {}
        if isinstance(content, str):
            try:
                parsed = _json.loads(content)
            except Exception:
                pass
        elif isinstance(content, dict):
            parsed = content

        ok = parsed.get("ok") if isinstance(parsed, dict) else None
        data = parsed.get("data") if isinstance(parsed, dict) else None
        provenance = data.get("source_provenance") if isinstance(data, dict) else None
        err = parsed.get("error") if isinstance(parsed, dict) else None

        trace.append({
            "step": step,
            "tool": tool_name,
            "result": "success" if ok else "error",
            "ok": ok,
            "source_provenance": provenance,
            "error_code": (err or {}).get("code") if isinstance(err, dict) else None,
            "latency_ms": None,
        })
    return trace


def aggregate_tokens(messages: list) -> dict:
    """전체 messages에서 token 사용량 합산 (캐시 토큰 포함)"""
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0

    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            details = usage.get("input_token_details", {}) or {}
            total_cache_creation += details.get("cache_creation", 0)
            total_cache_read += details.get("cache_read", 0)

    # claude-sonnet-4-6 기준 비용 계산
    # input: $3/MTok, output: $15/MTok
    # cache write: $3.75/MTok, cache read: $0.30/MTok
    estimated_cost = round(
        (total_input * 0.000003)
        + (total_output * 0.000015)
        + (total_cache_creation * 0.000003750)
        + (total_cache_read * 0.000000300),
        6
    )

    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "cache_creation_tokens": total_cache_creation,
        "cache_read_tokens": total_cache_read,
        "estimated_cost_usd": estimated_cost,
    }


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
    from langchain_core.messages import AIMessage, ToolMessage

    trace_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    total_latency_ms = round((time.time() - started_at) * 1000)

    tool_trace = extract_tool_trace(messages)
    token_usage = aggregate_tokens(messages)

    trace = {
        "trace_id": trace_id,
        "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
        "total_latency_ms": total_latency_ms,
        "request": {
            "user_input": user_input,
            "trend_focus": trend_focus,
            "difficulty_limit": difficulty_limit,
            "exclude_existing": exclude_existing,
        },
        "prompt_version": "system_prompt_v2",
        "model": {
            "agent": "claude-sonnet-4-6",
            "concept_generator": "claude-sonnet-4-6 (temperature=0.9)",
            "feasibility_checker": "claude-haiku-4-5-20251001 (temperature=0.3)",
        },
        "steps": tool_trace,
        "step_count": len(tool_trace),
        "stop_reason": stop_reason,
        "guardrail_blocked": guardrail_blocked,
        "final_answer": {
            "today_brief": final_result.get("today_brief"),
            "metadata": final_result.get("metadata"),
        },
        "metrics": {
            **token_usage,
            "total_latency_ms": total_latency_ms,
            "tool_error_count": sum(1 for t in tool_trace if not t.get("ok", True)),
        },
        "privacy": {
            "masked_fields": list(_MASK_KEYS),
            "excluded_fields": ["ANTHROPIC_API_KEY", "LANGCHAIN_API_KEY", "env_vars"],
        }
    }

    path = TRACES_DIR / f"{trace_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)

    cache_info = ""
    if token_usage["cache_creation_tokens"] or token_usage["cache_read_tokens"]:
        cache_info = f" | cache_write={token_usage['cache_creation_tokens']} cache_read={token_usage['cache_read_tokens']}"

    print(f"\n[Trace 저장] {path}")
    print(f"  총 latency: {total_latency_ms}ms | 토큰: {token_usage['total_tokens']}{cache_info} | Tool 호출: {len(tool_trace)}회")

    return trace_id