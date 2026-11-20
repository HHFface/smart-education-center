"""Uy vazifalari: yaratish, topshirish, tekshirish va bajarilmaganlik uchun C-variant jarima.

C-variant: 1-marta ogohlantirish, 2-marta -10 Coin, 3-marta va keyingilari -20 Coin
(qiymatlar `hw.penalties` sozlamasida). Har bir topshiriq bo'yicha jarima faqat BIR marta
qo'llanadi (homework_misses jadvalida UNIQUE). Uzrli sabab bo'lsa jarima qo'llanmaydi.
"""
import datetime

from .. import utils
from ..utils import AppError
from . import access, attendance, audit, notifications, points, settings

SUBMIT_KINDS = ("text", "image", "document", "audio", "video", "test")
OVERDUE_WINDOW_DAYS = 45


def create(db, group_id, title, description, due_at_utc, teacher_id):
    title = utils.clean_text(title, 200)
    if not title:
        raise AppError("title_required")
    if due_at_utc is None:
        raise AppError("bad_date")
    hw_id = db.insert(
        "INSERT INTO homework (group_id, title, description, due_at, created_by, created_at) VALUES (?,?,?,?,?,?)",
        (group_id, title, utils.clean_text(description, 4000), utils.fmt(due_at_utc), teacher_id,
         utils.fmt(utils.now())))
    students = [r["student_id"] for r in db.all("SELECT student_id FROM group_students WHERE group_id=?", (group_id,))]
    notifications.notify_many(db, students, "homework_new", {"title": title}, link="/student/homework/%d" % hw_id)
    return hw_id


def submit(db, homework_id, student_id, kind, text="", file_name=None):
    hw = db.one("SELECT * FROM homework WHERE id=?", (homework_id,))
    if hw is None:
        raise AppError("not_found")
    if db.one("SELECT 1 FROM group_students WHERE group_id=? AND student_id=?", (hw["group_id"], student_id)) is None:
        raise AppError("forbidden")
    if kind not in SUBMIT_KINDS:
        raise AppError("bad_kind")
    text = utils.clean_text(text, 8000)
    if not text and not file_name:
        raise AppError("empty_submission")
    now = utils.now()
    late = 1 if utils.fmt(now) > hw["due_at"] else 0
    with db.tx():
        existing = db.one("SELECT * FROM submissions WHERE homework_id=? AND student_id=?", (homework_id, student_id))
        if existing is not None and existing["status"] == "checked":
            raise AppError("already_checked")
        if existing is None:
            sub_id = db.insert(
                "INSERT INTO submissions (homework_id, student_id, kind, text, file_name, is_late, submitted_at) "
                "VALUES (?,?,?,?,?,?,?)", (homework_id, student_id, kind, text, file_name, late, utils.fmt(now)))
            if not late:
                reward = int(settings.get(db, "hw.reward_coin") or 0)
                if reward:
                    points.award(db, student_id, "coin", reward, "homework_done",
                                 ref_type="submission", ref_id=sub_id)
        else:
            sub_id = existing["id"]
            db.execute("UPDATE submissions SET kind=?, text=?, file_name=COALESCE(?, file_name), is_late=?, "
                       "submitted_at=? WHERE id=?", (kind, text, file_name, late, utils.fmt(now), sub_id))
    return sub_id


def check(db, submission_id, teacher, score, feedback):
    sub = db.one("SELECT s.*, h.group_id, h.title FROM submissions s JOIN homework h ON h.id=s.homework_id "
                 "WHERE s.id=?", (submission_id,))
    if sub is None:
        raise AppError("not_found")
    if not access.can_manage_group(db, teacher, sub["group_id"]):
        raise AppError("forbidden")
    score = utils.to_int(score)
    if score is None or not 0 <= score <= 100:
        raise AppError("bad_score")
    with db.tx():
        db.execute("UPDATE submissions SET status='checked', score=?, feedback=?, checked_by=?, checked_at=? "
                   "WHERE id=?", (score, utils.clean_text(feedback, 2000), teacher["id"],
                                  utils.fmt(utils.now()), submission_id))
        divisor = max(1, int(settings.get(db, "hw.xp_divisor") or 10))
        xp = score // divisor
        already = db.one("SELECT 1 FROM points_log WHERE ref_type='submission_xp' AND ref_id=?", (submission_id,))
        if xp and not already:
            points.award(db, sub["student_id"], "xp", xp, "homework_checked",
                         actor_id=teacher["id"], ref_type="submission_xp", ref_id=submission_id)
    notifications.notify(db, sub["student_id"], "homework_checked", {"title": sub["title"], "score": score},
                         link="/student/homework/%d" % sub["homework_id"])


def _penalty_for(db, seq):
    penalties = settings.get(db, "hw.penalties") or [0]
    return int(penalties[min(seq - 1, len(penalties) - 1)])


def record_miss(db, homework, student_id):
    """Bitta o'quvchining bitta topshiriq bo'yicha bajarmaganligini qayd etadi (takroran chaqirish xavfsiz)."""
    if db.one("SELECT 1 FROM homework_misses WHERE homework_id=? AND student_id=?", (homework["id"], student_id)):
        return None
    subject_id = db.scalar("SELECT c.subject_id FROM groups g JOIN courses c ON c.id=g.course_id WHERE g.id=?",
                           (homework["group_id"],))
    due_day = utils.local_date_of(homework["due_at"]).isoformat()
    with db.tx():
        if attendance.has_approved_absence(db, student_id, due_day, subject_id):
            return db.insert(
                "INSERT INTO homework_misses (homework_id, student_id, seq, action, coins, created_at) "
                "VALUES (?,?,?,?,?,?)", (homework["id"], student_id, 0, "excused", 0, utils.fmt(utils.now())))
        seq = db.scalar("SELECT COUNT(*) FROM homework_misses WHERE student_id=? AND action IN ('warning','penalty')",
                        (student_id,), 0) + 1
        coins = _penalty_for(db, seq)
        action = "penalty" if coins < 0 else "warning"
        miss_id = db.insert(
            "INSERT INTO homework_misses (homework_id, student_id, seq, action, coins, created_at) "
            "VALUES (?,?,?,?,?,?)", (homework["id"], student_id, seq, action, coins, utils.fmt(utils.now())))
        if coins < 0:
            points.award(db, student_id, "coin", coins, "homework_missed", reason=homework["title"],
                         ref_type="homework_miss", ref_id=miss_id)
    if action == "warning":
        payload = {"title": homework["title"], "seq": seq}
        notifications.notify(db, student_id, "hw_warning", payload, link="/student/homework")
        notifications.notify_parents(db, student_id, "hw_warning", payload)
    return miss_id


def process_overdue(db, homework_id=None, now=None):
    """Muddati o'tgan va topshirilmagan vazifalarni qayd etadi. Soni qaytariladi."""
    now = now or utils.now()
    cutoff = utils.fmt(now - datetime.timedelta(days=OVERDUE_WINDOW_DAYS))
    sql = "SELECT * FROM homework WHERE due_at < ? AND due_at > ?"
    args = [utils.fmt(now), cutoff]
    if homework_id is not None:
        sql += " AND id=?"
        args.append(homework_id)
    count = 0
    for hw in db.all(sql, args):
        rows = db.all(
            "SELECT gs.student_id FROM group_students gs WHERE gs.group_id=? AND gs.joined_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.homework_id=? AND s.student_id=gs.student_id) "
            "AND NOT EXISTS (SELECT 1 FROM homework_misses m WHERE m.homework_id=? AND m.student_id=gs.student_id) "
            "ORDER BY gs.student_id", (hw["group_id"], hw["due_at"], hw["id"], hw["id"]))
        for row in rows:
            if record_miss(db, hw, row["student_id"]):
                count += 1
    return count


def excuse_miss(db, miss_id, actor):
    miss = db.one("SELECT m.*, h.group_id FROM homework_misses m JOIN homework h ON h.id=m.homework_id WHERE m.id=?",
                  (miss_id,))
    if miss is None:
        raise AppError("not_found")
    if not access.can_manage_group(db, actor, miss["group_id"]):
        raise AppError("forbidden")
    with db.tx():
        points.revoke_by_ref(db, "homework_miss", miss_id, actor["id"], "excused")
        db.execute("UPDATE homework_misses SET action='excused' WHERE id=?", (miss_id,))
        audit.record(db, actor["id"], "homework.excuse", "homework_miss", miss_id)


def student_stats(db, student_id, now=None):
    """Bajarish foizi: muddati o'tgan vazifalardan nechtasi topshirilgan."""
    now_s = utils.fmt(now or utils.now())
    due = db.scalar(
        "SELECT COUNT(*) FROM homework h JOIN group_students gs ON gs.group_id=h.group_id "
        "WHERE gs.student_id=? AND h.due_at<=? AND h.due_at>=gs.joined_at", (student_id, now_s), 0)
    done = db.scalar(
        "SELECT COUNT(*) FROM homework h JOIN group_students gs ON gs.group_id=h.group_id "
        "JOIN submissions s ON s.homework_id=h.id AND s.student_id=gs.student_id "
        "WHERE gs.student_id=? AND h.due_at<=?", (student_id, now_s), 0)
    misses = db.scalar("SELECT COUNT(*) FROM homework_misses WHERE student_id=? AND action IN ('warning','penalty')",
                       (student_id,), 0)
    return {"due": due, "done": done, "misses": misses,
            "pct": round(100.0 * done / due) if due else None}
