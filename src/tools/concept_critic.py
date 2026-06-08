import os
import json
from typing import Any
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

# ── LLM 초기화 ─────────────────────────────────────────────
# 평가(채점)는 일관성이 중요하므로 낮은 temperature. 비용 절감 위해 haiku.

_llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001",
    temperature=0.2,
    max_tokens=512,
    anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
)


# ── 순수 함수: 채점 파싱·정규화 (LLM/네트워크 없이 테스트 가능) ──

def _clamp_score(v: Any) -> int:
    """1~5 정수로 클램프. 파싱 불가 시 3(중립)."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, n))


def _verdict(peak: int) -> str:
    """두 축 중 최고점(peak)으로 보조 라벨을 단다. (버리는 용도 아님 — 표시용)"""
    if peak >= 4:
        return "strong"
    if peak == 3:
        return "ok"
    return "weak"


def parse_critique(content: str) -> dict:
    """LLM 응답에서 critique JSON을 견고하게 파싱하고 점수를 1~5로 정규화한다.

    축① 실용성(practicality) / 축② 트렌드 폭발력(trend_power) 각 1~5.
    peak = max(두 축) — 추천 1위 산정의 1차 키. verdict는 보조 라벨.
    ⚠️ 이 결과는 컨셉을 버리는 데 쓰지 않는다(평가 메모일 뿐).
    """
    text = (content or "").strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            data = json.loads(text[start:end + 1])
        else:
            raise

    practicality = _clamp_score(data.get("practicality"))
    trend_power = _clamp_score(data.get("trend_power"))
    peak = max(practicality, trend_power)
    return {
        "practicality": practicality,
        "trend_power": trend_power,
        "peak": peak,
        "comment": str(data.get("comment", "")).strip(),
        "verdict": _verdict(peak),
    }


# ── LangChain Tool 정의 ────────────────────────────────────

@tool
def concept_critic(
    app_name: str,
    description: str,
    core_feature: str = "",
    meme_trend: str = "",
    it_trend: str = "",
) -> dict[str, Any]:
    """
    생성된 컨셉을 두 축으로 self-critique 평가한다 (점수 + 코멘트만 기록).

    ★ 중요: 이 평가는 컨셉을 "버리는" 용도가 아니다. 품질은 취향 문제라 정답이 없고
    LLM이 "별로"라 해도 사용자는 좋아할 수 있다. 따라서 절대 탈락시키지 말고,
    점수와 코멘트만 기록한다. 최종 결정권은 사람에게 있다(마지막 통합 선택).

    평가 기준 — 아래 두 축 중 "적어도 하나는 압도적"이어야 좋은 컨셉이다:
    - 축① 실용성(practicality, 1~5): 정보/알림/행동 교정/기록·동기부여 등 실질적 쓸모.
      · 강함 예: 거북목 교정, 제스처 투두 관리, GitHub 커밋 동기부여 시각화.
    - 축② 트렌드 폭발력(trend_power, 1~5): 실용성이 없어도 압도적으로 핫한 트렌드라
      "그것만으로 갖고 싶은가". · 강함 예: 클로드 코드로 타자 칠 때 춤추는 카피바라.
    - 둘 다 약하면 낮은 점수(예: "Rust 빠를수록 카피바라가 온천에 앉음" = 실용 0 + 트렌드 평범).

    Args:
        app_name: 평가할 앱 이름
        description: 한 줄 설명
        core_feature: 핵심 기능
        meme_trend: 밈 트렌드 근거 (평가 맥락)
        it_trend: IT 트렌드 근거 (평가 맥락)

    Returns:
        ok: 성공 여부
        data: practicality(1~5), trend_power(1~5), peak, comment, verdict
        error: 실패 시 에러 정보
    """
    prompt = f"""당신은 "귀엽고 하찮지만 실용적인" macOS 유틸 아이디어를 평가하는 심사위원입니다.
아래 컨셉을 두 축으로 채점하세요. 두 축 중 **적어도 하나가 압도적**이어야 좋은 컨셉입니다.
둘 다 약하면 낮게 주세요. (단, 이 점수는 컨셉을 버리는 데 쓰지 않고 추천 근거로만 씁니다.)

[축① 실용성 1~5] 사용자에게 실질적 쓸모(정보/알림/행동 교정/기록·동기부여)를 주는가.
[축② 트렌드 폭발력 1~5] 실용성이 없어도, 압도적으로 핫한 트렌드라 "그것만으로 갖고 싶은가".

평가 대상:
- 앱 이름: {app_name}
- 설명: {description}
- 핵심 기능: {core_feature}
- 밈 트렌드 근거: {meme_trend}
- IT 트렌드 근거: {it_trend}

아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요:
{{
  "practicality": 1~5 정수,
  "trend_power": 1~5 정수,
  "comment": "한 줄 평 (한국어, 왜 그 점수인지 + 어느 축이 강한지)"
}}"""

    try:
        response = _llm.invoke([HumanMessage(content=prompt)])
        data = parse_critique(response.content)
        return {
            "ok": True,
            "data": {
                "app_name": app_name,
                **data,
                "source_provenance": {
                    "data_source": "llm_inference",
                    "model": "claude-haiku-4-5-20251001",
                    "temperature": 0.2,
                },
            },
            "error": None,
        }
    except Exception as e:
        # 평가 실패해도 컨셉을 버리지 않는다 — 중립 점수로 기록.
        return {
            "ok": False,
            "data": {
                "app_name": app_name,
                "practicality": 3, "trend_power": 3, "peak": 3,
                "comment": "(평가 실패 — 중립 처리)", "verdict": "ok",
            },
            "error": {
                "code": "CRITIQUE_FAILED",
                "message": str(e),
                "fallback_action": "중립 점수(3/3)로 기록하고 진행 — 컨셉은 버리지 않음",
            },
        }
