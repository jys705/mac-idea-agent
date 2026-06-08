"""기능 A: 추천 1위 산정(rank_concepts) + 마지막 통합 선택 파싱(parse_user_selection)."""
import pytest

from src.agent import rank_concepts, parse_user_selection, _build_selection_payload


def _c(name, prac, trend, sim=0.1, similar=False, similar_apps=None):
    return {
        "app_name": name,
        "description": f"{name} 설명",
        "critique": {"practicality": prac, "trend_power": trend, "peak": max(prac, trend)},
        "similarity_score": sim,
        "similar_app_exists": similar,
        "similar_apps": similar_apps or [],
    }


def test_recommend_highest_peak_when_no_suspect():
    concepts = [_c("A", 4, 2), _c("B", 1, 2), _c("C", 5, 1)]
    r = rank_concepts(concepts)
    # C peak5 > A peak4 > B peak2
    assert r["recommended_index"] == 2
    assert r["order"][0] == 2
    assert [p["app_name"] for p in r["passed_over"]] == ["A", "B"]


def test_similarity_suspect_demoted_even_if_high_peak():
    # C는 최고 peak(5)이지만 유사도 0.83(≥0.78) → 후순위로 밀린다
    concepts = [
        _c("A", 4, 2, sim=0.1),
        _c("B", 1, 2, sim=0.1),
        _c("C", 5, 5, sim=0.83, similar_apps=[{"name": "Catdock"}]),
    ]
    r = rank_concepts(concepts)
    assert r["recommended_index"] == 0          # A (의심 없는 최고)
    reasons = {p["app_name"]: p["reason"] for p in r["passed_over"]}
    assert "유사앱 의심으로 밀림" in reasons["C"]
    assert "Catdock" in reasons["C"]
    assert "self-critique 낮아 밀림" in reasons["B"]


def test_clear_duplicate_similar_app_found_demoted():
    concepts = [_c("A", 3, 3, sim=0.9, similar=True), _c("B", 3, 2, sim=0.1)]
    r = rank_concepts(concepts)
    assert r["recommended_index"] == 1          # B (A는 명백 중복으로 후순위)


def test_all_weak_still_recommends_one():
    concepts = [_c("A", 1, 2), _c("B", 2, 1)]
    r = rank_concepts(concepts)
    assert r["recommended_index"] is not None    # 둘 다 약해도 버리지 않고 1위는 정한다


def test_empty_concepts():
    r = rank_concepts([])
    assert r["recommended_index"] is None
    assert r["order"] == [] and r["passed_over"] == []


@pytest.mark.parametrize("raw,expected", [
    ("", {"action": "accept", "index": 0}),
    ("1", {"action": "accept", "index": 0}),
    ("y", {"action": "accept", "index": 0}),
    ("2", {"action": "pick", "index": 1}),
    ("3", {"action": "pick", "index": 2}),
    ("p", {"action": "pass"}),
    ("패스", {"action": "pass"}),
    ("99", {"action": "accept", "index": 0}),   # 범위 밖 → 안전하게 추천 수락
    ("zzz", {"action": "accept", "index": 0}),  # 인식 불가 → 추천 수락
])
def test_parse_user_selection(raw, expected):
    assert parse_user_selection(raw, 3) == expected


def test_selection_payload_orders_by_ranking_with_reasons():
    concepts = [
        _c("A", 4, 2, sim=0.1),
        _c("B", 5, 5, sim=0.83, similar_apps=[{"name": "Catdock"}]),
    ]
    ranking = rank_concepts(concepts)
    payload = _build_selection_payload(concepts, ranking)
    cands = payload["candidates"]
    assert cands[0]["is_recommended"] is True
    assert cands[0]["app_name"] == "A"
    # 밀린 후보엔 사유가 붙는다
    assert cands[1]["is_recommended"] is False
    assert "유사앱 의심" in cands[1]["passed_reason"]
