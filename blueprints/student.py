"""O'quvchi kabineti."""
import datetime

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from .. import utils, web
from ..db import get_db
from ..security import roles_required
from ..services import (access, ai_assistant, analytics, attendance, battle, homework, learning, points,
                        schedule, shop)
from ..utils import AppError

bp = Blueprint("student", __name__, url_prefix="/student")
student_only = roles_required("student")


def _subjects(db):
    return access.student_subjects(db, g.user["id"])


@bp.route("/")
@student_only
def dashboard():
    db = get_db()
    uid = g.user["id"]
    today = utils.local_today()
    start, _ = utils.local_day_bounds(today)
    end, _ = utils.local_day_bounds(today + datetime.timedelta(days=1))
    lessons_today = schedule.student_lessons(db, uid, utils.parse(start), utils.parse(end))
    upcoming = schedule.student_lessons(db, uid, utils.now(), utils.now() + datetime.timedelta(days=14))
    summary = analytics.student_summary(db, uid)
    homework_due = db.all(
        "SELECT h.*, g.name AS group_name FROM homework h JOIN group_students gs ON gs.group_id=h.group_id "
        "JOIN groups g ON g.id=h.group_id WHERE gs.student_id=? AND h.due_at>? AND NOT EXISTS "
        "(SELECT 1 FROM submissions s WHERE s.homework_id=h.id AND s.student_id=?) ORDER BY h.due_at LIMIT 5",
        (uid, utils.fmt(utils.now()), uid))
    exams = db.all("SELECT * FROM club_events WHERE starts_at>? ORDER BY starts_at LIMIT 3", (utils.fmt(utils.now()),))
    notes = db.all("SELECT * FROM notifications WHERE user_id=? AND is_read=0 ORDER BY id DESC LIMIT 5", (uid,))
    from ..services import notifications as nsvc
    return render_template("student/dashboard.html", lessons_today=lessons_today, next_lesson=(upcoming[0] if upcoming else None),
                           summary=summary, homework_due=homework_due, events=exams,
                           notes=[{"text": nsvc.render(n["kind"], utils.jloads(n["payload"], {}), g.lang), "link": n["link"]} for n in notes],
                           incoming=battle.incoming_quick(db, uid))


@bp.route("/schedule", endpoint="schedule")
@student_only
def schedule_page():
    db = get_db()
    offset = utils.to_int(request.args.get("week"), 0) or 0
    offset = max(-52, min(52, offset))
    today = utils.local_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=offset)
    s, _ = utils.local_day_bounds(monday)
    e, _ = utils.local_day_bounds(monday + datetime.timedelta(days=7))
    lessons = schedule.student_lessons(db, g.user["id"], utils.parse(s), utils.parse(e))
    days = []
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        days.append({"date": day, "lessons": [l for l in lessons if utils.local_date_of(l["starts_at"]) == day]})
    return render_template("student/schedule.html", days=days, week=offset, today=today)



@bp.route("/attendance", endpoint="attendance")
@student_only
def attendance_page():
    db = get_db()
    rows = db.all(
        "SELECT a.*, l.starts_at, g.name AS group_name FROM attendance a JOIN lessons l ON l.id=a.lesson_id "
        "JOIN groups g ON g.id=l.group_id WHERE a.student_id=? ORDER BY l.starts_at DESC LIMIT 200", (g.user["id"],))
    return render_template("student/attendance.html", rows=rows, stats=attendance.stats(db, g.user["id"]),
                           missed=[r for r in rows if r["status"] in ("absent", "excused")])



# ---------------------------------------------------------------- uy vazifalari
@bp.route("/homework", endpoint="homework")
@student_only
def homework_list():
    db = get_db()
    uid = g.user["id"]
    rows = db.all(
        "SELECT h.*, g.name AS group_name, s.status AS sub_status, s.score, s.is_late, m.action AS miss_action, m.coins AS miss_coins "
        "FROM homework h JOIN group_students gs ON gs.group_id=h.group_id JOIN groups g ON g.id=h.group_id "
        "LEFT JOIN submissions s ON s.homework_id=h.id AND s.student_id=gs.student_id "
        "LEFT JOIN homework_misses m ON m.homework_id=h.id AND m.student_id=gs.student_id "
        "WHERE gs.student_id=? ORDER BY h.due_at DESC LIMIT 100", (uid,))
    return render_template("student/homework.html", rows=rows, stats=homework.student_stats(db, uid),
                           now=utils.fmt(utils.now()))



@bp.route("/homework/<int:hw_id>", methods=["GET", "POST"])
@student_only
def homework_detail(hw_id):
    db = get_db()
    uid = g.user["id"]
    hw = db.one("SELECT h.*, g.name AS group_name FROM homework h JOIN groups g ON g.id=h.group_id WHERE h.id=?", (hw_id,))
    if hw is None or db.one("SELECT 1 FROM group_students WHERE group_id=? AND student_id=?", (hw["group_id"], uid)) is None:
        abort(404)
    if request.method == "POST":
        upload = request.files.get("file")
        file_name, file_kind = (None, None)
        if upload is not None and upload.filename:
            file_name, file_kind = web.save_upload(upload, "hw")
        kind = request.form.get("kind") or file_kind or "text"
        if file_kind:
            kind = file_kind
        homework.submit(db, hw_id, uid, kind, request.form.get("text"), file_name)
        web.flash_t("msg.submitted")
        return redirect(url_for("student.homework_detail", hw_id=hw_id))
    sub = db.one("SELECT * FROM submissions WHERE homework_id=? AND student_id=?", (hw_id, uid))
    miss = db.one("SELECT * FROM homework_misses WHERE homework_id=? AND student_id=?", (hw_id, uid))
    return render_template("student/homework_detail.html", hw=hw, sub=sub, miss=miss, kinds=homework.SUBMIT_KINDS,
                           overdue=utils.fmt(utils.now()) > hw["due_at"])


@bp.route("/absence", methods=["GET", "POST"])
@student_only
def absence():
    db = get_db()
    if request.method == "POST":
        file_name, _kind = web.save_upload(request.files.get("document"), "absence", {"image", "document"})
        attendance.submit_absence(db, g.user["id"], utils.to_int(request.form.get("subject_id")),
                                  request.form.get("lesson_date"), request.form.get("reason"),
                                  request.form.get("comment"), request.form.get("return_date"), file_name)
        web.flash_t("msg.absence_sent")
        return redirect(url_for("student.absence"))
    rows = db.all("SELECT a.*, s.name AS subject_name, s.code AS subject_code FROM absence_requests a "
                  "JOIN subjects s ON s.id=a.subject_id WHERE a.student_id=? ORDER BY a.id DESC LIMIT 50", (g.user["id"],))
    return render_template("student/absence.html", rows=rows, subjects=_subjects(db), reasons=attendance.ABSENCE_REASONS,
                           today=utils.local_today().isoformat())


# ---------------------------------------------------------------- Smart Points, reyting
@bp.route("/points", endpoint="points")
@student_only
def points_page():
    db = get_db()
    uid = g.user["id"]
    log = points.history(db, uid)
    disputed = {r["ref_id"] for r in db.all("SELECT ref_id FROM disputes WHERE student_id=? AND ref_type='points_log'", (uid,))}
    return render_template("student/points.html", stats=points.stats(db, uid), log=log, disputed=disputed,
                           hw=homework.student_stats(db, uid), att=attendance.stats(db, uid))



@bp.route("/points/dispute", methods=["POST"])
@student_only
def dispute():
    db = get_db()
    log_id = utils.to_int(request.form.get("log_id"))
    row = db.one("SELECT * FROM points_log WHERE id=? AND student_id=? AND amount<0", (log_id, g.user["id"]))
    if row is None:
        abort(404)
    message = utils.clean_text(request.form.get("message"), 1000)
    if not message:
        raise AppError("message_required")
    if db.one("SELECT 1 FROM disputes WHERE student_id=? AND ref_type='points_log' AND ref_id=? AND status='open'",
              (g.user["id"], log_id)):
        raise AppError("dispute_exists")
    db.insert("INSERT INTO disputes (student_id, ref_type, ref_id, message, created_at) VALUES (?,?,?,?,?)",
              (g.user["id"], "points_log", log_id, message, utils.fmt(utils.now())))
    web.flash_t("msg.dispute_sent")
    return redirect(url_for("student.points"))


@bp.route("/leaderboard")
@student_only
def leaderboard():
    db = get_db()
    scope = request.args.get("scope", "overall")
    if scope not in ("overall", "subject", "group", "weekly", "monthly", "battle"):
        scope = "overall"
    subjects = _subjects(db)
    subject_id = utils.to_int(request.args.get("subject"))
    if subjects and subject_id not in [s["id"] for s in subjects]:
        subject_id = subjects[0]["id"]
    groups = access.student_group_ids(db, g.user["id"])
    group_id = utils.to_int(request.args.get("group"))
    if group_id not in groups:
        group_id = groups[0] if groups else None
    if scope in ("subject", "battle") and not subject_id:
        scope = "overall"
    if scope == "group" and not group_id:
        scope = "overall"
    rows = analytics.leaderboard(db, scope, subject_id, group_id, limit=0)
    me = analytics.position(rows, g.user["id"])
    group_rows = []
    if groups:
        group_rows = db.all("SELECT id, name FROM groups WHERE id IN (%s)" % ",".join("?" * len(groups)), groups)
    return render_template("student/leaderboard.html", rows=rows[:50], me=me, scope=scope, subjects=subjects,
                           subject_id=subject_id, groups=group_rows, group_id=group_id, my_id=g.user["id"],
                           above=[r for r in rows if me and me - 3 <= r["rank"] < me and r["id"] != g.user["id"]])


# ---------------------------------------------------------------- Smart Shop
@bp.route("/shop", methods=["GET", "POST"], endpoint="shop")
@student_only
def shop_page():
    db = get_db()
    if request.method == "POST":
        shop.buy(db, g.user["id"], utils.to_int(request.form.get("item_id")))
        web.flash_t("msg.purchased")
        return redirect(url_for("student.shop"))
    items = db.all("SELECT * FROM shop_items WHERE is_active=1 ORDER BY price_coins, id")
    orders = db.all("SELECT o.*, i.name AS item_name FROM shop_orders o JOIN shop_items i ON i.id=o.item_id "
                    "WHERE o.student_id=? ORDER BY o.id DESC LIMIT 30", (g.user["id"],))
    return render_template("student/shop.html", items=items, orders=orders, stats=points.stats(db, g.user["id"]))



# ---------------------------------------------------------------- natijalar
@bp.route("/results", endpoint="results")
@student_only
def results():
    db = get_db()
    uid = g.user["id"]
    subjects = _subjects(db)
    overview = {s["id"]: learning.skills_overview(db, uid, s["id"]) for s in subjects}
    exams = db.all("SELECT e.*, s.name AS subject_name, s.code AS subject_code FROM exam_results e "
                   "JOIN subjects s ON s.id=e.subject_id WHERE e.student_id=? ORDER BY e.taken_on DESC", (uid,))
    submissions = db.all("SELECT s.score, h.title, s.checked_at, s.feedback FROM submissions s JOIN homework h ON h.id=s.homework_id "
                         "WHERE s.student_id=? AND s.status='checked' ORDER BY s.checked_at DESC LIMIT 20", (uid,))
    return render_template("student/results.html", subjects=subjects, overview=overview, exams=exams,
                           official=[e for e in exams if e["official"]], internal=[e for e in exams if not e["official"]],
                           submissions=submissions)



# ---------------------------------------------------------------- lug'at, klublar, maqsadlar
@bp.route("/vocabulary", endpoint="vocabulary")
@student_only
def vocabulary():
    db = get_db()
    subjects = _subjects(db)
    subject_id = utils.to_int(request.args.get("subject"))
    if subjects and subject_id not in [s["id"] for s in subjects]:
        subject_id = subjects[0]["id"]
    words, stats, code = [], None, None
    if subject_id:
        words = [dict(w) for w in learning.next_words(db, g.user["id"], subject_id, 10)]
        stats = learning.vocab_stats(db, g.user["id"], subject_id)
        code = next((s["code"] for s in subjects if s["id"] == subject_id), None)
    return render_template("student/vocabulary.html", subjects=subjects, subject_id=subject_id, words=words, stats=stats,
                           subject_code=code)


@bp.route("/vocabulary/answer", methods=["POST"])
@student_only
def vocabulary_answer():
    from flask import jsonify
    db = get_db()
    data = request.get_json(silent=True) or {}
    box = learning.answer_word(db, g.user["id"], utils.to_int(data.get("word_id")), bool(data.get("known")))
    return jsonify({"ok": True, "box": box})



@bp.route("/clubs", methods=["GET", "POST"])
@student_only
def clubs():
    db = get_db()
    if request.method == "POST":
        learning.join_event(db, utils.to_int(request.form.get("event_id")), g.user["id"])
        web.flash_t("msg.saved")
        return redirect(url_for("student.clubs"))
    events = db.all("SELECT e.*, s.name AS subject_name, s.code AS subject_code, "
                    "(SELECT COUNT(*) FROM club_attendees a WHERE a.event_id=e.id) AS going, "
                    "(SELECT attended FROM club_attendees a WHERE a.event_id=e.id AND a.student_id=?) AS mine "
                    "FROM club_events e LEFT JOIN subjects s ON s.id=e.subject_id "
                    "WHERE e.starts_at>? OR EXISTS (SELECT 1 FROM club_attendees a WHERE a.event_id=e.id AND a.student_id=?) "
                    "ORDER BY e.starts_at DESC LIMIT 40",
                    (g.user["id"], utils.fmt(utils.now() - datetime.timedelta(days=1)), g.user["id"]))
    return render_template("student/clubs.html", events=events)


@bp.route("/goals", methods=["GET", "POST"])
@student_only
def goals():
    db = get_db()
    if request.method == "POST":
        learning.toggle_task(db, utils.to_int(request.form.get("task_id")), g.user["id"])
        return redirect(url_for("student.goals"))
    return render_template("student/goals.html", goals=learning.goals_for(db, g.user["id"]))


@bp.route("/certificates")
@student_only
def certificates():
    db = get_db()
    rows = db.all("SELECT * FROM certificates WHERE student_id=? ORDER BY id DESC", (g.user["id"],))
    return render_template("student/certificates.html", rows=rows)


@bp.route("/ai", methods=["GET", "POST"])
@student_only
def ai():
    db = get_db()
    answer = question = None
    if request.method == "POST":
        question = request.form.get("question")
        answer = ai_assistant.ask(db, g.user, question)
    return render_template("student/ai.html", enabled=ai_assistant.available(), answer=answer, question=question)
