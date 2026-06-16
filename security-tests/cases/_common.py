"""4개 케이스가 공유하는 결과 포맷/헬퍼.

원칙: case1/2/4는 실제 run_agent()를 그대로 호출한다(가드레일이 정확히
run_agent 진입점에서 동작하는지 검증하려면 그 경로를 우회하면 안 된다).
case3만 예외로, 외부 API 오염을 재현하기 위해 trend_scanner 내부 fetch
함수의 네트워크 호출(requests.get)만 monkeypatch하고 나머지 로직(sanitizer
포함)은 실제 코드 경로를 그대로 통과시킨다.
"""
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def noninteractive_pass(_payload: dict) -> str:
    """run_agent의 human_input_fn — 보안 테스트는 비대화형이므로 항상 패스.

    패스를 선택하면 feasibility_checker(추가 LLM 호출, SPEC/BRIEF 파일 생성)가
    스킵되어 테스트가 더 빠르고 부작용이 없다.
    """
    return "p"


def build_result(case_id: str, phase: str, input_text: str, *, guardrail_triggered: bool,
                  failure_type: str | None, llm_reached: bool, response_summary: str,
                  risk_level: str) -> dict:
    return {
        "case_id": case_id,
        "phase": phase,
        "input": input_text,
        "guardrail_triggered": guardrail_triggered,
        "failure_type": failure_type,
        "llm_reached": llm_reached,
        "response_summary": response_summary,
        "risk_level": risk_level,
        "timestamp": now_iso(),
    }
