"""Davomat va «Darsga kela olmayman» arizalari."""
import datetime

from .. import utils
from ..utils import AppError
from . import access, audit, notifications, points, settings

STATUSES = ("present", "late", "excused", "absent")
ABSENCE_REASONS = ("health", "family", "ceremony", "personal", "other")
ABSENCE_REVIEW = ("approved", "rejected", "info_needed")


def coin_rule(db, status):
    return int(settings.get(db, "coin.attendance_%s" % status) or 0)


def has_approved_absence(db, student_id, date_str, subject_id):
    return db.one(
        "SELECT 1 FROM absence_requests WHERE student_id=? AND lesson_date=? AND subject_id=? "
        "AND status='approved'", (student_id, date_str, subject_id)) is not None


def _lesson(db, lesson_id):
    row = db.one(
        "SELECT l.*, g.teacher_id, g.name AS group_name, c.subject_id FROM lessons l "
        "JOIN groups g ON g.id = l.group_id JOIN courses c ON c.id = g.course_id WHERE l.id=?",
        (lesson_id,))
    if row is None:
        raise AppError("not_found")
    return row


def mark(db, lesson_id, student_id, status, marked_by):
    """Davomatni belgilaydi va Coin qoidasini qo'llaydi. Qayta belgilansa, oldingi Coin qaytariladi."""
    if status not in STATUSES:
        raise AppError("bad_status")
    lesson = _lesson(db, lesson_id)
    if db.one("SELECT 1 FROM group_students WHERE group_id=? AND student_id=?",
              (lesson["group_id"], student_id)) is None:
        raise AppError("not_in_group")
    date_str = utils.local_date_of(lesson["starts_at"]).isoformat()
    if status == "absent" and has_approved_absence(db, student_id, date_str, lesson["subject_id"]):
        status = "excused"
    with db.tx():
        existing = db.one("SELECT * FROM attendance WHERE lesson_id=? AND student_id=?", (lesson_id, student_id))
        if existing is not None and existing["status"] == status:
            return status
        if existing is not None:
            points.revoke_by_ref(db, "attendance", existing["id"], marked_by, "attendance_changed")
            db.execute("UPDATE attendance SET status=?, marked_by=?, created_at=? WHERE id=?",
                       (status, marked_by, utils.fmt(utils.now()), existing["id"]))
            att_id = existing["id"]
        else:
            att_id = db.insert(
                "INSERT INTO attendance (lesson_id, student_id, status, marked_by, created_at) VALUES (?,?,?,?,?)",
                (lesson_id, student_id, status, marked_by, utils.fmt(utils.now())))
        amount = coin_rule(db, status)
        if amount:
            points.award(db, student_id, "coin", amount, "attendance_%s" % status,
                         actor_id=marked_by, ref_type="attendance", ref_id=att_id)
    payload = {"status": status, "group": lesson["group_name"]}
    notifications.notify(db, student_id, "attendance_marked", payload, link="/student/attendance")
    notifications.notify_parents(db, student_id, "attendance_marked", payload)
    return status


def stats(db, student_id):
    counts = {s: 0 for s in STATUSES}
    for row in db.all("SELECT status, COUNT(*) AS n FROM attendance WHERE student_id=? GROUP BY status",
                      (student_id,)):
        counts[row["status"]] = row["n"]
    attended = counts["present"] + counts["late"]
    denominator = attended + counts["absent"]
    counts["total"] = sum(counts[s] for s in STATUSES)
    counts["pct"] = round(100.0 * attended / denominator) if denominator else None
    return counts


# ---------------------------------------------------------------- uzrli sabab arizalari
def submit_absence(db, student_id, subject_id, lesson_date, reason, comment="", return_date=None, file_name=None):
    if reason not in ABSENCE_REASONS:
        raise AppError("bad_reason")
    day = utils.parse_date(lesson_date)
    if day is None:
        raise AppError("bad_date")
    limit = int(settings.get(db, "absence.late_days") or 0)
    if day < utils.local_today() - datetime.timedelta(days=limit):
        raise AppError("absence_too_old")
    if db.one("SELECT 1 FROM subjects s JOIN courses c ON c.subject_id=s.id JOIN groups g ON g.course_id=c.id "
              "JOIN group_students gs ON gs.group_id=g.id WHERE gs.student_id=? AND s.id=?",
              (student_id, subject_id)) is None:
        raise AppError("not_in_subject")
    back = utils.parse_date(return_date)
    if return_date and back is None:
        raise AppError("bad_date")
    if db.one("SELECT 1 FROM absence_requests WHERE student_id=? AND subject_id=? AND lesson_date=? "
              "AND status IN ('pending','approved','info_needed')", (student_id, subject_id, day.isoformat())):
        raise AppError("absence_duplicate")
    req_id = db.insert(
        "INSERT INTO absence_requests (student_id, subject_id, lesson_date, reason, comment, return_date, created_at, "
        "file_name) VALUES (?,?,?,?,?,?,?,?)",
        (student_id, subject_id, day.isoformat(), reason, utils.clean_text(comment, 500),
         back.isoformat() if back else None, utils.fmt(utils.now()), file_name))
    student = db.one("SELECT * FROM users WHERE id=?", (student_id,))
    teacher_ids = [r["teacher_id"] for r in db.all(
        "SELECT DISTINCT g.teacher_id FROM groups g JOIN courses c ON c.id=g.course_id "
        "JOIN group_students gs ON gs.group_id=g.id WHERE gs.student_id=? AND c.subject_id=? "
        "AND g.teacher_id IS NOT NULL", (student_id, subject_id))]
    admin_ids = [r["id"] for r in db.all("SELECT id FROM users WHERE role='admin' AND is_active=1")]
    notifications.notify_many(db, teacher_ids + admin_ids, "absence_new",
                              {"name": access.user_name(student), "date": day.strftime("%d.%m.%Y")},
                              link="/teacher/absences")
    return req_id


def can_review(db, user, req):
    if user["role"] == "admin":
        return True
    if user["role"] != "teacher":
        return False
    return db.one(
        "SELECT 1 FROM groups g JOIN courses c ON c.id=g.course_id JOIN group_students gs ON gs.group_id=g.id "
        "WHERE g.teacher_id=? AND gs.student_id=? AND c.subject_id=?",
        (user["id"], req["student_id"], req["subject_id"])) is not None


def review_absence(db, req_id, status, reviewer, note=""):
    if status not in ABSENCE_REVIEW:
        raise AppError("bad_status")
    req = db.one("SELECT * FROM absence_requests WHERE id=?", (req_id,))
    if req is None:
        raise AppError("not_found")
    if not can_review(db, reviewer, req):
        raise AppError("forbidden")
    if status == "rejected" and not (note or "").strip():
        raise AppError("review_note_required")           # rad etishda sabab ko'rsatilishi shart
    if req["status"] == "approved" and status != "approved":
        raise AppError("bad_state")                      # tasdiqlangan (davomat uzrliga o'tgan) arizani qaytarib bo'lmaydi
    with db.tx():
        db.execute("UPDATE absence_requests SET status=?, reviewed_by=?, review_note=? WHERE id=?",
                   (status, reviewer["id"], utils.clean_text(note, 500), req_id))
        if status == "approved":
            _excuse_existing_absences(db, req, reviewer["id"])
        audit.record(db, reviewer["id"], "absence.review", "absence_request", req_id, {"status": status})
    payload = {"status": status, "date": utils.parse_date(req["lesson_date"]).strftime("%d.%m.%Y")}
    notifications.notify(db, req["student_id"], "absence_reviewed", payload, link="/student/absence")
    notifications.notify_parents(db, req["student_id"], "absence_reviewed", payload)


def _excuse_existing_absences(db, req, actor_id):
    """Ariza tasdiqlansa, o'sha kunga allaqachon 'sababsiz' belgilangan davomat uzrliga o'tadi."""
    start, end = utils.local_day_bounds(utils.parse_date(req["lesson_date"]))
    rows = db.all(
        "SELECT a.id FROM attendance a JOIN lessons l ON l.id=a.lesson_id JOIN groups g ON g.id=l.group_id "
        "JOIN courses c ON c.id=g.course_id WHERE a.student_id=? AND a.status='absent' "
        "AND c.subject_id=? AND l.starts_at>=? AND l.starts_at<?",
        (req["student_id"], req["subject_id"], start, end))
    for row in rows:
        points.revoke_by_ref(db, "attendance", row["id"], actor_id, "absence_approved")
        db.execute("UPDATE attendance SET status='excused' WHERE id=?", (row["id"],))
