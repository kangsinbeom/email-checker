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
# 재학습 전 기본 모델용 보류 기준값.
MIN_CONFIDENCE = 0.9


def state(sender, subject, body) -> dict:
    # 학습과 추론이 같은 모양을 써야 한다.
    return {"email": f"From: {sender}\nSubject: {subject}\n\n{laya.clean_email_body(body)}"}


def label_from_answer(answer) -> str:
    if answer.get("abstention") == "abstained":
        return HOLD
    return TO_LABEL[answer["choice"]]


class Classifier:
    def __init__(self):
        # MX450(1.64GiB)은 추론에서도 OOM이 나서 CPU로 돌린다. 메일 한 통에 0.2~0.5초.
        self.agent = laya.load("ml", device="cpu")

    def classify(self, sender, subject, body) -> str:
        result = laya.decide(self.agent, state(sender, subject, body), questions=QUESTIONS,
                             return_details=True, min_confidence=MIN_CONFIDENCE)
        return label_from_answer(result.answers["label"])
