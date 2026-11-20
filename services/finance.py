"""To'lovlar, qarzdorlik, daromad va xarajatlar."""
import datetime

from .. import utils
from ..utils import AppError
from . import audit, notifications


def create_payment(db, student_id, course_id, amount, due_date, discount, note, actor):
    amount, discount = utils.to_int(amount), utils.to_int(discount, 0)
    if amount is None or amount < 0 or discount < 0 or discount > amount:
        raise AppError("bad_amount")
    due = utils.parse_date(due_date)
    if due is None:
        raise AppError("bad_date")
    pid = db.insert(
        "INSERT INTO payments (student_id, course_id, amount, discount, due_date, note, recorded_by) "
        "VALUES (?,?,?,?,?,?,?)", (student_id, course_id, amount, discount, due.isoformat(),
                                   utils.clean_text(note, 200), actor["id"]))
    audit.record(db, actor["id"], "payment.create", "payment", pid, {"amount": amount})
    return pid


def net(row):
    return row["amount"] - row["discount"]


def paid_total(db, payment_id):
    return db.scalar("SELECT COALESCE(SUM(amount),0) FROM payment_transactions WHERE payment_id=? AND voided=0",
                     (payment_id,), 0)


def add_transaction(db, payment_id, amount, actor, method="manual", external_id=None):
    """To'lovga (qisman yoki to'liq) pul qabul qilish.

    * Yig'indi to'lov summasidan oshmaydi (ortiqcha to'lov rad etiladi);
    * (method, external_id) takrorlanmaydi — takroriy webhook ikkinchi marta hisoblanmaydi (idempotent);
    * hammasi bitta tranzaksiyada — parallel so'rovlarda ham qoldiq buzilmaydi.
    Qaytaradi: (transaction_id, created_bool).
    """
    amount = utils.to_int(amount)
    if amount is None or amount <= 0:
        raise AppError("bad_amount")
    with db.tx():
        if external_id:
            dup = db.one("SELECT id FROM payment_transactions WHERE method=? AND external_id=?", (method, external_id))
            if dup is not None:
                return dup["id"], False
        row = db.one("SELECT * FROM payments WHERE id=?", (payment_id,))
        if row is None:
            raise AppError("not_found")
        if row["status"] != "pending":
            raise AppError("bad_state")
        remaining = net(row) - paid_total(db, payment_id)
        if amount > remaining:
            raise AppError("overpay")
        now = utils.fmt(utils.now())
        txn = db.insert("INSERT INTO payment_transactions (payment_id, amount, method, external_id, created_at, "
                        "recorded_by) VALUES (?,?,?,?,?,?)",
                        (payment_id, amount, method, external_id, now, actor["id"] if actor else None))
        settled = amount == remaining
        if settled:
            db.execute("UPDATE payments SET status='paid', paid_at=?, method=?, external_id=? WHERE id=?",
                       (now, method, external_id, payment_id))
        audit.record(db, actor["id"] if actor else None, "payment.paid" if settled else "payment.partial",
                     "payment", payment_id, {"amount": amount, "method": method, "txn": external_id})
    payload = {"amount": amount, "date": utils.to_local(utils.now()).strftime("%d.%m.%Y")}
    notifications.notify(db, row["student_id"], "payment_paid", payload, link="/student")
    notifications.notify_parents(db, row["student_id"], "payment_paid", payload)
    return txn, True


def mark_paid(db, payment_id, actor, method="manual", external_id=None):
    """Qolgan summaning hammasini to'langan deb qayd etadi."""
    row = db.one("SELECT * FROM payments WHERE id=?", (payment_id,))
    if row is None:
        raise AppError("not_found")
    if row["status"] != "pending":
        raise AppError("bad_state")
    remaining = net(row) - paid_total(db, payment_id)
    if remaining <= 0:                       # narxi 0 (to'liq chegirma): tranzaksiyasiz yopiladi
        if db.execute("UPDATE payments SET status='paid', paid_at=?, method=? WHERE id=? AND status='pending'",
                      (utils.fmt(utils.now()), method, payment_id)).rowcount != 1:
            raise AppError("bad_state")
        audit.record(db, actor["id"] if actor else None, "payment.paid", "payment", payment_id)
        return None
    return add_transaction(db, payment_id, remaining, actor, method, external_id)[0]


def cancel_payment(db, payment_id, actor):
    """To'lovni bekor qiladi (to'langan bo'lsa ham): tranzaksiyalar 'bekor' belgilanadi va daromaddan chiqadi."""
    with db.tx():
        if db.execute("UPDATE payments SET status='cancelled' WHERE id=? AND status<>'cancelled'",
                      (payment_id,)).rowcount != 1:
            raise AppError("bad_state")
        db.execute("UPDATE payment_transactions SET voided=1 WHERE payment_id=?", (payment_id,))
        audit.record(db, actor["id"], "payment.cancel", "payment", payment_id)


def student_payments(db, student_id):
    today = utils.local_today().isoformat()
    items = []
    for row in db.all("SELECT p.*, c.name AS course_name FROM payments p LEFT JOIN courses c ON c.id=p.course_id "
                      "WHERE p.student_id=? ORDER BY p.due_date DESC, p.id DESC", (student_id,)):
        paid = paid_total(db, row["id"]) if row["status"] != "cancelled" else 0
        remaining = max(net(row) - paid, 0) if row["status"] == "pending" else 0
        overdue = row["status"] == "pending" and row["due_date"] < today and remaining > 0
        items.append({"row": row, "net": net(row), "paid": paid, "remaining": remaining, "overdue": overdue})
    debt = sum(i["remaining"] for i in items if i["overdue"])
    return {"items": items, "debt": debt,
            "total_paid": sum(i["paid"] for i in items), "total_remaining": sum(i["remaining"] for i in items)}


def add_expense(db, title, category, amount, spent_on, actor):
    amount = utils.to_int(amount)
    day = utils.parse_date(spent_on)
    title = utils.clean_text(title, 150)
    if not title:
        raise AppError("title_required")
    if amount is None or amount < 0:
        raise AppError("bad_amount")
    if day is None:
        raise AppError("bad_date")
    return db.insert("INSERT INTO expenses (title, category, amount, spent_on, created_by) VALUES (?,?,?,?,?)",
                     (title, utils.clean_text(category, 60), amount, day.isoformat(), actor["id"]))


def _bounds(start, end):
    s_utc, _ = utils.local_day_bounds(start)
    e_utc, _ = utils.local_day_bounds(end)
    return s_utc, e_utc


def period_summary(db, start, end):
    """[start, end) oralig'i uchun: kelgan pul (bekor qilinmagan tranzaksiyalar), xarajat, foyda."""
    s_utc, e_utc = _bounds(start, end)
    income = db.scalar("SELECT COALESCE(SUM(amount),0) FROM payment_transactions WHERE voided=0 "
                       "AND created_at>=? AND created_at<?", (s_utc, e_utc), 0)
    spent = db.scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE spent_on>=? AND spent_on<?",
                      (start.isoformat(), end.isoformat()), 0)
    return {"income": income, "expenses": spent, "profit": income - spent}


def month_summary(db, year, month):
    start = datetime.date(year, month, 1)
    end = datetime.date(year + (month == 12), (month % 12) + 1, 1)
    result = period_summary(db, start, end)
    s_utc, e_utc = _bounds(start, end)
    today = utils.local_today().isoformat()
    result["debt"] = db.scalar(
        "SELECT COALESCE(SUM(p.amount - p.discount - COALESCE((SELECT SUM(t.amount) FROM payment_transactions t "
        "WHERE t.payment_id=p.id AND t.voided=0),0)),0) FROM payments p WHERE p.status='pending' AND p.due_date<?",
        (today,), 0)
    # har bir tranzaksiya faqat bir guruhga yoziladi (o'quvchi bir nechta guruhda bo'lsa ham ikki marta sanalmaydi)
    result["by_group"] = db.all(
        "SELECT COALESCE((SELECT MIN(g.name) FROM groups g JOIN group_students gs ON gs.group_id=g.id "
        "WHERE gs.student_id=p.student_id AND g.course_id=p.course_id), '—') AS group_name, "
        "SUM(t.amount) AS total FROM payment_transactions t JOIN payments p ON p.id=t.payment_id "
        "WHERE t.voided=0 AND t.created_at>=? AND t.created_at<? GROUP BY group_name ORDER BY total DESC",
        (s_utc, e_utc))
    return result


def year_summary(db, year):
    """Yillik hisobot: 12 oy bo'yicha daromad/xarajat/foyda va jami."""
    months = []
    for month in range(1, 13):
        row = period_summary(db, datetime.date(year, month, 1),
                             datetime.date(year + (month == 12), (month % 12) + 1, 1))
        row["month"] = month
        months.append(row)
    total = {k: sum(m[k] for m in months) for k in ("income", "expenses", "profit")}
    return {"months": months, "total": total}


def due_reminders(db, now=None):
    """Muddati 3 kun ichida yoki o'tgan to'lovlar haqida bir martalik eslatma."""
    today = utils.to_local(now or utils.now()).date()
    limit = (today + datetime.timedelta(days=3)).isoformat()
    rows = db.all("SELECT * FROM payments WHERE status='pending' AND reminded=0 AND due_date<=?", (limit,))
    for row in rows:
        payload = {"amount": net(row) - paid_total(db, row["id"]), "date": utils.parse_date(row["due_date"]).strftime("%d.%m.%Y")}
        notifications.notify(db, row["student_id"], "payment_due", payload, link="/student")
        notifications.notify_parents(db, row["student_id"], "payment_due", payload)
        db.execute("UPDATE payments SET reminded=1 WHERE id=?", (row["id"],))
    return len(rows)
