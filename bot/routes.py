"""Telegram webhook (bepul hostingda ham ishlaydi) va polling ishga tushiruvchi."""
import hmac
import logging
import time

from flask import Blueprint, abort, current_app, jsonify, request

from ..providers import telegram as tg
from . import handlers

log = logging.getLogger(__name__)
bp = Blueprint("bot", __name__, url_prefix="/telegram")


@bp.route("/webhook", methods=["POST"])
def webhook():
    secret = current_app.config.get("TELEGRAM_WEBHOOK_SECRET")
    if not current_app.config.get("TELEGRAM_BOT_TOKEN"):
        abort(404)
    if secret:
        sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(sent, secret):
            abort(403)
    update = request.get_json(silent=True)
    try:
        handlers.handle_update(current_app._get_current_object(), update)
    except Exception:
        # Telegram qayta-qayta yubormasligi uchun har doim 200 qaytaramiz
        log.exception("telegram update failed")
    return jsonify({"ok": True})


def run_polling(app, once=False):
    """Mahalliy ishlatish: webhook'siz. `python manage.py bot`."""
    from ..db import get_db
    from ..services import scheduler
    client = tg.get_client(app)
    if client is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN sozlanmagan (.env)")
    with app.app_context():
        try:
            client.delete_webhook()
        except tg.TelegramError as exc:
            log.warning("deleteWebhook: %s", exc)
    offset = None
    log.info("Bot polling rejimida ishga tushdi")
    while True:
        try:
            updates = client.get_updates(offset=offset, timeout=0 if once else 25)
        except tg.TelegramError as exc:
            log.warning("getUpdates xatosi: %s", exc)
            time.sleep(3)
            continue
        for upd in updates or []:
            offset = upd["update_id"] + 1
            with app.app_context():
                try:
                    handlers.handle_update(app, upd)
                except Exception:
                    log.exception("update failed")
        with app.app_context():
            try:
                scheduler.run_if_due(get_db())
            except Exception:
                log.exception("tick failed")
        if once:
            return
