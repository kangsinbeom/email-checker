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
    counts = {"train": 0, "eval": 0}
    with open(work / "train.jsonl", "w", encoding="utf-8") as tr, \
            open(work / "eval.jsonl", "w", encoding="utf-8") as ev:
        for row in db.labelled(conn):
            # 시험은 실제 메일로만 한다. id로 나눠야 매번 같은 메일이 시험용으로 남는다.
            split = "eval" if row["source"] == "mail" and row["id"] % 5 == 0 else "train"
            line = {"state": state(row["sender"], row["subject"], row["body"]),
                    "questions": QUESTIONS, "expected": {"label": FROM_LABEL[row["label"]]}}
            (ev if split == "eval" else tr).write(json.dumps(line, ensure_ascii=False) + "\n")
            counts[split] += 1
    return counts["train"], counts["eval"]


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


def new_accuracy(candidate: Path) -> float:
    return json.loads((candidate / "train_report.json").read_text())["after"]["accuracy"]


def promote(models_dir: Path, candidate: Path) -> None:
    current, previous = models_dir / "current", models_dir / "previous"
    shutil.rmtree(previous, ignore_errors=True)
    if current.exists():
        current.rename(previous)
    candidate.rename(current)
