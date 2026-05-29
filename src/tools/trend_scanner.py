import os
import requests
from typing import Any
from langchain_core.tools import tool

# ── 실제 API 호출 함수 ──────────────────────────────────────

def _fetch_hackernews(limit: int = 5) -> list[dict]:
    """HackerNews Top Stories 실제 API 호출"""
    try:
        res = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=5
        )
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
        return stories
    except Exception as e:
        return [{"error": str(e), "source": "hackernews"}]


def _fetch_github_trending(limit: int = 5) -> list[dict]:
    """GitHub Trending 실제 API 호출"""
    try:
        res = requests.get(
            "https://api.github.com/search/repositories",
            params={
                "q": "stars:>1000 pushed:>2026-01-01",
                "sort": "stars",
                "order": "desc",
                "per_page": limit
            },
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5
        )
        items = res.json().get("items", [])
        return [
            {
                "keyword": f"{item['name']} ({item.get('language', 'unknown')})",
                "source": "github",
                "url": item["html_url"],
                "stars": item["stargazers_count"]
            }
            for item in items
        ]
    except Exception as e:
        return [{"error": str(e), "source": "github"}]


def _fetch_reddit_mock(limit: int = 3) -> list[dict]:
    """Reddit 밈 트렌드 — Mock (OAuth 설정 전까지 사용)"""
    return [
        {"keyword": "카피바라 밈", "source": "reddit_mock",
         "url": "https://reddit.com/r/memes", "score": 94200},
        {"keyword": "게 사이드워크 밈", "source": "reddit_mock",
         "url": "https://reddit.com/r/dankmemes", "score": 67400},
        {"keyword": "Italian Brainrot 밈", "source": "reddit_mock",
         "url": "https://reddit.com/r/memes", "score": 52100},
    ][:limit]


def _fetch_youtube_mock(limit: int = 3) -> list[dict]:
    """YouTube Shorts 밈 트렌드 — Mock (API Key 설정 전까지 사용)"""
    return [
        {"keyword": "카피바라 느긋함", "source": "youtube_mock",
         "url": "https://youtube.com/shorts/example1", "views": 4200000},
        {"keyword": "고양이 찰떡 밈", "source": "youtube_mock",
         "url": "https://youtube.com/shorts/example2", "views": 3100000},
        {"keyword": "NPC 챌린지", "source": "youtube_mock",
         "url": "https://youtube.com/shorts/example3", "views": 2800000},
    ][:limit]


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def trend_scanner(trend_type: str = "both", limit: int = 5) -> dict[str, Any]:
    """
    Reddit·YouTube·GitHub·HackerNews에서 오늘의 밈 및 IT 트렌드 키워드를 실시간 수집한다.

    Args:
        trend_type: 수집 유형. "meme" = 밈만, "IT" = IT 트렌드만, "both" = 둘 다 (기본값)
        limit: 소스당 수집할 키워드 수 (기본값 5)

    Returns:
        ok: 성공 여부
        data: meme_trends와 it_trends 키워드 목록
        error: 실패 시 에러 정보
    """
    result = {"meme_trends": [], "it_trends": [], "partial_failure": []}

    # 밈 트렌드 수집
    if trend_type in ("meme", "both"):
        reddit = _fetch_reddit_mock(limit)
        youtube = _fetch_youtube_mock(limit)

        if any("error" in r for r in reddit):
            result["partial_failure"].append("reddit")
        else:
            result["meme_trends"].extend(reddit)

        if any("error" in r for r in youtube):
            result["partial_failure"].append("youtube")
        else:
            result["meme_trends"].extend(youtube)

    # IT 트렌드 수집
    if trend_type in ("IT", "both"):
        hn = _fetch_hackernews(limit)
        github = _fetch_github_trending(limit)

        if any("error" in r for r in hn):
            result["partial_failure"].append("hackernews")
        else:
            result["it_trends"].extend(hn)

        if any("error" in r for r in github):
            result["partial_failure"].append("github")
        else:
            result["it_trends"].extend(github)

    # 전체 실패 체크
    if not result["meme_trends"] and not result["it_trends"]:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "TOTAL_FAILURE",
                "message": "모든 트렌드 소스 수집 실패"
            }
        }

    return {
        "ok": True,
        "data": result,
        "error": None
    }