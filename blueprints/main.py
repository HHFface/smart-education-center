"""Umumiy sahifalar: bosh sahifa, til/rejim, sertifikat tekshiruvi, tick, fayllar."""
import hmac

from flask import (Blueprint, abort, current_app, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from .. import i18n, utils, web
from ..db import get_db
from ..providers import payments
from ..security import login_required
from ..services import access, finance, scheduler
from ..utils import AppError

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if g.user is not None:
        return redirect(web.home_for(g.user))
    return render_template("common/landing.html")


@bp.route("/healthz")
def healthz():
    get_db().scalar("SELECT 1")
    return jsonify({"ok": True})


@bp.route("/lang", methods=["POST"])
def set_lang():
    code = i18n.pick_lang(request.form.get("lang"))
    if code:
        session["lang"] = code
        if g.user is not None:
            get_db().execute("UPDATE users SET lang=? WHERE id=?", (code, g.user["id"]))
    return redirect(web.back("main.index"))


@bp.route("/theme", methods=["POST"])
def set_theme():
    mode = request.form.get("theme")
    if mode in ("light", "dark", "system"):
        session["theme"] = mode
        if g.user is not None:
            get_db().execute("UPDATE users SET theme=? WHERE id=?", (mode, g.user["id"]))
    return redirect(web.back("main.index"))


@bp.route("/verify/<number>")
def verify_certificate(number):
    db = get_db()
    cert = db.one(
        "SELECT c.*, u.first_name, u.last_name, u.username, s.name AS subject_name, s.code AS subject_code "
        "FROM certificates c JOIN users u ON u.id=c.student_id LEFT JOIN subjects s ON s.id=c.subject_id "
        "WHERE c.number=?", (number.strip().upper(),))
    return render_template("common/verify.html", cert=cert, number=number,
                           holder=access.user_name(cert) if cert else None)


@bp.route("/certificate/<number>")
@login_required
def certificate(number):
    db = get_db()
    cert = db.one(
        "SELECT c.*, u.first_name, u.last_name, u.username, s.name AS subject_name, s.code AS subject_code "
        "FROM certificates c JOIN users u ON u.id=c.student_id LEFT JOIN subjects s ON s.id=c.subject_id "
        "WHERE c.number=?", (number.strip().upper(),))
    if cert is None:
        abort(404)
    if not access.can_view_student(db, g.user, cert["student_id"]):
        abort(403)
    return render_template("common/certificate.html", cert=cert, holder=access.user_name(cert),
                           verify_url=current_app.config["BASE_URL"] + url_for("main.verify_certificate",
                                                                               number=cert["number"]))


@bp.route("/internal/tick", methods=["GET", "POST"])
def tick():
    """Bepul tashqi cron uchun: /internal/tick?token=TICK_TOKEN"""
    expected = current_app.config.get("TICK_TOKEN")
    sent = request.args.get("token") or request.headers.get("X-Tick-Token") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(404)
    return jsonify({"ok": True, "result": scheduler.run(get_db())})


@bp.route("/files/<bucket>/<name>")
@login_required
def files(bucket, name):
    """Yuklangan fayllar: faqat ruxsati bor foydalanuvchilarga."""
    db = get_db()
    path = web.upload_path(bucket, name)
    if path is None:
        abort(404)
    if bucket == "avatar":
        allowed = True
    elif bucket == "absence":
        req = db.one("SELECT student_id, subject_id FROM absence_requests WHERE file_name=?", (name,))
        allowed = req is not None and (access.can_view_student(db, g.user, req["student_id"]))
    elif bucket == "hw":
        sub = db.one("SELECT student_id FROM submissions WHERE file_name=?", (name,))
        allowed = sub is not None and access.can_view_student(db, g.user, sub["student_id"])
    else:
        allowed = False
    if not allowed:
        abort(403)
    kind, _ = web.kind_of(name)
    inline = kind in web.INLINE_KINDS or kind in ("audio", "video")
    resp = send_file(path, as_attachment=not inline, conditional=True)
    resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"   # yuklangan fayl hech qachon kod bajarmaydi
    resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return resp


@bp.route("/payments/webhook", methods=["POST"])
def payment_webhook():
    """Onlayn to'lov xabari (PAYMENT_PROVIDER=webhook). Imzo + tranzaksiya raqami bilan himoyalangan, takrorlanishga chidamli."""
    provider = payments.get_provider(current_app.config)
    if getattr(provider, "name", "") != "webhook":
        abort(404)
    try:
        payment_id, txn, amount, paid = provider.verify_callback(request.get_data(), request.headers)
    except payments.PaymentError as exc:
        status = 401 if str(exc) == "bad_signature" else 400
        return jsonify({"ok": False, "error": str(exc)}), status
    if not paid:
        return jsonify({"ok": True, "ignored": True})
    try:
        _txn_id, created = finance.add_transaction(get_db(), payment_id, amount, None, method="webhook", external_id=txn)
    except AppError as exc:
        return jsonify({"ok": False, "error": exc.code}), 409 if exc.code in ("bad_state", "overpay") else 404
    return jsonify({"ok": True, "duplicate": not created})
