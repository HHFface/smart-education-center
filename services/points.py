"""Smart Points: XP va Coin.

Qoidalar:
 * Coin balansi hech qachon noldan pastga tushmaydi.
 * XP intizomiy jarimalar tufayli kamaymaydi (faqat 'admin_adjust' sababi bilan).
 * Har bir o'zgarish sana, sabab va kim tomonidan bajarilgani bilan points_log da saqlanadi.
"""
from .. import utils
from ..utils import AppError
from . import audit, notifications

_COLUMN = {"xp": "xp", "coin": "coins"}


def ensure_stats(db, student_id):
    db.execute("INSERT OR IGNORE INTO student_stats (student_id) VALUES (?)", (student_id,))


def stats(db, student_id):
    ensure_stats(db, student_id)
    return db.one("SELECT * FROM student_stats WHERE student_id=?", (student_id,))


def award(db, student_id, kind, amount, code, reason="", actor_id=None,
          ref_type=None, ref_id=None, notify=True):
    """Ball qo'shadi/ayiradi. Haqiqatda qo'llangan miqdor bilan log id qaytaradi (0 bo'lsa None)."""
    if kind not in _COLUMN:
        raise ValueError("kind")
    amount = int(amount)
    if amount == 0:
        return None
    if kind == "xp" and amount < 0 and code != "admin_adjust":
        raise ValueError("xp_not_reducible")
    column = _COLUMN[kind]
    with db.tx():
        ensure_stats(db, student_id)
        balance = db.scalar("SELECT %s FROM student_stats WHERE student_id=?" % column, (student_id,), 0)
        applied = amount if amount > 0 else -min(balance, -amount)
        if applied:
            db.execute("UPDATE student_stats SET %s = %s + ? WHERE student_id=?" % (column, column),
                       (applied, student_id))
        log_id = db.insert(
            "INSERT INTO points_log (student_id, kind, amount, requested_amount, reason_code, reason, "
            "ref_type, ref_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (student_id, kind, applied, amount, code, reason or "", ref_type, ref_id, actor_id,
             utils.fmt(utils.now())),
        )
    if notify and (applied or amount < 0):
        payload = {"amount": abs(applied if applied else amount), "code": code, "reason": reason or ""}
        if kind == "coin":
            note_kind = "coin_added" if amount > 0 else "penalty_applied"
        else:
            note_kind = "xp_added"
        notifications.notify(db, student_id, note_kind, payload, link="/student/points")
        if amount < 0 and kind == "coin":
            notifications.notify_parents(db, student_id, "penalty_applied", payload)
    return log_id


def revoke(db, log_id, actor_id, note=""):
    """Jarima yoki mukofotni bekor qiladi (teskari yozuv bilan). Faqat bir marta — parallel so'rovda ham."""
    with db.tx():                       # BEGIN IMMEDIATE: yozuv qulfi olingach holat qayta o'qiladi
        row = db.one("SELECT * FROM points_log WHERE id=?", (log_id,))
        if row is None:
            raise AppError("not_found")
        if row["revoked"] or row["reason_code"] == "revoked":
            raise AppError("already_revoked")
        column = _COLUMN[row["kind"]]
        ensure_stats(db, row["student_id"])
        # shartli UPDATE: boshqa jarayon allaqachon bekor qilgan bo'lsa 0 qator o'zgaradi
        if db.execute("UPDATE points_log SET revoked=1 WHERE id=? AND revoked=0", (log_id,)).rowcount != 1:
            raise AppError("already_revoked")
        balance = db.scalar("SELECT %s FROM student_stats WHERE student_id=?" % column,
                            (row["student_id"],), 0)
        reverse = max(-row["amount"], -balance)        # balans noldan pastga tushmasin
        if reverse:
            db.execute("UPDATE student_stats SET %s = %s + ? WHERE student_id=?" % (column, column),
                       (reverse, row["student_id"]))
        db.insert(
            "INSERT INTO points_log (student_id, kind, amount, requested_amount, reason_code, reason, "
            "ref_type, ref_id, created_by, created_at, revoked) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (row["student_id"], row["kind"], reverse, reverse, "revoked", note or "",
             "points_log", log_id, actor_id, utils.fmt(utils.now())),
        )
        audit.record(db, actor_id, "points.revoke", "points_log", log_id, {"note": note})
    return reverse


def revoke_by_ref(db, ref_type, ref_id, actor_id, note=""):
    """Berilgan manbaga (masalan davomat) bog'langan bekor qilinmagan yozuvlarni qaytaradi."""
    rows = db.all("SELECT id FROM points_log WHERE ref_type=? AND ref_id=? AND revoked=0 AND reason_code<>'revoked'",
                  (ref_type, ref_id))
    for row in rows:
        revoke(db, row["id"], actor_id, note)
    return len(rows)


def history(db, student_id, kind=None, limit=100):
    sql = "SELECT * FROM points_log WHERE student_id=?"
    args = [student_id]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return db.all(sql, args)


def sum_between(db, student_id, kind, start, end):
    """Berilgan oraliqdagi (UTC satr) ijobiy ballar yig'indisi: haftalik/oylik reyting uchun."""
    return db.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM points_log WHERE student_id=? AND kind=? "
        "AND amount>0 AND revoked=0 AND reason_code<>'revoked' AND created_at>=? AND created_at<?",
        (student_id, kind, start, end), 0)
