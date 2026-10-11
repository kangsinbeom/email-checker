import csv
import io

COLUMNS = ["id", "분류", "제목", "보낸 사람", "링크"]


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
