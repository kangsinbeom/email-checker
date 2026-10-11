from email_checker import mailbox
from email_checker.mailbox import Mailbox

PLAIN = (
    "Message-ID: <m1@x.com>\r\nDate: Sun, 11 Oct 2026 01:08:00 +0000\r\nFrom: =?UTF-8?B?7ZmN6ri464+Z?= <hong@x.com>\r\n"
    "Subject: =?UTF-8?B?7ZqM7J2Y?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n안녕하세요\r\n"
).encode()


def test_parse_plain_utf8_with_encoded_headers():
    message_id, sender, subject, body, date = mailbox.parse_message(PLAIN)
    assert (message_id, sender, subject, body.strip()) == ("<m1@x.com>", "홍길동 <hong@x.com>", "회의", "안녕하세요")
    assert date == "2026-10-11 10:08"  # 한국 시간


def test_parse_missing_or_broken_date():
    assert mailbox.parse_message(b"Subject: s\r\n\r\nb")[4] == ""
    assert mailbox.parse_message(b"Date: yesterday\r\nSubject: s\r\n\r\nb")[4] == ""


def test_parse_html_only():
    raw = (b"Subject: s\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<style>p{}</style><p>Hi &amp; bye</p>")
    assert mailbox.parse_message(raw)[3].split() == ["Hi", "&", "bye"]


def test_parse_ks_c_5601_with_cp949_only_char():
    body = "똠방각하".encode("cp949")  # '똠'은 euc-kr에 없고 cp949에만 있다
    raw = b"Subject: s\r\nContent-Type: text/plain; charset=ks_c_5601-1987\r\n\r\n" + body
    assert mailbox.parse_message(raw)[3] == "똠방각하"


class FakeIMAP:
    def __init__(self, search=b"5", raw=PLAIN):
        self.search, self.raw, self.calls = search, raw, []

    def status(self, mailbox, items):
        return "OK", [b'"INBOX" (UIDVALIDITY 9)']

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [self.search]
        return "OK", [(b"1 (X-GM-MSGID 255 UID 5 BODY[] {10}", self.raw), b")"]


def box(imap):
    b = Mailbox.__new__(Mailbox)
    b.account, b.imap = "gmail", imap
    return b


def test_uidvalidity():
    assert box(FakeIMAP()).uidvalidity() == 9


def test_uids_after_ignores_last_mail_returned_by_star():
    assert box(FakeIMAP(search=b"3")).uids_after(3) == []
    assert box(FakeIMAP(search=b"4 7 5")).uids_after(3) == [4, 5, 7]


def test_fetch_peeks_and_builds_gmail_link():
    imap = FakeIMAP()
    m = box(imap).fetch(5, 9)
    assert imap.calls == [("FETCH", "5", "(X-GM-MSGID BODY.PEEK[])")]  # PEEK이라 읽음 표시가 바뀌지 않는다
    assert (m.message_id, m.subject) == ("<m1@x.com>", "회의")
    assert m.link == "https://mail.google.com/mail/u/0/#all/ff"


def test_fetch_without_message_id_uses_uid():
    m = box(FakeIMAP(raw=b"Subject: s\r\n\r\nbody")).fetch(5, 9)
    assert m.message_id == "uid:9:5"


def test_fetch_naver_peeks_without_gmail_link():
    imap = FakeIMAP()
    b = box(imap)
    b.account = "naver"
    m = b.fetch(5, 9)
    assert imap.calls == [("FETCH", "5", "(BODY.PEEK[])")]
    assert (m.subject, m.link) == ("회의", "")
