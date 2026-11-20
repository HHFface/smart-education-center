import datetime
import hashlib
import hmac
import json

import pytest

from conftest import csrf_of
from smartedu import utils
from smartedu.providers import payments
from smartedu.services import finance, learning
from smartedu.utils import AppError


@pytest.fixture()
def pay(db, uid):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    sid = uid["demo_s1"]
    pid = finance.create_payment(db, sid, None, 1000, (utils.local_today() + datetime.timedelta(days=2)).isoformat(),
                                 100, "test", admin)
    return pid, sid, admin


def test_partial_then_full_payment(db, pay):
    pid, sid, admin = pay                          # to'lanadigan summa 900
    finance.add_transaction(db, pid, 300, admin, "cash")
    assert db.one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "pending"
    info = finance.student_payments(db, sid)["items"][0]
    assert (info["paid"], info["remaining"], info["net"]) == (300, 600, 900)
    with pytest.raises(AppError) as exc:
        finance.add_transaction(db, pid, 700, admin, "cash")          # ortiqcha to'lov
    assert exc.value.code == "overpay"
    finance.add_transaction(db, pid, 600, admin, "card")
    assert db.one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "paid"
    with pytest.raises(AppError):
        finance.add_transaction(db, pid, 1, admin, "cash")            # to'langan to'lovga qayta to'lov yo'q
    with pytest.raises(AppError):
        finance.mark_paid(db, pid, admin)                             # ikki marta "to'landi" emas


def test_mark_paid_pays_remaining_once(db, pay):
    pid, _, admin = pay
    finance.add_transaction(db, pid, 200, admin)
    finance.mark_paid(db, pid, admin)
    assert finance.paid_total(db, pid) == 900


def test_cancel_removes_income(db, pay):
    pid, _, admin = pay
    finance.mark_paid(db, pid, admin)
    today = utils.local_today()
    before = finance.period_summary(db, today, today + datetime.timedelta(days=1))["income"]
    assert before >= 900
    finance.cancel_payment(db, pid, admin)
    after = finance.period_summary(db, today, today + datetime.timedelta(days=1))["income"]
    assert after == before - 900
    with pytest.raises(AppError):
        finance.cancel_payment(db, pid, admin)                         # qayta bekor qilib bo'lmaydi


def test_validation(db, uid):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    for amount, disc, due in ((-1, 0, "2030-01-01"), (100, 200, "2030-01-01"), (100, 0, "garbage")):
        with pytest.raises(AppError):
            finance.create_payment(db, uid["demo_s1"], None, amount, due, disc, "", admin)


def test_debt_counts_only_overdue_remaining(db, uid):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    sid = uid["demo_s2"]
    past = (utils.local_today() - datetime.timedelta(days=3)).isoformat()
    pid = finance.create_payment(db, sid, None, 500, past, 0, "", admin)
    finance.add_transaction(db, pid, 150, admin)
    assert finance.student_payments(db, sid)["debt"] == 350
    assert finance.month_summary(db, utils.local_today().year, utils.local_today().month)["debt"] >= 350


def test_year_summary_matches_months(db, pay):
    pid, _, admin = pay
    finance.mark_paid(db, pid, admin)
    year = utils.local_today().year
    summary = finance.year_summary(db, year)
    assert len(summary["months"]) == 12
    assert summary["total"]["income"] == sum(m["income"] for m in summary["months"]) >= 900


def test_by_group_not_double_counted(db, pay):
    pid, sid, admin = pay
    finance.mark_paid(db, pid, admin)
    today = utils.local_today()
    summary = finance.month_summary(db, today.year, today.month)
    assert sum(r["total"] for r in summary["by_group"]) == summary["income"]


def test_due_reminder_once(db, pay):
    first = finance.due_reminders(db)
    assert first >= 1
    assert finance.due_reminders(db) == 0


# ---------- webhook
def _signed(app, body, secret="s3cret"):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Signature": sig, "Content-Type": "application/json"}


@pytest.fixture()
def webhook_app(app):
    app.config.update(PAYMENT_PROVIDER="webhook", PAYMENT_SECRET="s3cret")
    return app


def test_webhook_disabled_by_default(app):
    assert app.test_client().post("/payments/webhook", data=b"{}").status_code == 404


def test_webhook_signature_idempotency_overpay(webhook_app, db, pay):
    pid, _, _ = pay
    client = webhook_app.test_client()
    body = {"payment_id": pid, "transaction_id": "T-1", "amount": 400, "status": "paid"}
    raw, headers = _signed(webhook_app, body)
    bad = client.post("/payments/webhook", data=raw, headers={**headers, "X-Signature": "sha256=deadbeef"})
    assert bad.status_code == 401
    assert client.post("/payments/webhook", data=raw, headers={"Content-Type": "application/json"}).status_code == 401
    ok = client.post("/payments/webhook", data=raw, headers=headers)
    assert ok.status_code == 200 and ok.get_json() == {"ok": True, "duplicate": False}
    again = client.post("/payments/webhook", data=raw, headers=headers)          # takroriy webhook
    assert again.get_json()["duplicate"] is True
    assert finance.paid_total(db, pid) == 400                                     # ikki marta hisoblanmadi
    raw2, h2 = _signed(webhook_app, {**body, "transaction_id": "T-2", "amount": 9999})
    assert client.post("/payments/webhook", data=raw2, headers=h2).status_code == 409   # ortiqcha summa
    raw3, h3 = _signed(webhook_app, {**body, "transaction_id": "T-3", "amount": 500})
    assert client.post("/payments/webhook", data=raw3, headers=h3).status_code == 200
    assert db.one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "paid"


def test_webhook_rejects_garbage_and_wrong_secret(webhook_app, pay):
    client = webhook_app.test_client()
    raw = b"not json"
    sig = "sha256=" + hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
    assert client.post("/payments/webhook", data=raw, headers={"X-Signature": sig}).status_code == 400
    raw, headers = _signed(webhook_app, {"payment_id": pay[0], "transaction_id": "X", "amount": 5}, secret="other")
    assert client.post("/payments/webhook", data=raw, headers=headers).status_code == 401
    raw, headers = _signed(webhook_app, {"payment_id": 424242, "transaction_id": "Y", "amount": 5})
    assert client.post("/payments/webhook", data=raw, headers=headers).status_code == 404


def test_webhook_not_paid_status_ignored(webhook_app, db, pay):
    raw, headers = _signed(webhook_app, {"payment_id": pay[0], "transaction_id": "Z", "amount": 5, "status": "failed"})
    resp = webhook_app.test_client().post("/payments/webhook", data=raw, headers=headers)
    assert resp.get_json()["ignored"] is True and finance.paid_total(db, pay[0]) == 0


def test_click_payme_are_not_pretended(app):
    app.config["PAYMENT_PROVIDER"] = "click"
    provider = payments.get_provider(app.config)
    assert provider.online is False
    with pytest.raises(payments.PaymentError):
        provider.create_checkout({}, "/")
    assert app.test_client().post("/payments/webhook", data=b"{}").status_code == 404


# ---------- admin sahifasi
def test_admin_payments_page_and_actions(app, db, uid, client_for):
    admin = client_for("demo_admin")
    tok = csrf_of(admin, "/admin/payments")
    admin.post("/admin/payments", data={"_csrf": tok, "action": "create", "username": "demo_s3", "amount": "1000",
                                        "discount": "0", "due_date": utils.local_today().isoformat()})
    pid = db.one("SELECT id FROM payments WHERE student_id=? ORDER BY id DESC", (uid["demo_s3"],))["id"]
    admin.post("/admin/payments", data={"_csrf": tok, "action": "partial", "payment_id": pid, "amount": "400", "method": "cash"})
    assert finance.paid_total(db, pid) == 400
    for status in ("", "pending", "debtors", "paid", "cancelled"):
        assert admin.get("/admin/payments?status=%s&q=demo" % status).status_code == 200


def test_parent_sees_child_debt(app, db, uid, client_for):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    child = db.one("SELECT student_id FROM parent_students WHERE parent_id=?", (uid["demo_parent"],))["student_id"]
    finance.create_payment(db, child, None, 777, (utils.local_today() - datetime.timedelta(days=2)).isoformat(), 0, "", admin)
    html = client_for("demo_parent").get("/parent/").get_data(as_text=True)
    assert "777" in html.replace(" ", "").replace("\xa0", "")


# ---------- sertifikat
def test_certificate_revoke_flow(app, db, uid, client_for):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    number = learning.issue_certificate(db, uid["demo_s1"], None, "English B1", "B1", admin)
    anon = app.test_client()
    assert "✔" in anon.get("/verify/%s" % number).get_data(as_text=True)
    with pytest.raises(AppError):
        learning.revoke_certificate(db, number, "", admin)                       # sabab shart
    learning.revoke_certificate(db, number, "xato berilgan", admin)
    html = anon.get("/verify/%s" % number).get_data(as_text=True)
    assert "✔" not in html and "✖" in html
    with pytest.raises(AppError):
        learning.revoke_certificate(db, number, "yana", admin)
    n2 = learning.issue_certificate(db, uid["demo_s1"], None, "English B1", "B1", admin)
    assert n2 != number
    assert "✖" in anon.get("/verify/NOPE-123").get_data(as_text=True)
