"""Xavfsizlik: parollar, sessiyalar, CSRF, rol tekshiruvlari, bir martalik tokenlar."""
import datetime
import hashlib
import hmac
import secrets
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import utils
from .db import get_db

MIN_PASSWORD = 8
CSRF_EXEMPT = {"bot.webhook", "main.tick", "main.payment_webhook"}   # imzo / token bilan himoyalangan


# ---------------------------------------------------------------- parollar
def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    try:
        return check_password_hash(stored_hash, password)
    except Exception:
        return False


def password_problem(password):
    """Parol yaroqsiz bo'lsa xatolik kodini qaytaradi."""
    if not password or len(password) < MIN_PASSWORD:
        return "password_short"
    if password.isdigit() or password.isalpha():
        return "password_weak"
    return None


def _digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- sessiyalar
def create_session(user_id):
    db = get_db()
    raw = secrets.token_urlsafe(32)
    now = utils.fmt(utils.now())
    ua = (request.headers.get("User-Agent") or "")[:200]
    db.insert(
        "INSERT INTO user_sessions (user_id, token_hash, user_agent, ip, created_at, last_seen) "
        "VALUES (?,?,?,?,?,?)",
        (user_id, _digest(raw), ua, client_ip(), now, now),
    )
    session.clear()
    session["uid"] = user_id
    session["sid"] = raw
    session.permanent = True
    return raw


def client_ip():
    """Mijoz IP manzili. X-Forwarded-For faqat TRUSTED_PROXIES>0 bo'lsa ishoniladi (aks holda soxtalashtirish mumkin)."""
    trusted = current_app.config.get("TRUSTED_PROXIES", 0)
    forwarded = request.headers.get("X-Forwarded-For", "")
    if trusted > 0 and forwarded:
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= trusted:
            return chain[-trusted][:45]      # o'ng tomondan: ishonchli proksi qo'shgan manzil
    return (request.remote_addr or "")[:45]


# ---------------------------------------------------------------- tezlik cheklovi
def rate_count(db, bucket, key, window_minutes):
    since = utils.fmt(utils.now() - datetime.timedelta(minutes=window_minutes))
    return db.scalar("SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND key=? AND created_at>=?",
                     (bucket, key, since), 0)


def rate_hit(db, bucket, key):
    db.execute("INSERT INTO rate_hits (bucket, key, created_at) VALUES (?,?,?)",
               (bucket, key, utils.fmt(utils.now())))
    if secrets.randbelow(50) == 0:       # ba'zan eski yozuvlarni tozalaymiz
        old = utils.fmt(utils.now() - datetime.timedelta(days=2))
        db.execute("DELETE FROM rate_hits WHERE created_at<?", (old,))


def rate_limited(db, bucket, key, limit, window_minutes=15):
    return rate_count(db, bucket, key, window_minutes) >= limit


def destroy_session():
    sid = session.get("sid")
    if sid:
        get_db().execute("UPDATE user_sessions SET revoked=1 WHERE token_hash=?", (_digest(sid),))
    session.pop("uid", None)
    session.pop("sid", None)


def load_user():
    """Har bir so'rovdan oldin: joriy foydalanuvchini aniqlaydi (g.user)."""
    g.user = None
    g.session_row_id = None
    uid, sid = session.get("uid"), session.get("sid")
    if not uid or not sid:
        return
    db = get_db()
    row = db.one(
        "SELECT s.id AS _sid, s.revoked AS _revoked, s.last_seen AS _s_seen, s.created_at AS _s_created, u.* "
        "FROM user_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=? AND s.user_id=?",
        (_digest(sid), uid),
    )
    if row is not None and not _session_fresh(row):
        db.execute("UPDATE user_sessions SET revoked=1 WHERE id=?", (row["_sid"],))
        row = None
    if row is None or row["_revoked"] or not row["is_active"]:
        session.pop("uid", None)
        session.pop("sid", None)
        return
    g.user = row
    g.session_row_id = row["_sid"]
    now = utils.now()
    last = utils.parse(row["last_seen"]) if row["last_seen"] else None
    if last is None or (now - last).total_seconds() > 60:
        stamp = utils.fmt(now)
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (stamp, uid))
        db.execute("UPDATE user_sessions SET last_seen=? WHERE id=?", (stamp, row["_sid"]))


def _session_fresh(row):
    """Server tomonida sessiya muddati: umumiy (SESSION_MAX_DAYS) va harakatsizlik (SESSION_IDLE_DAYS)."""
    now = utils.now()
    cfg = current_app.config
    created, seen = utils.parse(row["_s_created"]), utils.parse(row["_s_seen"])
    if created and (now - created).days >= cfg.get("SESSION_MAX_DAYS", 30):
        return False
    if seen and (now - seen).days >= cfg.get("SESSION_IDLE_DAYS", 14):
        return False
    return True


def revoke_user_sessions(user_id, except_row_id=None):
    db = get_db()
    if except_row_id:
        db.execute("UPDATE user_sessions SET revoked=1 WHERE user_id=? AND id<>?", (user_id, except_row_id))
    else:
        db.execute("UPDATE user_sessions SET revoked=1 WHERE user_id=?", (user_id,))


# ---------------------------------------------------------------- CSRF
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


def check_csrf():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint in CSRF_EXEMPT:
        return
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="csrf")


# ---------------------------------------------------------------- dekoratorlar
def _wants_json():
    return request.path.startswith("/battle/api") or request.is_json


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.get("user") is None:
            if _wants_json():
                return jsonify({"ok": False, "error": "auth"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if g.get("user") is None:
                if _wants_json():
                    return jsonify({"ok": False, "error": "auth"}), 401
                return redirect(url_for("auth.login", next=request.path))
            if g.user["role"] not in roles:
                if _wants_json():
                    return jsonify({"ok": False, "error": "forbidden"}), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------- bir martalik tokenlar
def make_token(db, user_id, purpose, hours=2):
    raw = secrets.token_urlsafe(32)
    expires = utils.fmt(utils.now() + datetime.timedelta(hours=hours))
    db.execute("UPDATE tokens SET used=1 WHERE user_id=? AND purpose=? AND used=0", (user_id, purpose))
    db.insert(
        "INSERT INTO tokens (user_id, purpose, token_hash, expires_at) VALUES (?,?,?,?)",
        (user_id, purpose, _digest(raw), expires),
    )
    return raw


def consume_token(db, raw, purpose, mark_used=True):
    """Tokenni tekshiradi. mark_used=True bo'lsa bir martalik: parallel so'rovlarda ham faqat bittasi o'tadi."""
    if not raw:
        return None
    row = db.one("SELECT * FROM tokens WHERE token_hash=? AND purpose=? AND used=0", (_digest(raw), purpose))
    if row is None or row["expires_at"] < utils.fmt(utils.now()):
        return None
    if mark_used:
        if db.execute("UPDATE tokens SET used=1 WHERE id=? AND used=0", (row["id"],)).rowcount != 1:
            return None
    return row["user_id"]
