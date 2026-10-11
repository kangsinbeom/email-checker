import csv
import io

import pytest

from email_checker import DELETE, HOLD, KEEP, db, jobs, report
from email_checker.mailbox import Mail


class FakeBox:
    def __init__(self, account, mails, validity=1):
        self.account, self.mails, self.validity = account, mails, validity
        self.closed = False

    def uidvalidity(self):
        return self.validity

    def uids_after(self, last):
        return sorted(u for u in self.mails if u > last)

    def fetch(self, uid, validity):
        return self.mails[uid]

    def close(self):
        self.closed = True


class FakeClassifier:
    def classify(self, sender, subject, body):
        return {"광고": DELETE, "회의": KEEP}.get(subject, HOLD)


def mail(n, subject, sender="A <a@x.com>", date=None):
    return Mail(f"<{n}>", sender, subject, "본문", f"https://mail.google.com/mail/u/0/#all/{n:x}",
                date or f"2026-10-{n:02d} 09:00")


def read(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def run(conn, box):
    return jobs.daily(conn, lambda: [box], FakeClassifier())


def test_daily_reports_every_unreviewed_mail_newest_first(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의"), 3: mail(3, "애매")})
    data, counts = run(conn, box)
    assert data.startswith(b"\xef\xbb\xbf")  # Numbers/Excel이 한글을 UTF-8로 읽게 하는 BOM
    assert read(data) == [
        ["분류", "날짜", "메일함", "보낸 사람", "제목", "링크", "id"],
        [HOLD, "2026-10-03 09:00", "Gmail", "A <a@x.com>", "애매", "https://mail.google.com/mail/u/0/#all/3", "3"],
        [KEEP, "2026-10-02 09:00", "Gmail", "A <a@x.com>", "회의", "https://mail.google.com/mail/u/0/#all/2", "2"],
        [DELETE, "2026-10-01 09:00", "Gmail", "A <a@x.com>", "광고", "https://mail.google.com/mail/u/0/#all/1", "1"],
    ]
    assert counts == {DELETE: 1, KEEP: 1, HOLD: 1}
    assert box.closed


def test_daily_keeps_reporting_unreviewed_mail_and_fetches_only_new(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고")})
    run(conn, box)
    box.mails[2] = mail(2, "회의")
    fetched = []
    box.fetch = lambda uid, v: fetched.append(uid) or box.mails[uid]
    data, counts = run(conn, box)
    assert fetched == [2]
    assert sorted(r[-1] for r in read(data)[1:]) == ["1", "2"]


def test_daily_skips_known_mail_after_uidvalidity_reset(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run(conn, FakeBox("gmail", {1: mail(1, "광고")}))
    data, _ = run(conn, FakeBox("gmail", {1: mail(1, "광고"), 2: mail(9, "회의")}, validity=2))
    assert [r[4] for r in read(data)[1:]] == ["회의", "광고"]


def test_daily_neutralises_formula_in_subject_and_sender(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, '=HYPERLINK("http://evil")', sender="@evil"),
                            2: mail(2, "+1"), 3: mail(3, "-50% 세일")})
    rows = read(run(conn, box)[0])[1:]
    assert [(r[4], r[3]) for r in rows] == [
        ("'-50% 세일", "A <a@x.com>"), ("'+1", "A <a@x.com>"), ('\'=HYPERLINK("http://evil")', "'@evil")]


def test_daily_with_no_mail_reports_nothing(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _, counts = run(conn, FakeBox("gmail", {}))
    assert sum(counts.values()) == 0


def test_daily_closes_mailboxes_when_fetch_fails(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고")})
    box.fetch = lambda uid, v: 1 / 0
    try:
        run(conn, box)
    except ZeroDivisionError:
        pass
    assert box.closed


def reviewed(conn, text):
    return jobs.review(conn, text.encode())


def test_daily_reports_both_mailboxes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    naver = FakeBox("naver", {1: mail(1, "광고")})
    gmail = FakeBox("gmail", {1: mail(1, "회의", date="2026-10-05 09:00")})  # 같은 Message-ID라도 계정이 다르면 다른 메일
    data, _ = jobs.daily(conn, lambda: [naver, gmail], FakeClassifier())
    assert [(r[2], r[4]) for r in read(data)[1:]] == [("Gmail", "회의"), ("네이버", "광고")]


def test_review_reports_initial_accuracy_as_unchanged_rows(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의"), 3: mail(3, "애매"),
                            4: mail(4, "광고"), 5: mail(5, "애매")})
    run(conn, box)  # 예측: 1 삭제, 2 확인, 3 보류, 4 삭제, 5 보류
    text, learned = reviewed(conn, "id,분류\n1,삭제\n2,확인\n3,확인\n4,확인\n5,보류\n")
    assert "고치지 않은 줄: 3/5 (60%)" in text
    assert "삭제 1/2" in text and "확인 1/1" in text
    assert "보류였던 메일 → 삭제 0, 확인 1, 그대로 보류 1" in text
    assert learned == 4
    data, _ = run(conn, box)
    assert [r[-1] for r in read(data)[1:]] == ["5"]  # 보류로 남긴 줄만 다시 실린다


def test_review_reads_cp949_csv_saved_by_excel(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run(conn, FakeBox("gmail", {1: mail(1, "광고")}))
    text, learned = jobs.review(conn, "id,분류,제목\r\n1, 확인 ,광고\r\n".encode("cp949"))
    assert learned == 1 and "고치지 않은 줄: 0/1" in text


def test_review_rejects_bad_rows_without_saving_anything(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run(conn, FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의")}))
    with pytest.raises(report.CsvError, match="3, 4"):
        reviewed(conn, "id,분류\n1,확인\n2,지워\nx,확인\n")
    assert len(db.unreviewed(conn)) == 2


def test_review_warns_about_unknown_ids(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run(conn, FakeBox("gmail", {1: mail(1, "광고")}))
    text, learned = reviewed(conn, "id,분류\n1,삭제\n99,삭제\n")
    assert learned == 1 and "99" in text
