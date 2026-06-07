"""Feature 1 (a): 의미 유사도 순수 로직 — 글자매칭 → 임베딩 교체 검증."""
import math

from src.tools import embeddings
from src.tools.app_existence_checker import (
    classify_band,
    map_human_decision,
    score_candidates,
    AUTO_LOOPBACK_THRESHOLD,
    CONFIRM_THRESHOLD,
)


def test_cosine_similarity_basic():
    assert embeddings.cosine_similarity([1, 0], [1, 0]) == 1.0
    assert embeddings.cosine_similarity([1, 0], [0, 1]) == 0.0
    # 반대 방향은 0으로 클램프
    assert embeddings.cosine_similarity([1, 0], [-1, 0]) == 0.0
    # 빈 벡터 방어
    assert embeddings.cosine_similarity([], [1, 0]) == 0.0


def test_classify_band_three_tiers():
    # 임계값: >=0.85 루프백 / 0.65~0.85 사용자확인 / <0.65 진행
    assert classify_band(0.90) == "auto_loopback"
    assert classify_band(AUTO_LOOPBACK_THRESHOLD) == "auto_loopback"  # 경계 포함
    assert classify_band(0.74) == "human_confirm"
    assert classify_band(CONFIRM_THRESHOLD) == "human_confirm"        # 경계 포함
    assert classify_band(0.64) == "auto_proceed"
    assert classify_band(0.0) == "auto_proceed"


def test_map_human_decision():
    # 재탐색류 → 루프백(True)
    assert map_human_decision("재탐색") is True
    assert map_human_decision("research") is True
    assert map_human_decision("r") is True
    # 진행류 / 엔터 → 진행(False)
    assert map_human_decision("그대로 진행") is False
    assert map_human_decision("proceed") is False
    assert map_human_decision("") is False


def test_score_candidates_with_injected_embedder():
    """한국어 컨셉 ↔ 영어 앱이 섞여도 임베딩으로 점수가 매겨지고 내림차순 정렬된다."""
    def fake(texts):
        # concept=[1,0]; 'Catdock' 0.88, 'Calculator' 0.10
        out = [[1.0, 0.0]]
        for t in texts[1:]:
            s = 0.88 if "Catdock" in t else 0.10
            out.append([s, math.sqrt(1 - s * s)])
        return out

    apps = [
        {"name": "Calculator", "description": "basic math"},
        {"name": "Catdock", "description": "a cat that lives in your Mac Dock"},
    ]
    scored = score_candidates("Dock에 고양이를 띄우는 메뉴바 앱", apps, embed_fn=fake)
    assert scored[0]["name"] == "Catdock"           # 점수 내림차순
    assert scored[0]["similarity_score"] == 0.88
    assert scored[1]["similarity_score"] == 0.10


def test_embedding_available_toggle():
    embeddings.set_embed_fn(lambda texts: [[1.0, 0.0]] * len(texts))
    try:
        assert embeddings.embedding_available() is True
    finally:
        embeddings.set_embed_fn(None)
    # 주입 해제 후엔 OPENAI_API_KEY 유무에 따름 (이 환경엔 키 없음)
    import os
    assert embeddings.embedding_available() == bool(os.getenv("OPENAI_API_KEY"))
