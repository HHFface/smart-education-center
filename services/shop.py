"""Smart Shop: Coin evaziga mukofotlar."""
from .. import utils
from ..utils import AppError
from . import audit, points, settings


def buy(db, student_id, item_id, now=None):
    now = now or utils.now()
    month_start = utils.to_local(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_utc = utils.fmt(month_start - utils.tz_offset())
    with db.tx():
        item = db.one("SELECT * FROM shop_items WHERE id=? AND is_active=1", (item_id,))
        if item is None:
            raise AppError("not_found")
        if item["stock"] is not None and item["stock"] <= 0:
            raise AppError("out_of_stock")
        bought = db.scalar("SELECT COUNT(*) FROM shop_orders WHERE student_id=? AND status<>'cancelled' "
                           "AND created_at>=?", (student_id, month_start_utc), 0)
        if bought >= int(settings.get(db, "shop.monthly_limit")):
            raise AppError("shop_limit")
        if points.stats(db, student_id)["coins"] < item["price_coins"]:
            raise AppError("not_enough_coins")
        order_id = db.insert(
            "INSERT INTO shop_orders (student_id, item_id, price, created_at) VALUES (?,?,?,?)",
            (student_id, item_id, item["price_coins"], utils.fmt(now)))
        if item["price_coins"]:
            points.award(db, student_id, "coin", -item["price_coins"], "shop_purchase", reason=item["name"],
                         ref_type="shop_order", ref_id=order_id, notify=False)
        if item["stock"] is not None:
            db.execute("UPDATE shop_items SET stock=stock-1 WHERE id=?", (item_id,))
    return order_id


def set_order_status(db, order_id, status, actor):
    from . import notifications
    if status not in ("fulfilled", "cancelled"):
        raise AppError("bad_status")
    with db.tx():
        order = db.one("SELECT o.*, i.name AS item_name FROM shop_orders o JOIN shop_items i ON i.id=o.item_id "
                       "WHERE o.id=?", (order_id,))
        if order is None:
            raise AppError("not_found")
        if order["status"] != "pending":
            raise AppError("bad_state")
        db.execute("UPDATE shop_orders SET status=?, handled_by=? WHERE id=?", (status, actor["id"], order_id))
        if status == "cancelled":
            if order["price"]:
                points.award(db, order["student_id"], "coin", order["price"], "shop_refund",
                             reason=order["item_name"], actor_id=actor["id"], ref_type="shop_order",
                             ref_id=order_id, notify=False)
            db.execute("UPDATE shop_items SET stock=stock+1 WHERE id=? AND stock IS NOT NULL", (order["item_id"],))
        audit.record(db, actor["id"], "shop.order_" + status, "shop_order", order_id)
    notifications.notify(db, order["student_id"], "shop_order_status",
                         {"item": order["item_name"], "status": status}, link="/student/shop")
