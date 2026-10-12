from collections import Counter

from . import DELETE, HOLD, KEEP, db, report

REPORT_LIMIT = 100  # 한 번에 검토하기 버거우면 줄인다


def collect(conn, box) -> int:
    validity = box.uidvalidity()
    cursor = db.get_cursor(conn, box.account)
    last = cursor[1] if cursor and cursor[0] == validity else 0
    added = 0
    for uid in box.uids_after(last):
        m = box.fetch(uid, validity)
        if db.add_mail(conn, box.account, m.message_id, m.sender, m.subject, m.body, m.link, m.date) is not None:
            added += 1
        db.set_cursor(conn, box.account, validity, uid)
    return added


def daily(conn, open_boxes, classifier):
    boxes = open_boxes()
    try:
        for b in boxes:
            collect(conn, b)
    finally:
        for b in boxes:
            b.close()
    for row in db.unpredicted(conn):
        db.set_predicted(conn, row["id"], classifier.classify(row["sender"], row["subject"], row["body"]))
    unreviewed = db.unreviewed(conn)
    rows = unreviewed[:REPORT_LIMIT]
    counts = {label: sum(r["predicted"] == label for r in rows) for label in (DELETE, KEEP, HOLD)}
    return report.write_report(rows), counts, len(unreviewed) - len(rows)


def review(conn, data):
    labels = report.parse_review(data)
    tally, unknown = Counter(), []
    for mail_id, label in labels.items():
        row = db.get(conn, mail_id)
        if row is None or row["source"] != "mail":
            unknown.append(mail_id)
            continue
        tally[row["predicted"], label] += 1
        if label != HOLD:  # 보류로 남긴 줄은 검토되지 않은 것이다
            db.set_label(conn, mail_id, label)

    total = sum(tally.values())
    unchanged = sum(tally[p, p] for p in (DELETE, KEEP, HOLD))
    learned = total - sum(tally[p, HOLD] for p in (DELETE, KEEP, HOLD))
    lines = [f"검토 반영: {learned}통 (보류로 남김 {total - learned}통)"]
    if total:
        lines.append(f"고치지 않은 줄: {unchanged}/{total} ({unchanged / total:.0%})")
    accuracy = [f"{p} {tally[p, p]}/{tally[p, DELETE] + tally[p, KEEP]}"
                for p in (DELETE, KEEP) if tally[p, DELETE] + tally[p, KEEP]]
    if accuracy:
        lines.append("모델 분류별 정확도: " + ", ".join(accuracy))
    if sum(tally[HOLD, x] for x in (DELETE, KEEP, HOLD)):
        lines.append(f"보류였던 메일 → 삭제 {tally[HOLD, DELETE]}, 확인 {tally[HOLD, KEEP]}, "
                     f"그대로 보류 {tally[HOLD, HOLD]}")
    if unknown:
        lines.append("⚠️ 보고서에 없는 id라서 건너뛰었어요: " + ", ".join(map(str, unknown)))
    return "\n".join(lines), learned
