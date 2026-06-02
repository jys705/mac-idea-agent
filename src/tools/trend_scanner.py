import os
import requests
from datetime import datetime, timezone
from typing import Any
from langchain_core.tools import tool

# ── 엔드포인트 상수 ────────────────────────────────────────

HN_ENDPOINT = "https://hacker-news.firebaseio.com/v0/topstories.json"
GITHUB_ENDPOINT = "https://api.github.com/search/repositories"
YOUTUBE_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"


# ── 실제 API 호출 함수 ──────────────────────────────────────

def _fetch_hackernews(limit: int = 5) -> dict:
    """HackerNews Top Stories 실제 API 호출"""
    try:
        res = requests.get(HN_ENDPOINT, timeout=5)
        ids = res.json()[:limit * 2]
        stories = []
        for story_id in ids:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=5
            )
            item = r.json()
            if item and item.get("type") == "story" and item.get("title"):
                stories.append({
                    "keyword": item["title"],
                    "source": "hackernews",
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "score": item.get("score", 0)
                })
            if len(stories) >= limit:
                break
        return {"items": stories, "data_source": "real_api", "endpoint": HN_ENDPOINT}
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": HN_ENDPOINT,
            "fallback_reason": f"hackernews_request_failed: {e}",
        }


def _fetch_github_trending(limit: int = 5) -> dict:
    """GitHub Trending 실제 API 호출"""
    try:
        res = requests.get(
            GITHUB_ENDPOINT,
            params={
                "q": "stars:>1000 pushed:>2026-01-01",
                "sort": "stars",
                "order": "desc",
                "per_page": limit,
            },
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        items = res.json().get("items", [])
        parsed = [
            {
                "keyword": f"{item['name']} ({item.get('language', 'unknown')})",
                "source": "github",
                "url": item["html_url"],
                "stars": item["stargazers_count"],
            }
            for item in items
        ]
        return {"items": parsed, "data_source": "real_api", "endpoint": GITHUB_ENDPOINT}
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": GITHUB_ENDPOINT,
            "fallback_reason": f"github_request_failed: {e}",
        }


def _fetch_youtube(limit: int = 5) -> dict:
    """YouTube Data API v3 mostPopular 실제 호출"""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return {
            "items": [],
            "data_source": "mock",
            "endpoint": None,
            "fallback_reason": "youtube_api_key_not_configured",
        }
    try:
        res = requests.get(
            YOUTUBE_ENDPOINT,
            params={
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "regionCode": "US",
                "maxResults": limit,
                "key": api_key,
            },
            timeout=5,
        )
        res.raise_for_status()
        raw_items = res.json().get("items", [])
        parsed = []
        for item in raw_items:
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            title = snippet.get("title", "")
            tags = snippet.get("tags", [])
            # 태그가 있으면 태그 기반, 없으면 제목 기반 키워드
            keyword = tags[0] if tags else title
            parsed.append({
                "keyword": keyword,
                "title": title,
                "source": "youtube",
                "url": f"https://youtube.com/watch?v={item.get('id', '')}",
                "views": int(stats.get("viewCount", 0)),
            })
        return {
            "items": parsed,
            "data_source": "real_api",
            "endpoint": YOUTUBE_ENDPOINT,
        }
    except Exception as e:
        # API 키는 있지만 호출 실패 → fallback (7주차 피드백: 실패 시 partial_failure 처리)
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": YOUTUBE_ENDPOINT,
            "fallback_reason": f"youtube_request_failed: {e}",
        }


def _fetch_reddit(limit: int = 5) -> dict:
    """meme-api.com 경유 Reddit 밈 트렌드 (OAuth 불필요, 무료)"""
    MEME_API_ENDPOINT = "https://meme-api.com/gimme/memes"
    try:
        res = requests.get(
            f"{MEME_API_ENDPOINT}/{limit}",
            timeout=5,
        )
        res.raise_for_status()
        memes = res.json().get("memes", [])
        items = [
            {
                "keyword": m["title"],
                "source": "reddit",
                "url": m["postLink"],
                "score": m.get("ups", 0),
            }
            for m in memes
            if not m.get("nsfw", False)
        ]
        return {
            "items": items,
            "data_source": "real_api",
            "endpoint": MEME_API_ENDPOINT,
        }
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": MEME_API_ENDPOINT,
            "fallback_reason": f"meme_api_request_failed: {e}",
        }


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def trend_scanner(trend_type: str = "both", limit: int = 5) -> dict[str, Any]:
    """
    Reddit·YouTube·GitHub·HackerNews에서 오늘의 밈 및 IT 트렌드 키워드를 실시간 수집한다.
    YouTube API Key가 설정된 경우 실제 API를 호출하고, 미설정 시 Mock으로 fallback한다.
    YouTube API 호출 실패 시 partial_failure로 기록하고 Reddit Mock으로 계속 진행한다.

    Args:
        trend_type: 수집 유형. "meme" = 밈만, "IT" = IT 트렌드만, "both" = 둘 다 (기본값)
        limit: 소스당 수집할 키워드 수 (기본값 5)

    Returns:
        ok: 성공 여부
        data: meme_trends, it_trends, source_provenance
        error: 실패 시 에러 정보
    """
    result: dict[str, Any] = {
        "meme_trends": [],
        "it_trends": [],
        "partial_failure": [],
        "source_provenance": {},
    }
    fetched_at = datetime.now(timezone.utc).isoformat()

    def _record(name: str, fetched: dict) -> None:
        result["source_provenance"][name] = {
            "data_source": fetched["data_source"],
            "endpoint": fetched.get("endpoint"),
            "fallback_reason": fetched.get("fallback_reason"),
            "items_returned": len(fetched.get("items", [])),
            "fetched_at": fetched_at,
        }

    # 밈 트렌드 수집
    if trend_type in ("meme", "both"):
        reddit = _fetch_reddit(limit)
        youtube = _fetch_youtube(limit)  # ← Mock → 실제 API
        _record("reddit", reddit)
        _record("youtube", youtube)

        if reddit["data_source"] == "fallback" and not reddit["items"]:
            result["partial_failure"].append("reddit")
        else:
            result["meme_trends"].extend(reddit["items"])

        if youtube["data_source"] == "fallback" and not youtube["items"]:
            result["partial_failure"].append("youtube")
        else:
            result["meme_trends"].extend(youtube["items"])

    # IT 트렌드 수집
    if trend_type in ("IT", "both"):
        hn = _fetch_hackernews(limit)
        github = _fetch_github_trending(limit)
        _record("hackernews", hn)
        _record("github", github)

        if hn["data_source"] == "fallback" and not hn["items"]:
            result["partial_failure"].append("hackernews")
        else:
            result["it_trends"].extend(hn["items"])

        if github["data_source"] == "fallback" and not github["items"]:
            result["partial_failure"].append("github")
        else:
            result["it_trends"].extend(github["items"])

    # 전체 실패 체크
    if not result["meme_trends"] and not result["it_trends"]:
        return {
            "ok": False,
            "data": result,
            "error": {
                "code": "TOTAL_FAILURE",
                "message": "모든 트렌드 소스 수집 실패",
            },
        }

    return {"ok": True, "data": result, "error": None}