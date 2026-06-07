"""Feature 2: feasibility_checker 재설계 — SPEC.md/BRIEF.md 킥오프 산출물."""
from pathlib import Path

import pytest

from src.tools.feasibility_checker import (
    render_spec_md,
    render_brief_md,
    write_kickoff_docs,
    _slugify,
    _difficulty_exceeded,
)


@pytest.fixture
def sample_plan():
    return {
        "app_name": "MCPurr",
        "one_liner": "연결된 MCP 서버를 고양이로 시각화하는 메뉴바 앱",
        "app_form": "menubar",
        "difficulty": "2~3days",
        "stack": ["Swift", "SwiftUI", "MenuBarExtra"],
        "mvp_scope": {
            "include": ["서버 연결 상태를 고양이 표정으로 표시"],
            "exclude": ["다중 워크스페이스 대시보드", "히스토리 그래프"],
        },
        "file_structure": ["MCPurr/App.swift", "MCPurr/MenuBarController.swift"],
        "implementation_order": ["1. MenuBarExtra 기본 아이콘", "2. 상태 polling", "3. 표정 매핑"],
        "tricky_points": ["polling 주기와 배터리 트레이드오프"],
        "completion_criteria": ["메뉴바에서 상태가 고양이로 보이면 완료"],
        "kickoff_prompt": "이 폴더의 SPEC.md를 기준으로 MCPurr를 plan 모드로 개발해줘.",
        "brief": {
            "why_trivial_useful": "서버 상태를 표정 하나로 — 하찮지만 한눈에 들어온다",
            "positioning": "개발자 메뉴바를 귀엽게 만드는 실용 장식",
            "user_value": ["창 전환 없이 상태 인지", "귀여워서 계속 보게 됨"],
            "trend_basis": "고양이 밈 × MCP 핫함",
            "monetization": {
                "model_ref": "RunCat",
                "rationale": "추가 표정/테마를 IAP로 판매",
                "free_paid_boundary": "기본 표정 무료 / 시즌 테마 유료",
            },
        },
    }


def test_spec_has_required_sections(sample_plan):
    spec = render_spec_md(sample_plan)
    # MVP 범위(넣을것/뺄것)
    assert "MVP 범위" in spec
    assert "다중 워크스페이스 대시보드" in spec      # exclude 반영
    # 추천 스택 / 파일 구조 / 구현 순서
    assert "MenuBarExtra" in spec
    assert "MCPurr/App.swift" in spec
    assert "상태 polling" in spec
    # 까다로운 지점 (난이도 정보의 의미 전환)
    assert "까다로운 지점" in spec
    assert "polling 주기" in spec
    # 단일 기능 macOS 유틸리티 명시
    assert "단일 기능 macOS 유틸리티" in spec
    # 킥오프 프롬프트 복붙용 (고정 템플릿)
    assert "킥오프 프롬프트" in spec
    assert "plan 모드로 전체 구현 계획" in spec
    assert "CLAUDE.md" in spec


def test_spec_completion_criteria_includes_signing_and_dmg(sample_plan):
    """완료 기준에 .app/.dmg 배포·서명·공증이 빠지면 자동 보강된다."""
    spec = render_spec_md(sample_plan)  # plan에는 서명 언급 없음
    assert ".dmg" in spec
    assert ("공증" in spec) or ("notarization" in spec)
    assert "$99" in spec


def test_brief_has_required_sections(sample_plan):
    brief = render_brief_md(sample_plan)
    assert "하찮지만 실용적" in brief             # 제품 의도
    assert "포지셔닝" in brief                     # 포지셔닝
    assert "사용자 가치" in brief                  # 사용자 가치
    assert "창 전환 없이 상태 인지" in brief        # user_value 반영
    assert "트렌드 근거" in brief                  # 트렌드 근거
    assert "고양이 밈 × MCP" in brief
    assert "RunCat" in brief                       # 수익화 모델


def test_write_kickoff_docs_creates_two_files(tmp_path, sample_plan):
    paths = write_kickoff_docs(sample_plan, output_dir=str(tmp_path))
    spec = Path(paths["spec_path"])
    brief = Path(paths["brief_path"])
    assert spec.exists() and brief.exists()
    # output/{app_name}/ 구조
    assert spec.parent.name == "MCPurr"
    assert spec.name == "SPEC.md"
    assert brief.name == "BRIEF.md"
    assert "MCPurr" in spec.read_text(encoding="utf-8")


def test_slugify_filesystem_safe():
    assert _slugify("MCPurr") == "MCPurr"
    assert _slugify("Half/Bag :Key!") == "Half_Bag_Key"
    assert _slugify("   ") == "app"


def test_difficulty_repurposed_but_limit_still_enforced():
    # 난이도 정보는 버려지지 않고 difficulty_limit 초과 판정에 계속 쓰인다
    assert _difficulty_exceeded("2~3days", "1day") is True
    assert _difficulty_exceeded("1day", "1day") is False
    assert _difficulty_exceeded("1week", "3days") is True
    assert _difficulty_exceeded("2~3days", None) is False
