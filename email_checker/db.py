import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS mails (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    link TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
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


def add_mail(conn, account, message_id, sender, subject, body, link, date):
    cur = conn.execute(
        "INSERT OR IGNORE INTO mails (account, message_id, sender, subject, body, link, date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (account, message_id, sender, subject, body, link, date),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def unpredicted(conn):
    return conn.execute(
        "SELECT * FROM mails WHERE source = 'mail' AND predicted IS NULL ORDER BY id"
    ).fetchall()


def set_predicted(conn, mail_id, label):
    conn.execute("UPDATE mails SET predicted = ? WHERE id = ?", (label, mail_id))
    conn.commit()


def unreviewed(conn):
    return conn.execute(
        "SELECT * FROM mails WHERE source = 'mail' AND label IS NULL ORDER BY date DESC, id DESC"
    ).fetchall()


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


def get(conn, mail_id):
    return conn.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()


def set_label(conn, mail_id, label):
    conn.execute("UPDATE mails SET label = ? WHERE id = ?", (label, mail_id))
    conn.commit()


def labelled(conn):
    return conn.execute("SELECT * FROM mails WHERE label IS NOT NULL ORDER BY id").fetchall()


def reset_predictions(conn):
    # 모델이 바뀌면 아직 검토하지 않은 메일만 다시 분류한다. 검토한 메일의 예측은 정답률 기록으로 남긴다.
    conn.execute("UPDATE mails SET predicted = NULL WHERE source = 'mail' AND label IS NULL")
    conn.commit()
