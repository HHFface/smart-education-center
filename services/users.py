"""Foydalanuvchilar: yaratish, validatsiya, takliflar (invite)."""
import datetime
import re

from .. import i18n, security, utils
from ..utils import AppError
from . import audit, points, settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
ROLES = ("admin", "teacher", "student", "parent")


def clean_phone(value):
    value = re.sub(r"[\s\-()]", "", value or "")
    if not value:
        return None
    if not PHONE_RE.match(value):
        raise AppError("bad_phone")
    return value


def clean_email(value):
    value = (value or "").strip().lower()
    if not value:
        return None
    if not EMAIL_RE.match(value) or len(value) > 120:
        raise AppError("bad_email")
    return value


def create_user(db, username, password, role, first_name="", last_name="", phone="", email="",
                birth_date="", lang="uz", actor_id=None):
    if role not in ROLES:
        raise AppError("bad_role")
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AppError("bad_username")
    problem = security.password_problem(password)
    if problem:
        raise AppError(problem)
    phone = clean_phone(phone)
    email = clean_email(email)
    birth = utils.parse_date(birth_date)
    if birth_date and birth is None:
        raise AppError("bad_date")
    if birth and birth > utils.local_today():
        raise AppError("bad_date")
    if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise AppError("username_taken")
    if email and db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise AppError("email_taken")
    lang = lang if lang in i18n.LANG_CODES else "uz"
    uid = db.insert(
        "INSERT INTO users (username, password_hash, role, first_name, last_name, phone, email, birth_date, lang, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (username, security.hash_password(password), role, utils.clean_text(first_name, 60),
         utils.clean_text(last_name, 60), phone, email, birth.isoformat() if birth else None, lang,
         utils.fmt(utils.now())))
    if role == "student":
        points.ensure_stats(db, uid)
    audit.record(db, actor_id, "user.create", "user", uid, {"role": role})
    return uid


# ---------------------------------------------------------------- takliflar
def create_invite(db, role, creator, days=7, student_id=None, group_id=None):
    if role not in ROLES:
        raise AppError("bad_role")
    if role == "parent" and not student_id:
        raise AppError("student_required")
    code = utils.new_code(10)
    expires = utils.now() + datetime.timedelta(days=max(1, min(int(days), 60)))
    inv_id = db.insert(
        "INSERT INTO invites (code, role, student_id, group_id, created_by, expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
        (code, role, student_id, group_id, creator["id"], utils.fmt(expires), utils.fmt(utils.now())))
    audit.record(db, creator["id"], "invite.create", "invite", inv_id, {"role": role})
    return code


def find_invite(db, code):
    code = (code or "").strip().upper()
    if not code:
        return None
    row = db.one("SELECT * FROM invites WHERE code=? AND used_by IS NULL", (code,))
    if row is None or row["expires_at"] < utils.fmt(utils.now()):
        return None
    return row


def register(db, form, lang):
    """Ro'yxatdan o'tish. Taklif kodi bo'lsa shu rol, bo'lmasa (sozlama ruxsat bersa) faqat o'quvchi."""
    invite = find_invite(db, form.get("invite")) if form.get("invite") else None
    if form.get("invite") and invite is None:
        raise AppError("invite_invalid")
    if invite is None and not settings.get(db, "registration.open_for_students"):
        raise AppError("registration_closed")
    role = invite["role"] if invite else "student"
    if form.get("password") != form.get("password2"):
        raise AppError("password_mismatch")
    with db.tx():
        uid = create_user(db, form.get("username"), form.get("password"), role, form.get("first_name"),
                          form.get("last_name"), form.get("phone"), form.get("email"), form.get("birth_date"), lang)
        if invite:
            db.execute("UPDATE invites SET used_by=? WHERE id=?", (uid, invite["id"]))
            if role == "parent" and invite["student_id"]:
                db.execute("INSERT OR IGNORE INTO parent_students (parent_id, student_id) VALUES (?,?)",
                           (uid, invite["student_id"]))
            if role == "student" and invite["group_id"]:
                db.execute("INSERT OR IGNORE INTO group_students (group_id, student_id, joined_at) VALUES (?,?,?)",
                           (invite["group_id"], uid, utils.fmt(utils.now())))
    return uid


def new_link_code(db, user_id):
    """Telegram bog'lash kodi (15 daqiqa amal qiladi)."""
    code = utils.new_code(8)
    db.execute("UPDATE users SET link_code=?, link_code_expires=? WHERE id=?",
               (code, utils.fmt(utils.now() + datetime.timedelta(minutes=15)), user_id))
    return code


def link_telegram(db, code, chat_id):
    """Kod bo'yicha Telegram chat'ni hisobga bog'laydi. Foydalanuvchi qatorini qaytaradi."""
    code = (code or "").strip().upper()
    if len(code) != 8:
        return None
    row = db.one("SELECT * FROM users WHERE link_code=? AND is_active=1", (code,))
    if row is None or not row["link_code_expires"] or row["link_code_expires"] < utils.fmt(utils.now()):
        return None
    db.execute("UPDATE users SET telegram_chat_id=NULL WHERE telegram_chat_id=? AND id<>?", (str(chat_id), row["id"]))
    db.execute("UPDATE users SET telegram_chat_id=?, link_code=NULL, link_code_expires=?, phone_verified=1 WHERE id=?",
               (str(chat_id), None, row["id"]))
    return db.one("SELECT * FROM users WHERE id=?", (row["id"],))
