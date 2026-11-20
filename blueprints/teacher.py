"""O'qituvchi kabineti: faqat o'ziga biriktirilgan guruh va o'quvchilar."""
import datetime

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from .. import utils, web
from ..db import get_db
from ..security import roles_required
from ..services import (access, analytics, attendance, audit, battle, homework, learning, notifications, points,
                        questions, schedule)
from ..utils import AppError

bp = Blueprint("teacher", __name__, url_prefix="/teacher")
teacher_only = roles_required("teacher")


def _my_groups(db):
    return db.all("SELECT g.*, c.name AS course_name, c.subject_id, s.code AS subject_code, s.name AS subject_name, "
                  "(SELECT COUNT(*) FROM group_students gs WHERE gs.group_id=g.id) AS students "
                  "FROM groups g JOIN courses c ON c.id=g.course_id JOIN subjects s ON s.id=c.subject_id "
                  "WHERE g.teacher_id=? AND g.is_active=1 ORDER BY g.name", (g.user["id"],))


def _own_group(db, group_id):
    if not access.can_manage_group(db, g.user, group_id):
        abort(403)
    row = db.one("SELECT g.*, c.name AS course_name, c.subject_id FROM groups g JOIN courses c ON c.id=g.course_id "
                 "WHERE g.id=?", (group_id,))
    if row is None:
        abort(404)
    return row


def _own_student(db, student_id):
    if student_id not in access.teacher_student_ids(db, g.user["id"]):
        abort(403)
    return db.one("SELECT * FROM users WHERE id=? AND role='student'", (student_id,))


def _my_students(db):
    ids = access.teacher_student_ids(db, g.user["id"])
    if not ids:
        return []
    return db.all("SELECT id, first_name, last_name, username FROM users WHERE id IN (%s) ORDER BY first_name, last_name"
                  % ",".join("?" * len(ids)), ids)


def _my_subjects(db):
    return db.all("SELECT DISTINCT s.* FROM subjects s JOIN courses c ON c.subject_id=s.id JOIN groups g ON g.course_id=c.id "
                  "WHERE g.teacher_id=? ORDER BY s.id", (g.user["id"],))


@bp.route("/")
@teacher_only
def dashboard():
    db = get_db()
    today = utils.local_today()
    s, _ = utils.local_day_bounds(today)
    e, _ = utils.local_day_bounds(today + datetime.timedelta(days=1))
    lessons = schedule.teacher_lessons(db, g.user["id"], utils.parse(s), utils.parse(e))
    pending = db.scalar(
        "SELECT COUNT(*) FROM submissions s JOIN homework h ON h.id=s.homework_id JOIN groups g ON g.id=h.group_id "
        "WHERE g.teacher_id=? AND s.status='submitted'", (g.user["id"],), 0)
    absences = db.scalar(
        "SELECT COUNT(DISTINCT a.id) FROM absence_requests a JOIN group_students gs ON gs.student_id=a.student_id "
        "JOIN groups g ON g.id=gs.group_id JOIN courses c ON c.id=g.course_id AND c.subject_id=a.subject_id "
        "WHERE g.teacher_id=? AND a.status='pending'", (g.user["id"],), 0)
    return render_template("teacher/dashboard.html", groups=_my_groups(db), lessons=lessons, pending=pending, absences=absences)


@bp.route("/group/<int:group_id>")
@teacher_only
def group(group_id):
    db = get_db()
    grp = _own_group(db, group_id)
    rows = []
    for st in db.all("SELECT u.* FROM users u JOIN group_students gs ON gs.student_id=u.id WHERE gs.group_id=? "
                     "ORDER BY u.first_name, u.last_name", (group_id,)):
        rows.append({"user": st, "name": access.user_name(st), "stats": points.stats(db, st["id"]),
                     "att": attendance.stats(db, st["id"]), "hw": homework.student_stats(db, st["id"])})
    board = analytics.leaderboard(db, "group", group_id=group_id, limit=0)
    for r in rows:
        r["rank"] = analytics.position(board, r["user"]["id"])
    return render_template("teacher/group.html", grp=grp, rows=rows)


@bp.route("/student/<int:student_id>")
@teacher_only
def student(student_id):
    db = get_db()
    user = _own_student(db, student_id)
    subjects = access.student_subjects(db, student_id)
    return render_template(
        "teacher/student.html", st=user, name=access.user_name(user), summary=analytics.student_summary(db, student_id),
        log=points.history(db, student_id, limit=30), subjects=subjects,
        skills={s["id"]: learning.skills_overview(db, student_id, s["id"]) for s in subjects},
        battles=battle.history(db, student_id, 10), goals=learning.goals_for(db, student_id),
        exams=db.all("SELECT e.*, s.name AS subject_name FROM exam_results e JOIN subjects s ON s.id=e.subject_id "
                     "WHERE e.student_id=? ORDER BY e.taken_on DESC LIMIT 10", (student_id,)))


@bp.route("/schedule", endpoint="schedule")
@teacher_only
def schedule_page():
    db = get_db()
    offset = max(-52, min(52, utils.to_int(request.args.get("week"), 0) or 0))
    today = utils.local_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=offset)
    s, _ = utils.local_day_bounds(monday)
    e, _ = utils.local_day_bounds(monday + datetime.timedelta(days=7))
    lessons = schedule.teacher_lessons(db, g.user["id"], utils.parse(s), utils.parse(e))
    days = [{"date": monday + datetime.timedelta(days=i),
             "lessons": [l for l in lessons if utils.local_date_of(l["starts_at"]) == monday + datetime.timedelta(days=i)]}
            for i in range(7)]
    return render_template("teacher/schedule.html", days=days, week=offset, today=today)


@bp.route("/attendance/<int:lesson_id>", methods=["GET", "POST"])
@teacher_only
def attendance_mark(lesson_id):
    db = get_db()
    lesson = db.one("SELECT l.*, g.name AS group_name, g.teacher_id, c.subject_id FROM lessons l JOIN groups g ON g.id=l.group_id "
                    "JOIN courses c ON c.id=g.course_id WHERE l.id=?", (lesson_id,))
    if lesson is None:
        abort(404)
    _own_group(db, lesson["group_id"])
    students = db.all("SELECT u.* FROM users u JOIN group_students gs ON gs.student_id=u.id WHERE gs.group_id=? "
                      "ORDER BY u.first_name, u.last_name", (lesson["group_id"],))
    if request.method == "POST":
        count = 0
        for st in students:
            status = request.form.get("st_%d" % st["id"])
            if status in attendance.STATUSES:
                attendance.mark(db, lesson_id, st["id"], status, g.user["id"])
                count += 1
        web.flash_t("msg.attendance_saved")
        return redirect(url_for("teacher.attendance_mark", lesson_id=lesson_id))
    marked = {r["student_id"]: r["status"] for r in db.all("SELECT * FROM attendance WHERE lesson_id=?", (lesson_id,))}
    date_str = utils.local_date_of(lesson["starts_at"]).isoformat()
    approved = {st["id"] for st in students if attendance.has_approved_absence(db, st["id"], date_str, lesson["subject_id"])}
    return render_template("teacher/attendance.html", lesson=lesson, students=students, marked=marked, approved=approved,
                           names=access.user_name, statuses=attendance.STATUSES)


# ---------------------------------------------------------------- uy vazifalari
@bp.route("/homework", methods=["GET", "POST"])
@teacher_only
def homework_list():
    db = get_db()
    if request.method == "POST":
        group_id = utils.to_int(request.form.get("group_id"))
        _own_group(db, group_id)
        due = utils.parse_local_input(request.form.get("due_at"))
        hw_id = homework.create(db, group_id, request.form.get("title"), request.form.get("description"), due, g.user["id"])
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.homework_detail", hw_id=hw_id))
    rows = db.all(
        "SELECT h.*, g.name AS group_name, (SELECT COUNT(*) FROM submissions s WHERE s.homework_id=h.id) AS subs, "
        "(SELECT COUNT(*) FROM submissions s WHERE s.homework_id=h.id AND s.status='submitted') AS unchecked, "
        "(SELECT COUNT(*) FROM group_students gs WHERE gs.group_id=h.group_id) AS total "
        "FROM homework h JOIN groups g ON g.id=h.group_id WHERE g.teacher_id=? ORDER BY h.due_at DESC LIMIT 100", (g.user["id"],))
    return render_template("teacher/homework_list.html", rows=rows, groups=_my_groups(db))


@bp.route("/homework/<int:hw_id>", methods=["GET", "POST"])
@teacher_only
def homework_detail(hw_id):
    db = get_db()
    hw = db.one("SELECT h.*, g.name AS group_name FROM homework h JOIN groups g ON g.id=h.group_id WHERE h.id=?", (hw_id,))
    if hw is None:
        abort(404)
    _own_group(db, hw["group_id"])
    if request.method == "POST":
        action = request.form.get("action")
        if action == "check":
            homework.check(db, utils.to_int(request.form.get("submission_id")), g.user, request.form.get("score"),
                           request.form.get("feedback"))
            web.flash_t("msg.saved")
        elif action == "excuse":
            homework.excuse_miss(db, utils.to_int(request.form.get("miss_id")), g.user)
            web.flash_t("msg.saved")
        return redirect(url_for("teacher.homework_detail", hw_id=hw_id))
    if utils.fmt(utils.now()) > hw["due_at"]:
        homework.process_overdue(db, hw_id)
    subs = db.all("SELECT s.*, u.first_name, u.last_name, u.username FROM submissions s JOIN users u ON u.id=s.student_id "
                  "WHERE s.homework_id=? ORDER BY s.status, s.submitted_at", (hw_id,))
    missing = db.all("SELECT u.id, u.first_name, u.last_name, u.username, m.id AS miss_id, m.action FROM users u "
                     "JOIN group_students gs ON gs.student_id=u.id LEFT JOIN homework_misses m ON m.homework_id=? AND m.student_id=u.id "
                     "WHERE gs.group_id=? AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.homework_id=? AND s.student_id=u.id)",
                     (hw_id, hw["group_id"], hw_id))
    return render_template("teacher/homework_detail.html", hw=hw, subs=subs, missing=missing, names=access.user_name,
                           overdue=utils.fmt(utils.now()) > hw["due_at"])


# ---------------------------------------------------------------- uzrli sabab arizalari
@bp.route("/absences", methods=["GET", "POST"])
@teacher_only
def absences():
    db = get_db()
    if request.method == "POST":
        attendance.review_absence(db, utils.to_int(request.form.get("id")), request.form.get("status"), g.user,
                                  request.form.get("note"))
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.absences"))
    rows = db.all(
        "SELECT DISTINCT a.*, u.first_name, u.last_name, u.username, s.name AS subject_name, s.code AS subject_code "
        "FROM absence_requests a JOIN users u ON u.id=a.student_id JOIN subjects s ON s.id=a.subject_id "
        "JOIN group_students gs ON gs.student_id=a.student_id JOIN groups g ON g.id=gs.group_id "
        "JOIN courses c ON c.id=g.course_id AND c.subject_id=a.subject_id WHERE g.teacher_id=? "
        "ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'info_needed' THEN 1 ELSE 2 END, a.id DESC LIMIT 100", (g.user["id"],))
    return render_template("teacher/absences.html", rows=rows, names=access.user_name)


# ---------------------------------------------------------------- XP / Coin berish
@bp.route("/award", methods=["GET", "POST"])
@teacher_only
def award():
    db = get_db()
    if request.method == "POST":
        sid = utils.to_int(request.form.get("student_id"))
        _own_student(db, sid)
        kind = request.form.get("kind")
        amount = utils.to_int(request.form.get("amount"))
        if kind not in ("xp", "coin") or amount is None or not 1 <= amount <= 500:
            raise AppError("bad_amount")
        reason = utils.clean_text(request.form.get("reason"), 200)
        points.award(db, sid, kind, amount, "teacher_award", reason=reason, actor_id=g.user["id"])
        audit.record(db, g.user["id"], "points.award", "user", sid, {"kind": kind, "amount": amount})
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.award"))
    return render_template("teacher/award.html", students=_my_students(db), names=access.user_name)


# ---------------------------------------------------------------- imtihon va ko'nikmalar
@bp.route("/exams", methods=["GET", "POST"])
@teacher_only
def exams():
    db = get_db()
    if request.method == "POST":
        sid = utils.to_int(request.form.get("student_id"))
        _own_student(db, sid)
        subject_id = utils.to_int(request.form.get("subject_id"))
        if request.form.get("form") == "skill":
            learning.record_skill(db, sid, subject_id, request.form.get("skill"), request.form.get("score"), g.user,
                                  comment=request.form.get("comment"))
        else:
            learning.add_exam_result(db, sid, subject_id, request.form.get("exam_type"), request.form.get("title"),
                                     request.form.get("score"), request.form.get("max_score") or 100,
                                     request.form.get("taken_on"), bool(request.form.get("official")), g.user,
                                     request.form.get("comment"))
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.exams"))
    ids = access.teacher_student_ids(db, g.user["id"])
    recent = []
    if ids:
        recent = db.all("SELECT e.*, u.first_name, u.last_name, u.username, s.name AS subject_name FROM exam_results e "
                        "JOIN users u ON u.id=e.student_id JOIN subjects s ON s.id=e.subject_id WHERE e.student_id IN (%s) "
                        "ORDER BY e.id DESC LIMIT 30" % ",".join("?" * len(ids)), ids)
    return render_template("teacher/exams.html", students=_my_students(db), subjects=_my_subjects(db), recent=recent,
                           names=access.user_name, exam_types=learning.EXAM_TYPES, skills=learning.SKILLS,
                           today=utils.local_today().isoformat())


# ---------------------------------------------------------------- Battle: o'qituvchi savollari
@bp.route("/challenges", methods=["GET", "POST"])
@teacher_only
def challenges():
    db = get_db()
    if request.method == "POST":
        form = request.form
        if form.get("form") == "challenge":
            subject_id = utils.to_int(form.get("subject_id"))
            if subject_id not in [s["id"] for s in _my_subjects(db)]:
                raise AppError("forbidden")
            group_id = utils.to_int(form.get("group_id"))
            if group_id:
                _own_group(db, group_id)
            title = utils.clean_text(form.get("title"), 120)
            if not title:
                raise AppError("title_required")
            level = form.get("level") if form.get("level") in battle.LEVELS else "A1"
            db.insert("INSERT INTO challenges (teacher_id, subject_id, group_id, title, level, created_at) VALUES (?,?,?,?,?,?)",
                      (g.user["id"], subject_id, group_id, title, level, utils.fmt(utils.now())))
        else:
            ch = db.one("SELECT * FROM challenges WHERE id=? AND teacher_id=?", (utils.to_int(form.get("challenge_id")), g.user["id"]))
            if ch is None:
                abort(404)
            options = [form.get("opt%d" % i) for i in range(1, 5)]
            questions.add_question(db, ch["subject_id"], ch["level"], form.get("qtype") or "vocabulary", form.get("text"),
                                   options, form.get("correct"), form.get("explanation"), challenge_id=ch["id"],
                                   created_by=g.user["id"])
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.challenges"))
    rows = db.all("SELECT c.*, s.name AS subject_name, s.code AS subject_code, "
                  "(SELECT COUNT(*) FROM questions q WHERE q.challenge_id=c.id) AS qn FROM challenges c "
                  "JOIN subjects s ON s.id=c.subject_id WHERE c.teacher_id=? ORDER BY c.id DESC", (g.user["id"],))
    return render_template("teacher/challenges.html", rows=rows, subjects=_my_subjects(db), groups=_my_groups(db),
                           levels=battle.LEVELS, qtypes=battle.QTYPES, min_q=battle.MIN_QUESTIONS)


@bp.route("/vocab", methods=["GET", "POST"])
@teacher_only
def vocab():
    db = get_db()
    if request.method == "POST":
        subject_id = utils.to_int(request.form.get("subject_id"))
        if subject_id not in [s["id"] for s in _my_subjects(db)]:
            raise AppError("forbidden")
        learning.add_word(db, subject_id, request.form.get("level") or "A1", request.form.get("word"),
                          request.form.get("translation"), request.form.get("pronunciation"),
                          request.form.get("example"), request.form.get("audio_url"))
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.vocab"))
    subjects = _my_subjects(db)
    words = []
    if subjects:
        words = db.all("SELECT w.*, s.code AS subject_code FROM vocab_words w JOIN subjects s ON s.id=w.subject_id "
                       "WHERE w.subject_id IN (%s) ORDER BY w.id DESC LIMIT 60" % ",".join("?" * len(subjects)),
                       [s["id"] for s in subjects])
    return render_template("teacher/vocab.html", subjects=subjects, words=words, levels=battle.LEVELS)


@bp.route("/clubs", methods=["GET", "POST"])
@teacher_only
def clubs():
    db = get_db()
    if request.method == "POST":
        form = request.form
        if form.get("form") == "attend":
            learning.mark_attended(db, utils.to_int(form.get("event_id")), utils.to_int(form.get("student_id")), g.user)
        else:
            learning.create_event(db, form.get("kind"), utils.to_int(form.get("subject_id")), form.get("title"),
                                  utils.parse_local_input(form.get("starts_at")), form.get("description"),
                                  form.get("questions"), form.get("new_words"), utils.to_int(form.get("xp"), 0),
                                  utils.to_int(form.get("coin"), 0), g.user)
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.clubs"))
    events = db.all("SELECT e.*, s.name AS subject_name FROM club_events e LEFT JOIN subjects s ON s.id=e.subject_id "
                    "ORDER BY e.starts_at DESC LIMIT 30")
    attendees = {}
    for ev in events:
        attendees[ev["id"]] = db.all(
            "SELECT a.*, u.first_name, u.last_name, u.username FROM club_attendees a JOIN users u ON u.id=a.student_id "
            "WHERE a.event_id=? ORDER BY u.first_name", (ev["id"],))
    return render_template("teacher/clubs.html", events=events, attendees=attendees, subjects=_my_subjects(db),
                           names=access.user_name)


@bp.route("/goals", methods=["GET", "POST"])
@teacher_only
def goals():
    db = get_db()
    if request.method == "POST":
        form = request.form
        if form.get("form") == "task":
            goal = db.one("SELECT * FROM goals WHERE id=?", (utils.to_int(form.get("goal_id")),))
            if goal is None:
                abort(404)
            _own_student(db, goal["student_id"])
            learning.add_goal_task(db, goal["id"], form.get("title"), form.get("due_on"))
        else:
            sid = utils.to_int(form.get("student_id"))
            _own_student(db, sid)
            learning.create_goal(db, sid, utils.to_int(form.get("subject_id")), form.get("title"), form.get("target_date"), g.user)
        web.flash_t("msg.saved")
        return redirect(url_for("teacher.goals"))
    items = []
    for st in _my_students(db):
        for goal in learning.goals_for(db, st["id"]):
            goal["student"] = st
            items.append(goal)
    return render_template("teacher/goals.html", items=items, students=_my_students(db), subjects=_my_subjects(db),
                           names=access.user_name)


@bp.route("/announce", methods=["POST"])
@teacher_only
def announce():
    db = get_db()
    group_id = utils.to_int(request.form.get("group_id"))
    _own_group(db, group_id)
    text = utils.clean_text(request.form.get("text"), 500)
    if not text:
        raise AppError("message_required")
    ids = [r["student_id"] for r in db.all("SELECT student_id FROM group_students WHERE group_id=?", (group_id,))]
    for sid in list(ids):
        ids += [r["parent_id"] for r in db.all("SELECT parent_id FROM parent_students WHERE student_id=?", (sid,))]
    notifications.notify_many(db, ids, "announcement", {"text": text})
    web.flash_t("msg.saved")
    return redirect(url_for("teacher.group", group_id=group_id))
