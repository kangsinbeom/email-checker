import email
import html
import imaplib
import re
from dataclasses import dataclass
from email import policy

HOSTS = {"gmail": "imap.gmail.com"}
KOREAN_CHARSETS = {"ks_c_5601-1987", "ks_c_5601", "euc-kr", "euc_kr"}


@dataclass
class Mail:
    message_id: str
    sender: str
    subject: str
    body: str
    link: str


def html_to_text(s: str) -> str:
    return html.unescape(re.sub(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>", " ", s))


def _text(part) -> str:
    # get_content()는 디코딩 실패를 조용히 �로 바꾼다. 그래서 직접 디코딩한다.
    charset = (part.get_content_charset() or "utf-8").lower()
    if charset in KOREAN_CHARSETS:
        charset = "cp949"  # cp949는 euc-kr의 상위 집합이고, 한국 메일은 cp949 글자를 섞어 쓴다
    payload = part.get_payload(decode=True) or b""
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def parse_message(raw: bytes):
    msg = email.message_from_bytes(raw, policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = "" if part is None else _text(part)
    if part is not None and part.get_content_type() == "text/html":
        body = html_to_text(body)
    return (str(msg.get("Message-ID") or "").strip(), str(msg.get("From") or ""),
            str(msg.get("Subject") or ""), body)


def gmail_link(msgid: int) -> str:
    # ponytail: 브라우저의 첫 번째 Google 계정(/u/0/)을 가정한다. 여러 계정을 쓰면 다른 계정이 열린다.
    return f"https://mail.google.com/mail/u/0/#all/{msgid:x}"


class Mailbox:
    def __init__(self, account, user, password):
        self.account = account
        self.imap = imaplib.IMAP4_SSL(HOSTS[account])
        self.imap.login(user, password)
        # 이 단계에서는 메일함을 바꾸지 않는다. 휴지통 이동(#7)을 넣을 때 readonly를 푼다.
        self.imap.select("INBOX", readonly=True)

    def uidvalidity(self) -> int:
        _, data = self.imap.status("INBOX", "(UIDVALIDITY)")
        return int(re.search(rb"UIDVALIDITY (\d+)", data[0]).group(1))

    def uids_after(self, last_uid) -> list[int]:
        _, data = self.imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        # "N:*"는 N보다 큰 UID가 없어도 마지막 메일 하나를 돌려준다.
        return sorted(u for u in map(int, data[0].split()) if u > last_uid)

    def fetch(self, uid, uidvalidity) -> Mail:
        gmail = self.account == "gmail"
        _, data = self.imap.uid("FETCH", str(uid), "(X-GM-MSGID BODY.PEEK[])" if gmail else "(BODY.PEEK[])")
        meta, raw = data[0]
        message_id, sender, subject, body = parse_message(raw)
        link = gmail_link(int(re.search(rb"X-GM-MSGID (\d+)", meta).group(1))) if gmail else ""
        return Mail(message_id or f"uid:{uidvalidity}:{uid}", sender, subject, body, link)

    def close(self):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # 이미 끊긴 연결은 닫을 게 없다
