"""테스트 공통 픽스처/헬퍼.

핵심 원칙: 임베딩·LLM·네트워크 같은 비결정적/외부 의존성은 주입 가능하게 만들어
단위 테스트는 결정적이고 오프라인으로 돌아간다.
"""
import math
import sys
from pathlib import Path

import pytest

# 프로젝트 루트를 import 경로에 추가 (src.* import)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_fake_embedder(score_by_text: dict[str, float], default: float = 0.0):
    """텍스트별 '컨셉과의 목표 코사인 유사도'를 지정하는 fake 임베더를 만든다.

    embed([concept, app1, app2, ...]) 호출에서:
      - 첫 텍스트(concept)는 기준 벡터 [1, 0]
      - 각 app 텍스트는 [s, sqrt(1-s^2)] → concept과의 코사인 유사도 = s
    score_by_text는 부분 문자열 매칭으로 점수를 찾는다.
    """
    def _fake(texts):
        vectors = []
        for i, t in enumerate(texts):
            if i == 0:
                vectors.append([1.0, 0.0])
                continue
            s = default
            for key, val in score_by_text.items():
                if key in t:
                    s = val
                    break
            s = max(0.0, min(1.0, s))
            vectors.append([s, math.sqrt(max(0.0, 1.0 - s * s))])
        return vectors
    return _fake


@pytest.fixture
def inject_embedder():
    """fake 임베더를 주입하고 테스트 종료 시 해제한다."""
    from src.tools import embeddings

    def _inject(score_by_text, default=0.0):
        embeddings.set_embed_fn(make_fake_embedder(score_by_text, default))

    yield _inject
    embeddings.set_embed_fn(None)
