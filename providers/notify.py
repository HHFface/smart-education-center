"""Xabar yetkazish kanallari: Telegram va Email.

EMAIL_PROVIDER=console  -> xabar logga yoziladi (bepul, sinash uchun)
EMAIL_PROVIDER=smtp     -> haqiqiy SMTP (Gmail ilova paroli kabi bepul variantlar yoki pullik xizmat)
"""
import logging
import smtplib
from email.message import EmailMessage

from . import telegram as tg

log = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


def send_email(config, to, subject, body):
    provider = config.get("EMAIL_PROVIDER", "console")
    if provider == "smtp":
        host = config.get("SMTP_HOST")
        if not host:
            raise DeliveryError("SMTP_HOST sozlanmagan")
        msg = EmailMessage()
        msg["From"] = config.get("SMTP_FROM") or config.get("SMTP_USER") or "noreply@localhost"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(host, config.get("SMTP_PORT", 587), timeout=15) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                    smtp.ehlo()
                except smtplib.SMTPException:
                    pass
                if config.get("SMTP_USER"):
                    smtp.login(config["SMTP_USER"], config.get("SMTP_PASSWORD", ""))
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError("smtp: %s" % exc.__class__.__name__)
        return
    log.info("[EMAIL:console] to=%s subject=%s\n%s", to, subject, body)


def deliver(app, item):
    """outbox yozuvini yetkazadi. Muvaffaqiyatsizlikda DeliveryError."""
    if item["channel"] == "telegram":
        client = tg.get_client(app)
        if client is None:
            raise DeliveryError("telegram sozlanmagan")
        markup = None
        if item["markup"]:
            import json
            markup = json.loads(item["markup"])
        try:
            client.send_message(item["target"], item["body"], markup)
        except tg.TelegramError as exc:
            raise DeliveryError(str(exc))
    elif item["channel"] == "email":
        send_email(app.config, item["target"], item["subject"] or "", item["body"])
    else:
        raise DeliveryError("noma'lum kanal")
