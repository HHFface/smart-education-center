"""Telegram Bot API mijozi (faqat `requests`, qo'shimcha kutubxona shart emas)."""
import logging

import requests

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token, timeout=10):
        self.token = token
        self.timeout = timeout
        self.base = "https://api.telegram.org/bot%s/" % token

    def call(self, method, _timeout=None, **params):
        try:
            resp = requests.post(self.base + method, json=params, timeout=_timeout or self.timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramError("network: %s" % exc.__class__.__name__)
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "telegram error"))[:200])
        return data.get("result")

    def send_message(self, chat_id, text, reply_markup=None):
        params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", **params)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        params = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000],
                  "disable_web_page_preview": True}
        if reply_markup:
            params["reply_markup"] = reply_markup
        try:
            return self.call("editMessageText", **params)
        except TelegramError as exc:
            if "not modified" in str(exc):
                return None
            raise

    def answer_callback_query(self, callback_id, text=None):
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text[:190]
        try:
            return self.call("answerCallbackQuery", **params)
        except TelegramError:
            return None

    def get_updates(self, offset=None, timeout=25):
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", _timeout=timeout + 10, **params)

    def set_webhook(self, url, secret=None):
        params = {"url": url, "allowed_updates": ["message", "callback_query"]}
        if secret:
            params["secret_token"] = secret
        return self.call("setWebhook", **params)

    def delete_webhook(self):
        return self.call("deleteWebhook")

    def get_me(self):
        return self.call("getMe")


def get_client(app):
    """Sozlangan Telegram mijozini qaytaradi (token yo'q bo'lsa None). Testlarda almashtiriladi."""
    client = app.extensions.get("telegram_client")
    if client is not None:
        return client
    token = app.config.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    client = TelegramClient(token)
    app.extensions["telegram_client"] = client
    return client
