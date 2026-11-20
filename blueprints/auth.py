"""Kirish, ro'yxatdan o'tish, parolni tiklash, email tasdiqlash."""
import datetime

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from .. import i18n, security, utils, web
from ..db import get_db
from ..security import login_required
from ..services import audit, notifications, users
from ..utils import AppError

bp = Blueprint("auth", __name__)
# Foydalanuvchi topilmaganda ham vaqt farqi sezilmasligi uchun soxta xesh
_DUMMY_HASH = security.hash_password("dummy-password-1")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(web.home_for(g.user))
    if request.method == "POST":
        db = get_db()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = db.one("SELECT * FROM users WHERE username=?", (username,)) if username else None
        now = utils.now()
        ip = security.client_ip()
        if security.rate_limited(db, "login_fail_ip", ip, current_app.config["LOGIN_IP_LIMIT"]):
            web.flash_t("err.account_locked", "error")           # bir IP dan juda ko'p xato urinish
            return render_template("auth/login.html", username=username), 429
        if user is not None and user["locked_until"] and user["locked_until"] > utils.fmt(now):
            web.flash_t("err.account_locked", "error")
            return render_template("auth/login.html", username=username), 429
        ok = security.verify_password(user["password_hash"] if user else _DUMMY_HASH, password)
        if user is None or not ok:
            security.rate_hit(db, "login_fail_ip", ip)
            if user is not None:
                fails = user["failed_logins"] + 1
                locked = None
                if fails >= current_app.config["MAX_FAILED_LOGINS"]:
                    locked = utils.fmt(now + datetime.timedelta(minutes=current_app.config["LOCK_MINUTES"]))
                    fails = 0
                db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (fails, locked, user["id"]))
            web.flash_t("err.bad_credentials", "error")
            return render_template("auth/login.html", username=username), 401
        if not user["is_active"]:
            web.flash_t("err.account_blocked", "error")
            return render_template("auth/login.html", username=username), 403
        db.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user["id"],))
        security.create_session(user["id"])
        audit.record(db, user["id"], "login", "user", user["id"])
        target = request.args.get("next")
        if target and web.is_safe_target(target) and target.startswith("/"):
            return redirect(target)
        return redirect(web.home_for(user))
    return render_template("auth/login.html", username="")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    security.destroy_session()
    return redirect(url_for("main.index"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(web.home_for(g.user))
    db = get_db()
    if request.method == "POST":
        ip = security.client_ip()
        if security.rate_limited(db, "register_ip", ip, 20, 60):
            web.flash_t("err.too_many_requests", "error")
            return render_template("auth/register.html", form=request.form), 429
        security.rate_hit(db, "register_ip", ip)
        try:
            uid = users.register(db, request.form, g.lang)
        except AppError as exc:
            web.flash_error(exc)
            return render_template("auth/register.html", form=request.form), 400
        security.create_session(uid)
        web.flash_t("msg.welcome")
        return redirect(web.home_for(db.one("SELECT * FROM users WHERE id=?", (uid,))))
    return render_template("auth/register.html", form={"invite": request.args.get("invite", "")})


def _send_link(db, user, purpose, route, subject_key, hours):
    token = security.make_token(db, user["id"], purpose, hours)
    link = current_app.config["BASE_URL"] + url_for(route, token=token)
    body = i18n.t("mail.link_body", user["lang"], link=link, hours=hours)
    if user["email"]:
        notifications.queue_email(db, user["email"], i18n.t(subject_key, user["lang"]), body)
    if user["telegram_chat_id"]:
        notifications.queue_telegram(db, user["telegram_chat_id"], body)
    notifications.flush_outbox(db)


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        db = get_db()
        ident = (request.form.get("identifier") or "").strip()
        ip = security.client_ip()
        limited = (security.rate_limited(db, "forgot_ip", ip, 10, 60)
                   or (ident and security.rate_limited(db, "forgot_ident", ident.lower(), 3, 60)))
        if ident and not limited:
            security.rate_hit(db, "forgot_ip", ip)
            security.rate_hit(db, "forgot_ident", ident.lower())
            user = db.one("SELECT * FROM users WHERE is_active=1 AND (username=? OR email=?)", (ident, ident.lower()))
            if user is not None and (user["email"] or user["telegram_chat_id"]):
                _send_link(db, user, "reset", "auth.reset", "mail.reset_subject", 2)
        web.flash_t("msg.reset_sent")  # foydalanuvchi mavjudligini oshkor qilmaydi
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    db = get_db()
    if request.method == "POST":
        if request.form.get("password") != request.form.get("password2"):
            web.flash_t("err.password_mismatch", "error")
            return render_template("auth/reset.html", token=token), 400
        problem = security.password_problem(request.form.get("password"))
        if problem:
            web.flash_t("err.%s" % problem, "error")
            return render_template("auth/reset.html", token=token), 400
        uid = security.consume_token(db, token, "reset")
        if uid is None:
            web.flash_t("err.token_invalid", "error")
            return redirect(url_for("auth.forgot"))
        db.execute("UPDATE users SET password_hash=?, failed_logins=0, locked_until=NULL WHERE id=?",
                   (security.hash_password(request.form.get("password")), uid))
        security.revoke_user_sessions(uid)
        audit.record(db, uid, "password.reset", "user", uid)
        web.flash_t("msg.password_changed")
        return redirect(url_for("auth.login"))
    if security.consume_token(db, token, "reset", mark_used=False) is None:
        web.flash_t("err.token_invalid", "error")
        return redirect(url_for("auth.forgot"))
    return render_template("auth/reset.html", token=token)


@bp.route("/verify-email/<token>")
def verify_email(token):
    db = get_db()
    uid = security.consume_token(db, token, "verify_email")
    if uid is None:
        web.flash_t("err.token_invalid", "error")
    else:
        db.execute("UPDATE users SET email_verified=1 WHERE id=?", (uid,))
        web.flash_t("msg.email_verified")
    return redirect(url_for("main.index"))
