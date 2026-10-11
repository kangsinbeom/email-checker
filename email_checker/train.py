import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import db
from .classify import FROM_LABEL, QUESTIONS, state

MIN_TRAIN, MIN_EVAL = 20, 10


def export(conn, work: Path):
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    train, evals = {}, []
    for row in db.labelled(conn):
        line = json.dumps({"state": state(row["sender"], row["subject"], row["body"]),
                           "questions": QUESTIONS, "expected": {"label": FROM_LABEL[row["label"]]}},
                          ensure_ascii=False) + "\n"
        # 시험은 실제 메일로만 한다. id로 나눠야 매번 같은 메일이 시험용으로 남는다.
        if row["source"] == "mail" and row["id"] % 5 == 0:
            evals.append(line)
        else:
            train.setdefault(row["label"], []).append(line)
    # 적은 쪽 정답을 반복해 수를 맞춘다. 삭제 77 : 확인 12로 학습하면 전부 삭제라고 답하는
    # 모델이 나왔다(2026-10-12 실측).
    most = max(map(len, train.values()), default=0)
    lines = [rows[i % len(rows)] for rows in train.values() for i in range(most)]
    (work / "train.jsonl").write_text("".join(lines), encoding="utf-8")
    (work / "eval.jsonl").write_text("".join(evals), encoding="utf-8")
    return len(lines), len(evals)


def run_laya(work: Path) -> Path:
    candidate = work / "candidate"
    # MX450은 학습 중 메모리가 부족해서 CPU로 돌린다. nice로 bot 응답을 덜 막는다.
    subprocess.run(
        ["nice", "-n", "10", str(Path(sys.executable).with_name("laya-train")),
         "--data", str(work / "train.jsonl"), "--eval", str(work / "eval.jsonl"),
         "--base", "multilingual", "--out", str(candidate), "--device", "cpu"],
        check=True, capture_output=True, text=True,
    )
    return candidate


def promote(models_dir: Path, candidate: Path) -> None:
    current, previous = models_dir / "current", models_dir / "previous"
    shutil.rmtree(previous, ignore_errors=True)
    if current.exists():
        current.rename(previous)
    candidate.rename(current)
