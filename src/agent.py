import os
import json
import re
import time
import uuid
from typing import Any, Callable
from dotenv import load_dotenv

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from src.tools import tools
from src.tools.app_existence_checker import map_human_decision
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.observability import save_trace

load_dotenv()


# ── 커스텀 예외 ────────────────────────────────────────────

class LoopLimitReached(Exception):
    def __init__(self, count: int):
        self.count = count
        super().__init__(f"concept_generator {count}회 호출 초과")


# ── LLM 초기화 ─────────────────────────────────────────────

_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0,
    max_tokens=4096,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY")
)


# ── 9주차 최적화: Tool result 축소 ─────────────────────────
# source_provenance는 Observability용 메타데이터.
# LLM 판단(ok, data 핵심 필드)에는 불필요하므로 제거하여
# 누적되는 input token을 줄인다.

def _slim_tool_observations(messages: list) -> list:
    """
    LLM에 전달되기 직전, ToolMessage에서 source_provenance를 제거한다.
    accumulated_messages (원본)에는 source_provenance가 보존되므로
    trace 파일 및 _extract_tool_trace에 영향 없음.
    """
    result = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            try:
                content = json.loads(msg.content)
                if isinstance(content, dict) and isinstance(content.get("data"), dict):
                    slim_data = {
                        k: v for k, v in content["data"].items()
                        if k != "source_provenance"
                    }
                    slim_content = {**content, "data": slim_data}
                    result.append(ToolMessage(
                        content=json.dumps(slim_content, ensure_ascii=False),
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", msg.tool_call_id),
                        status=getattr(msg, "status", "success"),
                    ))
                else:
                    result.append(msg)
            except Exception:
                result.append(msg)
        else:
            result.append(msg)
    return result

def _build_prompt(state: dict | list) -> list:
    """
    매 LLM 호출 전 실행:
    1. source_provenance 제거 (tool result 축소)
    2. SystemMessage + cache_control 추가 (prompt caching)
    """
    messages = state["messages"] if isinstance(state, dict) else state
    slimmed = _slim_tool_observations(messages)
    return [
        SystemMessage(content=[{           # ← content block 형식
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }])
    ] + slimmed

# ── Agent 생성 ─────────────────────────────────────────────

# checkpointer: Human-in-the-loop interrupt(애매 구간 사용자 확인)에 필요.
# interrupt → 일시정지 → Command(resume)로 재개하려면 thread 단위 상태 저장이 필수.
_checkpointer = MemorySaver()

agent = create_react_agent(
    model=_llm,
    tools=tools,
    prompt=_build_prompt,   # ← 9주차: callable로 교체
    checkpointer=_checkpointer,
)


# ── Human-in-the-loop: 애매 구간 사용자 확인 (CLI) ──────────

def _default_human_input(evidence: dict) -> str:
    """interrupt 근거(evidence)를 CLI에 출력하고 사용자 입력을 받는다.

    design_v2.md 섹션 5: 단순 "유사 앱 있음"이 아니라 근거(앱명·유사도·겹치는 점)를
    함께 제시한다. 엔터/‘진행’ → 그대로 진행, ‘재탐색’/‘research’ → 루프백.
    """
    print("\n" + "=" * 50)
    print("⚠️  유사한 앱이 있을 수 있습니다 — 사용자 확인이 필요합니다")
    print(f"  의미 유사도 점수: {evidence.get('similarity_score')}")
    for a in evidence.get("similar_apps", []):
        print(f"   • {a.get('name')} ({a.get('source')}, 유사도 {a.get('similarity_score')})")
        if a.get("overlap"):
            print(f"     겹치는 점: {a.get('overlap')}")
        if a.get("url"):
            print(f"     {a.get('url')}")
    print("  → [그대로 진행]: 엔터 또는 '진행'   |   [재탐색]: '재탐색' 입력")
    print("=" * 50)
    try:
        return input("선택 > ").strip() or "proceed"
    except EOFError:
        return "proceed"


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
    """각 Tool의 핵심 관찰 결과 요약 (8주차 피드백)"""
    if not isinstance(data, dict):
        return None

    if tool_name == "trend_scanner":
        meme = [t.get("keyword") for t in data.get("meme_trends", []) if t.get("keyword")]
        it = [t.get("keyword") for t in data.get("it_trends", []) if t.get("keyword")]
        return {
            "top_meme_keywords": meme[:3],
            "top_it_keywords": it[:3],
            "partial_failure": data.get("partial_failure", []),
        }
    if tool_name == "concept_generator":
        return {
            "app_name": data.get("app_name"),
            "description": data.get("description"),
            "core_feature": data.get("core_feature"),
            "app_form": data.get("app_form"),
            "concept_basis": data.get("concept_basis"),
            "scope_check": data.get("scope_check"),
        }
    if tool_name == "app_existence_checker":
        similar = data.get("similar_apps", [])
        return {
            "similar_app_found": data.get("similar_app_found"),
            "similar_app_names": [a.get("name") for a in similar if a.get("name")],
            "similarity_score": data.get("similarity_score"),
            "decision_band": data.get("decision_band"),
            "next_action": "concept_generator 재호출 (루프백)" if data.get("similar_app_found") else "feasibility_checker 진행",
        }
    if tool_name == "feasibility_checker":
        return {
            "app_name": data.get("app_name"),
            "difficulty": data.get("difficulty"),
            "stack": data.get("stack"),
            "differentiation": data.get("differentiation"),
            "difficulty_limit_exceeded": data.get("difficulty_limit_exceeded"),
            "spec_path": data.get("spec_path"),
            "brief_path": data.get("brief_path"),
            "vibe_coding_tip": data.get("vibe_coding_tip"),
        }
    return None


def _get_tool_name(tool_msg: ToolMessage, messages: list) -> str:
    """ToolMessage의 tool_call_id로 AIMessage에서 tool name을 역추적한다."""
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                if tc_id == tool_msg.tool_call_id:
                    return tc_name
    return "unknown"


def _extract_tool_trace(messages: list) -> list[dict]:
    """
    accumulated_messages (원본, source_provenance 포함)에서 trace 추출.
    _slim_tool_observations로 slimming한 버전이 아닌 원본을 사용하므로
    provenance 정보가 그대로 보존됨.
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
        key_result = _extract_key_result(tool_name, data) if isinstance(data, dict) else None

        trace.append({
            "step": step,
            "tool": tool_name,
            "ok": ok,
            "key_result": key_result,
            "source_provenance": provenance,
            "error_code": (err or {}).get("code") if isinstance(err, dict) else None,
        })
    return trace


# ── 최종 브리핑 코드 조립 (LLM 비거치) ──────────────────────
# tool_trace의 key_result만으로 today_brief를 결정적으로 조립한다.
# LLM이 JSON을 생성/파싱하던 단계를 제거 → 출력 스키마 안정화 + 호출 1회 절감.

def _collect_sources(trace: list[dict]) -> dict:
    """trend_scanner provenance에서 출처 URL/소스를 모은다."""
    meme_src, it_src = [], []
    for t in trace:
        if t["tool"] != "trend_scanner":
            continue
        prov = t.get("source_provenance") or {}
        for name in ("reddit", "youtube"):
            ep = (prov.get(name) or {}).get("endpoint")
            if ep:
                meme_src.append(ep)
        for name in ("hackernews", "github"):
            ep = (prov.get(name) or {}).get("endpoint")
            if ep:
                it_src.append(ep)
    return {"meme": meme_src, "it": it_src}


def assemble_today_brief(trace: list[dict]) -> dict:
    """tool_trace로부터 today_brief를 코드로 조립한다 (LLM 미사용).

    - trend_scanner: 최신 호출의 meme/it 키워드를 트렌드로
    - concept_generator: 생성된 각 컨셉 (app_name 기준)
    - app_existence_checker: 컨셉의 유사앱 여부/점수 (순서로 매칭)
    - feasibility_checker: 난이도/스택/차별점/킥오프 경로 (app_name으로 매칭)
    """
    meme_trend, it_trend = [], []
    for t in trace:
        if t["tool"] == "trend_scanner" and t.get("key_result"):
            kr = t["key_result"]
            if kr.get("top_meme_keywords"):
                meme_trend = kr["top_meme_keywords"]
            if kr.get("top_it_keywords"):
                it_trend = kr["top_it_keywords"]

    # concept_generator 결과를 순서대로 수집 (app_name 기준 최종본만 유지)
    concepts: list[dict] = []
    concept_index: dict[str, int] = {}
    for t in trace:
        kr = t.get("key_result") or {}
        if t["tool"] == "concept_generator" and kr.get("app_name"):
            name = kr["app_name"]
            entry = {
                "app_name": name,
                "description": kr.get("description"),
                "core_feature": kr.get("core_feature"),
                "app_form": kr.get("app_form"),
                "similar_app_exists": False,
                "similarity_score": None,
                "difficulty": None,
                "stack": [],
                "differentiation": None,
                "kickoff": {},
            }
            if name in concept_index:
                concepts[concept_index[name]] = entry
            else:
                concept_index[name] = len(concepts)
                concepts.append(entry)

    # app_existence_checker 결과를 컨셉에 순서대로 매핑
    checker_results = [t["key_result"] for t in trace
                       if t["tool"] == "app_existence_checker" and t.get("key_result")]
    for i, chk in enumerate(checker_results):
        if i < len(concepts):
            concepts[i]["similar_app_exists"] = bool(chk.get("similar_app_found"))
            concepts[i]["similarity_score"] = chk.get("similarity_score")

    # feasibility_checker 결과를 app_name으로 매핑
    for t in trace:
        if t["tool"] != "feasibility_checker":
            continue
        kr = t.get("key_result") or {}
        name = kr.get("app_name")
        idx = concept_index.get(name)
        if idx is None and concepts:
            idx = len(concepts) - 1  # app_name 누락 시 마지막 컨셉에 귀속
        if idx is not None:
            concepts[idx]["difficulty"] = kr.get("difficulty")
            concepts[idx]["stack"] = kr.get("stack", [])
            concepts[idx]["differentiation"] = kr.get("differentiation")
            if kr.get("spec_path") or kr.get("brief_path"):
                concepts[idx]["kickoff"] = {
                    "spec_path": kr.get("spec_path"),
                    "brief_path": kr.get("brief_path"),
                }

    return {
        "meme_trend": meme_trend,
        "it_trend": it_trend,
        "concepts": concepts,
        "sources": _collect_sources(trace),
    }


# ── 실행 함수 ──────────────────────────────────────────────

def run_agent(
    user_query: str,
    trend_focus: str = "both",
    difficulty_limit: str | None = None,
    exclude_existing: bool = False,
    human_input_fn: Callable[[dict], str] | None = None,
) -> dict[str, Any]:
    started_at = time.time()
    human_input_fn = human_input_fn or _default_human_input
    human_decisions: list[dict] = []

    # 가드레일
    guard = check_guardrail(user_query)
    if guard["blocked"]:
        print(f"\n[가드레일 차단] reason={guard['reason']}")
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
            user_input=user_query, trend_focus=trend_focus,
            difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
            messages=[], final_result=result, started_at=started_at,
            stop_reason="guardrail_blocked", guardrail_blocked=True,
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
    concept_gen_count = 0
    MAX_CONCEPT_LOOP = 3
    # checkpointer 사용 → thread 단위 상태 저장 (interrupt 재개에 필요)
    config = {"recursion_limit": 25, "configurable": {"thread_id": uuid.uuid4().hex}}

    def _drain_stream(payload) -> dict | None:
        """스트림을 소진하며 메시지/루프카운트를 갱신한다.
        애매 구간 interrupt가 발생하면 그 evidence(value)를 반환, 아니면 None."""
        nonlocal accumulated_messages, concept_gen_count
        pending = None
        for chunk in agent.stream(payload, config=config, stream_mode="values"):
            if isinstance(chunk, dict) and "__interrupt__" in chunk:
                pending = chunk["__interrupt__"][0].value
                if chunk.get("messages"):
                    accumulated_messages = chunk["messages"]
                continue
            accumulated_messages = chunk.get("messages", accumulated_messages)

            concept_gen_count = sum(
                1 for m in accumulated_messages
                if isinstance(m, ToolMessage)
                and _get_tool_name(m, accumulated_messages) == "concept_generator"
            )
            if concept_gen_count >= MAX_CONCEPT_LOOP:
                print(f"[루프 탈출] concept_generator {concept_gen_count}회 호출 감지")
                raise LoopLimitReached(concept_gen_count)
        return pending

    try:
        payload: Any = {"messages": [HumanMessage(content=input_context)]}
        while True:
            pending = _drain_stream(payload)
            if pending is None:
                break
            # ── Human-in-the-loop: 애매 구간 사용자 확인 ──
            decision_raw = human_input_fn(pending)
            looped_back = map_human_decision(decision_raw)
            human_decisions.append({
                "app_name": pending.get("concept"),
                "similarity_score": pending.get("similarity_score"),
                "decision": "research" if looped_back else "proceed",
            })
            print(f"[사용자 확인] {'재탐색(루프백)' if looped_back else '그대로 진행'} 선택")
            payload = Command(resume=decision_raw)

        messages = accumulated_messages
        tool_trace = _extract_tool_trace(messages)

        print(f"\n[Agent 완료] 총 메시지 수: {len(messages)} / Tool 호출 수: {len(tool_trace)}")

        # ── 최종 브리핑을 코드로 조립 (LLM JSON 생성/파싱 제거) ──
        today_brief = assemble_today_brief(tool_trace)
        used_tools = list(dict.fromkeys(t["tool"] for t in tool_trace))
        loop_count = max(0, sum(1 for t in tool_trace if t["tool"] == "concept_generator") - 1)

        result = {
            "today_brief": today_brief,
            "metadata": {
                "used_tools": used_tools,
                "loop_count": loop_count,
                "failure_type": None,
                "fallback_action": None,
                "sources": today_brief.get("sources", {}),
                "tool_trace": tool_trace,
                "human_decisions": human_decisions,
            },
        }
        save_trace(
            user_input=user_query, trend_focus=trend_focus,
            difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
            messages=messages, final_result=result, started_at=started_at,
            stop_reason="final_answer",
        )
        return result

    except LoopLimitReached as e:
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []
        concepts_so_far = []
        for t in partial_trace:
            if t["tool"] == "concept_generator" and t.get("key_result"):
                name = t["key_result"].get("app_name")
                desc = t["key_result"].get("description")
                if name:
                    concepts_so_far.append({"app_name": name, "description": desc})

        result = {
            "today_brief": {
                "meme_trend": [], "it_trend": [],
                "concepts": concepts_so_far,
                "notice": f"동일 조합 {e.count}회 재시도로 루프 탈출",
            },
            "metadata": {
                "used_tools": list(dict.fromkeys(t["tool"] for t in partial_trace)),
                "loop_count": e.count,
                "failure_type": "loop_overflow",
                "fallback_action": f"동일 조합 {e.count}회 초과 — 차별화 포인트 제안으로 대체",
                "tool_trace": partial_trace,
                "human_decisions": human_decisions,
            },
        }
        save_trace(
            user_input=user_query, trend_focus=trend_focus,
            difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
            messages=accumulated_messages, final_result=result,
            started_at=started_at, stop_reason="loop_overflow",
        )
        return result

    except Exception as e:
        error_str = str(e)
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []

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
                    "meme_trend": [], "it_trend": [],
                    "concepts": concepts_so_far,
                    "notice": "유사 앱 탐색 반복으로 최대 단계에 도달했습니다. 조건을 완화하거나 다른 트렌드로 재시도해주세요.",
                },
                "metadata": {
                    "used_tools": list(dict.fromkeys(t["tool"] for t in partial_trace)),
                    "loop_count": sum(1 for t in partial_trace if t["tool"] == "concept_generator"),
                    "failure_type": "loop_overflow",
                    "fallback_action": "부분 결과 반환 — 루프 제한 도달. exclude_existing 해제 또는 difficulty_limit 완화 권장",
                    "tool_trace": partial_trace,
                    "human_decisions": human_decisions,
                },
            }
            save_trace(
                user_input=user_query, trend_focus=trend_focus,
                difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
                messages=accumulated_messages, final_result=result,
                started_at=started_at, stop_reason="loop_overflow",
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
                    "human_decisions": human_decisions,
                },
            }
            save_trace(
                user_input=user_query, trend_focus=trend_focus,
                difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
                messages=accumulated_messages, final_result=result,
                started_at=started_at, stop_reason="agent_error",
            )
        return result