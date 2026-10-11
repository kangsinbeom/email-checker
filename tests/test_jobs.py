import csv
import io

from email_checker import DELETE, HOLD, KEEP, db, jobs
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


def mail(n, subject, sender="A <a@x.com>"):
    return Mail(f"<{n}>", sender, subject, "본문", f"https://mail.google.com/mail/u/0/#all/{n:x}")


def read(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def run(conn, box):
    return jobs.daily(conn, lambda: [box], FakeClassifier())


def test_daily_reports_every_unreviewed_mail_with_its_classification(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의"), 3: mail(3, "애매")})
    data, counts = run(conn, box)
    assert data.startswith(b"\xef\xbb\xbf")  # Numbers/Excel이 한글을 UTF-8로 읽게 하는 BOM
    assert read(data) == [
        ["id", "분류", "제목", "보낸 사람", "링크"],
        ["1", DELETE, "광고", "A <a@x.com>", "https://mail.google.com/mail/u/0/#all/1"],
        ["2", KEEP, "회의", "A <a@x.com>", "https://mail.google.com/mail/u/0/#all/2"],
        ["3", HOLD, "애매", "A <a@x.com>", "https://mail.google.com/mail/u/0/#all/3"],
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
    assert [r[0] for r in read(data)[1:]] == ["1", "2"]


def test_daily_skips_known_mail_after_uidvalidity_reset(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    run(conn, FakeBox("gmail", {1: mail(1, "광고")}))
    data, _ = run(conn, FakeBox("gmail", {1: mail(1, "광고"), 2: mail(9, "회의")}, validity=2))
    assert [r[2] for r in read(data)[1:]] == ["광고", "회의"]


def test_daily_neutralises_formula_in_subject_and_sender(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, '=HYPERLINK("http://evil")', sender="@evil"),
                            2: mail(2, "+1"), 3: mail(3, "-50% 세일")})
    rows = read(run(conn, box)[0])[1:]
    assert [(r[2], r[3]) for r in rows] == [
        ('\'=HYPERLINK("http://evil")', "'@evil"), ("'+1", "A <a@x.com>"), ("'-50% 세일", "A <a@x.com>")]


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
