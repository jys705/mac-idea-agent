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


def _fetch_youtube(limit: int = 5, force_fail: bool = False) -> dict:
    """YouTube Data API v3 mostPopular 실제 호출"""
    if force_fail:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": YOUTUBE_ENDPOINT,
            "fallback_reason": "youtube_request_failed: forced_timeout_test",
        }
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


def _fetch_productivity(limit: int = 5) -> dict:
    """r/productivity 인기 글 (Reddit 공개 RSS, OAuth/키 불필요).

    생활·생산성 트렌드 소재(거북목·집중·루틴 등)를 제공한다. HackerNews/GitHub가
    못 주는 '생활 밀착 실용' 재료를 보강하는 가벼운 소스.
    참고: Reddit JSON(.json)은 2026년 anti-bot으로 403 차단되지만 RSS(.rss)는 열려 있다.
    """
    import re as _re
    import html as _html
    PROD_ENDPOINT = "https://www.reddit.com/r/productivity/hot.rss"
    try:
        res = requests.get(
            PROD_ENDPOINT,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
            timeout=6,
        )
        res.raise_for_status()
        # RSS(Atom) <entry><title>...</title><link href="..."/></entry> 파싱
        entries = _re.findall(r"<entry>(.*?)</entry>", res.text, _re.S)
        items = []
        for e in entries:
            tm = _re.search(r"<title>(.*?)</title>", e, _re.S)
            lm = _re.search(r'<link[^>]*href="([^"]+)"', e)
            if not tm:
                continue
            title = _html.unescape(tm.group(1).strip())
            # 모더레이터 공지/메타 글 제외
            if not title or "Moderator" in title or title.startswith("/r/"):
                continue
            items.append({
                "keyword": title,
                "source": "reddit_productivity",
                "url": lm.group(1) if lm else PROD_ENDPOINT,
                "score": 0,  # RSS는 ups 미제공
            })
            if len(items) >= limit:
                break
        if not items:
            return {"items": [], "data_source": "fallback", "endpoint": PROD_ENDPOINT,
                    "fallback_reason": "productivity_rss_empty"}
        return {"items": items, "data_source": "real_api", "endpoint": PROD_ENDPOINT}
    except Exception as e:
        return {
            "items": [],
            "data_source": "fallback",
            "endpoint": PROD_ENDPOINT,
            "fallback_reason": f"productivity_request_failed: {e}",
        }


# ── 충분성 판단 (★기능 B — 반복적 리서치 근거, 순수 함수) ──

def _item_signal(item: dict) -> int:
    """아이템의 트렌드 강도 신호(ups/stars/views 중 존재하는 값)."""
    for k in ("score", "stars", "views"):
        v = item.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return 0


def compute_sufficiency(
    meme_trends: list[dict],
    it_trends: list[dict],
    partial_failure: list[str],
    trend_type: str = "both",
) -> dict:
    """수집 결과가 컨셉 생성에 충분한지 판단 근거를 만든다(★기능 B).

    빈약하면(키워드 적음/강도 약함/한쪽 쏠림/소스 실패) is_sufficient=False →
    LLM이 이 근거를 보고 재탐색 여부를 판단한다(횟수 상한은 코드가 강제).
    """
    meme_count = len(meme_trends)
    it_count = len(it_trends)
    total = meme_count + it_count

    # 강도: 높은 신호(밈 ups 1만+, github stars 1천+, youtube views 10만+) 개수
    strong_signals = 0
    for it in list(meme_trends) + list(it_trends):
        sig = _item_signal(it)
        if sig >= 100000 or sig >= 10000 or sig >= 1000:
            strong_signals += 1
    if strong_signals >= 3:
        strength = "strong"
    elif strong_signals >= 1 or total >= 6:
        strength = "medium"
    else:
        strength = "weak"

    # 다양성: 아이템을 실제로 준 distinct 소스 수
    sources = {it.get("source") for it in (list(meme_trends) + list(it_trends)) if it.get("source")}
    diversity = len(sources)

    # 쏠림: both를 요청했는데 한쪽이 비면 쏠림
    if trend_type == "both":
        if meme_count == 0 and it_count > 0:
            skew = "it_only"
        elif it_count == 0 and meme_count > 0:
            skew = "meme_only"
        else:
            skew = "balanced"
    else:
        skew = "single_focus"  # meme/IT 단독 요청은 한쪽만 있는 게 정상

    is_sufficient = (
        total >= 4
        and diversity >= 2
        and strength != "weak"
        and skew not in ("meme_only", "it_only")
        and not (partial_failure and total < 6)
    )

    return {
        "meme_count": meme_count,
        "it_count": it_count,
        "total_count": total,
        "strength": strength,
        "diversity": diversity,
        "skew": skew,
        "partial_failure": list(partial_failure),
        "is_sufficient": is_sufficient,
    }


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def trend_scanner(
    trend_type: str = "both",
    limit: int = 5,
    force_youtube_fail: bool = False,
) -> dict[str, Any]:
    """
    Reddit·YouTube·GitHub·HackerNews·r/productivity에서 오늘의 밈·IT·생활 트렌드를 실시간 수집한다.
    YouTube API Key가 설정된 경우 실제 API를 호출하고, 미설정 시 Mock으로 fallback한다.
    r/productivity는 생활·생산성 소재(거북목·집중·루틴 등)를 보강한다(키 불필요, 공개 .json).
    각 소스 실패 시 partial_failure로 기록하고 나머지로 계속 진행한다.

    Args:
        trend_type: 수집 유형. "meme" = 밈만, "IT" = IT 트렌드만, "both" = 둘 다 (기본값)
        limit: 소스당 수집할 키워드 수 (기본값 5)
        force_youtube_fail: True이면 YouTube API 실패를 강제 시뮬레이션 (테스트용)

    Returns:
        ok: 성공 여부
        data: meme_trends, it_trends, partial_failure, source_provenance,
              sufficiency(★기능 B: 키워드 수/강도/다양성/쏠림/소스실패/is_sufficient).
              sufficiency.is_sufficient=False(빈약)이면 다른 키워드·소스로 한 번 더 호출을
              고려하라(재탐색은 코드가 최대 2회로 강제).
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
        youtube = _fetch_youtube(limit, force_fail=force_youtube_fail)
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

        # 생활·생산성 트렌드 (r/productivity) — 거북목·집중·루틴 등 생활 밀착 실용 소재 보강
        productivity = _fetch_productivity(limit)
        _record("productivity", productivity)
        if productivity["data_source"] == "fallback" and not productivity["items"]:
            result["partial_failure"].append("productivity")
        else:
            result["it_trends"].extend(productivity["items"])

    # ★기능 B: 충분성 판단 근거 첨부 (재탐색 여부의 객관 근거)
    result["sufficiency"] = compute_sufficiency(
        result["meme_trends"], result["it_trends"],
        result["partial_failure"], trend_type,
    )

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