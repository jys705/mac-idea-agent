"""기존 examples/ 골든 케이스가 스키마 변경 후에도 깨지지 않는지 회귀 검증.

이번 변경은 모두 '추가(additive)'여야 한다 — 기존 필드를 제거/변경하지 않는다.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FILES = sorted((ROOT / "examples").glob("*.json"))


def test_examples_exist():
    assert EXAMPLE_FILES, "examples/*.json 이 존재해야 한다"


def _is_agent_output(data: dict) -> bool:
    """에이전트 최종 브리핑 출력(카드) 형식인지 — 일부 예시는 tool-test 픽스처다."""
    return isinstance(data, dict) and "today_brief" in data and "metadata" in data


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_structure_intact(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    if not _is_agent_output(data):
        # tool-test 픽스처(input/result 형식) — 파싱만 되면 OK
        return

    meta = data["metadata"]
    assert isinstance(meta.get("used_tools"), list)
    assert "loop_count" in meta

    brief = data["today_brief"]
    if brief is not None:
        assert isinstance(brief.get("concepts"), list)
        for c in brief["concepts"]:
            # 기존 필수 필드는 그대로 유지되어야 한다
            assert "app_name" in c
            assert "description" in c


def test_new_fields_are_additive_not_breaking():
    """새 필드(similarity_score/kickoff/scope_check)는 선택적 — 옛 예시에 없어도 무방."""
    for path in EXAMPLE_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not _is_agent_output(data):
            continue
        brief = data.get("today_brief")
        if not brief:
            continue
        for c in brief.get("concepts", []):
            # 없을 수도 있고(옛 예시), 있으면 타입이 맞아야 한다
            if "similarity_score" in c:
                assert isinstance(c["similarity_score"], (int, float))
            if "kickoff" in c:
                assert isinstance(c["kickoff"], dict)


def test_core_modules_import_clean():
    """스키마/프롬프트 변경 후에도 핵심 모듈이 깨끗이 import 되어야 한다."""
    import importlib

    from src.prompts.system_prompt import SYSTEM_PROMPT
    from src.tools import tools

    assert "단일 기능" in SYSTEM_PROMPT or "컨셉" in SYSTEM_PROMPT
    # ★기능 A: 에이전트 도구는 scan/generate/check/critic 4종.
    # feasibility_checker는 ReAct 도구에서 빠지고(선택 뒤 코드가 직접 호출) concept_critic이 추가됨.
    names = {t.name for t in tools}
    assert names == {
        "trend_scanner", "concept_generator",
        "app_existence_checker", "concept_critic",
    }
    # agent 모듈 import 가능 + 마지막 통합 선택용 순수 함수 노출
    agent_mod = importlib.import_module("src.agent")
    assert hasattr(agent_mod, "run_agent")
    assert hasattr(agent_mod, "rank_concepts")
    assert hasattr(agent_mod, "parse_user_selection")
    # interrupt/MemorySaver 제거됨
    assert not hasattr(agent_mod, "_checkpointer")
