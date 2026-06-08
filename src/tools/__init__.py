from .trend_scanner import trend_scanner
from .concept_generator import concept_generator
from .app_existence_checker import app_existence_checker
from .concept_critic import concept_critic
from .feasibility_checker import feasibility_checker

# ReAct 에이전트가 흐름 중 호출하는 도구들.
# ★기능 A: feasibility_checker는 여기서 제외한다 — SPEC.md/BRIEF.md 생성은
# 마지막 통합 선택 뒤에 코드가 직접 호출한다(.md 추출 = 파이프라인의 가장 마지막).
tools = [
    trend_scanner,
    concept_generator,
    app_existence_checker,
    concept_critic,
]
