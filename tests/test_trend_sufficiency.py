"""기능 B: trend_scanner 충분성 판단(compute_sufficiency) — 재탐색 근거 (순수 함수)."""
import pytest

from src.tools.trend_scanner import compute_sufficiency


def test_strong_balanced_is_sufficient():
    meme = [{"source": "reddit", "score": 83728}, {"source": "youtube", "views": 4200000}]
    it = [{"source": "github", "stars": 6821}, {"source": "hackernews", "score": 847},
          {"source": "reddit_productivity"}]
    s = compute_sufficiency(meme, it, [], "both")
    assert s["total_count"] == 5
    assert s["strength"] == "strong"      # 강한 신호 3개+
    assert s["diversity"] >= 3
    assert s["skew"] == "balanced"
    assert s["is_sufficient"] is True


def test_too_few_keywords_insufficient():
    s = compute_sufficiency([{"source": "reddit", "score": 3}], [], ["youtube", "github"], "both")
    assert s["total_count"] == 1
    assert s["is_sufficient"] is False    # 키워드 너무 적음 + 쏠림 + 약함


def test_skew_both_requested_but_one_side_empty():
    meme = [{"source": "reddit", "score": 90000}, {"source": "youtube", "views": 500000},
            {"source": "reddit", "score": 80000}, {"source": "youtube", "views": 600000}]
    s = compute_sufficiency(meme, [], [], "both")
    assert s["skew"] == "meme_only"
    assert s["is_sufficient"] is False    # both를 원했는데 IT가 비어 쏠림


def test_single_focus_not_treated_as_skew():
    it = [{"source": "github", "stars": 5000}, {"source": "hackernews", "score": 900},
          {"source": "github", "stars": 3000}, {"source": "hackernews", "score": 700}]
    s = compute_sufficiency([], it, [], "IT")
    assert s["skew"] == "single_focus"    # IT 단독 요청은 한쪽만 있는 게 정상
    assert s["is_sufficient"] is True


def test_partial_failure_with_thin_results_insufficient():
    meme = [{"source": "reddit", "score": 50}, {"source": "reddit", "score": 40}]
    it = [{"source": "github", "stars": 20}]
    s = compute_sufficiency(meme, it, ["youtube"], "both")
    # 총 3개(<4)이고 강도 약함 → 불충분
    assert s["is_sufficient"] is False
    assert "youtube" in s["partial_failure"]
