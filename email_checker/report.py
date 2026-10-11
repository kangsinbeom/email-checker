import csv
import io

from . import DELETE, HOLD, KEEP

COLUMNS = ["분류", "날짜", "메일함", "보낸 사람", "제목", "링크", "id"]
ACCOUNTS = {"naver": "네이버", "gmail": "Gmail"}


def _cell(value: str) -> str:
    # 메일 제목은 외부 입력이라, 엑셀이 수식으로 실행하지 못하게 막는다.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def write_report(rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r["predicted"], r["date"], ACCOUNTS[r["account"]], _cell(r["sender"]),
                         _cell(r["subject"]), r["link"], r["id"]])
    return buf.getvalue().encode("utf-8-sig")


class CsvError(ValueError):
    pass


def _decode(data: bytes) -> str:
    # Numbers/Excel이 고쳐 저장하면 cp949가 되기도 한다.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp949")


def parse_review(data: bytes) -> dict[int, str]:
    reader = csv.DictReader(io.StringIO(_decode(data)))
    if not reader.fieldnames or not {"id", "분류"} <= {f.strip() for f in reader.fieldnames}:
        raise CsvError("보고서 CSV에는 'id'와 '분류' 열이 있어야 해요.")
    reader.fieldnames = [f.strip() for f in reader.fieldnames]
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
