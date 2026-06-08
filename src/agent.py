import os
import json
import re
import time
from typing import Any, Callable
from dotenv import load_dotenv

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from src.tools import tools
from src.tools.feasibility_checker import feasibility_checker
from src.tools.app_existence_checker import CONFIRM_THRESHOLD
from src.prompts.system_prompt import SYSTEM_PROMPT
from src.observability import save_trace

# 기본 생성 컨셉 수(N). 추천 1위 + 밀린 후보 이력을 만들려면 2개 이상 필요.
DEFAULT_NUM_CONCEPTS = 3
# 코드 카운터 상한(무한 방지): 컨셉 생성·트렌드 재탐색.
MAX_CONCEPT_GEN = 6          # N=3 + 재생성 버퍼
MAX_RESEARCH_CALLS = 3       # 최초 1회 + 재탐색 2회 (★기능 B)

load_dotenv()


# ── LLM 초기화 ─────────────────────────────────────────────

_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0,
    # 이 LLM은 더 이상 최종 브리핑 JSON을 생성하지 않는다(assemble_today_brief가 코드로 조립).
    # tool 호출 결정 + 짧은 완료 보고만 하므로 1024로 충분하다.
    max_tokens=1024,
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

# ★기능 A: 중간 interrupt를 제거했으므로 MemorySaver 체크포인터도 정리한다.
# 사람 개입은 마지막 통합 선택 1회(human_input_fn, CLI input)로 이동했다.
agent = create_react_agent(
    model=_llm,
    tools=tools,
    prompt=_build_prompt,   # ← 9주차: callable로 교체
)


# ── Human-in-the-loop: 마지막 통합 선택 (CLI) ──────────────
# ★기능 A: 개입을 흐름 중간이 아니라 "마지막 한 번"으로 통합한다.
# 추천 1위 + 밀린 후보 이력을 제시하고 [수락/다른후보/패스]를 받는다.

def _fmt_critique(c: dict) -> str:
    crit = c.get("critique") or {}
    return f"실용 {crit.get('practicality', '?')} / 트렌드 {crit.get('trend_power', '?')}"


def _default_human_input(payload: dict) -> str:
    """추천 1위 + 밀린 후보 이력을 CLI에 출력하고 사용자 선택을 받는다.

    웹 전환 대비: run_agent는 이 함수를 주입받는다(human_input_fn). 반환 문자열은
    parse_user_selection으로 해석한다. 엔터/1=추천 수락, 2..N=다른 후보, p=패스.
    """
    candidates = payload.get("candidates") or []
    print("\n" + "=" * 60)
    print("🧑‍💻 오늘의 추천 — 마지막으로 하나만 고르세요 (중간엔 안 물어봤습니다)")
    print("=" * 60)
    for i, c in enumerate(candidates):
        tag = "⭐ 추천" if c.get("is_recommended") else f"   후보 {i + 1}"
        print(f"{tag}) {c.get('app_name')}  [{_fmt_critique(c)}]")
        if c.get("description"):
            print(f"      {c.get('description')}")
        if not c.get("is_recommended") and c.get("passed_reason"):
            print(f"      ↳ {c.get('passed_reason')}")
    print("-" * 60)
    print("  [엔터/1] 추천 수락   [2..] 다른 후보 선택   [p] 오늘은 패스(생성 안 함)")
    print("=" * 60)
    try:
        return input("선택 > ").strip()
    except EOFError:
        return ""


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
            "similar_apps": [
                {"name": a.get("name"), "source": a.get("source"),
                 "url": a.get("url"), "similarity_score": a.get("similarity_score")}
                for a in similar
            ],
            "similarity_score": data.get("similarity_score"),
            "decision_band": data.get("decision_band"),
        }
    if tool_name == "concept_critic":
        return {
            "app_name": data.get("app_name"),
            "practicality": data.get("practicality"),
            "trend_power": data.get("trend_power"),
            "peak": data.get("peak"),
            "comment": data.get("comment"),
            "verdict": data.get("verdict"),
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

    ★기능 A: 각 컨셉에 유사도(app_existence_checker)와 self-critique(concept_critic)를
    함께 붙인다. **어떤 컨셉도 버리지 않는다.** feasibility(SPEC/BRIEF)는 여기서 채우지
    않는다 — 마지막 통합 선택 뒤 코드가 선택된 1개에만 호출해 채운다(run_agent).
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
                "concept_basis": kr.get("concept_basis") or {},
                "similar_app_exists": False,
                "similarity_score": None,
                "similar_apps": [],
                "critique": None,
                "is_recommended": False,
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

    # app_existence_checker 결과를 컨셉에 순서대로 매핑 (점수만, 멈추지 않음)
    checker_results = [t["key_result"] for t in trace
                       if t["tool"] == "app_existence_checker" and t.get("key_result")]
    for i, chk in enumerate(checker_results):
        if i < len(concepts):
            concepts[i]["similar_app_exists"] = bool(chk.get("similar_app_found"))
            concepts[i]["similarity_score"] = chk.get("similarity_score")
            concepts[i]["similar_apps"] = chk.get("similar_apps", [])

    # concept_critic 결과 매핑 (app_name 우선, 없으면 순서)
    critic_results = [t["key_result"] for t in trace
                      if t["tool"] == "concept_critic" and t.get("key_result")]
    used = set()
    for cr in critic_results:
        name = cr.get("app_name")
        idx = concept_index.get(name)
        if idx is None or idx in used:
            # app_name 매칭 실패 → 아직 critique 없는 첫 컨셉에 귀속
            idx = next((j for j in range(len(concepts)) if j not in used), None)
        if idx is not None:
            concepts[idx]["critique"] = {
                "practicality": cr.get("practicality"),
                "trend_power": cr.get("trend_power"),
                "peak": cr.get("peak"),
                "comment": cr.get("comment"),
                "verdict": cr.get("verdict"),
            }
            used.add(idx)

    return {
        "meme_trend": meme_trend,
        "it_trend": it_trend,
        "concepts": concepts,
        "sources": _collect_sources(trace),
    }


# ── 추천 1위 산정 (코드, 결정적 — LLM 미사용) ──────────────
# 공식: peak=max(실용,트렌드) 높은 컨셉 우선, 단 "유사앱 의심"(유사도≥0.78 또는
# similar_app_found)이면 후순위로 민다. 둘 다 약함(peak≤2)도 자동 후순위.

def _is_similarity_suspect(concept: dict) -> bool:
    if concept.get("similar_app_exists"):
        return True
    sim = concept.get("similarity_score")
    return sim is not None and sim >= CONFIRM_THRESHOLD


def _peak_of(concept: dict) -> int:
    crit = concept.get("critique") or {}
    p, t = crit.get("practicality"), crit.get("trend_power")
    return max(int(p or 0), int(t or 0))


def rank_concepts(concepts: list[dict]) -> dict:
    """컨셉을 추천 우선순위로 정렬한다.

    Returns:
      order: concepts의 원본 인덱스를 순위대로 (order[0] = 추천 1위)
      recommended_index: 추천 1위의 원본 인덱스 (없으면 None)
      passed_over: 밀린 후보 [{app_name, reason}] (순위 순)
    """
    if not concepts:
        return {"order": [], "recommended_index": None, "passed_over": []}

    def sort_key(i: int):
        c = concepts[i]
        crit = c.get("critique") or {}
        peak = _peak_of(c)
        total = int(crit.get("practicality") or 0) + int(crit.get("trend_power") or 0)
        # (유사앱 의심 후순위) → (peak 높은 순) → (합 높은 순) → (입력 순서 안정)
        return (_is_similarity_suspect(c), -peak, -total, i)

    order = sorted(range(len(concepts)), key=sort_key)
    recommended_index = order[0]

    passed_over = []
    for idx in order:
        if idx == recommended_index:
            continue
        c = concepts[idx]
        if _is_similarity_suspect(c):
            names = ", ".join(a.get("name") for a in (c.get("similar_apps") or []) if a.get("name"))
            sim = c.get("similarity_score")
            reason = f"유사앱 의심으로 밀림 (유사도 {sim}{', ' + names if names else ''})"
        elif _peak_of(c) <= 2:
            crit = c.get("critique") or {}
            reason = (f"self-critique 낮아 밀림 "
                      f"(실용 {crit.get('practicality')} / 트렌드 {crit.get('trend_power')})")
        else:
            reason = "차순위"
        passed_over.append({"app_name": c.get("app_name"), "reason": reason})

    return {"order": order, "recommended_index": recommended_index, "passed_over": passed_over}


def parse_user_selection(raw: str, n: int) -> dict:
    """마지막 통합 선택 입력을 해석한다.

    엔터/1/y → 추천 수락(index 0) / 2..N → 다른 후보(pick) / p,pass,패스 → 패스.
    index는 '표시(=랭킹) 순서' 기준 0-base. 인식 불가 입력은 추천 수락으로 안전 처리.
    """
    t = (raw or "").strip().lower()
    if t in ("p", "pass", "패스", "n", "no", "skip", "ㅍ"):
        return {"action": "pass"}
    if t in ("", "1", "y", "yes", "accept", "수락", "ㅇ", "enter"):
        return {"action": "accept", "index": 0}
    if t.isdigit():
        k = int(t)
        if k == 1:
            return {"action": "accept", "index": 0}
        if 2 <= k <= n:
            return {"action": "pick", "index": k - 1}
    return {"action": "accept", "index": 0}


def _build_selection_payload(concepts: list[dict], ranking: dict) -> dict:
    """human_input_fn에 넘길 추천+이력 페이로드 (랭킹 순서)."""
    order = ranking["order"]
    rec_idx = ranking["recommended_index"]
    reason_by_name = {p["app_name"]: p["reason"] for p in ranking["passed_over"]}
    candidates = []
    for pos, idx in enumerate(order):
        c = concepts[idx]
        candidates.append({
            "app_name": c.get("app_name"),
            "description": c.get("description"),
            "critique": c.get("critique"),
            "similarity_score": c.get("similarity_score"),
            "is_recommended": idx == rec_idx,
            "passed_reason": reason_by_name.get(c.get("app_name")),
        })
    return {"candidates": candidates, "num": len(concepts)}


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
            tool_trace=[],
            stop_reason="guardrail_blocked", guardrail_blocked=True,
        )
        return result

    input_context = f"""사용자 요청: {user_query}

설정값:
- trend_focus: {trend_focus}
- difficulty_limit: {difficulty_limit if difficulty_limit else "제한 없음"}
- exclude_existing: {exclude_existing}
- 생성할 컨셉 수: {DEFAULT_NUM_CONCEPTS}개 (각각 유사앱 검사 + self-critique)

위 설정에 맞게 트렌드를 수집하고, 컨셉 {DEFAULT_NUM_CONCEPTS}개를 만들어
각각 app_existence_checker와 concept_critic을 호출하세요. 중간에 멈추거나 컨셉을 버리지 마세요.
feasibility_checker는 호출하지 마세요(선택 뒤 시스템이 처리합니다)."""

    print(f"\n{'='*50}")
    print(f"[Agent 시작] {user_query}")
    print(f"{'='*50}")

    accumulated_messages: list = []
    config = {"recursion_limit": 40}

    try:
        # ── 1) 리서치·생성·평가 단계 (LLM ReAct, 중간에 안 멈춤) ──
        forced_stop = None
        for chunk in agent.stream(
            {"messages": [HumanMessage(content=input_context)]},
            config=config, stream_mode="values",
        ):
            accumulated_messages = chunk.get("messages", accumulated_messages)
            cg = sum(1 for m in accumulated_messages if isinstance(m, ToolMessage)
                     and _get_tool_name(m, accumulated_messages) == "concept_generator")
            ts = sum(1 for m in accumulated_messages if isinstance(m, ToolMessage)
                     and _get_tool_name(m, accumulated_messages) == "trend_scanner")
            # 코드 카운터 강제(무한 방지): 컨셉 생성 / 트렌드 재탐색 상한 도달 시 우아하게 중단
            if cg > MAX_CONCEPT_GEN:
                forced_stop = "concept_gen_limit"
                print(f"[강제 중단] concept_generator {cg}회 — 상한({MAX_CONCEPT_GEN}) 초과")
                break
            if ts > MAX_RESEARCH_CALLS:
                forced_stop = "research_limit"
                print(f"[강제 중단] trend_scanner {ts}회 — 재탐색 상한({MAX_RESEARCH_CALLS}) 초과")
                break

        messages = accumulated_messages
        tool_trace = _extract_tool_trace(messages)
        print(f"\n[Agent 완료] 메시지 수: {len(messages)} / Tool 호출: {len(tool_trace)}")

        # ── 2) 컨셉 조립 (유사도·critique 포함, 아무것도 안 버림) ──
        today_brief = assemble_today_brief(tool_trace)
        concepts = today_brief["concepts"]

        # ── 3) 추천 1위 산정 (코드, 결정적) ──
        ranking = rank_concepts(concepts)
        rec_idx = ranking["recommended_index"]
        for i, c in enumerate(concepts):
            c["is_recommended"] = (i == rec_idx)

        recommendation = None
        if rec_idx is not None:
            rc = concepts[rec_idx]
            crit = rc.get("critique") or {}
            recommendation = {
                "app_name": rc.get("app_name"),
                "reason": f"실용 {crit.get('practicality')} / 트렌드 {crit.get('trend_power')}"
                          f"{'' if not _is_similarity_suspect(rc) else ' (유사앱 의심 있으나 최상위)'}",
            }

        # ── 4) 마지막 통합 선택 (Human-in-the-loop, 1회) ──
        user_selection = {"action": "pass"}
        chosen_concept_idx = None
        if concepts:
            payload = _build_selection_payload(concepts, ranking)
            raw = human_input_fn(payload)
            sel = parse_user_selection(raw, len(concepts))
            user_selection = sel
            if sel["action"] in ("accept", "pick"):
                ranked_pos = sel.get("index", 0)
                if 0 <= ranked_pos < len(ranking["order"]):
                    chosen_concept_idx = ranking["order"][ranked_pos]
            print(f"[사용자 선택] action={sel['action']}"
                  + (f" → {concepts[chosen_concept_idx]['app_name']}" if chosen_concept_idx is not None else " (패스)"))

        # ── 5) 선택된 1개에만 feasibility 호출 → SPEC.md/BRIEF.md (가장 마지막) ──
        if chosen_concept_idx is not None:
            c = concepts[chosen_concept_idx]
            basis = c.get("concept_basis") or {}
            feas = feasibility_checker.invoke({
                "concept": c.get("app_name"),
                "core_feature": c.get("core_feature") or "",
                "description": c.get("description") or "",
                "meme_trend": basis.get("meme", "") or "",
                "it_trend": basis.get("it", "") or "",
                "difficulty_limit": difficulty_limit,
            })
            if feas.get("ok"):
                fd = feas["data"]
                c["difficulty"] = fd.get("difficulty")
                c["stack"] = fd.get("stack", [])
                c["differentiation"] = fd.get("differentiation")
                if fd.get("spec_path") or fd.get("brief_path"):
                    c["kickoff"] = {"spec_path": fd.get("spec_path"),
                                    "brief_path": fd.get("brief_path")}

        # ── 6) 결과·메타데이터 조립 ──
        used_tools = list(dict.fromkeys(t["tool"] for t in tool_trace))
        cg_calls = sum(1 for t in tool_trace if t["tool"] == "concept_generator")
        research_rounds = sum(1 for t in tool_trace if t["tool"] == "trend_scanner")
        result = {
            "today_brief": today_brief,
            "metadata": {
                "used_tools": used_tools,
                "concepts_generated": len(concepts),
                "concept_gen_calls": cg_calls,
                # loop_count = 재생성(루프백) 횟수 = 호출수 − 유지된 컨셉 수 (정상 흐름 0)
                "loop_count": max(0, cg_calls - len(concepts)),
                "research_rounds": research_rounds,
                "recommendation": recommendation,
                "passed_over": ranking["passed_over"],
                "user_selection": user_selection,
                "failure_type": None if not forced_stop else "forced_stop",
                "fallback_action": None if not forced_stop else f"코드 상한 도달({forced_stop}) — 현재까지 결과로 진행",
                "sources": today_brief.get("sources", {}),
                "tool_trace": tool_trace,
            },
        }
        save_trace(
            user_input=user_query, trend_focus=trend_focus,
            difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
            messages=messages, final_result=result, started_at=started_at,
            tool_trace=tool_trace,
            stop_reason="user_pass" if user_selection.get("action") == "pass" else "final_answer",
        )
        return result

    except Exception as e:
        error_str = str(e)
        partial_trace = _extract_tool_trace(accumulated_messages) if accumulated_messages else []
        print(f"[Agent 오류] {error_str}")
        is_recursion = "Recursion limit" in error_str or "recursion_limit" in error_str.lower()
        result = {
            "today_brief": None,
            "metadata": {
                "used_tools": list(dict.fromkeys(t["tool"] for t in partial_trace)),
                "loop_count": 0,
                "concepts_generated": 0,
                "failure_type": "loop_overflow" if is_recursion else "agent_error",
                "fallback_action": "recursion_limit 도달 — 조건 완화 권장" if is_recursion else None,
                "error": error_str,
                "tool_trace": partial_trace,
            },
        }
        save_trace(
            user_input=user_query, trend_focus=trend_focus,
            difficulty_limit=difficulty_limit, exclude_existing=exclude_existing,
            messages=accumulated_messages, final_result=result,
            started_at=started_at, tool_trace=partial_trace,
            stop_reason="loop_overflow" if is_recursion else "agent_error",
        )
        return result