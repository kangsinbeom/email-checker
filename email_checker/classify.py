import json
from pathlib import Path

import laya

from . import DELETE, HOLD, KEEP

QUESTIONS = {
    "label": {
        "type": "choice",
        "instructions": "Should the user read this email, or can it be deleted?",
        "criteria": {
            # "사용자가 고른 뉴스레터" 같은 문구는 모델이 판단할 수 없고,
            # 넣으면 삭제를 하나도 못 고른다(2026-10-08 실측 0/11).
            "delete": "advertising, marketing, promotions, spam, bulk newsletters",
            "read": "personal mail from a real person, work mail, government or military notices",
        },
    }
}
TO_LABEL = {"delete": DELETE, "read": KEEP}
FROM_LABEL = {v: k for k, v in TO_LABEL.items()}
# 재학습 전 기본 모델용 보류 기준값. 재학습한 모델은 train_report.json의 값을 쓴다.
MIN_CONFIDENCE = 0.9


def state(sender, subject, body) -> dict:
    # 학습과 추론이 같은 모양을 써야 한다.
    return {"email": f"From: {sender}\nSubject: {subject}\n\n{laya.clean_email_body(body)}"}


def label_from_answer(answer) -> str:
    if answer.get("abstention") == "abstained":
        return HOLD
    return TO_LABEL[answer["choice"]]


def load_min_confidence(model_dir):
    report = Path(model_dir) / "train_report.json" if model_dir else None
    if report and report.exists():
        thresholds = json.loads(report.read_text()).get("abstention_thresholds")
        if thresholds:
            return thresholds
    return MIN_CONFIDENCE


class Classifier:
    def __init__(self, model_dir=None):
        # MX450(1.64GiB)은 추론에서도 OOM이 나서 CPU로 돌린다. 메일 한 통에 0.2~0.5초.
        self.agent = laya.load(str(model_dir) if model_dir else "ml", device="cpu")
        self.min_confidence = load_min_confidence(model_dir)

    def _answer(self, st, min_confidence=None) -> dict:
        result = laya.decide(self.agent, st, questions=QUESTIONS, return_details=True,
                             min_confidence=min_confidence)
        return result.answers["label"]

    def classify(self, sender, subject, body) -> str:
        return label_from_answer(self._answer(state(sender, subject, body), self.min_confidence))

    def accuracy(self, jsonl_path) -> float:
        rows = [json.loads(line) for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines() if line]
        hits = sum(self._answer(r["state"])["choice"] == r["expected"]["label"] for r in rows)
        return hits / len(rows)
