"""dataset.jsonl (messages 형식) → train.jsonl + validation.jsonl (컬럼 형식) 변환.

분리 규칙 (random_seed=42):
  각 label 그룹에서 ~10%를 validation으로 분리
    prompt_injection 16 → train 14 / val 2
    out_of_scope    9  → train 8  / val 1
    safe           15  → train 13 / val 2
  합계: train 35 / validation 5
"""
import json
import random
from pathlib import Path

SEED = 42
DATA_DIR = Path(__file__).parent
INPUT = DATA_DIR / "dataset.jsonl"
TRAIN_OUT = DATA_DIR / "train.jsonl"
VALID_OUT = DATA_DIR / "validation.jsonl"

VAL_COUNTS = {
    "prompt_injection": 2,
    "out_of_scope": 1,
    "safe": 2,
}


def convert_row(row: dict) -> dict:
    msgs = row["messages"]
    return {
        "system_prompt": msgs[0]["content"],
        "user_prompt": msgs[1]["content"],
        "assistant": msgs[2]["content"],
    }


def main():
    rows_by_label: dict[str, list] = {}
    with open(INPUT, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line.strip())
            label = json.loads(row["messages"][2]["content"])["risk"]
            rows_by_label.setdefault(label, []).append(row)

    rng = random.Random(SEED)
    train, val = [], []

    for label, rows in sorted(rows_by_label.items()):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        n_val = VAL_COUNTS.get(label, max(1, len(rows) // 10))
        val.extend(shuffled[:n_val])
        train.extend(shuffled[n_val:])

    rng.shuffle(train)
    rng.shuffle(val)

    for path, data in [(TRAIN_OUT, train), (VALID_OUT, val)]:
        with open(path, "w", encoding="utf-8") as f:
            for row in data:
                f.write(json.dumps(convert_row(row), ensure_ascii=False) + "\n")

    print(f"train: {len(train)} rows → {TRAIN_OUT}")
    print(f"validation: {len(val)} rows → {VALID_OUT}")

    for label in sorted(rows_by_label):
        t = sum(1 for r in train if json.loads(convert_row(r)["assistant"])["risk"] == label)
        v = sum(1 for r in val if json.loads(convert_row(r)["assistant"])["risk"] == label)
        print(f"  {label}: train={t}, val={v}")


if __name__ == "__main__":
    main()
