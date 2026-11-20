"""To'lov provayderlari.

* ``manual``  — administrator to'lovni qo'lda qayd etadi (to'liq bepul, standart).
* ``webhook`` — HMAC-SHA256 imzoli umumiy webhook: istalgan tashqi tizim (yoki o'zingiz yozgan Click/Payme
  adapteri) to'lovni ``POST /payments/webhook`` orqali xabar qiladi. Imzo, tranzaksiya raqami (takroriy
  so'rovdan himoya) va summa tekshiriladi.
* ``click`` / ``payme`` — HAQIQIY INTEGRATSIYA YO'Q. Ular uchun provayder bilan shartnoma, merchant ID va
  maxfiy kalitlar kerak; ularning o'z protokoli (Click: prepare/complete, Payme: JSON-RPC) alohida yoziladi.
  Hozircha bu nomlar tanlansa, onlayn to'lov o'chiq turadi va tizim ishlashda davom etadi.
"""
import hashlib
import hmac
import json


class PaymentError(Exception):
    pass


class PaymentProvider:
    name = "manual"
    online = False

    def create_checkout(self, payment_row, return_url):
        """Onlayn to'lov havolasini qaytaradi (manual rejimda yo'q)."""
        raise PaymentError("online_payments_disabled")

    def verify_callback(self, data, headers):
        """Provayder webhook'ini tekshiradi va (payment_id, paid_bool) qaytaradi."""
        raise PaymentError("online_payments_disabled")


class ManualProvider(PaymentProvider):
    name = "manual"


class SignedWebhookProvider(PaymentProvider):
    """Umumiy imzolangan webhook. Tana: {"payment_id": 12, "transaction_id": "abc", "amount": 100000, "status": "paid"}.
    Sarlavha: ``X-Signature: sha256=<hex(HMAC_SHA256(PAYMENT_SECRET, xom_tana))>``."""
    name = "webhook"
    online = True

    def __init__(self, secret):
        self.secret = secret or ""

    def sign(self, raw_body):
        return "sha256=" + hmac.new(self.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def verify_callback(self, raw_body, headers):
        """(payment_id, transaction_id, amount, paid_bool) qaytaradi yoki PaymentError ko'taradi."""
        if not self.secret:
            raise PaymentError("not_configured")
        sent = headers.get("X-Signature", "")
        if not hmac.compare_digest(sent, self.sign(raw_body)):
            raise PaymentError("bad_signature")
        try:
            data = json.loads(raw_body.decode("utf-8"))
            payment_id = int(data["payment_id"])
            amount = int(data["amount"])
            txn = str(data["transaction_id"]).strip()
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise PaymentError("bad_payload")
        if not txn or len(txn) > 100 or amount <= 0:
            raise PaymentError("bad_payload")
        return payment_id, txn, amount, str(data.get("status", "paid")).lower() == "paid"


class NotConfiguredProvider(PaymentProvider):
    def __init__(self, name):
        self.name = name


def get_provider(config):
    name = config.get("PAYMENT_PROVIDER", "manual")
    if name == "manual":
        return ManualProvider()
    if name == "webhook":
        return SignedWebhookProvider(config.get("PAYMENT_SECRET"))
    # Click/Payme: shartnoma va kalitlar kerak — haqiqiy adapter hali yozilmagan.
    return NotConfiguredProvider(name)
