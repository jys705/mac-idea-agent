import requests
from typing import Any
from langchain_core.tools import tool


# ── 실제 API 호출 함수 ──────────────────────────────────────

def _search_appstore(query: str) -> list[dict]:
    """iTunes Search API 실제 호출"""
    try:
        res = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "entity": "macSoftware",
                "limit": 5
            },
            timeout=5
        )
        results = res.json().get("results", [])
        return [
            {
                "name": r.get("trackName", ""),
                "description": r.get("description", "")[:100],
                "rating": r.get("averageUserRating", 0),
                "source": "appstore",
                "url": r.get("trackViewUrl", "")
            }
            for r in results
        ]
    except Exception as e:
        return [{"error": str(e), "source": "appstore"}]


def _search_github(query: str) -> list[dict]:
    """GitHub Search API 실제 호출"""
    try:
        res = requests.get(
            "https://api.github.com/search/repositories",
            params={
                "q": f"{query} topic:macos",
                "sort": "stars",
                "order": "desc",
                "per_page": 5
            },
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5
        )
        items = res.json().get("items", [])
        return [
            {
                "name": item["name"],
                "description": (item.get("description") or "")[:100],
                "stars": item["stargazers_count"],
                "source": "github",
                "url": item["html_url"]
            }
            for item in items
        ]
    except Exception as e:
        return [{"error": str(e), "source": "github"}]


def _is_similar(query: str, name: str, description: str) -> bool:
    """단순 키워드 기반 유사도 판단"""
    query_words = set(query.lower().split())
    target = (name + " " + description).lower()
    matches = sum(1 for w in query_words if w in target)
    return matches >= max(1, len(query_words) // 2)


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def app_existence_checker(concept: str, description: str = "") -> dict[str, Any]:
    """
    생성된 앱 컨셉과 유사한 앱이 Mac App Store 또는 GitHub에 이미 존재하는지 확인한다.
    유사 앱 발견 시 concept_generator를 재호출(루프백)하도록 similar_app_found=True를 반환한다.

    Args:
        concept: 확인할 앱 이름 또는 컨셉 키워드
        description: 앱 설명 (유사도 판단에 활용)

    Returns:
        ok: 성공 여부
        data: similar_app_found, similar_apps 목록
        error: 실패 시 에러 정보
    """
    query = f"{concept} {description}".strip()

    appstore_results = _search_appstore(concept)
    github_results = _search_github(concept)

    # 에러 체크
    appstore_failed = any("error" in r for r in appstore_results)
    github_failed = any("error" in r for r in github_results)

    if appstore_failed and github_failed:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "SEARCH_FAILED",
                "message": "App Store + GitHub 검색 모두 실패",
                "fallback_action": "GitHub 단독 재시도 권장"
            }
        }

    # 유사 앱 판단
    similar_apps = []

    if not appstore_failed:
        for r in appstore_results:
            if _is_similar(query, r.get("name", ""), r.get("description", "")):
                similar_apps.append({
                    **r,
                    "similarity": "키워드 매칭"
                })

    if not github_failed:
        for r in github_results:
            if _is_similar(query, r.get("name", ""), r.get("description", "")):
                similar_apps.append({
                    **r,
                    "similarity": "키워드 매칭"
                })

    return {
        "ok": True,
        "data": {
            "similar_app_found": len(similar_apps) > 0,
            "similar_apps": similar_apps,
            "searched": {
                "appstore": not appstore_failed,
                "github": not github_failed
            }
        },
        "error": None
    }