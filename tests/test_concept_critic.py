"""기능 A: concept_critic self-critique — 채점 파싱/정규화 (LLM 없이 결정적)."""
import pytest

from src.tools.concept_critic import parse_critique, _clamp_score, _verdict


def test_parse_critique_basic_with_codefence():
    r = parse_critique('```json\n{"practicality": 4, "trend_power": 2, "comment": "실용 강함"}\n```')
    assert r["practicality"] == 4
    assert r["trend_power"] == 2
    assert r["peak"] == 4              # max(두 축)
    assert r["verdict"] == "strong"    # peak>=4
    assert r["comment"] == "실용 강함"


def test_parse_critique_clamps_out_of_range():
    r = parse_critique('{"practicality": 9, "trend_power": 0, "comment": "x"}')
    assert r["practicality"] == 5      # 9 → 5
    assert r["trend_power"] == 1       # 0 → 1
    assert r["peak"] == 5


def test_parse_critique_tolerates_prose_around_json():
    r = parse_critique('여기 평가입니다: {"practicality": 3, "trend_power": 3} 끝.')
    assert r["practicality"] == 3 and r["trend_power"] == 3
    assert r["verdict"] == "ok"        # peak==3


def test_clamp_and_verdict_units():
    assert _clamp_score("4") == 4
    assert _clamp_score(None) == 3     # 파싱 불가 → 중립 3
    assert _clamp_score(99) == 5
    assert _clamp_score(-3) == 1
    assert _verdict(5) == "strong"
    assert _verdict(3) == "ok"
    assert _verdict(2) == "weak"       # 둘 다 약함 — 그래도 버리지 않음(표시용 라벨)


def test_both_axes_weak_is_weak_but_not_dropped():
    r = parse_critique('{"practicality": 1, "trend_power": 2, "comment": "그냥 장식"}')
    assert r["peak"] == 2
    assert r["verdict"] == "weak"
    # 점수는 기록되지만 컨셉을 버리는 신호가 아니다 (필드만 존재)
    assert "practicality" in r and "trend_power" in r
