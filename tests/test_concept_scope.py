"""Feature 3: concept_generator 단일 유틸 스코프 제약 검증 (Golden Dataset 케이스 7)."""
import pytest

from src.tools.concept_generator import validate_scope, ALLOWED_APP_FORMS


# future-work 기능3: 스코프 이탈 ❌ 케이스
OUT_OF_SCOPE = [
    ("NPCron", "노코드로 봇을 조립하는 자동화 플랫폼, 인터프리터 내장", "드래그앤드롭 봇 빌더", "menubar"),
    ("RedisBuddy", "Redis를 직접 만들며 배우는 학습 앱", "단계별 학습으로 Redis 구현", "menubar"),
    ("BabyBird", "단계별 튜토리얼로 배우는 봇 만들기 앱", "직접 만들며 배우는 학습", "dock"),
]

# 단일 기능 유틸 ✅ 케이스 (RunCat/Rectangle 구조)
IN_SCOPE = [
    ("SignalLadybug", "메뉴바에서 네트워크 신호를 무당벌레로 표시", "신호세기 무당벌레 표시", "menubar"),
    ("WTFKey", "까먹은 단축키를 메뉴바에서 즉시 보여주는 앱", "단축키 치트시트 표시", "menubar"),
    ("APISnack", "메뉴바에서 랜덤 공개 API를 던져주는 앱", "랜덤 API 아이디어", "menubar"),
    ("GridWhisperer", "단축키로 창을 그리드에 정렬", "창 정렬 단축키", "shortcut"),
    ("FakeSnap", "Dock에서 가짜 스냅 알림 위젯", "가짜 알림 표시", "widget"),
]


@pytest.mark.parametrize("name,desc,feat,form", OUT_OF_SCOPE)
def test_out_of_scope_flagged(name, desc, feat, form):
    r = validate_scope(name, desc, feat, form)
    assert r["is_single_utility"] is False, f"{name} 은(는) 스코프 이탈로 잡혀야 한다"
    assert r["violation"] in {"platform", "no_code_builder", "bot_framework", "learning_app"}


@pytest.mark.parametrize("name,desc,feat,form", IN_SCOPE)
def test_in_scope_passes(name, desc, feat, form):
    r = validate_scope(name, desc, feat, form)
    assert r["is_single_utility"] is True, f"{name} 은(는) 단일 유틸로 통과해야 한다"
    assert r["violation"] is None


def test_unknown_app_form_flagged():
    r = validate_scope("FooApp", "메뉴바 유틸", "표시", app_form="webapp")
    assert r["is_single_utility"] is False
    assert r["violation"] == "unknown_form"


def test_allowed_forms_pass_when_clean():
    for form in ALLOWED_APP_FORMS:
        r = validate_scope("CleanUtil", "메뉴바에서 시간을 표시", "시계 표시", app_form=form)
        assert r["is_single_utility"] is True


def test_learning_curriculum_generator_is_not_learning_app():
    """'학습 커리큘럼 생성기'는 학습앱이 아니라 생성 유틸 — 과탐지하지 않는다."""
    r = validate_scope("LLMentor", "Claude로 나만의 학습 커리큘럼 즉석 생성하는 메뉴바 앱",
                       "커리큘럼 생성", "menubar")
    assert r["is_single_utility"] is True
