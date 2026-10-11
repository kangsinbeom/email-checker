# email-checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네이버·Gmail 메일을 매일 07:00에 삭제/확인/보류로 분류해 디스코드에 CSV 보고서로 올리고, 사용자가 고쳐 올린 CSV로 메일을 지우고 Laya 모델을 재학습하는 bot을 만든다.

**Architecture:** 디스코드 bot 프로세스 하나가 전부 한다. 07:00 루프가 IMAP으로 메일을 모아 SQLite에 넣고 분류해 CSV를 올린다. 채널에 올라온 CSV는 검토(또는 예시 데이터)로 처리하고, 정답이 생기면 `laya-train`을 CPU 서브프로세스로 돌려 더 나은 모델만 교체한다. 메일함·모델·디스코드에 닿는 부분은 얇게 두고, 판단 로직은 순수 함수와 가짜 객체로 테스트한다.

**Tech Stack:** Python 3.12, uv, laya 0.4.0, discord.py 2.7, stdlib `imaplib`/`email`/`sqlite3`/`csv`/`tomllib`, pytest, systemd.

**Spec:** `docs/superpowers/specs/2026-10-08-email-checker-design.md` (용어: `CONTEXT.md`)

## Global Constraints

- 분류 값은 정확히 `삭제`, `확인`, `보류` 세 문자열뿐이다(`email_checker/__init__.py`의 `DELETE`, `KEEP`, `HOLD`).
- **검토 없이 메일을 지우거나 옮기지 않는다.** 휴지통 이동은 정답이 `삭제`인 메일만 한다.
- 메일을 가져올 때 읽음 표시를 바꾸지 않는다(`BODY.PEEK[]`).
- 학습(`--device cpu`)과 추론(`laya.load(..., device="cpu")`) 모두 CPU로 한다. MX450(사용 가능 1.64GiB)은 둘 다 OOM이 난다.
- 모델 질문 문구는 Task 2의 `QUESTIONS`를 그대로 쓴다. "사용자가 고른 뉴스레터" 같은 모델이 알 수 없는 조건을 넣지 않는다(넣으면 삭제 정확도 0/11, 2026-10-08 실측).
- 모델 기본값은 Laya `multilingual` 체크포인트(`laya.load("ml")`, 학습 시 `--base multilingual`)다. 영어 체크포인트는 한국어에서 무작위 수준이다.
- 비밀값은 `.env`에만 두고 커밋하지 않는다. 코드는 환경변수로 읽는다(`python-dotenv` 쓰지 않음, systemd `EnvironmentFile`과 `uv run --env-file .env` 사용).
- 사용자에게 보이는 디스코드 메시지와 CSV 열 이름은 한국어다.
- 보고서 CSV는 UTF-8 BOM(`utf-8-sig`)으로 쓰고, 받을 때는 `utf-8-sig`를 먼저 시도하고 실패하면 `cp949`로 읽는다.

## Review Focus

1. **한국어 옛 인코딩 메일** (`ks_c_5601-1987`인데 cp949 전용 글자가 든 본문) — 크래시 없이 본문을 읽어야 한다. → Task 4 `test_parse_ks_c_5601_with_cp949_only_char`
2. **엑셀/Numbers로 고쳐 cp949로 저장한 CSV** — 검토가 그대로 처리돼야 한다. → Task 3 `test_parse_review_cp949`
3. **검토 전에 사용자가 직접 지운 메일** — 휴지통 이동 시 못 찾아도 실패하지 않고 "이미 없음"으로 센다. → Task 5 `test_review_counts_missing_mail`
4. **제목이 `=`로 시작하는 메일** (엑셀 수식 실행) — CSV에서 무력화돼야 한다. → Task 3 `test_report_neutralises_formula`
5. **Message-ID 헤더가 없는 메일** — 대신 쓸 id로 저장하고 그 UID로 휴지통에 옮길 수 있어야 한다. → Task 4 `test_fetch_without_message_id`, `test_move_by_synthetic_uid`

---

## File Structure

```
email_checker/
  __init__.py   분류 값 상수
  db.py         SQLite 스키마와 쿼리 (메일, 정답, 커서)
  classify.py   발신자 규칙, Laya 질문/상태, Classifier
  report.py     보고서 CSV 쓰기, 검토/예시 CSV 읽기
  mailbox.py    IMAP: 메일 파싱, 가져오기, 휴지통 이동
  jobs.py       daily / review / examples 작업 (위 모듈 조합)
  train.py      학습자료 내보내기, laya-train 실행, 모델 교체
  bot.py        디스코드 bot: 07:00 루프, CSV 업로드 처리, 재학습 예약
main.py         bot.run()
rules.toml      지정 뉴스레터·국방부 발신자 규칙
deploy/email-checker.service
tests/test_*.py
```

---

### Task 1: 프로젝트 골격과 저장소(db)

**Files:**
- Modify: `pyproject.toml`
- Create: `email_checker/__init__.py`, `email_checker/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `email_checker.DELETE = "삭제"`, `KEEP = "확인"`, `HOLD = "보류"`
  - `db.connect(path) -> sqlite3.Connection` (row_factory=`sqlite3.Row`, `check_same_thread=False`)
  - `db.add_mail(conn, account, message_id, sender, subject, body, link) -> int | None` (중복이면 None)
  - `db.add_example(conn, sender, subject, body, label) -> int`
  - `db.get(conn, mail_id) -> sqlite3.Row | None`
  - `db.unpredicted(conn) -> list[Row]`, `db.set_predicted(conn, mail_id, label)`
  - `db.unreviewed(conn) -> list[Row]`, `db.set_label(conn, mail_id, label)`
  - `db.pending_trash(conn) -> list[Row]`, `db.mark_trashed(conn, mail_id)`
  - `db.labelled(conn) -> list[Row]`
  - `db.get_cursor(conn, account) -> tuple[int, int] | None` (uidvalidity, last_uid), `db.set_cursor(conn, account, uidvalidity, last_uid)`
  - Row 열: `id, account, message_id, sender, subject, body, link, source('mail'|'example'), predicted, label, trashed`

- [ ] **Step 1: 의존성과 pytest 설정 추가**

```bash
uv add "discord.py>=2.7"
uv add --dev pytest
```

`pyproject.toml` 끝에 추가:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_db.py`

```python
from email_checker import DELETE, KEEP, db


def make(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_add_mail_ignores_duplicate_message_id(tmp_path):
    conn = make(tmp_path)
    first = db.add_mail(conn, "gmail", "<m1>", "A <a@x.com>", "s", "b", "")
    again = db.add_mail(conn, "gmail", "<m1>", "A <a@x.com>", "s", "b", "")
    other_account = db.add_mail(conn, "naver", "<m1>", "A <a@x.com>", "s", "b", "")
    assert first is not None and again is None and other_account is not None


def test_unreviewed_excludes_labelled_and_examples(tmp_path):
    conn = make(tmp_path)
    a = db.add_mail(conn, "gmail", "<a>", "s", "t", "b", "")
    b = db.add_mail(conn, "gmail", "<b>", "s", "t", "b", "")
    db.add_example(conn, "", "t", "b", KEEP)
    db.set_label(conn, b, DELETE)
    assert [r["id"] for r in db.unreviewed(conn)] == [a]


def test_unpredicted_and_set_predicted(tmp_path):
    conn = make(tmp_path)
    a = db.add_mail(conn, "gmail", "<a>", "s", "t", "b", "")
    assert [r["id"] for r in db.unpredicted(conn)] == [a]
    db.set_predicted(conn, a, KEEP)
    assert db.unpredicted(conn) == []
    assert db.get(conn, a)["predicted"] == KEEP


def test_pending_trash_only_reviewed_delete_not_trashed(tmp_path):
    conn = make(tmp_path)
    a = db.add_mail(conn, "gmail", "<a>", "s", "t", "b", "")
    b = db.add_mail(conn, "gmail", "<b>", "s", "t", "b", "")
    db.add_mail(conn, "gmail", "<c>", "s", "t", "b", "")
    db.set_label(conn, a, DELETE)
    db.set_label(conn, b, KEEP)
    assert [r["id"] for r in db.pending_trash(conn)] == [a]
    db.mark_trashed(conn, a)
    assert db.pending_trash(conn) == []


def test_labelled_includes_examples(tmp_path):
    conn = make(tmp_path)
    a = db.add_mail(conn, "gmail", "<a>", "s", "t", "b", "")
    db.set_label(conn, a, DELETE)
    e = db.add_example(conn, "", "t", "b", KEEP)
    assert sorted(r["id"] for r in db.labelled(conn)) == sorted([a, e])


def test_cursor_roundtrip(tmp_path):
    conn = make(tmp_path)
    assert db.get_cursor(conn, "naver") is None
    db.set_cursor(conn, "naver", 7, 42)
    db.set_cursor(conn, "naver", 7, 43)
    assert db.get_cursor(conn, "naver") == (7, 43)
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'email_checker'`)

- [ ] **Step 4: 구현** — `email_checker/__init__.py`

```python
DELETE, KEEP, HOLD = "삭제", "확인", "보류"
```

`email_checker/db.py`

```python
import sqlite3
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS mails (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    link TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'mail',
    predicted TEXT,
    label TEXT,
    trashed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account, message_id)
);
CREATE TABLE IF NOT EXISTS cursors (
    account TEXT PRIMARY KEY,
    uidvalidity INTEGER NOT NULL,
    last_uid INTEGER NOT NULL
);
"""


def connect(path) -> sqlite3.Connection:
    # bot이 asyncio.to_thread로 부르므로 스레드 검사를 끈다. 동시 접근은 bot의 lock이 막는다.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_mail(conn, account, message_id, sender, subject, body, link):
    cur = conn.execute(
        "INSERT OR IGNORE INTO mails (account, message_id, sender, subject, body, link)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (account, message_id, sender, subject, body, link),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def add_example(conn, sender, subject, body, label) -> int:
    cur = conn.execute(
        "INSERT INTO mails (account, message_id, sender, subject, body, source, label)"
        " VALUES ('example', ?, ?, ?, ?, 'example', ?)",
        (str(uuid.uuid4()), sender, subject, body, label),
    )
    conn.commit()
    return cur.lastrowid


def get(conn, mail_id):
    return conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()


def unpredicted(conn):
    return conn.execute(
        "SELECT * FROM mails WHERE source = 'mail' AND predicted IS NULL ORDER BY id"
    ).fetchall()


def set_predicted(conn, mail_id, label):
    conn.execute("UPDATE mails SET predicted = ? WHERE id = ?", (label, mail_id))
    conn.commit()


def unreviewed(conn):
    return conn.execute(
        "SELECT * FROM mails WHERE source = 'mail' AND label IS NULL ORDER BY id"
    ).fetchall()


def set_label(conn, mail_id, label):
    conn.execute("UPDATE mails SET label = ? WHERE id = ?", (label, mail_id))
    conn.commit()


def pending_trash(conn):
    return conn.execute(
        "SELECT * FROM mails WHERE source = 'mail' AND label = '삭제' AND trashed = 0 ORDER BY id"
    ).fetchall()


def mark_trashed(conn, mail_id):
    conn.execute("UPDATE mails SET trashed = 1 WHERE id = ?", (mail_id,))
    conn.commit()


def labelled(conn):
    return conn.execute("SELECT * FROM mails WHERE label IS NOT NULL ORDER BY id").fetchall()


def get_cursor(conn, account):
    row = conn.execute(
        "SELECT uidvalidity, last_uid FROM cursors WHERE account = ?", (account,)
    ).fetchone()
    return (row[0], row[1]) if row else None


def set_cursor(conn, account, uidvalidity, last_uid):
    conn.execute(
        "INSERT INTO cursors (account, uidvalidity, last_uid) VALUES (?, ?, ?)"
        " ON CONFLICT (account) DO UPDATE SET uidvalidity = excluded.uidvalidity,"
        " last_uid = excluded.last_uid",
        (account, uidvalidity, last_uid),
    )
    conn.commit()
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_db.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock email_checker/ tests/test_db.py
git commit -m "feat: add sqlite store for mails, labels and IMAP cursors"
```

---

### Task 2: 분류기 (발신자 규칙 + Laya)

**Files:**
- Create: `email_checker/classify.py`, `rules.toml`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `DELETE`, `KEEP`, `HOLD`
- Produces:
  - `classify.QUESTIONS: dict` (질문 id `"label"`, 선택지 `"delete"`/`"read"`)
  - `classify.TO_LABEL = {"delete": DELETE, "read": KEEP}`, `classify.FROM_LABEL` (역방향)
  - `classify.state(sender, subject, body) -> dict` (`{"email": str}`; 학습과 추론이 같은 모양을 쓴다)
  - `classify.load_rules(path) -> tuple[frozenset[str], frozenset[str]]` (보낸 사람 주소들, 도메인들)
  - `classify.rule_label(sender, rules) -> str | None`
  - `classify.label_from_answer(answer: dict) -> str`
  - `classify.load_min_confidence(model_dir: Path | None) -> float | dict`
  - `classify.Classifier(model_dir: Path | None, rules)` with `.classify(sender, subject, body) -> str`, `.accuracy(jsonl_path: Path) -> float`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_classify.py`

```python
import json
import os

import pytest

from email_checker import DELETE, HOLD, KEEP, classify

RULES = (frozenset({"news@letter.com"}), frozenset({"mnd.go.kr"}))


@pytest.mark.parametrize("sender, expected", [
    ("뉴스레터 <News@Letter.com>", KEEP),
    ("국방부 <notice@mnd.go.kr>", KEEP),
    ("육군 <a@army.mnd.go.kr>", KEEP),
    ("가짜 <a@evilmnd.go.kr>", None),
    ("광고 <ad@shop.com>", None),
    ("", None),
])
def test_rule_label(sender, expected):
    assert classify.rule_label(sender, RULES) == expected


def test_load_rules(tmp_path):
    p = tmp_path / "rules.toml"
    p.write_text('keep_senders = ["News@Letter.com"]\nkeep_domains = ["MND.go.kr"]\n', encoding="utf-8")
    assert classify.load_rules(p) == RULES


def test_label_from_answer():
    assert classify.label_from_answer({"choice": "delete"}) == DELETE
    assert classify.label_from_answer({"choice": "read", "abstention": "passed"}) == KEEP
    assert classify.label_from_answer({"choice": "delete", "abstention": "abstained"}) == HOLD


def test_load_min_confidence(tmp_path):
    assert classify.load_min_confidence(None) == classify.DEFAULT_MIN_CONFIDENCE
    assert classify.load_min_confidence(tmp_path) == classify.DEFAULT_MIN_CONFIDENCE
    (tmp_path / "train_report.json").write_text(json.dumps({"abstention_thresholds": {}}))
    assert classify.load_min_confidence(tmp_path) == classify.DEFAULT_MIN_CONFIDENCE
    (tmp_path / "train_report.json").write_text(json.dumps({"abstention_thresholds": {"choice:2": 0.7}}))
    assert classify.load_min_confidence(tmp_path) == {"choice:2": 0.7}


def test_state_shape():
    s = classify.state("A <a@x.com>", "제목", "본문")
    assert list(s) == ["email"] and "제목" in s["email"] and "본문" in s["email"]


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="모델을 내려받는다. RUN_SLOW=1로 실행")
def test_real_model_korean():
    c = classify.Classifier(None, (frozenset(), frozenset()))
    assert c.classify("쇼핑몰 <ad@shop.com>", "(광고) 신규 가입 쿠폰 5만원", "오늘 자정 마감 이벤트") in (DELETE, HOLD)
    assert c.classify("민수 <minsu@x.com>", "주말 저녁", "토요일 7시에 저녁 같이 먹을래?") in (KEEP, HOLD)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_classify.py -v`
Expected: FAIL (`ImportError: cannot import name 'classify'`)

- [ ] **Step 3: 구현** — `email_checker/classify.py`

```python
import json
import tomllib
from email.utils import parseaddr
from pathlib import Path

import laya

from . import DELETE, HOLD, KEEP

QUESTIONS = {
    "label": {
        "type": "choice",
        "instructions": "Should the user read this email, or can it be deleted?",
        "criteria": {
            # 지정 뉴스레터는 발신자 규칙이 먼저 거른다. "사용자가 고른 뉴스레터" 같은 문구는
            # 모델이 판단할 수 없고, 넣으면 삭제를 하나도 못 고른다(2026-10-08 실측 0/11).
            "delete": "advertising, marketing, promotions, spam, bulk newsletters",
            "read": "personal mail from a real person, work mail, government or military notices",
        },
    }
}
TO_LABEL = {"delete": DELETE, "read": KEEP}
FROM_LABEL = {v: k for k, v in TO_LABEL.items()}
# 재학습 전 기본 모델용. 재학습한 모델은 train_report.json의 보류 기준값을 쓴다.
DEFAULT_MIN_CONFIDENCE = 0.9


def state(sender, subject, body) -> dict:
    return {"email": f"From: {sender}\nSubject: {subject}\n\n{laya.clean_email_body(body)}"}


def load_rules(path):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return (
        frozenset(s.lower() for s in data.get("keep_senders", [])),
        frozenset(d.lower() for d in data.get("keep_domains", [])),
    )


def rule_label(sender, rules):
    senders, domains = rules
    addr = parseaddr(sender)[1].lower()
    if not addr:
        return None
    domain = addr.rpartition("@")[2]
    if addr in senders or any(domain == d or domain.endswith("." + d) for d in domains):
        return KEEP
    return None


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
    return DEFAULT_MIN_CONFIDENCE


class Classifier:
    def __init__(self, model_dir, rules):
        # MX450(1.64GiB)은 추론에서도 OOM이 나서 CPU로 돌린다. 메일 한 통에 0.2~0.5초.
        self.agent = laya.load(str(model_dir) if model_dir else "ml", device="cpu")
        self.min_confidence = load_min_confidence(model_dir)
        self.rules = rules

    def _answer(self, st, min_confidence=None) -> dict:
        result = laya.decide(self.agent, st, questions=QUESTIONS, return_details=True,
                             min_confidence=min_confidence)
        return result.answers["label"]

    def classify(self, sender, subject, body) -> str:
        return rule_label(sender, self.rules) or label_from_answer(
            self._answer(state(sender, subject, body), self.min_confidence))

    def accuracy(self, jsonl_path) -> float:
        rows = [json.loads(line) for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines() if line]
        hits = sum(self._answer(r["state"])["choice"] == r["expected"]["label"] for r in rows)
        return hits / len(rows)
```

`rules.toml`

```toml
# 이 발신자의 메일은 모델을 거치지 않고 바로 확인으로 분류한다.
# 지정 뉴스레터 발신 주소
keep_senders = []
# 국방부 등 도메인 (하위 도메인 포함)
keep_domains = ["mnd.go.kr"]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_classify.py -v`
Expected: 10 passed, 1 skipped

Run: `RUN_SLOW=1 USE_TF=0 uv run pytest tests/test_classify.py::test_real_model_korean -v`
Expected: 1 passed (모델은 Mac에 이미 받아 둠)

- [ ] **Step 5: Commit**

```bash
git add email_checker/classify.py rules.toml tests/test_classify.py
git commit -m "feat: classify mails with sender rules and Laya multilingual"
```

---

### Task 3: 보고서와 검토 CSV

**Files:**
- Create: `email_checker/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `DELETE`, `KEEP`, `HOLD`; Row 열 `id, predicted, subject, sender, link`
- Produces:
  - `report.COLUMNS = ["id", "분류", "제목", "보낸 사람", "링크"]`
  - `report.write_report(rows) -> bytes` (utf-8-sig)
  - `report.CsvError(ValueError)` — 사용자에게 그대로 보여 줄 한국어 메시지
  - `report.is_review(data: bytes) -> bool`
  - `report.parse_review(data: bytes) -> dict[int, str]` (id → 삭제/확인/보류)
  - `report.parse_examples(data: bytes) -> list[dict]` (키: `sender, subject, body, label`)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_report.py`

```python
import csv
import io

import pytest

from email_checker import DELETE, HOLD, KEEP, report


def rows(*items):
    return [dict(id=i, predicted=p, subject=s, sender="A <a@x.com>", link="") for i, p, s in items]


def read(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_write_report_has_bom_and_columns():
    data = report.write_report(rows((1, DELETE, "광고"), (2, HOLD, "애매")))
    assert data.startswith(b"\xef\xbb\xbf")
    assert read(data) == [report.COLUMNS, ["1", DELETE, "광고", "A <a@x.com>", ""], ["2", HOLD, "애매", "A <a@x.com>", ""]]


def test_report_neutralises_formula():
    data = report.write_report(rows((1, DELETE, "=HYPERLINK(\"http://evil\")"), (2, KEEP, "-50% 세일")))
    assert read(data)[1][2] == "'=HYPERLINK(\"http://evil\")"
    assert read(data)[2][2] == "'-50% 세일"


def test_review_roundtrip():
    data = report.write_report(rows((1, DELETE, "a"), (2, HOLD, "b"), (3, KEEP, "c")))
    assert report.is_review(data)
    assert report.parse_review(data) == {1: DELETE, 2: HOLD, 3: KEEP}


def test_parse_review_cp949():
    text = "id,분류,제목,보낸 사람,링크\r\n1,확인,제목,A,\r\n2, 삭제 ,제목,A,\r\n"
    assert report.parse_review(text.encode("cp949")) == {1: KEEP, 2: DELETE}


def test_parse_review_rejects_bad_rows_without_partial_result():
    text = "id,분류\n1,삭제\n2,지워\nx,확인\n"
    with pytest.raises(report.CsvError, match="3, 4"):
        report.parse_review(text.encode())


def test_parse_review_requires_columns():
    with pytest.raises(report.CsvError):
        report.parse_review("제목,본문\na,b\n".encode())


def test_parse_examples():
    text = "제목,본문,정답,보낸 사람\n(광고) 할인,지금 구매,삭제,\n회의,내일 10시,확인,팀장 <b@x.com>\n"
    assert not report.is_review(text.encode())
    assert report.parse_examples(text.encode("utf-8-sig")) == [
        {"sender": "", "subject": "(광고) 할인", "body": "지금 구매", "label": DELETE},
        {"sender": "팀장 <b@x.com>", "subject": "회의", "body": "내일 10시", "label": KEEP},
    ]


def test_parse_examples_rejects_hold_and_empty():
    with pytest.raises(report.CsvError, match="2"):
        report.parse_examples("제목,본문,정답\na,b,보류\n".encode())
    with pytest.raises(report.CsvError):
        report.parse_examples("제목,본문,정답\n".encode())
    with pytest.raises(report.CsvError):
        report.parse_examples(b"")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL (`ImportError: cannot import name 'report'`)

- [ ] **Step 3: 구현** — `email_checker/report.py`

```python
import csv
import io

from . import DELETE, HOLD, KEEP

COLUMNS = ["id", "분류", "제목", "보낸 사람", "링크"]
EXAMPLE_COLUMNS = ("제목", "본문", "정답")


class CsvError(ValueError):
    pass


def _cell(value: str) -> str:
    # 메일 제목은 외부 입력이라, 엑셀이 수식으로 실행하지 못하게 막는다.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def write_report(rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r["id"], r["predicted"], _cell(r["subject"]), _cell(r["sender"]), r["link"]])
    return buf.getvalue().encode("utf-8-sig")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp949")


def is_review(data: bytes) -> bool:
    header = next(csv.reader(io.StringIO(_decode(data))), [])
    return "id" in [h.strip() for h in header]


def parse_review(data: bytes) -> dict[int, str]:
    reader = csv.DictReader(io.StringIO(_decode(data)))
    if not reader.fieldnames or not {"id", "분류"} <= set(reader.fieldnames):
        raise CsvError("보고서 CSV에는 'id'와 '분류' 열이 있어야 해요.")
    labels, bad = {}, []
    for line, row in enumerate(reader, start=2):
        mail_id, label = (row["id"] or "").strip(), (row["분류"] or "").strip()
        if not mail_id.isdigit() or label not in (DELETE, KEEP, HOLD):
            bad.append(line)
            continue
        labels[int(mail_id)] = label
    if bad:
        raise CsvError(f"{', '.join(map(str, bad))}번째 줄의 id나 분류가 잘못됐어요. "
                       "분류는 삭제/확인/보류 중 하나여야 해요. 아무것도 반영하지 않았어요.")
    return labels


def parse_examples(data: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(_decode(data)))
    if not reader.fieldnames or not set(EXAMPLE_COLUMNS) <= set(reader.fieldnames):
        raise CsvError("예시 데이터 CSV에는 '제목', '본문', '정답' 열이 있어야 해요.")
    examples, bad = [], []
    for line, row in enumerate(reader, start=2):
        label = (row["정답"] or "").strip()
        if label not in (DELETE, KEEP):
            bad.append(line)
            continue
        examples.append({"sender": (row.get("보낸 사람") or "").strip(), "subject": row["제목"] or "",
                         "body": row["본문"] or "", "label": label})
    if bad:
        raise CsvError(f"{', '.join(map(str, bad))}번째 줄의 정답이 잘못됐어요. "
                       "정답은 삭제나 확인이어야 해요. 아무것도 반영하지 않았어요.")
    if not examples:
        raise CsvError("예시 데이터가 비어 있어요.")
    return examples
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_report.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add email_checker/report.py tests/test_report.py
git commit -m "feat: write report CSV and parse review and example CSVs"
```

---

### Task 4: IMAP 메일함

**Files:**
- Create: `email_checker/mailbox.py`
- Test: `tests/test_mailbox.py`

**Interfaces:**
- Produces:
  - `mailbox.Mail` dataclass: `message_id, sender, subject, body, link` (모두 str)
  - `mailbox.parse_message(raw: bytes) -> tuple[str, str, str, str]` (message_id, sender, subject, body)
  - `mailbox.html_to_text(s) -> str`, `mailbox.gmail_link(msgid: int) -> str`
  - `mailbox.Mailbox(account: "naver"|"gmail", user, password)` with
    `.account`, `.uidvalidity() -> int`, `.uids_after(last_uid) -> list[int]`,
    `.fetch(uid, uidvalidity) -> Mail`, `.move_to_trash(message_id) -> bool`, `.close()`
  - Message-ID가 없으면 `message_id = f"uid:{uidvalidity}:{uid}"`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_mailbox.py`

```python
from email_checker import mailbox
from email_checker.mailbox import Mailbox

PLAIN = (
    "Message-ID: <m1@x.com>\r\nFrom: =?UTF-8?B?7ZmN6ri464+Z?= <hong@x.com>\r\n"
    "Subject: =?UTF-8?B?7ZqM7J2Y?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n안녕하세요\r\n"
).encode()


def test_parse_plain_utf8():
    message_id, sender, subject, body = mailbox.parse_message(PLAIN)
    assert (message_id, sender, subject, body.strip()) == ("<m1@x.com>", "홍길동 <hong@x.com>", "회의", "안녕하세요")


def test_parse_html_only():
    raw = (b"Subject: s\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<style>p{}</style><p>Hi &amp; bye</p>")
    assert mailbox.parse_message(raw)[3].split() == ["Hi", "&", "bye"]


def test_parse_ks_c_5601_with_cp949_only_char():
    body = "똠방각하".encode("cp949")  # '똠'은 euc-kr에 없고 cp949에만 있다
    raw = b"Subject: s\r\nContent-Type: text/plain; charset=ks_c_5601-1987\r\n\r\n" + body
    assert mailbox.parse_message(raw)[3] == "똠방각하"


def test_gmail_link():
    assert mailbox.gmail_link(255) == "https://mail.google.com/mail/u/0/#all/ff"


class FakeIMAP:
    def __init__(self, capabilities=("IMAP4REV1", "MOVE"), folders=None, search=b"5"):
        self.capabilities = capabilities
        self.folders = folders or [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"', b'(\\HasNoChildren) "/" "INBOX"']
        self.search = search
        self.calls = []

    def status(self, mailbox, items):
        return "OK", [b'"INBOX" (UIDVALIDITY 9)']

    def list(self):
        return "OK", self.folders

    def expunge(self):
        self.calls.append(("EXPUNGE",))
        return "OK", []

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [self.search]
        if command == "FETCH":
            return "OK", [(b"1 (X-GM-MSGID 255 UID 5 BODY[] {10}", self.raw), b")"]
        return "OK", [b""]


def box(account, imap):
    b = Mailbox.__new__(Mailbox)
    b.account, b.imap = account, imap
    return b


def test_uids_after_filters_star_returning_last_message():
    b = box("naver", FakeIMAP(search=b"3"))
    assert b.uids_after(3) == []
    b = box("naver", FakeIMAP(search=b"4 7 5"))
    assert b.uids_after(3) == [4, 5, 7]


def test_fetch_gmail_builds_link_and_peeks():
    imap = FakeIMAP()
    imap.raw = PLAIN
    m = box("gmail", imap).fetch(5, 9)
    assert m.link == "https://mail.google.com/mail/u/0/#all/ff"
    assert imap.calls[0] == ("FETCH", "5", "(X-GM-MSGID BODY.PEEK[])")


def test_fetch_without_message_id():
    imap = FakeIMAP()
    imap.raw = b"Subject: s\r\n\r\nbody"
    m = box("naver", imap).fetch(5, 9)
    assert m.message_id == "uid:9:5" and m.link == ""
    assert imap.calls[0] == ("FETCH", "5", "(BODY.PEEK[])")


def test_move_uses_special_use_trash():
    imap = FakeIMAP()
    assert box("gmail", imap).move_to_trash("<m1@x.com>") is True
    assert ("SEARCH", None, "HEADER", "Message-ID", '"<m1@x.com>"') in imap.calls
    assert ("MOVE", "5", '"[Gmail]/Trash"') in imap.calls


def test_move_falls_back_to_trash_name_and_copy():
    imap = FakeIMAP(capabilities=("IMAP4REV1",), folders=[b'(\\HasNoChildren) "/" "Deleted Messages"'])
    assert box("naver", imap).move_to_trash("<m1@x.com>") is True
    assert ("COPY", "5", '"Deleted Messages"') in imap.calls
    assert ("STORE", "5", "+FLAGS", "(\\Deleted)") in imap.calls
    assert ("EXPUNGE",) in imap.calls


def test_move_returns_false_when_mail_gone():
    imap = FakeIMAP(search=b"")
    assert box("naver", imap).move_to_trash("<m1@x.com>") is False


def test_move_by_synthetic_uid():
    imap = FakeIMAP()
    assert box("naver", imap).move_to_trash("uid:9:5") is True
    assert ("MOVE", "5", '"[Gmail]/Trash"') in imap.calls
    assert box("naver", FakeIMAP()).move_to_trash("uid:8:5") is False  # UIDVALIDITY가 바뀌면 그 UID는 다른 메일이다
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_mailbox.py -v`
Expected: FAIL (`ImportError: cannot import name 'mailbox'`)

- [ ] **Step 3: 구현** — `email_checker/mailbox.py`

```python
import email
import html
import imaplib
import re
from dataclasses import dataclass
from email import policy

HOSTS = {"naver": "imap.naver.com", "gmail": "imap.gmail.com"}
# SPECIAL-USE \Trash 표시가 없는 서버용. "&1zTJwNG1-"은 "휴지통"의 IMAP UTF-7 표기다.
TRASH_NAMES = ("Deleted Messages", "Trash", "&1zTJwNG1-")
LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


@dataclass
class Mail:
    message_id: str
    sender: str
    subject: str
    body: str
    link: str


def html_to_text(s: str) -> str:
    return html.unescape(re.sub(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>", " ", s))


KOREAN_CHARSETS = {"ks_c_5601-1987", "ks_c_5601", "euc-kr", "euc_kr"}


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
        self.imap.select("INBOX")

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
        # ponytail: 네이버 메일별 링크 형식을 모른다. 알게 되면 여기서 만든다.
        link = gmail_link(int(re.search(rb"X-GM-MSGID (\d+)", meta).group(1))) if gmail else ""
        return Mail(message_id or f"uid:{uidvalidity}:{uid}", sender, subject, body, link)

    def _trash(self) -> str:
        _, lines = self.imap.list()
        names = {}
        for line in lines:
            m = LIST_LINE.match(line)
            if not m:
                continue
            name = m.group("name").decode()
            if b"\\Trash" in m.group("flags"):
                return name
            names[name.strip('"')] = name
        for candidate in TRASH_NAMES:
            if candidate in names:
                return names[candidate]
        raise RuntimeError(f"{self.account} 휴지통 폴더를 찾지 못했어요: {sorted(names)}")

    def move_to_trash(self, message_id) -> bool:
        if message_id.startswith("uid:"):
            _, validity, uid = message_id.split(":")
            uids = [uid] if int(validity) == self.uidvalidity() else []
        else:
            quoted = '"' + message_id.replace('"', "") + '"'
            _, data = self.imap.uid("SEARCH", None, "HEADER", "Message-ID", quoted)
            uids = [u.decode() for u in data[0].split()]
        if not uids:
            return False
        trash = self._trash()
        for uid in uids:
            if "MOVE" in self.imap.capabilities:
                self.imap.uid("MOVE", uid, trash)
            else:
                self.imap.uid("COPY", uid, trash)
                self.imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if "MOVE" not in self.imap.capabilities:
            self.imap.expunge()
        return True

    def close(self):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # 이미 끊긴 연결은 닫을 게 없다
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_mailbox.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add email_checker/mailbox.py tests/test_mailbox.py
git commit -m "feat: read and trash mail over IMAP for naver and gmail"
```

---

### Task 5: 작업 조합 (daily / review / examples)

**Files:**
- Create: `email_checker/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `db.*`, `report.*`, `Mailbox` 메서드(`account, uidvalidity, uids_after, fetch, move_to_trash, close`), `Classifier.classify`
- Produces:
  - `jobs.collect(conn, box) -> int` (새로 넣은 메일 수)
  - `jobs.trash_pending(conn, boxes) -> tuple[int, int]` (옮긴 수, 메일함에 없던 수)
  - `jobs.daily(conn, open_boxes: Callable[[], list], classifier) -> tuple[bytes, dict[str, int]]` (보고서 CSV, 분류별 통 수)
  - `jobs.review(conn, data: bytes, open_boxes) -> tuple[str, int]` (답장 문구, 새로 생긴 정답 수)
  - `jobs.examples(conn, data: bytes) -> tuple[str, int]`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_jobs.py`

```python
from email_checker import DELETE, HOLD, KEEP, db, jobs, report
from email_checker.mailbox import Mail


class FakeBox:
    def __init__(self, account, mails, validity=1, present=None):
        self.account, self.mails, self.validity = account, mails, validity
        self.present = set(present if present is not None else (m.message_id for m in mails.values()))
        self.trashed, self.closed = [], False

    def uidvalidity(self):
        return self.validity

    def uids_after(self, last):
        return sorted(u for u in self.mails if u > last)

    def fetch(self, uid, validity):
        return self.mails[uid]

    def move_to_trash(self, message_id):
        if message_id not in self.present:
            return False
        self.trashed.append(message_id)
        return True

    def close(self):
        self.closed = True


class FakeClassifier:
    def classify(self, sender, subject, body):
        return {"광고": DELETE, "회의": KEEP}.get(subject, HOLD)


def mail(n, subject):
    return Mail(f"<{n}>", "A <a@x.com>", subject, "본문", "")


def test_collect_resumes_from_cursor_and_survives_uidvalidity_reset(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의")})
    assert jobs.collect(conn, box) == 2
    box.mails[3] = mail(3, "광고")
    assert jobs.collect(conn, box) == 1
    assert db.get_cursor(conn, "gmail") == (1, 3)
    reset = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(4, "새 메일")}, validity=2)
    assert jobs.collect(conn, reset) == 1  # 처음부터 다시 읽되 이미 있는 메일은 건너뛴다


def test_daily_classifies_and_reports_all_unreviewed(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의"), 3: mail(3, "애매")})
    data, counts = jobs.daily(conn, lambda: [box], FakeClassifier())
    assert counts == {DELETE: 1, KEEP: 1, HOLD: 1}
    assert report.parse_review(data) == {1: DELETE, 2: KEEP, 3: HOLD}
    assert box.closed and box.trashed == []  # 검토 전에는 아무것도 지우지 않는다
    db.set_label(conn, 1, DELETE)
    data, counts = jobs.daily(conn, lambda: [box], FakeClassifier())
    assert report.parse_review(data) == {2: KEEP, 3: HOLD}


def test_review_labels_trashes_and_reports_accuracy(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고"), 2: mail(2, "회의"), 3: mail(3, "애매"), 4: mail(4, "광고")})
    jobs.daily(conn, lambda: [box], FakeClassifier())
    csv_bytes = "id,분류\n1,삭제\n2,확인\n3,보류\n4,확인\n99,삭제\n".encode()
    text, learned = jobs.review(conn, csv_bytes, lambda: [box])
    assert learned == 3
    assert box.trashed == ["<1>"]
    assert [r["id"] for r in db.unreviewed(conn)] == [3]
    assert "삭제 1/2" in text and "확인 1/1" in text
    assert "99" in text


def test_review_counts_missing_mail(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고")}, present=[])
    jobs.daily(conn, lambda: [box], FakeClassifier())
    text, _ = jobs.review(conn, "id,분류\n1,삭제\n".encode(), lambda: [box])
    assert db.pending_trash(conn) == []
    assert "이미 없던 메일 1통" in text


def test_review_warns_when_trashed_mail_becomes_keep(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "광고")})
    jobs.daily(conn, lambda: [box], FakeClassifier())
    jobs.review(conn, "id,분류\n1,삭제\n".encode(), lambda: [box])
    text, _ = jobs.review(conn, "id,분류\n1,확인\n".encode(), lambda: [box])
    assert "휴지통에서 직접 꺼내" in text


def test_review_does_not_open_mailboxes_when_nothing_to_trash(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    box = FakeBox("gmail", {1: mail(1, "회의")})
    jobs.daily(conn, lambda: [box], FakeClassifier())

    def fail():
        raise AssertionError("메일함을 열면 안 된다")

    text, learned = jobs.review(conn, "id,분류\n1,확인\n".encode(), fail)
    assert learned == 1


def test_examples_are_stored_as_labelled(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    text, learned = jobs.examples(conn, "제목,본문,정답\n(광고) 할인,구매,삭제\n".encode())
    assert learned == 1 and "1통" in text
    assert [(r["source"], r["label"]) for r in db.labelled(conn)] == [("example", DELETE)]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: FAIL (`ImportError: cannot import name 'jobs'`)

- [ ] **Step 3: 구현** — `email_checker/jobs.py`

```python
from collections import Counter

from . import DELETE, HOLD, KEEP, db, report


def collect(conn, box) -> int:
    validity = box.uidvalidity()
    cursor = db.get_cursor(conn, box.account)
    last = cursor[1] if cursor and cursor[0] == validity else 0
    added = 0
    for uid in box.uids_after(last):
        m = box.fetch(uid, validity)
        if db.add_mail(conn, box.account, m.message_id, m.sender, m.subject, m.body, m.link) is not None:
            added += 1
        db.set_cursor(conn, box.account, validity, uid)
    return added


def trash_pending(conn, boxes):
    by_account = {b.account: b for b in boxes}
    moved = missing = 0
    for row in db.pending_trash(conn):
        if by_account[row["account"]].move_to_trash(row["message_id"]):
            moved += 1
        else:
            missing += 1
        db.mark_trashed(conn, row["id"])
    return moved, missing


def _with_boxes(open_boxes, work):
    boxes = open_boxes()
    try:
        return work(boxes)
    finally:
        for b in boxes:
            b.close()


def daily(conn, open_boxes, classifier):
    def work(boxes):
        trash_pending(conn, boxes)  # 지난 검토 때 실패한 이동을 다시 시도한다
        for b in boxes:
            collect(conn, b)

    _with_boxes(open_boxes, work)
    for row in db.unpredicted(conn):
        db.set_predicted(conn, row["id"], classifier.classify(row["sender"], row["subject"], row["body"]))
    rows = db.unreviewed(conn)
    counts = {label: sum(r["predicted"] == label for r in rows) for label in (DELETE, KEEP, HOLD)}
    return report.write_report(rows), counts


def review(conn, data, open_boxes):
    labels = report.parse_review(data)
    tally, unknown, late_keep, held = Counter(), [], [], 0
    for mail_id, label in labels.items():
        row = db.get(conn, mail_id)
        if row is None or row["source"] != "mail":
            unknown.append(mail_id)
            continue
        if label == HOLD:
            held += 1
            continue
        if row["trashed"] and label == KEEP:
            late_keep.append(mail_id)
        tally[row["predicted"], label] += 1
        db.set_label(conn, mail_id, label)

    moved = missing = 0
    if db.pending_trash(conn):
        moved, missing = _with_boxes(open_boxes, lambda boxes: trash_pending(conn, boxes))

    learned = sum(tally.values())
    lines = [f"검토 반영: {learned}통 (보류로 남김 {held}통)"]
    accuracy = [f"{p} {tally[p, p]}/{tally[p, DELETE] + tally[p, KEEP]}"
                for p in (DELETE, KEEP) if tally[p, DELETE] + tally[p, KEEP]]
    if accuracy:
        lines.append("분류 정확도: " + ", ".join(accuracy))
    if tally[HOLD, DELETE] + tally[HOLD, KEEP]:
        lines.append(f"보류였던 메일 → 삭제 {tally[HOLD, DELETE]}, 확인 {tally[HOLD, KEEP]}")
    if moved or missing:
        lines.append(f"휴지통으로 옮김: {moved}통" + (f" (메일함에 이미 없던 메일 {missing}통)" if missing else ""))
    if late_keep:
        lines.append("⚠️ 이미 휴지통으로 옮긴 메일을 확인으로 바꿨어요. 메일함 휴지통에서 직접 꺼내 주세요: id "
                     + ", ".join(map(str, late_keep)))
    if unknown:
        lines.append("⚠️ 보고서에 없는 id라서 건너뛰었어요: " + ", ".join(map(str, unknown)))
    return "\n".join(lines), learned


def examples(conn, data):
    rows = report.parse_examples(data)
    for r in rows:
        db.add_example(conn, r["sender"], r["subject"], r["body"], r["label"])
    return f"예시 데이터 {len(rows)}통을 학습자료에 넣었어요.", len(rows)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add email_checker/jobs.py tests/test_jobs.py
git commit -m "feat: daily report, review and example jobs"
```

---

### Task 6: 재학습

**Files:**
- Create: `email_checker/train.py`
- Test: `tests/test_train.py`

**Interfaces:**
- Consumes: `db.labelled`, `classify.QUESTIONS`, `classify.FROM_LABEL`, `classify.state`
- Produces:
  - `train.MIN_TRAIN = 20`, `train.MIN_EVAL = 10`
  - `train.export(conn, work: Path) -> tuple[int, int]` (`work/train.jsonl`, `work/eval.jsonl`를 새로 만든다)
  - `train.run_laya(work: Path) -> Path` (후보 모델 디렉터리, 실패 시 `subprocess.CalledProcessError`)
  - `train.new_accuracy(candidate: Path) -> float`
  - `train.promote(models_dir: Path, candidate: Path) -> None` (`current` → `previous`, 후보 → `current`)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_train.py`

```python
import json

from email_checker import DELETE, KEEP, db, train


def lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_export_splits_real_mail_and_keeps_examples_in_train(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for n in range(1, 11):
        mail_id = db.add_mail(conn, "gmail", f"<{n}>", "A <a@x.com>", f"제목{n}", "본문", "")
        db.set_label(conn, mail_id, DELETE if n % 2 else KEEP)
    db.add_mail(conn, "gmail", "<x>", "A", "검토 안 됨", "본문", "")
    for _ in range(5):
        db.add_example(conn, "", "예시", "본문", KEEP)  # id 12..16, 15도 학습에만 간다
    n_train, n_eval = train.export(conn, tmp_path / "work")
    assert (n_train, n_eval) == (13, 2)
    ev = lines(tmp_path / "work" / "eval.jsonl")
    assert [r["state"]["email"].split("\n")[1] for r in ev] == ["Subject: 제목5", "Subject: 제목10"]
    assert ev[0]["expected"] == {"label": "delete"} and ev[1]["expected"] == {"label": "read"}
    assert ev[0]["questions"]["label"]["type"] == "choice"


def test_export_replaces_previous_work(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    work = tmp_path / "work"
    (work / "candidate").mkdir(parents=True)
    train.export(conn, work)
    assert not (work / "candidate").exists()


def test_new_accuracy_and_promote(tmp_path):
    models = tmp_path / "models"
    cand = models / "work" / "candidate"
    cand.mkdir(parents=True)
    (cand / "train_report.json").write_text(json.dumps({"after": {"accuracy": 0.8}}))
    assert train.new_accuracy(cand) == 0.8
    train.promote(models, cand)
    assert (models / "current" / "train_report.json").exists() and not cand.exists()
    cand2 = models / "work" / "candidate"
    cand2.mkdir(parents=True)
    train.promote(models, cand2)
    assert (models / "previous" / "train_report.json").exists()
    assert not (models / "current" / "train_report.json").exists()
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_train.py -v`
Expected: FAIL (`ImportError: cannot import name 'train'`)

- [ ] **Step 3: 구현** — `email_checker/train.py`

```python
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
    with open(work / "train.jsonl", "w", encoding="utf-8") as tr, open(work / "eval.jsonl", "w", encoding="utf-8") as ev:
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
        ["nice", "-n", "10", str(Path(sys.executable).with_name("laya-train")), "--data", str(work / "train.jsonl"),
         "--eval", str(work / "eval.jsonl"), "--base", "multilingual", "--out", str(candidate),
         "--device", "cpu"],
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_train.py -v`
Expected: 3 passed

- [ ] **Step 5: 내보낸 JSONL을 laya-train이 읽는지 확인**

```bash
uv run python -c "
from pathlib import Path
from email_checker import db, train, DELETE, KEEP
conn = db.connect(':memory:')
for n in range(30):
    i = db.add_mail(conn, 'gmail', f'<{n}>', 'A <a@x.com>', ('(광고) 할인 ' if n % 2 else '회의 일정 ') + str(n), '본문', '')
    db.set_label(conn, i, DELETE if n % 2 else KEEP)
print(train.export(conn, Path('/tmp/ec-work')))
"
USE_TF=0 uv run laya-train --data /tmp/ec-work/train.jsonl --eval /tmp/ec-work/eval.jsonl --base multilingual --dry-run
```

Expected: `(24, 6)` 출력, 그리고 dry-run이 오류 없이 학습 항목 수(24)를 보고하고 `skipped {}`

- [ ] **Step 6: Commit**

```bash
git add email_checker/train.py tests/test_train.py
git commit -m "feat: export labelled mail and retrain Laya on CPU"
```

---

### Task 7: 디스코드 bot과 배포 파일

**Files:**
- Create: `email_checker/bot.py`, `deploy/email-checker.service`
- Modify: `main.py`, `.env.example`, `.gitignore`

**Interfaces:**
- Consumes: 앞 Task의 모든 공개 함수
- Produces: `bot.run() -> None` (환경변수: `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `NAVER_USER`, `NAVER_APP_PASSWORD`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, 선택 `DATA_DIR`(기본 `data`), `RULES_PATH`(기본 `rules.toml`))

- [ ] **Step 1: 구현** — `email_checker/bot.py`

```python
import asyncio
import datetime
import io
import logging
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks

from . import DELETE, HOLD, KEEP, classify, db, jobs, report, train
from .mailbox import Mailbox

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(exist_ok=True)
    models_dir = data_dir / "models"
    channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
    conn = db.connect(data_dir / "email-checker.db")
    rules = classify.load_rules(os.environ.get("RULES_PATH", "rules.toml"))
    model = {"classifier": None}
    lock = asyncio.Lock()          # DB와 모델을 쓰는 작업을 한 번에 하나만
    retrain_lock = asyncio.Lock()  # 재학습은 한 번에 하나만, 밀린 요청은 차례로
    background = set()

    def load_classifier():
        current = models_dir / "current"
        return classify.Classifier(current if current.exists() else None, rules)

    def open_boxes():
        return [Mailbox(a, os.environ[f"{a.upper()}_USER"], os.environ[f"{a.upper()}_APP_PASSWORD"])
                for a in ("naver", "gmail")]

    intents = discord.Intents.default()
    intents.message_content = True  # 첨부 파일을 읽으려면 필요하다
    client = discord.Client(intents=intents)

    async def channel():
        return client.get_channel(channel_id) or await client.fetch_channel(channel_id)

    @tasks.loop(time=datetime.time(7, 0, tzinfo=KST))
    async def daily_report():
        ch = await channel()
        try:
            async with lock:
                data, counts = await asyncio.to_thread(jobs.daily, conn, open_boxes, model["classifier"])
        except Exception as e:
            log.exception("daily report failed")
            await ch.send(f"⚠️ 보고서를 만들지 못했어요: {e}")
            return
        if not sum(counts.values()):
            await ch.send("검토할 메일이 없어요.")
            return
        name = f"보고서-{datetime.datetime.now(KST):%Y-%m-%d}.csv"
        await ch.send(f"삭제 {counts[DELETE]} · 확인 {counts[KEEP]} · 보류 {counts[HOLD]}",
                      file=discord.File(io.BytesIO(data), filename=name))

    async def retrain():
        ch = await channel()
        async with retrain_lock:
            work = models_dir / "work"
            try:
                async with lock:
                    n_train, n_eval = await asyncio.to_thread(train.export, conn, work)
                if n_train < train.MIN_TRAIN or n_eval < train.MIN_EVAL:
                    await ch.send(f"학습자료가 아직 부족해서 재학습을 건너뛰었어요 "
                                  f"(학습 {n_train}/{train.MIN_TRAIN}통, 시험 {n_eval}/{train.MIN_EVAL}통).")
                    return
                await ch.send(f"재학습을 시작했어요 (학습 {n_train}통, 시험 {n_eval}통). 끝나면 알려 드릴게요.")
                candidate = await asyncio.to_thread(train.run_laya, work)
                async with lock:
                    old = await asyncio.to_thread(model["classifier"].accuracy, work / "eval.jsonl")
                    new = train.new_accuracy(candidate)
                    if new >= old:
                        train.promote(models_dir, candidate)
                        model["classifier"] = await asyncio.to_thread(load_classifier)
            except subprocess.CalledProcessError as e:
                log.error("laya-train failed: %s", e.stderr)
                await ch.send(f"⚠️ 재학습이 실패했어요:\n```{(e.stderr or '')[-1500:]}```")
                return
            except Exception as e:
                log.exception("retrain failed")
                await ch.send(f"⚠️ 재학습이 실패했어요: {e}")
                return
        verdict = "새 모델로 교체했어요" if new >= old else "기존 모델이 더 나아서 교체하지 않았어요"
        await ch.send(f"재학습 끝: 시험 정확도 기존 {old:.0%} → 새 모델 {new:.0%}. {verdict}.")

    @client.event
    async def on_ready():
        if model["classifier"] is None:
            model["classifier"] = await asyncio.to_thread(load_classifier)
        if not daily_report.is_running():
            daily_report.start()
        log.info("ready as %s", client.user)

    @client.event
    async def on_message(message):
        if message.author.bot or message.channel.id != channel_id:
            return
        if message.content.strip() == "!보고서":
            await daily_report()
            return
        for att in message.attachments:
            if not att.filename.lower().endswith(".csv"):
                continue
            data = await att.read()
            try:
                async with lock:
                    job = jobs.review if report.is_review(data) else jobs.examples
                    args = (conn, data, open_boxes) if job is jobs.review else (conn, data)
                    text, learned = await asyncio.to_thread(job, *args)
            except report.CsvError as e:
                await message.reply(f"⚠️ {e}")
                continue
            except Exception as e:
                log.exception("csv upload failed")
                await message.reply(f"⚠️ 처리하지 못했어요: {e}")
                continue
            await message.reply(text)
            if learned:
                task = asyncio.create_task(retrain())
                background.add(task)
                task.add_done_callback(background.discard)

    client.run(os.environ["DISCORD_TOKEN"])
```

`main.py` 전체를 바꾼다:

```python
from email_checker.bot import run

if __name__ == "__main__":
    run()
```

`.env.example` 전체를 바꾼다:

```bash
# Copy to .env and fill in values. Never commit .env.
USE_TF=0
NAVER_USER=
NAVER_APP_PASSWORD=
GMAIL_USER=
GMAIL_APP_PASSWORD=
DISCORD_TOKEN=
DISCORD_CHANNEL_ID=
```

`.gitignore` 끝에 추가:

```
# Runtime data (mail DB, models)
data/
```

`deploy/email-checker.service`

```ini
[Unit]
Description=email-checker Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=ogonge
WorkingDirectory=/home/ogonge/email-checker
EnvironmentFile=/home/ogonge/email-checker/.env
ExecStart=/home/ogonge/.local/bin/uv run --frozen python main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: import와 전체 테스트 확인**

Run: `uv run python -c "import email_checker.bot" && uv run pytest -q`
Expected: import 오류 없음, 전체 테스트 통과(slow 1개 skip)

- [ ] **Step 3: Commit**

```bash
git add email_checker/bot.py main.py .env.example .gitignore deploy/email-checker.service
git commit -m "feat: discord bot with 07:00 report, CSV review and retraining"
```

---

### Task 8: 서버 배포와 실제 계정 확인

사용자만 할 수 있는 단계가 있다. 단계마다 Mac(`%`)인지 서버(`ogonge@server1:~$`)인지 밝히고, 한 줄 명령을 주고, 끝나면 SSH로 확인한다. 비밀번호와 토큰은 대화에 받지 않는다.

- [ ] **Step 1: 사용자 준비 (wizard 스킬로 안내)**
  1. 네이버 메일 → 환경설정 → POP3/IMAP 설정에서 IMAP 사용, 네이버 앱 비밀번호 발급
  2. Google 계정 2단계 인증 확인 후 앱 비밀번호 발급(Gmail은 IMAP이 기본으로 켜져 있음)
  3. Discord Developer Portal에서 bot 생성 → **Message Content Intent** 켜기 → 토큰 복사 → 개인 서버에 초대(권한: 메시지 보기/보내기, 파일 첨부, 메시지 기록 보기) → 채널 ID 복사(개발자 모드)
- [ ] **Step 2: 코드 보내기 (합의: Mac → GitHub push → 서버 pull)**

Mac `%` — 사용자 확인을 받은 뒤 push한다:

```bash
git push origin main
```

서버(Mac에서 SSH) — 기존 `~/email-checker`는 실험용 rsync 복사본이라 지우고 clone한다. 레포가 PUBLIC이라 인증이 필요 없다:

```bash
ssh ogonge@192.168.0.26 'rm -rf ~/email-checker && git clone https://github.com/kangsinbeom/email-checker.git ~/email-checker && cd ~/email-checker && ~/.local/bin/uv sync --frozen && git log --oneline -1'
```

이후 업데이트는 `ssh ogonge@192.168.0.26 'cd ~/email-checker && git pull --ff-only && ~/.local/bin/uv sync --frozen'` 뒤 서비스 재시작(Step 6 이후 `sudo systemctl restart email-checker`, 사용자가 실행). 자동 배포(systemd 타이머로 `git fetch`)는 이 계획 범위 밖이다.

- [ ] **Step 3: `.env` 작성** — 서버 `ogonge@server1:~$`

```bash
cp ~/email-checker/.env.example ~/email-checker/.env && chmod 600 ~/email-checker/.env && nano ~/email-checker/.env
```

확인(Mac에서 SSH, 값은 출력하지 않음): `ssh ogonge@192.168.0.26 'grep -c "=." ~/email-checker/.env'` → `7`

- [ ] **Step 4: 실제 메일함으로 읽기 전용 확인** — Mac에서 SSH

```bash
ssh ogonge@192.168.0.26 'cd ~/email-checker && ~/.local/bin/uv sync --frozen && ~/.local/bin/uv run --env-file .env python -c "
import os
from email_checker.mailbox import Mailbox
for a in (\"naver\", \"gmail\"):
    b = Mailbox(a, os.environ[a.upper()+\"_USER\"], os.environ[a.upper()+\"_APP_PASSWORD\"])
    uids = b.uids_after(0)
    m = b.fetch(uids[-1], b.uidvalidity())
    print(a, len(uids), \"통 | trash =\", b._trash(), \"| 최근 제목:\", m.subject[:30], \"| link:\", m.link)
    b.close()
"'
```

Expected: 두 계정 모두 메일 수, 휴지통 폴더 이름, 최근 메일 제목이 나온다. 휴지통을 못 찾으면 출력된 폴더 목록을 보고 `TRASH_NAMES`에 추가한다. Gmail 링크를 Mac 브라우저에서 열어 그 메일이 열리는지 사용자에게 확인받는다.

- [ ] **Step 5: 첫 실행과 첫 보고서** — Mac에서 SSH로 포그라운드 실행

```bash
ssh ogonge@192.168.0.26 'cd ~/email-checker && timeout 1800 ~/.local/bin/uv run --env-file .env python main.py'
```

로그에 `ready as ...`가 나오면 사용자가 디스코드 채널에 `!보고서`를 보낸다. Expected: 기존 메일 전부가 담긴 CSV가 올라오고, 메일함에서 아무것도 지워지지 않는다. 사용자가 CSV를 열어 한글이 깨지지 않는지 확인한다.

- [ ] **Step 6: 서비스 등록** — 서버 `ogonge@server1:~$` (sudo, 사용자가 실행)

```bash
sudo cp ~/email-checker/deploy/email-checker.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now email-checker
```

확인(Mac에서 SSH): `ssh ogonge@192.168.0.26 'systemctl is-active email-checker && journalctl -u email-checker -n 20 --no-pager'` → `active`, 로그에 `ready as`

- [ ] **Step 7: 첫 검토와 재학습 확인**

사용자가 첫 보고서를 검토해 올린다. Expected: 답장에 반영 수와 분류 정확도, 휴지통 이동 수가 나오고, 메일함 휴지통에 정답이 `삭제`인 메일만 들어 있다. 학습자료가 기준(학습 20, 시험 10) 이상이면 재학습 시작/끝 메시지가 오고, `ls ~/email-checker/data/models/current`에 모델이 생긴다(교체된 경우).
