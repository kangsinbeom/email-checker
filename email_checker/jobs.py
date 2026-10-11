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
    rows = db.unreviewed(conn)
    counts = {label: sum(r["predicted"] == label for r in rows) for label in (DELETE, KEEP, HOLD)}
    return report.write_report(rows), counts
