"""Administrator paneli."""
import datetime
import json
import os
import sqlite3

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .. import i18n, security, utils, web
from ..db import backup_database, get_db
from ..security import roles_required
from ..services import (access, analytics, attendance, audit, battle, finance, learning, points, questions, schedule,
                        settings as settings_svc, shop, users)
from ..utils import AppError

bp = Blueprint("admin", __name__, url_prefix="/admin")
admin_only = roles_required("admin")


def _done(endpoint="admin.dashboard", **values):
    web.flash_t("msg.saved")
    return redirect(url_for(endpoint, **values))


@bp.route("/")
@admin_only
def dashboard():
    return render_template("admin/dashboard.html", d=analytics.admin_dashboard(get_db()))


# ---------------------------------------------------------------- foydalanuvchilar
@bp.route("/users", methods=["GET", "POST"], endpoint="users")
@admin_only
def users_page():
    db = get_db()
    if request.method == "POST":
        form = request.form
        uid = users.create_user(db, form.get("username"), form.get("password"), form.get("role"), form.get("first_name"),
                                form.get("last_name"), form.get("phone"), form.get("email"), form.get("birth_date"),
                                form.get("lang") or "uz", actor_id=g.user["id"])
        return _done("admin.user_detail", user_id=uid)
    role = request.args.get("role")
    q = (request.args.get("q") or "").strip()
    sql, args = "SELECT * FROM users WHERE 1=1", []
    if role in users.ROLES:
        sql += " AND role=?"
        args.append(role)
    if q:
        sql += " AND (username LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR phone LIKE ?)"
        args += ["%" + q + "%"] * 4
    rows = db.all(sql + " ORDER BY id DESC LIMIT 200", args)
    return render_template("admin/users.html", rows=rows, role=role, q=q, roles=users.ROLES, names=access.user_name)


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@admin_only
def user_detail(user_id):
    db = get_db()
    user = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    if user is None:
        abort(404)
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        if action == "profile":
            email = users.clean_email(form.get("email"))
            if email and db.one("SELECT 1 FROM users WHERE email=? AND id<>?", (email, user_id)):
                raise AppError("email_taken")
            db.execute("UPDATE users SET first_name=?, last_name=?, phone=?, email=? WHERE id=?",
                       (utils.clean_text(form.get("first_name"), 60), utils.clean_text(form.get("last_name"), 60),
                        users.clean_phone(form.get("phone")), email, user_id))
        elif action in ("block", "unblock"):
            if user_id == g.user["id"]:
                raise AppError("cannot_block_self")
            db.execute("UPDATE users SET is_active=? WHERE id=?", (1 if action == "unblock" else 0, user_id))
            if action == "block":
                security.revoke_user_sessions(user_id)
        elif action == "password":
            problem = security.password_problem(form.get("password"))
            if problem:
                raise AppError(problem)
            db.execute("UPDATE users SET password_hash=?, failed_logins=0, locked_until=NULL WHERE id=?",
                       (security.hash_password(form.get("password")), user_id))
            security.revoke_user_sessions(user_id)
        elif action == "role":
            new_role = form.get("role")
            if new_role not in users.ROLES:
                raise AppError("bad_role")
            if user_id == g.user["id"] and new_role != "admin":
                raise AppError("cannot_block_self")
            db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
            if new_role == "student":
                points.ensure_stats(db, user_id)
        elif action == "link_parent":
            child = db.one("SELECT id FROM users WHERE username=? AND role='student'", ((form.get("child") or "").strip(),))
            if user["role"] != "parent" or child is None:
                raise AppError("not_found")
            db.execute("INSERT OR IGNORE INTO parent_students (parent_id, student_id) VALUES (?,?)", (user_id, child["id"]))
        elif action == "unlink_parent":
            db.execute("DELETE FROM parent_students WHERE parent_id=? AND student_id=?",
                       (user_id, utils.to_int(form.get("student_id"))))
        elif action == "level":
            if user["role"] != "student" or form.get("level") not in battle.LEVELS:
                raise AppError("bad_level")
            db.execute("INSERT OR REPLACE INTO student_levels (student_id, subject_id, level) VALUES (?,?,?)",
                       (user_id, utils.to_int(form.get("subject_id")), form.get("level")))
        elif action == "adjust":
            kind, amount = form.get("kind"), utils.to_int(form.get("amount"))
            if kind not in ("xp", "coin") or not amount or abs(amount) > 10000:
                raise AppError("bad_amount")
            points.award(db, user_id, kind, amount, "admin_adjust", reason=utils.clean_text(form.get("reason"), 200),
                         actor_id=g.user["id"])
        audit.record(db, g.user["id"], "user." + str(action), "user", user_id)
        return _done("admin.user_detail", user_id=user_id)
    ctx = {"u": user, "name": access.user_name(user), "roles": users.ROLES}
    if user["role"] == "student":
        ctx.update(groups=db.all("SELECT g.name FROM groups g JOIN group_students gs ON gs.group_id=g.id WHERE gs.student_id=?",
                                 (user_id,)),
                   parents=db.all("SELECT u.id, u.username, u.first_name, u.last_name FROM users u JOIN parent_students ps ON ps.parent_id=u.id "
                                  "WHERE ps.student_id=?", (user_id,)),
                   summary=analytics.student_summary(db, user_id), subjects=db.all("SELECT * FROM subjects WHERE is_active=1"),
                   levels=battle.LEVELS, log=points.history(db, user_id, limit=20),
                   payments=finance.student_payments(db, user_id))
    if user["role"] == "parent":
        ctx["children"] = db.all("SELECT u.id, u.username, u.first_name, u.last_name FROM users u "
                                 "JOIN parent_students ps ON ps.student_id=u.id WHERE ps.parent_id=?", (user_id,))
    return render_template("admin/user_detail.html", names=access.user_name, **ctx)


@bp.route("/invites", methods=["GET", "POST"])
@admin_only
def invites():
    db = get_db()
    if request.method == "POST":
        form = request.form
        student_id = None
        if form.get("role") == "parent":
            kid = db.one("SELECT id FROM users WHERE username=? AND role='student'", ((form.get("child") or "").strip(),))
            if kid is None:
                raise AppError("not_found")
            student_id = kid["id"]
        group_id = utils.to_int(form.get("group_id"))
        code = users.create_invite(db, form.get("role"), g.user, utils.to_int(form.get("days"), 7), student_id, group_id)
        web.flash_t("msg.invite_created", code=code)
        return redirect(url_for("admin.invites"))
    rows = db.all("SELECT i.*, u.username AS used_name, c.username AS child_name, g.name AS group_name FROM invites i "
                  "LEFT JOIN users u ON u.id=i.used_by LEFT JOIN users c ON c.id=i.student_id LEFT JOIN groups g ON g.id=i.group_id "
                  "ORDER BY i.id DESC LIMIT 100")
    groups = db.all("SELECT id, name FROM groups WHERE is_active=1 ORDER BY name")
    return render_template("admin/invites.html", rows=rows, roles=users.ROLES, groups=groups, now=utils.fmt(utils.now()),
                           base_url=current_app.config["BASE_URL"])


# ---------------------------------------------------------------- tuzilma: fanlar, kurslar, guruhlar
@bp.route("/structure", methods=["GET", "POST"])
@admin_only
def structure():
    db = get_db()
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        if action == "subject":
            code = utils.clean_text(form.get("code"), 12).lower()
            name = utils.clean_text(form.get("name"), 60)
            if not code or not name or not code.replace("_", "").isalnum():
                raise AppError("title_required")
            if db.one("SELECT 1 FROM subjects WHERE code=?", (code,)):
                raise AppError("duplicate")
            db.insert("INSERT INTO subjects (code, name) VALUES (?,?)", (code, name))
        elif action == "course":
            price = utils.to_int(form.get("price"), 0)
            name = utils.clean_text(form.get("name"), 100)
            if not name or price < 0 or form.get("level") not in battle.LEVELS:
                raise AppError("title_required")
            db.insert("INSERT INTO courses (subject_id, name, level, price) VALUES (?,?,?,?)",
                      (utils.to_int(form.get("subject_id")), name, form.get("level"), price))
        elif action == "group":
            name = utils.clean_text(form.get("name"), 80)
            if not name:
                raise AppError("title_required")
            teacher = utils.to_int(form.get("teacher_id"))
            if teacher and db.one("SELECT 1 FROM users WHERE id=? AND role='teacher'", (teacher,)) is None:
                raise AppError("not_found")
            db.insert("INSERT INTO groups (course_id, name, teacher_id, room) VALUES (?,?,?,?)",
                      (utils.to_int(form.get("course_id")), name, teacher, utils.clean_text(form.get("room"), 40)))
        elif action == "group_edit":
            teacher = utils.to_int(form.get("teacher_id"))
            db.execute("UPDATE groups SET teacher_id=?, room=?, is_active=? WHERE id=?",
                       (teacher, utils.clean_text(form.get("room"), 40), 1 if form.get("active") else 0,
                        utils.to_int(form.get("group_id"))))
        elif action == "add_student":
            student = db.one("SELECT id FROM users WHERE username=? AND role='student'", ((form.get("username") or "").strip(),))
            if student is None:
                raise AppError("not_found")
            db.execute("INSERT OR IGNORE INTO group_students (group_id, student_id, joined_at) VALUES (?,?,?)",
                       (utils.to_int(form.get("group_id")), student["id"], utils.fmt(utils.now())))
        elif action == "remove_student":
            db.execute("DELETE FROM group_students WHERE group_id=? AND student_id=?",
                       (utils.to_int(form.get("group_id")), utils.to_int(form.get("student_id"))))
        elif action == "toggle_subject":
            db.execute("UPDATE subjects SET is_active=1-is_active WHERE id=?", (utils.to_int(form.get("subject_id")),))
        audit.record(db, g.user["id"], "structure." + str(action))
        return _done("admin.structure")
    groups = db.all("SELECT g.*, c.name AS course_name, c.subject_id, t.first_name AS tf, t.last_name AS tl, t.username AS tu "
                    "FROM groups g JOIN courses c ON c.id=g.course_id LEFT JOIN users t ON t.id=g.teacher_id ORDER BY g.is_active DESC, g.name")
    members = {gr["id"]: db.all("SELECT u.id, u.username, u.first_name, u.last_name FROM users u JOIN group_students gs ON gs.student_id=u.id "
                                "WHERE gs.group_id=? ORDER BY u.first_name", (gr["id"],)) for gr in groups}
    return render_template("admin/structure.html", subjects=db.all("SELECT * FROM subjects ORDER BY id"),
                           courses=db.all("SELECT c.*, s.name AS subject_name, s.code AS subject_code FROM courses c "
                                          "JOIN subjects s ON s.id=c.subject_id ORDER BY s.id, c.level"),
                           groups=groups, members=members, levels=battle.LEVELS, names=access.user_name,
                           teachers=db.all("SELECT * FROM users WHERE role='teacher' AND is_active=1 ORDER BY first_name"))


# ---------------------------------------------------------------- dars jadvali
@bp.route("/lessons", methods=["GET", "POST"])
@admin_only
def lessons():
    db = get_db()
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        if action == "single":
            starts = utils.parse_local_input(form.get("starts_at"))
            minutes = utils.to_int(form.get("duration"), 90)
            if starts is None:
                raise AppError("bad_date")
            schedule.create_lesson(db, utils.to_int(form.get("group_id")), starts, starts + datetime.timedelta(minutes=minutes),
                                   form.get("room"), form.get("topic"), g.user)
        elif action == "weekly":
            days = [d for d in (utils.to_int(x) for x in form.getlist("weekday")) if d is not None and 0 <= d <= 6]
            created, skipped = schedule.create_weekly(db, utils.to_int(form.get("group_id")), days, form.get("start"),
                                                      form.get("duration"), utils.parse_date(form.get("first_day")),
                                                      form.get("weeks"), form.get("room"), g.user)
            web.flash_t("msg.lessons_created", created=created, skipped=skipped)
            return redirect(url_for("admin.lessons"))
        elif action == "reschedule":
            starts = utils.parse_local_input(form.get("starts_at"))
            minutes = utils.to_int(form.get("duration"), 90)
            if starts is None:
                raise AppError("bad_date")
            schedule.reschedule(db, utils.to_int(form.get("lesson_id")), starts, starts + datetime.timedelta(minutes=minutes),
                                form.get("room"), g.user)
        elif action == "cancel":
            schedule.cancel_lesson(db, utils.to_int(form.get("lesson_id")), g.user)
        return _done("admin.lessons")
    start = utils.fmt(utils.now() - datetime.timedelta(days=1))
    rows = db.all("SELECT l.*, g.name AS group_name FROM lessons l JOIN groups g ON g.id=l.group_id "
                  "WHERE l.starts_at>=? ORDER BY l.starts_at LIMIT 150", (start,))
    return render_template("admin/lessons.html", rows=rows, groups=db.all("SELECT id, name FROM groups WHERE is_active=1 ORDER BY name"))


# ---------------------------------------------------------------- moliya
@bp.route("/payments", methods=["GET", "POST"])
@admin_only
def payments():
    db = get_db()
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        if action == "create":
            student = db.one("SELECT id FROM users WHERE username=? AND role='student'", ((form.get("username") or "").strip(),))
            if student is None:
                raise AppError("not_found")
            finance.create_payment(db, student["id"], utils.to_int(form.get("course_id")), form.get("amount"),
                                   form.get("due_date"), form.get("discount") or 0, form.get("note"), g.user)
        elif action == "paid":
            finance.mark_paid(db, utils.to_int(form.get("payment_id")), g.user, method=_pay_method(form))
        elif action == "partial":
            finance.add_transaction(db, utils.to_int(form.get("payment_id")), form.get("amount"), g.user,
                                    method=_pay_method(form))
        elif action == "cancel":
            finance.cancel_payment(db, utils.to_int(form.get("payment_id")), g.user)
        return _done("admin.payments")
    status = request.args.get("status")
    q = (request.args.get("q") or "").strip()
    paid_sql = ("COALESCE((SELECT SUM(t.amount) FROM payment_transactions t WHERE t.payment_id=p.id AND t.voided=0),0)")
    sql = ("SELECT p.*, u.first_name, u.last_name, u.username, c.name AS course_name, %s AS paid_sum FROM payments p "
           "JOIN users u ON u.id=p.student_id LEFT JOIN courses c ON c.id=p.course_id WHERE 1=1" % paid_sql)
    args = []
    if status == "debtors":
        sql += " AND p.status='pending' AND p.due_date<?"
        args.append(utils.local_today().isoformat())
    elif status in ("pending", "paid", "cancelled"):
        sql += " AND p.status=?"
        args.append(status)
    if q:
        sql += " AND (u.username LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)"
        args += ["%" + q + "%"] * 3
    rows = db.all(sql + " ORDER BY p.due_date DESC, p.id DESC LIMIT 200", args)
    local = utils.to_local(utils.now())
    return render_template("admin/payments.html", rows=rows, status=status, q=q, today=utils.local_today().isoformat(),
                           courses=db.all("SELECT * FROM courses WHERE is_active=1 ORDER BY name"), names=access.user_name,
                           summary=finance.month_summary(db, local.year, local.month), methods=PAY_METHODS)


PAY_METHODS = ("cash", "card", "transfer", "manual")


def _pay_method(form):
    method = form.get("method") or "manual"
    return method if method in PAY_METHODS else "manual"


@bp.route("/expenses", methods=["GET", "POST"])
@admin_only
def expenses():
    db = get_db()
    if request.method == "POST":
        finance.add_expense(db, request.form.get("title"), request.form.get("category"), request.form.get("amount"),
                            request.form.get("spent_on"), g.user)
        return _done("admin.expenses")
    rows = db.all("SELECT * FROM expenses ORDER BY spent_on DESC, id DESC LIMIT 100")
    return render_template("admin/expenses.html", rows=rows, today=utils.local_today().isoformat())


# ---------------------------------------------------------------- savollar, do'kon, Battle, e'tirozlar
@bp.route("/questions", methods=["GET", "POST"], endpoint="questions")
@admin_only
def questions_page():
    db = get_db()
    if request.method == "POST":
        form = request.form
        if form.get("form") == "import":
            added, errors = questions.import_json(db, form.get("json") or "", g.user["id"])
            web.flash_t("msg.imported", added=added, errors=len(errors))
            for err in errors[:5]:
                flash(err, "error")
        elif form.get("form") == "toggle":
            db.execute("UPDATE questions SET is_active=1-is_active WHERE id=?", (utils.to_int(form.get("id")),))
        else:
            questions.add_question(db, utils.to_int(form.get("subject_id")), form.get("level"), form.get("qtype"),
                                   form.get("text"), [form.get("opt%d" % i) for i in range(1, 5)], form.get("correct"),
                                   form.get("explanation"), form.get("passage"), form.get("audio_url"), None, g.user["id"])
        return _done("admin.questions")
    subject_id = utils.to_int(request.args.get("subject"))
    sql, args = ("SELECT q.*, s.code AS subject_code FROM questions q JOIN subjects s ON s.id=q.subject_id "
                 "WHERE q.challenge_id IS NULL"), []
    if subject_id:
        sql += " AND q.subject_id=?"
        args.append(subject_id)
    counts = db.all("SELECT s.code, q.level, COUNT(*) AS n FROM questions q JOIN subjects s ON s.id=q.subject_id "
                    "WHERE q.is_active=1 AND q.challenge_id IS NULL GROUP BY s.code, q.level ORDER BY s.code, q.level")
    rows = db.all(sql + " ORDER BY q.id DESC LIMIT 100", args)
    return render_template("admin/questions.html", rows=rows, counts=counts, subject_id=subject_id,
                           subjects=db.all("SELECT * FROM subjects WHERE is_active=1"), levels=battle.LEVELS,
                           qtypes=battle.QTYPES, jloads=utils.jloads)


@bp.route("/shop", methods=["GET", "POST"], endpoint="shop")
@admin_only
def shop_page():
    db = get_db()
    if request.method == "POST":
        form = request.form
        action = form.get("action")
        if action == "item":
            name, price = utils.clean_text(form.get("name"), 100), utils.to_int(form.get("price"))
            stock = utils.to_int(form.get("stock"))
            if not name or price is None or price < 0 or (stock is not None and stock < 0):
                raise AppError("bad_amount")
            db.insert("INSERT INTO shop_items (name, description, price_coins, stock) VALUES (?,?,?,?)",
                      (name, utils.clean_text(form.get("description"), 300), price, stock))
        elif action == "toggle":
            db.execute("UPDATE shop_items SET is_active=1-is_active WHERE id=?", (utils.to_int(form.get("item_id")),))
        elif action == "update":
            price, stock = utils.to_int(form.get("price")), utils.to_int(form.get("stock"))
            if price is None or price < 0 or (stock is not None and stock < 0):
                raise AppError("bad_amount")
            db.execute("UPDATE shop_items SET price_coins=?, stock=? WHERE id=?", (price, stock, utils.to_int(form.get("item_id"))))
        elif action in ("fulfilled", "cancelled"):
            shop.set_order_status(db, utils.to_int(form.get("order_id")), action, g.user)
        return _done("admin.shop")
    orders = db.all("SELECT o.*, i.name AS item_name, u.first_name, u.last_name, u.username FROM shop_orders o "
                    "JOIN shop_items i ON i.id=o.item_id JOIN users u ON u.id=o.student_id ORDER BY o.status='pending' DESC, o.id DESC LIMIT 80")
    return render_template("admin/shop.html", items=db.all("SELECT * FROM shop_items ORDER BY is_active DESC, id DESC"),
                           orders=orders, names=access.user_name)


@bp.route("/battles", methods=["GET", "POST"])
@admin_only
def battles():
    db = get_db()
    if request.method == "POST":
        bid = utils.to_int(request.form.get("battle_id"))
        if request.form.get("action") == "clear_flag":
            db.execute("UPDATE battles SET suspicious=NULL WHERE id=?", (bid,))
            audit.record(db, g.user["id"], "battle.clear_flag", "battle", bid)
        return _done("admin.battles")
    rows = db.all("SELECT b.*, s.name AS subject_name, cu.username AS cu, ou.username AS ou FROM battles b "
                  "JOIN subjects s ON s.id=b.subject_id JOIN users cu ON cu.id=b.challenger_id JOIN users ou ON ou.id=b.opponent_id "
                  "ORDER BY (b.suspicious IS NOT NULL) DESC, b.id DESC LIMIT 100")
    noshows = db.all("SELECT u.id, u.username, COUNT(*) AS n FROM battles b JOIN users u ON u.id=b.no_show_user_id "
                     "WHERE b.status IN ('no_show','void') GROUP BY u.id HAVING n>=2 ORDER BY n DESC LIMIT 20")
    return render_template("admin/battles.html", rows=rows, noshows=noshows)


@bp.route("/disputes", methods=["GET", "POST"])
@admin_only
def disputes():
    db = get_db()
    if request.method == "POST":
        did = utils.to_int(request.form.get("id"))
        row = db.one("SELECT * FROM disputes WHERE id=? AND status='open'", (did,))
        if row is None:
            raise AppError("not_found")
        decision = request.form.get("decision")
        note = utils.clean_text(request.form.get("note"), 300)
        if decision not in ("accepted", "rejected"):
            raise AppError("bad_status")
        with db.tx():
            if decision == "accepted" and row["ref_type"] == "points_log":
                try:
                    points.revoke(db, row["ref_id"], g.user["id"], note)
                except AppError as exc:
                    if exc.code != "already_revoked":
                        raise
            if decision == "accepted" and row["ref_type"] == "battle":
                db.execute("UPDATE battles SET suspicious=COALESCE(suspicious||',','')||'disputed' WHERE id=?", (row["ref_id"],))
            db.execute("UPDATE disputes SET status=?, resolved_by=?, note=?, resolved_at=? WHERE id=?",
                       (decision, g.user["id"], note, utils.fmt(utils.now()), did))
            audit.record(db, g.user["id"], "dispute." + decision, "dispute", did)
        from ..services import notifications
        notifications.notify(db, row["student_id"], "announcement",
                             {"text": i18n.t("msg.dispute_" + decision, note=note)})
        return _done("admin.disputes")
    rows = db.all("SELECT d.*, u.first_name, u.last_name, u.username FROM disputes d JOIN users u ON u.id=d.student_id "
                  "ORDER BY d.status='open' DESC, d.id DESC LIMIT 100")
    return render_template("admin/disputes.html", rows=rows, names=access.user_name)


@bp.route("/certificates", methods=["GET", "POST"])
@admin_only
def certificates():
    db = get_db()
    if request.method == "POST":
        if request.form.get("action") == "revoke":
            learning.revoke_certificate(db, request.form.get("number"), request.form.get("reason"), g.user)
            web.flash_t("msg.saved")
            return redirect(url_for("admin.certificates"))
        student = db.one("SELECT id FROM users WHERE username=? AND role='student'", ((request.form.get("username") or "").strip(),))
        if student is None:
            raise AppError("not_found")
        number = learning.issue_certificate(db, student["id"], utils.to_int(request.form.get("subject_id")),
                                            request.form.get("course"), request.form.get("level"), g.user)
        web.flash_t("msg.certificate_issued", number=number)
        return redirect(url_for("admin.certificates"))
    rows = db.all("SELECT c.*, u.first_name, u.last_name, u.username FROM certificates c JOIN users u ON u.id=c.student_id "
                  "ORDER BY c.id DESC LIMIT 100")
    return render_template("admin/certificates.html", rows=rows, subjects=db.all("SELECT * FROM subjects WHERE is_active=1"),
                           names=access.user_name, levels=battle.LEVELS)


# ---------------------------------------------------------------- hisobotlar, sozlamalar, audit, zaxira
@bp.route("/reports")
@admin_only
def reports():
    db = get_db()
    today = utils.local_today()
    start = utils.parse_date(request.args.get("from")) or (today - datetime.timedelta(days=30))
    end = utils.parse_date(request.args.get("to")) or today
    if end < start:
        start, end = end, start
    subject_id = utils.to_int(request.args.get("subject"))
    s_utc, _ = utils.local_day_bounds(start)
    e_utc, _ = utils.local_day_bounds(end + datetime.timedelta(days=1))
    sf, args = "", [s_utc, e_utc]
    if subject_id:
        sf = " AND c.subject_id=?"
        args.append(subject_id)
    att = db.all("SELECT s.code, s.name, SUM(a.status='present') AS present, SUM(a.status='late') AS late, "
                 "SUM(a.status='excused') AS excused, SUM(a.status='absent') AS absent FROM attendance a "
                 "JOIN lessons l ON l.id=a.lesson_id JOIN groups g ON g.id=l.group_id JOIN courses c ON c.id=g.course_id "
                 "JOIN subjects s ON s.id=c.subject_id WHERE l.starts_at>=? AND l.starts_at<?" + sf + " GROUP BY s.id", args)
    hw = db.all("SELECT s.code, s.name, COUNT(sb.id) AS submitted, SUM(sb.is_late) AS late, ROUND(AVG(sb.score),1) AS avg_score "
                "FROM submissions sb JOIN homework h ON h.id=sb.homework_id JOIN groups g ON g.id=h.group_id "
                "JOIN courses c ON c.id=g.course_id JOIN subjects s ON s.id=c.subject_id WHERE sb.submitted_at>=? AND sb.submitted_at<?"
                + sf + " GROUP BY s.id", args)
    bt = db.all("SELECT s.code, s.name, COUNT(*) AS games, SUM(b.suspicious IS NOT NULL) AS flagged FROM battles b "
                "JOIN subjects s ON s.id=b.subject_id WHERE b.status='finished' AND b.finished_at>=? AND b.finished_at<?"
                + (" AND b.subject_id=?" if subject_id else "") + " GROUP BY s.id", args)
    top = analytics.leaderboard(db, "subject" if subject_id else "overall", subject_id, limit=10)
    return render_template("admin/reports.html", att=att, hw=hw, bt=bt, top=top, start=start.isoformat(), end=end.isoformat(),
                           subject_id=subject_id, subjects=db.all("SELECT * FROM subjects WHERE is_active=1"),
                           d=analytics.admin_dashboard(db))


@bp.route("/settings", methods=["GET", "POST"])
@admin_only
def settings():
    db = get_db()
    if request.method == "POST":
        key = request.form.get("key")
        raw = request.form.get("value", "")
        if key not in settings_svc.DEFAULTS or key.startswith("sys."):
            raise AppError("bad_setting")
        try:
            value = json.loads(raw)
            settings_svc.validate(key, value)
        except ValueError:
            raise AppError("bad_setting")
        settings_svc.set_value(db, key, value)
        audit.record(db, g.user["id"], "settings.update", "setting", None, {"key": key})
        return _done("admin.settings")
    current = settings_svc.get_all(db)
    groups = {}
    for key in settings_svc.DEFAULTS:
        groups.setdefault(key.split(".")[0], []).append((key, json.dumps(current[key], ensure_ascii=False), type(settings_svc.DEFAULTS[key]).__name__))
    return render_template("admin/settings.html", groups=groups, cfg=current_app.config)


@bp.route("/audit", endpoint="audit")
@admin_only
def audit_page():
    db = get_db()
    rows = db.all("SELECT a.*, u.username FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 200")
    return render_template("admin/audit.html", rows=rows)


@bp.route("/backup", methods=["POST"])
@admin_only
def backup():
    """Bazaning xavfsiz nusxasi (SQLite backup API): yuklab olinadi va backups/ papkasiga ham saqlanadi."""
    db = get_db()
    name = "smartedu-%s.db" % utils.to_local(utils.now()).strftime("%Y%m%d-%H%M%S")
    path = backup_database(current_app.config["DATABASE_PATH"], os.path.join(current_app.config["BACKUP_DIR"], name))
    audit.record(db, g.user["id"], "backup.create", None, None, {"file": name})
    return send_file(path, as_attachment=True, download_name=name)


# ---------------------------------------------------------------- uzrli sabab arizalari (administrator)
@bp.route("/absences", methods=["GET", "POST"])
@admin_only
def absences():
    db = get_db()
    if request.method == "POST":
        attendance.review_absence(db, utils.to_int(request.form.get("id")), request.form.get("status"), g.user,
                                  request.form.get("note"))
        web.flash_t("msg.saved")
        return redirect(url_for("admin.absences"))
    rows = db.all(
        "SELECT a.*, u.first_name, u.last_name, u.username, s.name AS subject_name, s.code AS subject_code "
        "FROM absence_requests a JOIN users u ON u.id=a.student_id JOIN subjects s ON s.id=a.subject_id "
        "ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'info_needed' THEN 1 ELSE 2 END, a.id DESC LIMIT 200")
    return render_template("teacher/absences.html", rows=rows, names=access.user_name, action=url_for("admin.absences"))
