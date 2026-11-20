"""Ota-onalar kabineti: faqat o'z farzandlarining ma'lumotlari."""
import datetime

from flask import Blueprint, abort, g, render_template, request

from .. import utils
from ..db import get_db
from ..security import roles_required
from ..services import access, analytics, battle, finance, learning, notifications, schedule

bp = Blueprint("parent", __name__, url_prefix="/parent")
parent_only = roles_required("parent")


@bp.route("/")
@parent_only
def dashboard():
    db = get_db()
    kids = access.parent_child_ids(db, g.user["id"])
    children = []
    for kid in kids:
        user = db.one("SELECT * FROM users WHERE id=?", (kid,))
        summary = analytics.student_summary(db, kid)
        pay = finance.student_payments(db, kid)
        children.append({"user": user, "name": access.user_name(user), "summary": summary, "debt": pay["debt"]})
    selected = utils.to_int(request.args.get("child"))
    if selected not in kids:
        selected = kids[0] if kids else None
    detail = _detail(db, selected) if selected else None
    return render_template("parent/dashboard.html", children=children, selected=selected, d=detail)


def _detail(db, kid):
    if kid not in access.parent_child_ids(db, g.user["id"]):
        abort(403)
    user = db.one("SELECT * FROM users WHERE id=?", (kid,))
    now = utils.now()
    lessons = schedule.student_lessons(db, kid, now - datetime.timedelta(hours=3), now + datetime.timedelta(days=14))
    att = db.all("SELECT a.*, l.starts_at, g.name AS group_name FROM attendance a JOIN lessons l ON l.id=a.lesson_id "
                 "JOIN groups g ON g.id=l.group_id WHERE a.student_id=? ORDER BY l.starts_at DESC LIMIT 30", (kid,))
    hw = db.all(
        "SELECT h.title, h.due_at, s.status AS sub_status, s.score, s.feedback, m.action AS miss_action "
        "FROM homework h JOIN group_students gs ON gs.group_id=h.group_id "
        "LEFT JOIN submissions s ON s.homework_id=h.id AND s.student_id=gs.student_id "
        "LEFT JOIN homework_misses m ON m.homework_id=h.id AND m.student_id=gs.student_id "
        "WHERE gs.student_id=? ORDER BY h.due_at DESC LIMIT 20", (kid,))
    exams = db.all("SELECT e.*, s.name AS subject_name, s.code AS subject_code FROM exam_results e "
                   "JOIN subjects s ON s.id=e.subject_id WHERE e.student_id=? ORDER BY e.taken_on DESC LIMIT 20", (kid,))
    penalties = db.all("SELECT * FROM points_log WHERE student_id=? AND amount<0 AND reason_code<>'revoked' "
                       "ORDER BY id DESC LIMIT 20", (kid,))
    comments = db.all("SELECT s.feedback, s.checked_at, h.title FROM submissions s JOIN homework h ON h.id=s.homework_id "
                      "WHERE s.student_id=? AND s.feedback IS NOT NULL AND s.feedback<>'' ORDER BY s.checked_at DESC LIMIT 10", (kid,))
    subjects = access.student_subjects(db, kid)
    skills = {s["id"]: learning.skills_overview(db, kid, s["id"]) for s in subjects}
    battle_info = [{"subject": s, "rating": battle.get_rating_readonly(db, kid, s["id"])} for s in subjects]
    return {"user": user, "name": access.user_name(user), "summary": analytics.student_summary(db, kid), "lessons": lessons,
            "attendance": att, "homework": hw, "exams": exams, "penalties": penalties, "comments": comments,
            "payments": finance.student_payments(db, kid), "skills": skills, "subjects": subjects,
            "battle": battle_info, "goals": learning.goals_for(db, kid),
            "notices": [{"text": notifications.render(n["kind"], utils.jloads(n["payload"], {}), g.lang),
                         "created_at": n["created_at"]}
                        for n in db.all("SELECT * FROM notifications WHERE user_id=? AND kind='announcement' "
                                        "ORDER BY id DESC LIMIT 5", (g.user["id"],))]}
