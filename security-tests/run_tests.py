"""
10주차 보안 테스트 실행기
Before/After 4개 케이스를 순서대로 실행하고 results/에 JSON으로 저장한다.

실행 방법:
  # Before (가드레일 적용 전 코드 상태로 실행해야 의미가 있다)
  python security-tests/run_tests.py --phase before

  # After (가드레일 적용 후)
  python security-tests/run_tests.py --phase after

  # 결과 비교
  python security-tests/run_tests.py --compare

주의: case1/2/4는 실제 run_agent()를 호출하므로 ANTHROPIC_API_KEY 등 .env 설정이
필요하고, LLM/외부 API 호출이 실제로 발생한다(과금·지연 있음). case3은 네트워크
경계만 monkeypatch하므로 외부 API를 호출하지 않는다.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

CASE_MODULES = [
    "case1_direct_injection",
    "case2_korean_injection",
    "case3_indirect_injection",
    "case4_normal",
]


def _run_phase(phase: str) -> None:
    import importlib

    for mod_name in CASE_MODULES:
        mod = importlib.import_module(f"cases.{mod_name}")
        print(f"\n{'='*60}\n[{phase}] {mod_name} 실행 중...\n{'='*60}")
        result = mod.run(phase)
        out_path = RESULTS_DIR / f"{phase}_{mod_name.split('_', 1)[0]}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  guardrail_triggered={result['guardrail_triggered']} "
              f"risk_level={result['risk_level']}")
        print(f"  → {out_path}")


def _compare() -> None:
    case_ids = ["case1", "case2", "case3", "case4"]
    print(f"\n{'='*70}\n결과 비교 (Before → After)\n{'='*70}")
    for cid in case_ids:
        before_path = RESULTS_DIR / f"before_{cid}.json"
        after_path = RESULTS_DIR / f"after_{cid}.json"
        if not before_path.exists() or not after_path.exists():
            print(f"\n[{cid}] before/after 결과 파일이 없습니다. 먼저 두 phase를 모두 실행하세요.")
            continue
        before = json.loads(before_path.read_text(encoding="utf-8"))
        after = json.loads(after_path.read_text(encoding="utf-8"))
        print(f"\n[{cid}] {before.get('case_id')}")
        print(f"  Before: guardrail_triggered={before['guardrail_triggered']:<5} "
              f"risk={before['risk_level']:<6} | {before['response_summary']}")
        print(f"  After : guardrail_triggered={after['guardrail_triggered']:<5} "
              f"risk={after['risk_level']:<6} | {after['response_summary']}")


def main():
    parser = argparse.ArgumentParser(description="10주차 보안 테스트 실행기")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phase", choices=["before", "after"], help="실행할 phase")
    group.add_argument("--compare", action="store_true", help="before/after 결과 비교")
    args = parser.parse_args()

    if args.compare:
        _compare()
    else:
        _run_phase(args.phase)


if __name__ == "__main__":
    main()
