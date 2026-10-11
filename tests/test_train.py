import json

from email_checker import DELETE, KEEP, db, train


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_export_holds_out_every_fifth_reviewed_mail(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for n in range(1, 11):
        mail_id = db.add_mail(conn, "gmail", f"<{n}>", "A <a@x.com>", f"제목{n}", "본문", "", "")
        db.set_label(conn, mail_id, DELETE if n % 2 else KEEP)
    db.add_mail(conn, "gmail", "<x>", "A", "검토 안 됨", "본문", "", "")
    assert train.export(conn, tmp_path / "work") == (8, 2)
    ev = lines(tmp_path / "work" / "eval.jsonl")
    assert [r["state"]["email"].split("\n")[1] for r in ev] == ["Subject: 제목5", "Subject: 제목10"]
    assert [r["expected"] for r in ev] == [{"label": "delete"}, {"label": "read"}]
    assert ev[0]["questions"]["label"]["type"] == "choice"


def test_export_replaces_previous_work(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    (tmp_path / "work" / "candidate").mkdir(parents=True)
    train.export(conn, tmp_path / "work")
    assert not (tmp_path / "work" / "candidate").exists()


def test_new_accuracy_and_promote_keeps_one_previous(tmp_path):
    models = tmp_path / "models"

    def candidate(acc):
        c = models / "work" / "candidate"
        c.mkdir(parents=True)
        (c / "train_report.json").write_text(json.dumps({"after": {"accuracy": acc}}))
        return c

    c = candidate(0.8)
    assert train.new_accuracy(c) == 0.8
    train.promote(models, c)
    train.promote(models, candidate(0.9))
    assert train.new_accuracy(models / "current") == 0.9
    assert train.new_accuracy(models / "previous") == 0.8
    assert not c.exists()
