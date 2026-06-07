"""Feature 1 (b): app_existence_checker Tool 동작 — 구간별 분기/폴백/force_similar."""
import math

import importlib

import pytest

from src.tools import embeddings
from src.tools.app_existence_checker import app_existence_checker

# 패키지 __init__이 동명의 tool을 re-export하므로 진짜 모듈은 importlib로 얻는다.
aec = importlib.import_module("src.tools.app_existence_checker")


@pytest.fixture
def patch_search(monkeypatch):
    """iTunes/GitHub 검색을 네트워크 없이 고정 결과로 대체한다."""
    def _patch(appstore_items=None, github_items=None, fail_both=False):
        def fake_appstore(query):
            if fail_both:
                return {"items": [], "data_source": "fallback", "endpoint": "x",
                        "fallback_reason": "test"}
            return {"items": appstore_items or [], "data_source": "real_api", "endpoint": "itunes"}

        def fake_github(query):
            if fail_both:
                return {"items": [], "data_source": "fallback", "endpoint": "x",
                        "fallback_reason": "test"}
            return {"items": github_items or [], "data_source": "real_api", "endpoint": "github"}

        monkeypatch.setattr(aec, "_search_appstore", fake_appstore)
        monkeypatch.setattr(aec, "_search_github", fake_github)
    return _patch


def _embedder_scoring(score_for_substr):
    def fake(texts):
        out = [[1.0, 0.0]]
        for t in texts[1:]:
            s = 0.0
            for key, val in score_for_substr.items():
                if key in t:
                    s = val
                    break
            out.append([s, math.sqrt(max(0.0, 1 - s * s))])
        return out
    return fake


def teardown_function(_):
    embeddings.set_embed_fn(None)


def test_force_similar_short_circuits():
    res = app_existence_checker.invoke({"concept": "X", "force_similar": True})
    data = res["data"]
    assert data["similar_app_found"] is True
    assert data["decision_band"] == "forced"
    assert data["similarity_method"] == "forced"


def test_auto_proceed_low_score(patch_search):
    patch_search(github_items=[{"name": "Calculator", "description": "basic math",
                                "stars": 1, "source": "github", "url": "u"}])
    embeddings.set_embed_fn(_embedder_scoring({"Calculator": 0.10}))
    res = app_existence_checker.invoke({
        "concept": "MCPurr", "description": "Dock에 고양이를 띄우는 앱", "core_feature": "상태 표시"})
    data = res["data"]
    assert data["similarity_method"] == "embedding"
    assert data["decision_band"] == "auto_proceed"
    assert data["similar_app_found"] is False
    assert data["similarity_score"] == 0.10


def test_auto_loopback_high_score(patch_search):
    patch_search(github_items=[{"name": "Catdock", "description": "cat in your dock",
                                "stars": 1200, "source": "github", "url": "u"}])
    embeddings.set_embed_fn(_embedder_scoring({"Catdock": 0.90}))
    res = app_existence_checker.invoke({
        "concept": "MCPurr", "description": "Dock에 고양이를 띄우는 앱", "core_feature": "상태 표시"})
    data = res["data"]
    assert data["decision_band"] == "auto_loopback"
    assert data["similar_app_found"] is True
    assert data["similar_apps"][0]["name"] == "Catdock"
    assert data["similarity_score"] == 0.90


def test_human_confirm_outside_graph_defaults_to_proceed(patch_search):
    """그래프 컨텍스트 밖에서 애매 구간이면 interrupt 불가 → 안전 기본값(진행)."""
    patch_search(github_items=[{"name": "Catdock", "description": "cat in dock",
                                "stars": 50, "source": "github", "url": "u"}])
    embeddings.set_embed_fn(_embedder_scoring({"Catdock": 0.74}))
    res = app_existence_checker.invoke({
        "concept": "MCPurr", "description": "Dock에 고양이를 띄우는 앱", "core_feature": "상태 표시"})
    data = res["data"]
    assert data["decision_band"] == "human_confirmed"
    assert data["similarity_score"] == 0.74
    assert data["similar_app_found"] is False          # 진행
    assert data["human_decision"] == "proceed"


def test_substring_fallback_when_no_embedding(patch_search):
    """임베딩 불가 환경 → 글자 매칭 fallback, interrupt 없음."""
    embeddings.set_embed_fn(None)  # 주입 없음 + 키 없음 → embedding_available False
    patch_search(github_items=[{"name": "cat-dock", "description": "cat dock app",
                                "stars": 1, "source": "github", "url": "u"}])
    res = app_existence_checker.invoke({
        "concept": "catdock", "description": "cat dock", "core_feature": "cat"})
    data = res["data"]
    assert data["similarity_method"] == "substring_fallback"
    assert data["decision_band"] == "substring_fallback"
    assert data["similarity_score"] is None


def test_search_failed_both(patch_search):
    patch_search(fail_both=True)
    embeddings.set_embed_fn(_embedder_scoring({}))
    res = app_existence_checker.invoke({
        "concept": "X", "description": "d", "core_feature": "f"})
    assert res["ok"] is False
    assert res["error"]["code"] == "SEARCH_FAILED"
