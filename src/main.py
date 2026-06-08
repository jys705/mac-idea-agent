import json
import argparse
from src.agent import run_agent


def main():
    parser = argparse.ArgumentParser(
        description="하찮은 맥앱 아이디어 브리핑 에이전트"
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default="오늘 뭐 만들면 재밌을까?",
        help="사용자 요청 (기본값: '오늘 뭐 만들면 재밌을까?')"
    )
    parser.add_argument(
        "--focus", "-f",
        type=str,
        choices=["meme", "IT", "both"],
        default="both",
        help="트렌드 수집 방향 (기본값: both)"
    )
    parser.add_argument(
        "--difficulty", "-d",
        type=str,
        choices=["1day", "3days", "1week"],
        default=None,
        help="최대 구현 난이도 제한 (기본값: 제한 없음)"
    )
    parser.add_argument(
        "--exclude-existing", "-e",
        action="store_true",
        default=False,
        help="유사 앱 있으면 자동 루프백 (기본값: False)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="결과를 저장할 JSON 파일 경로 (기본값: 터미널 출력)"
    )

    args = parser.parse_args()

    result = run_agent(
        user_query=args.query,
        trend_focus=args.focus,
        difficulty_limit=args.difficulty,
        exclude_existing=args.exclude_existing,
    )

    output = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n결과 저장 완료: {args.output}")
    else:
        print("\n" + "="*50)
        print("📱 오늘의 맥앱 아이디어 브리핑")
        print("="*50)
        print(output)

    brief = result.get("today_brief") or {}
    concepts = brief.get("concepts") or []
    meta = result.get("metadata") or {}

    # 추천 1위 + 밀린 후보 이력 (마지막 통합 선택의 근거)
    rec = meta.get("recommendation")
    passed = meta.get("passed_over") or []
    if rec or passed:
        print("\n" + "="*50)
        print("🏁 추천 & 밀린 후보 이력")
        print("="*50)
        if rec:
            print(f"  ⭐ 추천 1위: {rec.get('app_name')} — {rec.get('reason')}")
        for p in passed:
            print(f"     · {p.get('app_name')}: {p.get('reason')}")

    # 사용자 최종 선택 결과
    sel = meta.get("user_selection") or {}
    if sel.get("action") == "pass":
        print("\n🛌 오늘은 패스했습니다 — SPEC.md/BRIEF.md를 생성하지 않았습니다.")

    # 생성된 개발 킥오프 산출물(SPEC.md / BRIEF.md) 경로 안내 (선택한 1개만)
    kickoff_lines = []
    for c in concepts:
        kickoff = c.get("kickoff") or {}
        spec = kickoff.get("spec_path")
        brief_path = kickoff.get("brief_path")
        if spec or brief_path:
            kickoff_lines.append(f"  • {c.get('app_name', '?')}")
            if spec:
                kickoff_lines.append(f"      개발용  SPEC : {spec}")
            if brief_path:
                kickoff_lines.append(f"      사람용  BRIEF: {brief_path}")
    if kickoff_lines:
        print("\n" + "="*50)
        print("🚀 바로 개발 시작하기 — 생성된 킥오프 문서")
        print("="*50)
        print("\n".join(kickoff_lines))


if __name__ == "__main__":
    main()