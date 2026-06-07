"""
의미 유사도용 임베딩 유틸 (Gemini gemini-embedding-001).

설계 의도 (design_v2.md 섹션 6 — app_existence_checker):
- app_existence_checker의 글자 매칭(_is_similar)을 임베딩 코사인 유사도로 교체한다.
- 한국어 컨셉(description) ↔ 영어 앱(name+description)이 섞여도 의미 기반으로 매칭된다.

모델 선택 근거:
- 하루 1~2회 실행(소량) 사용 패턴 → Gemini 무료 티어가 가장 효율적.
  로컬 모델(BGE-m3)은 ~1GB 다운로드/로딩 오버헤드가 소량 사용엔 과함, OpenAI는 유료(quota).
  Gemini 무료 한도 안에서 비용 0으로 동작한다.

설계 원칙:
- 임베딩 호출은 외부 의존성이므로 **주입 가능(injectable)** 하게 둔다.
  → 단위 테스트는 네트워크/키 없이 결정적 fake embedder로 검증한다.
- GOOGLE_API_KEY가 없으면 임베딩이 불가하므로 embedding_available()이 False를 반환하고,
  호출부(app_existence_checker)는 글자 매칭 fallback으로 강등한다.
"""

from __future__ import annotations

import os
import math
from typing import Callable, Sequence

from dotenv import load_dotenv

load_dotenv()

EMBED_MODEL = "models/gemini-embedding-001"

# 테스트/대체 구현을 끼워 넣기 위한 주입 지점.
# None이면 Gemini 실호출(_gemini_embed)을 사용한다.
_EMBED_FN: Callable[[Sequence[str]], list[list[float]]] | None = None

_gemini_client = None  # lazy 초기화


def embedding_available() -> bool:
    """임베딩을 실제로 쓸 수 있는 환경인지 여부.

    - 테스트가 fake embedder를 주입했다면 항상 True.
    - 아니면 GOOGLE_API_KEY 존재 여부로 판단.
    """
    if _EMBED_FN is not None:
        return True
    return bool(os.getenv("GOOGLE_API_KEY"))


def set_embed_fn(fn: Callable[[Sequence[str]], list[list[float]]] | None) -> None:
    """임베딩 함수를 주입/해제한다 (테스트 전용 훅)."""
    global _EMBED_FN
    _EMBED_FN = fn


def _gemini_embed(texts: Sequence[str]) -> list[list[float]]:
    """gemini-embedding-001 실제 호출 (lazy init)."""
    global _gemini_client
    if _gemini_client is None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        _gemini_client = GoogleGenerativeAIEmbeddings(model=EMBED_MODEL)
    return _gemini_client.embed_documents(list(texts))


def embed(texts: Sequence[str]) -> list[list[float]]:
    """주입된 함수 우선, 없으면 Gemini 실호출로 임베딩한다."""
    fn = _EMBED_FN or _gemini_embed
    return fn(texts)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """두 벡터의 코사인 유사도(0~1 범위로 클램프)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    score = dot / (na * nb)
    # 부동소수 오차로 1.0을 살짝 넘는 경우 방지, 음수는 0으로 클램프
    return max(0.0, min(1.0, score))
