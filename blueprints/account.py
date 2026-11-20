"""Shaxsiy sozlamalar, bildirishnomalar, xabarlar, seanslar, Telegram bog'lash."""
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from .. import security, utils, web
from ..db import get_db
from ..security import login_required
from ..services import access, audit, messaging, notifications, users
from ..utils import AppError

bp = Blueprint("account", __name__, url_prefix="/account")


@bp.route("/notifications", endpoint="notifications")
@login_required
def notifications_page():
    db = get_db()
    items = notifications.list_for(db, g.user["id"], g.lang)
    return render_template("account/notifications.html", items=items)


@bp.route("/notifications/read", methods=["POST"])
@login_required
def notifications_read():
    db = get_db()
    nid = utils.to_int(request.form.get("id"))
    if nid:
        db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (nid, g.user["id"]))
        row = db.one("SELECT link FROM notifications WHERE id=? AND user_id=?", (nid, g.user["id"]))
        if row and row["link"] and row["link"].startswith("/") and not row["link"].startswith("//"):
            return redirect(row["link"])
    else:
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return redirect(url_for("account.notifications"))


@bp.route("/messages")
@login_required
def messages():
    db = get_db()
    return render_template("account/messages.html", convs=messaging.conversations(db, g.user["id"]),
                           contacts=messaging.contacts(db, g.user), user_name=access.user_name)


@bp.route("/messages/<int:other_id>", methods=["GET", "POST"])
@login_required
def thread(other_id):
    db = get_db()
    other = db.one("SELECT id, first_name, last_name, username, role FROM users WHERE id=?", (other_id,))
    if other is None:
        abort(404)
    if request.method == "POST":
        messaging.send(db, g.user, other_id, request.form.get("body"))
        return redirect(url_for("account.thread", other_id=other_id))
    if not access.can_message(db, g.user, other_id) and not db.one(
            "SELECT 1 FROM messages WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)",
            (g.user["id"], other_id, other_id, g.user["id"])):
        abort(403)
    items = messaging.thread(db, g.user["id"], other_id)
    return render_template("account/thread.html", other=other, other_name=access.user_name(other), items=items,
                           can_reply=access.can_message(db, g.user, other_id))


@bp.route("/settings")
@login_required
def settings():
    db = get_db()
    uid = g.user["id"]
    prefs = {r["kind"]: r for r in db.all("SELECT * FROM notification_prefs WHERE user_id=?", (uid,))}
    sessions = db.all("SELECT * FROM user_sessions WHERE user_id=? AND revoked=0 ORDER BY last_seen DESC", (uid,))
    return render_template(
        "account/settings.html", prefs=prefs, kinds=notifications.KINDS, sessions=sessions,
        current_session=g.session_row_id, bot_username=current_app.config.get("TELEGRAM_BOT_USERNAME"),
        bot_enabled=bool(current_app.config.get("TELEGRAM_BOT_TOKEN")),
        link_code=request.args.get("code"))


@bp.route("/settings/profile", methods=["POST"])
@login_required
def save_profile():
    db = get_db()
    form = request.form
    phone = users.clean_phone(form.get("phone"))
    email = users.clean_email(form.get("email"))
    birth = utils.parse_date(form.get("birth_date"))
    if form.get("birth_date") and (birth is None or birth > utils.local_today()):
        raise AppError("bad_date")
    if email and db.one("SELECT 1 FROM users WHERE email=? AND id<>?", (email, g.user["id"])):
        raise AppError("email_taken")
    avatar = g.user["avatar"]
    upload = request.files.get("avatar")
    if upload is not None and upload.filename:
        avatar, _kind = web.save_upload(upload, "avatar", {"image"})
    email_changed = (email or None) != (g.user["email"] or None)
    db.execute(
        "UPDATE users SET first_name=?, last_name=?, phone=?, email=?, birth_date=?, avatar=?, "
        "email_verified=CASE WHEN ? THEN 0 ELSE email_verified END WHERE id=?",
        (utils.clean_text(form.get("first_name"), 60), utils.clean_text(form.get("last_name"), 60), phone, email,
         birth.isoformat() if birth else None, avatar, 1 if email_changed else 0, g.user["id"]))
    web.flash_t("msg.saved")
    return redirect(url_for("account.settings"))


@bp.route("/settings/preferences", methods=["POST"])
@login_required
def save_preferences():
    db = get_db()
    form = request.form
    lang = form.get("lang")
    theme = form.get("theme")
    from .. import i18n
    if lang in i18n.LANG_CODES:
        db.execute("UPDATE users SET lang=? WHERE id=?", (lang, g.user["id"]))
    if theme in ("light", "dark", "system"):
        db.execute("UPDATE users SET theme=? WHERE id=?", (theme, g.user["id"]))
    minutes = sorted({m for m in (utils.to_int(x.strip()) for x in (form.get("reminders") or "").split(",")) if m and 1 <= m <= 10080},
                     reverse=True)[:5]
    if form.get("reminders") is not None:
        db.execute("UPDATE users SET reminder_minutes=? WHERE id=?", (",".join(str(m) for m in minutes), g.user["id"]))
    with db.tx():
        for kind in notifications.KINDS:
            in_app = 1 if form.get("app_" + kind) else 0
            tg = 1 if form.get("tg_" + kind) else 0
            db.execute("INSERT OR REPLACE INTO notification_prefs (user_id, kind, in_app, telegram) VALUES (?,?,?,?)",
                       (g.user["id"], kind, in_app, tg))
    web.flash_t("msg.saved")
    return redirect(url_for("account.settings"))


@bp.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    db = get_db()
    form = request.form
    if not security.verify_password(g.user["password_hash"], form.get("current") or ""):
        raise AppError("bad_credentials")
    if form.get("password") != form.get("password2"):
        raise AppError("password_mismatch")
    problem = security.password_problem(form.get("password"))
    if problem:
        raise AppError(problem)
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (security.hash_password(form.get("password")), g.user["id"]))
    security.revoke_user_sessions(g.user["id"], except_row_id=g.session_row_id)
    audit.record(db, g.user["id"], "password.change", "user", g.user["id"])
    web.flash_t("msg.password_changed")
    return redirect(url_for("account.settings"))


@bp.route("/settings/sessions/revoke", methods=["POST"])
@login_required
def revoke_sessions():
    sid = utils.to_int(request.form.get("id"))
    db = get_db()
    if sid:
        if sid != g.session_row_id:
            db.execute("UPDATE user_sessions SET revoked=1 WHERE id=? AND user_id=?", (sid, g.user["id"]))
    else:
        security.revoke_user_sessions(g.user["id"], except_row_id=g.session_row_id)
    web.flash_t("msg.saved")
    return redirect(url_for("account.settings"))


@bp.route("/settings/telegram", methods=["POST"])
@login_required
def telegram_link():
    db = get_db()
    action = request.form.get("action")
    if action == "unlink":
        db.execute("UPDATE users SET telegram_chat_id=NULL WHERE id=?", (g.user["id"],))
        web.flash_t("msg.saved")
        return redirect(url_for("account.settings"))
    if not current_app.config.get("TELEGRAM_BOT_TOKEN"):
        raise AppError("bot_disabled")
    code = users.new_link_code(db, g.user["id"])
    return redirect(url_for("account.settings", code=code))


@bp.route("/settings/verify-email", methods=["POST"])
@login_required
def send_verification():
    db = get_db()
    if not g.user["email"]:
        raise AppError("bad_email")
    token = security.make_token(db, g.user["id"], "verify_email", 24)
    from .. import i18n
    link = current_app.config["BASE_URL"] + url_for("auth.verify_email", token=token)
    notifications.queue_email(db, g.user["email"], i18n.t("mail.verify_subject"),
                              i18n.t("mail.link_body", link=link, hours=24))
    notifications.flush_outbox(db)
    web.flash_t("msg.verify_sent")
    return redirect(url_for("account.settings"))
