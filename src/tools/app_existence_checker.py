import requests
from datetime import datetime, timezone
from typing import Any, Callable, Sequence
from langchain_core.tools import tool

from src.tools.embeddings import embed, cosine_similarity, embedding_available


# ── 의미 유사도 임계값 (design_v2.md 섹션 6 — 3-tier 결정권자) ──
# Gemini(gemini-embedding-001) 실측 분포 기반 튜닝 (tmp_threshold_probe 측정):
#   DUP(명백중복)    : 0.813 ~ 0.886 (avg 0.852)
#   SAMECAT(애매)    : 0.765 ~ 0.830 (avg 0.805)
#   UNREL(무관)      : 0.705 ~ 0.770 (avg 0.731)
# Gemini는 점수를 전반적으로 높게 깔기 때문에(무관도 ~0.7) 초기값 0.85/0.65는
# 하한이 무용지물이었다. 실측 분포의 경계에 맞춰 재설정:
#   >= 0.85  : 자동 루프백 (명백 중복, DUP 상위) — LLM/Agent 결정
#   0.78~0.85: 사용자 확인 (애매, SAMECAT 구간) — Human-in-the-loop (interrupt)
#   < 0.78   : 자동 진행 (무관, UNREL) — 코드/Workflow 결정
AUTO_LOOPBACK_THRESHOLD = 0.85
CONFIRM_THRESHOLD = 0.78


# ── 실제 API 호출 함수 ──────────────────────────────────────

ITUNES_ENDPOINT = "https://itunes.apple.com/search"
GITHUB_ENDPOINT = "https://api.github.com/search/repositories"


def _search_appstore(query: str) -> dict:
    """iTunes Search API 실제 호출"""
    try:
        res = requests.get(
            ITUNES_ENDPOINT,
            params={"term": query, "entity": "macSoftware", "limit": 5},
            timeout=5,
        )
        results = res.json().get("results", [])
        items = [
            {
                "name": r.get("trackName", ""),
                "description": (r.get("description") or "")[:100],
                "rating": r.get("averageUserRating", 0),
                "source": "appstore",
                "url": r.get("trackViewUrl", ""),
            }
            for r in results
        ]
        return {"items": items, "data_source": "real_api", "endpoint": ITUNES_ENDPOINT}
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": ITUNES_ENDPOINT,
            "fallback_reason": f"appstore_request_failed: {e}",
        }


def _search_github(query: str) -> dict:
    """GitHub Search API 실제 호출"""
    try:
        res = requests.get(
            GITHUB_ENDPOINT,
            params={
                "q": f"{query} topic:macos",
                "sort": "stars",
                "order": "desc",
                "per_page": 5,
            },
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        items_raw = res.json().get("items", [])
        items = [
            {
                "name": item["name"],
                "description": (item.get("description") or "")[:100],
                "stars": item["stargazers_count"],
                "source": "github",
                "url": item["html_url"],
            }
            for item in items_raw
        ]
        return {"items": items, "data_source": "real_api", "endpoint": GITHUB_ENDPOINT}
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": GITHUB_ENDPOINT,
            "fallback_reason": f"github_request_failed: {e}",
        }


# ── 글자 매칭 (임베딩 불가 시 fallback 전용) ────────────────

def _is_similar(query: str, name: str, description: str) -> bool:
    """단순 키워드 기반 유사도 판단 — 임베딩 불가(키 미설정) 시 fallback."""
    query_words = set(query.lower().split())
    target = (name + " " + description).lower()
    matches = sum(1 for w in query_words if w in target)
    return matches >= max(1, len(query_words) // 2)


# ── 의미 유사도 순수 함수 (네트워크/그래프 없이 단위 테스트 가능) ──

def classify_band(score: float) -> str:
    """유사도 점수를 3-tier 결정 구간으로 분류한다."""
    if score >= AUTO_LOOPBACK_THRESHOLD:
        return "auto_loopback"
    if score >= CONFIRM_THRESHOLD:
        return "human_confirm"
    return "auto_proceed"


def map_human_decision(decision: str) -> bool:
    """사용자 확인 입력을 similar_app_found(루프백 여부)로 매핑한다.

    "재탐색"/"research"/"r" 류 → True (유사 앱으로 간주, 루프백)
    그 외("그대로 진행"/"proceed"/"p"/엔터) → False (진행)
    """
    text = (decision or "").strip().lower()
    research_tokens = {"research", "재탐색", "r", "regenerate", "redo", "again", "다시"}
    if text in research_tokens:
        return True
    # "재탐색" 같은 한국어 부분 일치도 허용
    if any(tok in text for tok in ("재탐색", "research", "regenerate")):
        return True
    return False


def score_candidates(
    concept_text: str,
    apps: list[dict],
    embed_fn: Callable[[Sequence[str]], list[list[float]]] | None = None,
) -> list[dict]:
    """내 컨셉과 각 후보 앱의 코사인 유사도를 계산해 점수를 부여한다.

    embed_fn을 주입하면 임베딩 구현을 갈아끼울 수 있다(테스트). None이면
    embeddings.embed(gemini-embedding-001)를 사용한다.
    반환: similarity_score가 부여된 앱 리스트(점수 내림차순).
    """
    if not apps:
        return []
    _embed = embed_fn or embed

    app_texts = [f"{a.get('name', '')} {a.get('description', '')}".strip() for a in apps]
    vectors = _embed([concept_text] + app_texts)
    concept_vec, app_vecs = vectors[0], vectors[1:]

    scored = []
    for app, vec in zip(apps, app_vecs):
        score = round(cosine_similarity(concept_vec, vec), 4)
        scored.append({**app, "similarity_score": score})
    scored.sort(key=lambda a: a["similarity_score"], reverse=True)
    return scored


def _build_evidence(top_apps: list[dict], top_score: float) -> dict:
    """interrupt 시 사용자에게 보여줄 근거(evidence)를 구성한다."""
    shown = []
    for a in top_apps[:3]:
        shown.append({
            "name": a.get("name"),
            "source": a.get("source"),
            "url": a.get("url"),
            "description": a.get("description"),
            "similarity_score": a.get("similarity_score"),
            "overlap": a.get("description"),
        })
    return {
        "type": "app_existence_confirmation",
        "message": "유사한 앱이 있을 수 있습니다. 그대로 진행할까요, 다시 찾을까요?",
        "similarity_score": top_score,
        "similar_apps": shown,
        "options": ["그대로 진행", "재탐색"],
    }


def _request_human_confirmation(evidence: dict) -> str:
    """LangGraph interrupt로 그래프를 일시정지하고 사용자 결정을 받는다.

    - 그래프 컨텍스트 안: interrupt()가 GraphInterrupt를 일으켜 그래프를 멈춘다.
      run_agent가 근거를 출력하고 input()으로 받은 값을 Command(resume=...)로 재개하면
      interrupt()가 그 값을 반환한다.
    - 그래프 컨텍스트 밖(테스트에서 Tool 직접 호출): RuntimeError → 안전 기본값 "proceed".
    """
    from langgraph.types import interrupt
    from langgraph.errors import GraphInterrupt

    try:
        return interrupt(evidence)
    except GraphInterrupt:
        raise  # 그래프 일시정지는 그대로 전파해야 한다
    except Exception:
        return "proceed"


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def app_existence_checker(
    concept: str,
    description: str = "",
    core_feature: str = "",
    force_similar: bool = False,
) -> dict[str, Any]:
    """
    생성된 앱 컨셉과 의미적으로 유사한 앱이 Mac App Store 또는 GitHub에 이미 존재하는지 확인한다.

    판정 방식 (글자 매칭이 아니라 의미 유사도):
    - 검색어는 지어낸 앱 이름이 아니라 "기능 설명(description + core_feature)"으로 만든다.
    - 내 컨셉과 검색된 각 앱을 임베딩하여 코사인 유사도(0~1)를 계산한다.
    - similarity_score 구간에 따라 결정 주체가 다르다:
        * >= 0.85 : 명백한 중복 → similar_app_found=true (concept_generator 재호출/루프백)
        * 0.65~0.85: 애매 → 사용자에게 근거를 보여주고 "그대로 진행/재탐색" 확인을 받는다
        * < 0.65 : 유사하지 않음 → similar_app_found=false (그대로 진행)
    similar_app_found=true가 반환되면 exclude_concepts에 해당 앱명을 넣어 concept_generator를 재호출한다.
    force_similar=true이면 실제 API 호출 없이 similar_app_found=true를 강제 반환한다(테스트용).

    Args:
        concept: 확인할 앱 이름 또는 컨셉 키워드 (식별/로그용)
        description: 앱의 기능 설명 — 검색어와 의미 유사도의 핵심 입력
        core_feature: 핵심 기능 — 의미 유사도 입력 보강
        force_similar: True이면 실제 API 호출 없이 similar_app_found=True를 강제 반환 (테스트용)

    Returns:
        ok: 성공 여부
        data: similar_app_found, similar_apps, similarity_score, decision_band,
              similarity_method, source_provenance
        error: 실패 시 에러 정보
    """
    if force_similar:
        return {
            "ok": True,
            "data": {
                "similar_app_found": True,
                "similar_apps": [{"name": "ForceTestApp", "source": "test", "similarity_score": 1.0, "overlap": "강제 테스트"}],
                "similarity_score": 1.0,
                "decision_band": "forced",
                "similarity_method": "forced",
                "searched": {"appstore": False, "github": False},
                "source_provenance": {
                    "appstore": {"data_source": "mock", "endpoint": None,
                                 "fallback_reason": "force_similar_test", "items_returned": 1},
                    "github": {"data_source": "mock", "endpoint": None,
                               "fallback_reason": "force_similar_test", "items_returned": 0},
                },
            },
            "error": None,
        }

    # 검색어 = 기능 설명 중심 (지어낸 앱 이름이 아니라). 설명이 비면 concept로 fallback.
    concept_text = f"{description} {core_feature}".strip() or concept
    search_query = (f"{description} {core_feature}".strip() or concept)
    fetched_at = datetime.now(timezone.utc).isoformat()

    appstore = _search_appstore(search_query)
    github = _search_github(search_query)

    appstore_failed = appstore["data_source"] == "fallback"
    github_failed = github["data_source"] == "fallback"

    source_provenance = {
        "appstore": {
            "data_source": appstore["data_source"],
            "endpoint": appstore.get("endpoint"),
            "fallback_reason": appstore.get("fallback_reason"),
            "items_returned": len(appstore.get("items", [])),
            "fetched_at": fetched_at,
        },
        "github": {
            "data_source": github["data_source"],
            "endpoint": github.get("endpoint"),
            "fallback_reason": github.get("fallback_reason"),
            "items_returned": len(github.get("items", [])),
            "fetched_at": fetched_at,
        },
    }

    if appstore_failed and github_failed:
        return {
            "ok": False,
            "data": {"source_provenance": source_provenance},
            "error": {
                "code": "SEARCH_FAILED",
                "message": "App Store + GitHub 검색 모두 실패",
                "fallback_action": "GitHub 단독 재시도 권장",
            },
        }

    candidates = list(appstore.get("items", [])) + list(github.get("items", []))

    # ── 임베딩 불가 환경 → 글자 매칭 fallback (interrupt 없음) ──
    if not embedding_available():
        similar_apps = [
            {**r, "similarity": "키워드 매칭"}
            for r in candidates
            if _is_similar(search_query, r.get("name", ""), r.get("description", ""))
        ]
        return {
            "ok": True,
            "data": {
                "similar_app_found": len(similar_apps) > 0,
                "similar_apps": similar_apps,
                "similarity_score": None,
                "decision_band": "substring_fallback",
                "similarity_method": "substring_fallback",
                "searched": {"appstore": not appstore_failed, "github": not github_failed},
                "source_provenance": source_provenance,
            },
            "error": None,
        }

    # ── 의미 유사도(임베딩) 경로 ──
    scored = score_candidates(concept_text, candidates)
    top_score = scored[0]["similarity_score"] if scored else 0.0
    band = classify_band(top_score)

    base_data = {
        "similarity_score": top_score,
        "similarity_method": "embedding",
        "searched": {"appstore": not appstore_failed, "github": not github_failed},
        "source_provenance": source_provenance,
    }

    if band == "auto_proceed":
        return {"ok": True, "error": None, "data": {
            **base_data,
            "similar_app_found": False,
            "similar_apps": [],
            "decision_band": "auto_proceed",
        }}

    if band == "auto_loopback":
        return {"ok": True, "error": None, "data": {
            **base_data,
            "similar_app_found": True,
            "similar_apps": scored[:3],
            "decision_band": "auto_loopback",
        }}

    # band == "human_confirm" → 애매 구간 → 사용자 확인 (interrupt)
    evidence = _build_evidence(scored, top_score)
    evidence["concept"] = concept
    decision = _request_human_confirmation(evidence)
    similar = map_human_decision(decision)
    return {"ok": True, "error": None, "data": {
        **base_data,
        "similar_app_found": similar,
        "similar_apps": scored[:3],
        "decision_band": "human_confirmed",
        "human_decision": "research" if similar else "proceed",
    }}
