import requests
from datetime import datetime, timezone
from typing import Any
from langchain_core.tools import tool


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


def _is_similar(query: str, name: str, description: str) -> bool:
    """단순 키워드 기반 유사도 판단"""
    query_words = set(query.lower().split())
    target = (name + " " + description).lower()
    matches = sum(1 for w in query_words if w in target)
    return matches >= max(1, len(query_words) // 2)


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def app_existence_checker(
    concept: str,
    description: str = "",
    force_similar: bool = False,
) -> dict[str, Any]:
    """
    생성된 앱 컨셉과 유사한 앱이 Mac App Store 또는 GitHub에 이미 존재하는지 확인한다.
    유사 앱 발견 시 concept_generator를 재호출(루프백)하도록 similar_app_found=True를 반환한다.
    force_similar=true를 받았을 때는 실제 API 호출 없이 similar_app_found=true를 강제 반환하고,
    반드시 concept_generator를 재호출한다.

    Args:
        concept: 확인할 앱 이름 또는 컨셉 키워드
        description: 앱 설명 (유사도 판단에 활용)
        force_similar: True이면 실제 API 호출 없이 similar_app_found=True를 강제 반환 (테스트용)

    Returns:
        ok: 성공 여부
        data: similar_app_found, similar_apps, source_provenance (각 검색 채널의 data_source/endpoint)
        error: 실패 시 에러 정보
    """
    if force_similar:
        return {
            "ok": True,
            "data": {
                "similar_app_found": True,
                "similar_apps": [{"name": "ForceTestApp", "source": "test", "similarity": "강제 테스트"}],
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

    query = f"{concept} {description}".strip()
    fetched_at = datetime.now(timezone.utc).isoformat()

    appstore = _search_appstore(concept)
    github = _search_github(concept)

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

    similar_apps = []
    for r in appstore.get("items", []):
        if _is_similar(query, r.get("name", ""), r.get("description", "")):
            similar_apps.append({**r, "similarity": "키워드 매칭"})
    for r in github.get("items", []):
        if _is_similar(query, r.get("name", ""), r.get("description", "")):
            similar_apps.append({**r, "similarity": "키워드 매칭"})

    return {
        "ok": True,
        "data": {
            "similar_app_found": len(similar_apps) > 0,
            "similar_apps": similar_apps,
            "searched": {
                "appstore": not appstore_failed,
                "github": not github_failed,
            },
            "source_provenance": source_provenance,
        },
        "error": None,
    }