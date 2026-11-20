"""Bildirishnomalar markazi: sayt ichida + Telegram (outbox navbati orqali) + Email."""
import json
import logging

from flask import current_app

from .. import i18n, utils
from ..providers import notify as delivery

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5

# Foydalanuvchi sozlay oladigan bildirishnoma turlari
KINDS = [
    "homework_new", "homework_checked", "lesson_changed", "attendance_marked", "penalty_applied",
    "hw_warning", "coin_added", "xp_added", "battle_invite", "battle_counter", "battle_scheduled",
    "battle_reminder", "battle_result", "battle_declined", "battle_ready", "battle_noshow",
    "certificate_new", "payment_due", "payment_paid", "absence_new", "absence_reviewed", "message_new",
    "shop_order_status", "club_reward", "announcement",
]


def _prefs(db, user_id, kind):
    row = db.one("SELECT in_app, telegram FROM notification_prefs WHERE user_id=? AND kind=?", (user_id, kind))
    if row is None:
        return True, True
    return bool(row["in_app"]), bool(row["telegram"])


def render(kind, payload, lang):
    payload = dict(payload or {})
    code = payload.get("code")
    if code and "reason_text" not in payload:
        key = "reason.%s" % code
        payload["reason_text"] = i18n.t(key, lang) if i18n.has_key(key) else payload.get("reason", "")
    # status / natija / tur qiymatlarini foydalanuvchi tiliga o'giramiz
    for field, prefix in (("status", "status."), ("result", "bt.res."), ("kind", "bt.kind.")):
        value = payload.get(field)
        if isinstance(value, str) and i18n.has_key(prefix + value):
            payload[field] = i18n.t(prefix + value, lang)
    return i18n.t("notif.%s" % kind, lang, **payload)


def notify(db, user_id, kind, payload=None, link=None):
    """Bitta foydalanuvchiga bildirishnoma. Hech qachon istisno ko'tarmaydi."""
    try:
        user = db.one("SELECT id, lang, telegram_chat_id FROM users WHERE id=? AND is_active=1", (user_id,))
        if user is None:
            return
        in_app, telegram = _prefs(db, user_id, kind)
        payload = payload or {}
        if in_app:
            db.insert(
                "INSERT INTO notifications (user_id, kind, payload, link, created_at) VALUES (?,?,?,?,?)",
                (user_id, kind, utils.jdumps(payload), link, utils.fmt(utils.now())),
            )
        if telegram and user["telegram_chat_id"] and current_app.config.get("TELEGRAM_BOT_TOKEN"):
            queue_telegram(db, user["telegram_chat_id"], render(kind, payload, user["lang"]))
    except Exception:
        log.exception("Bildirishnoma yuborilmadi: user=%s kind=%s", user_id, kind)


def notify_many(db, user_ids, kind, payload=None, link=None):
    for uid in set(user_ids):
        notify(db, uid, kind, payload, link)


def notify_parents(db, student_id, kind, payload=None, link=None):
    rows = db.all("SELECT parent_id FROM parent_students WHERE student_id=?", (student_id,))
    notify_many(db, [r["parent_id"] for r in rows], kind, payload, link)


def queue_telegram(db, chat_id, text, markup=None):
    db.insert(
        "INSERT INTO outbox (channel, target, body, markup, created_at) VALUES ('telegram',?,?,?,?)",
        (str(chat_id), text, json.dumps(markup) if markup else None, utils.fmt(utils.now())),
    )


def queue_email(db, to, subject, body):
    db.insert(
        "INSERT INTO outbox (channel, target, subject, body, created_at) VALUES ('email',?,?,?,?)",
        (to, subject, body, utils.fmt(utils.now())),
    )


def flush_outbox(db, limit=25):
    """Navbatdagi xabarlarni yuboradi. Xatolikda qayta urinadi (MAX_ATTEMPTS gacha)."""
    sent = 0
    rows = db.all("SELECT * FROM outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,))
    for row in rows:
        try:
            delivery.deliver(current_app._get_current_object(), row)
            db.execute("UPDATE outbox SET status='sent', attempts=attempts+1 WHERE id=?", (row["id"],))
            sent += 1
        except Exception as exc:
            attempts = row["attempts"] + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
            db.execute("UPDATE outbox SET status=?, attempts=?, last_error=? WHERE id=?",
                       (status, attempts, str(exc)[:200], row["id"]))
            if status == "failed":
                log.warning("Xabar yuborilmadi (outbox #%s): %s", row["id"], exc)
    return sent


def unread_count(db, user_id):
    return db.scalar("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,), 0)


def list_for(db, user_id, lang, limit=100):
    items = []
    for row in db.all("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)):
        payload = utils.jloads(row["payload"], {})
        items.append({
            "id": row["id"], "kind": row["kind"], "is_read": row["is_read"], "link": row["link"],
            "created_at": row["created_at"], "text": render(row["kind"], payload, lang),
        })
    return items
