import os

import pytest

from email_checker import DELETE, HOLD, KEEP, classify


def test_label_from_answer():
    assert classify.label_from_answer({"choice": "delete"}) == DELETE
    assert classify.label_from_answer({"choice": "read", "abstention": "passed"}) == KEEP
    assert classify.label_from_answer({"choice": "delete", "abstention": "abstained"}) == HOLD


def test_state_shape():
    s = classify.state("A <a@x.com>", "제목", "본문")
    assert list(s) == ["email"]
    assert s["email"].startswith("From: A <a@x.com>\nSubject: 제목\n\n") and "본문" in s["email"]


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="모델을 불러온다. RUN_SLOW=1로 실행")
def test_real_model_korean():
    c = classify.Classifier()
    assert c.classify("쇼핑몰 <ad@shop.com>", "(광고) 신규 가입 쿠폰 5만원", "오늘 자정 마감 이벤트") in (DELETE, HOLD)
    assert c.classify("민수 <minsu@x.com>", "주말 저녁", "토요일 7시에 저녁 같이 먹을래?") in (KEEP, HOLD)


def test_min_confidence_comes_from_retrained_model_report(tmp_path):
    import json
    assert classify.load_min_confidence(None) == classify.MIN_CONFIDENCE
    assert classify.load_min_confidence(tmp_path) == classify.MIN_CONFIDENCE
    (tmp_path / "train_report.json").write_text(json.dumps({"abstention_thresholds": {}}))
    assert classify.load_min_confidence(tmp_path) == classify.MIN_CONFIDENCE
    (tmp_path / "train_report.json").write_text(json.dumps({"abstention_thresholds": {"choice:2": 0.7}}))
    assert classify.load_min_confidence(tmp_path) == {"choice:2": 0.7}


def test_accuracy_reads_eval_lines_containing_unicode_line_separator(tmp_path):
    import json
    p = tmp_path / "eval.jsonl"
    rows = [{"state": {"email": "본문 둘째 줄"}, "expected": {"label": "delete"}},
            {"state": {"email": "회의"}, "expected": {"label": "read"}}]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    c = classify.Classifier.__new__(classify.Classifier)
    c._answer = lambda st, min_confidence=None: {"choice": "delete"}
    assert c.accuracy(p) == 0.5
