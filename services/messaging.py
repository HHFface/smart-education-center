"""Foydalanuvchilar o'rtasidagi xabarlar (o'quvchi - o'qituvchi - ota-ona - administrator)."""
from .. import utils
from ..utils import AppError
from . import access, notifications


def contacts(db, user):
    """Joriy foydalanuvchi xabar yoza oladigan odamlar ro'yxati."""
    role, uid = user["role"], user["id"]
    ids = set()
    if role == "admin":
        ids = {r["id"] for r in db.all("SELECT id FROM users WHERE is_active=1 AND id<>?", (uid,))}
    else:
        ids |= {r["id"] for r in db.all("SELECT id FROM users WHERE role='admin' AND is_active=1")}
        if role == "student":
            ids |= {r["teacher_id"] for r in db.all(
                "SELECT DISTINCT g.teacher_id FROM groups g JOIN group_students gs ON gs.group_id=g.id "
                "WHERE gs.student_id=? AND g.teacher_id IS NOT NULL", (uid,))}
        elif role == "teacher":
            kids = access.teacher_student_ids(db, uid)
            ids |= set(kids)
            for kid in kids:
                ids |= {r["parent_id"] for r in db.all("SELECT parent_id FROM parent_students WHERE student_id=?", (kid,))}
        elif role == "parent":
            for kid in access.parent_child_ids(db, uid):
                ids |= {r["teacher_id"] for r in db.all(
                    "SELECT DISTINCT g.teacher_id FROM groups g JOIN group_students gs ON gs.group_id=g.id "
                    "WHERE gs.student_id=? AND g.teacher_id IS NOT NULL", (kid,))}
    ids.discard(uid)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = db.all("SELECT id, first_name, last_name, username, role FROM users WHERE id IN (%s) AND is_active=1 "
                  "ORDER BY role, first_name, last_name" % marks, list(ids))
    return rows


def conversations(db, user_id):
    rows = db.all(
        "SELECT CASE WHEN from_id=? THEN to_id ELSE from_id END AS other, MAX(id) AS last_id, "
        "SUM(CASE WHEN to_id=? AND is_read=0 THEN 1 ELSE 0 END) AS unread FROM messages "
        "WHERE from_id=? OR to_id=? GROUP BY other ORDER BY last_id DESC", (user_id,) * 4)
    items = []
    for row in rows:
        other = db.one("SELECT id, first_name, last_name, username, role FROM users WHERE id=?", (row["other"],))
        last = db.one("SELECT body, created_at FROM messages WHERE id=?", (row["last_id"],))
        if other is not None:
            items.append({"user": other, "name": access.user_name(other), "last": last, "unread": row["unread"] or 0})
    return items


def thread(db, user_id, other_id, limit=200):
    db.execute("UPDATE messages SET is_read=1 WHERE to_id=? AND from_id=? AND is_read=0", (user_id, other_id))
    rows = db.all("SELECT * FROM messages WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?) "
                  "ORDER BY id DESC LIMIT ?", (user_id, other_id, other_id, user_id, limit))
    return list(reversed(rows))


def send(db, sender, to_id, body):
    body = utils.clean_text(body, 2000)
    if not body:
        raise AppError("message_required")
    if not access.can_message(db, sender, to_id):
        raise AppError("forbidden")
    mid = db.insert("INSERT INTO messages (from_id, to_id, body, created_at) VALUES (?,?,?,?)",
                    (sender["id"], to_id, body, utils.fmt(utils.now())))
    notifications.notify(db, to_id, "message_new", {"name": access.user_name(sender)},
                         link="/account/messages/%d" % sender["id"])
    return mid
