import datetime

import pytest

from smartedu import security, utils
from smartedu.services import attendance, homework, points, rating, settings, shop
from smartedu.utils import AppError


def _lesson_and_student(db):
    row = db.one("SELECT l.id lesson_id, gs.student_id FROM lessons l JOIN group_students gs "
                 "ON gs.group_id=l.group_id WHERE l.status<>'cancelled' AND NOT EXISTS "
                 "(SELECT 1 FROM attendance a WHERE a.lesson_id=l.id AND a.student_id=gs.student_id) "
                 "ORDER BY l.id, gs.student_id")
    assert row, "demo darsi yo'q"
    return row["lesson_id"], row["student_id"]


def _coins(db, sid):
    return points.stats(db, sid)["coins"]


@pytest.mark.parametrize("status,delta", [("present", 10), ("late", -5), ("excused", 0), ("absent", -20)])
def test_attendance_coin_rules(db, status, delta):
    lesson, sid = _lesson_and_student(db)
    before = _coins(db, sid)
    attendance.mark(db, lesson, sid, status, 1)
    assert _coins(db, sid) - before == delta


def test_attendance_remark_reverts_previous(db):
    lesson, sid = _lesson_and_student(db)
    before = _coins(db, sid)
    attendance.mark(db, lesson, sid, "absent", 1)
    attendance.mark(db, lesson, sid, "present", 1)
    assert _coins(db, sid) - before == 10
    attendance.mark(db, lesson, sid, "present", 1)          # takror — o'zgarmaydi
    assert _coins(db, sid) - before == 10


def test_attendance_bad_status_and_non_member(db, uid):
    lesson, sid = _lesson_and_student(db)
    with pytest.raises(AppError):
        attendance.mark(db, lesson, sid, "nonsense", 1)
    outsider = db.one("SELECT id FROM users WHERE role='student' AND id NOT IN "
                      "(SELECT student_id FROM group_students gs JOIN lessons l ON l.group_id=gs.group_id WHERE l.id=?)",
                      (lesson,))
    if outsider:
        with pytest.raises(AppError):
            attendance.mark(db, lesson, outsider["id"], "present", 1)


def test_points_never_negative_stats_and_revoke(db):
    _, sid = _lesson_and_student(db)
    log = points.award(db, sid, "xp", 50, "teacher_award", actor_id=1)
    xp = points.stats(db, sid)["xp"]
    points.revoke(db, log, 1, "test")
    assert points.stats(db, sid)["xp"] == xp - 50
    with pytest.raises(AppError):
        points.revoke(db, log, 1, "again")        # ikki marta bekor qilib bo'lmaydi


def test_homework_penalty_sequence(db):
    """1-marta ogohlantirish, 2-marta -10, 3-marta -20."""
    group = db.one("SELECT group_id, student_id FROM group_students ORDER BY group_id, student_id")
    teacher = db.one("SELECT id FROM users WHERE role='teacher'")["id"]
    sid = group["student_id"]
    past = utils.now() - datetime.timedelta(days=1)
    before = _coins(db, sid)
    deltas = []
    for i in range(3):
        hid = homework.create(db, group["group_id"], "HW%d" % i, "x", past, teacher)
        homework.process_overdue(db, homework_id=hid)
        deltas.append(_coins(db, sid) - before)
        before = _coins(db, sid)
    assert deltas == [0, -10, -20]
    # qayta ishlash ikkilamchi jarima bermaydi
    homework.process_overdue(db)
    assert _coins(db, sid) == before


def test_homework_submit_prevents_penalty(db):
    group = db.one("SELECT group_id, student_id FROM group_students ORDER BY group_id, student_id")
    teacher = db.one("SELECT id FROM users WHERE role='teacher'")["id"]
    sid = group["student_id"]
    due = utils.now() + datetime.timedelta(hours=1)
    hid = homework.create(db, group["group_id"], "OK", "x", due, teacher)
    homework.submit(db, hid, sid, "text", text="javob")
    before = _coins(db, sid)
    homework.process_overdue(db, homework_id=hid, now=utils.now() + datetime.timedelta(days=2))
    assert _coins(db, sid) == before


def test_shop_buy_rules(db):
    _, sid = _lesson_and_student(db)
    item = db.one("SELECT * FROM shop_items WHERE is_active=1 AND price_coins>0 ORDER BY price_coins LIMIT 1")
    assert item
    points.award(db, sid, "coin", item["price_coins"] + 5 - _coins(db, sid), "admin_adjust", actor_id=1)
    start = _coins(db, sid)
    shop.buy(db, sid, item["id"])
    assert _coins(db, sid) == start - item["price_coins"]
    poor = db.one("SELECT id FROM users WHERE role='student' AND id<>?", (sid,))["id"]
    points.award(db, poor, "coin", -_coins(db, poor), "admin_adjust", actor_id=1)
    with pytest.raises(AppError) as exc:
        shop.buy(db, poor, item["id"])
    assert exc.value.code == "not_enough_coins"
    with pytest.raises(AppError):
        shop.buy(db, sid, 999999)


def test_shop_monthly_limit(db):
    _, sid = _lesson_and_student(db)
    settings.set_value(db, "shop.monthly_limit", 1)
    item = db.one("SELECT * FROM shop_items WHERE is_active=1 ORDER BY price_coins LIMIT 1")
    points.award(db, sid, "coin", 10000, "admin_adjust", actor_id=1)
    shop.buy(db, sid, item["id"])
    with pytest.raises(AppError) as exc:
        shop.buy(db, sid, item["id"])
    assert exc.value.code in ("shop_limit", "out_of_stock")


def test_elo():
    assert rating.expected(1000, 1000) == pytest.approx(0.5)
    a, b = rating.new_ratings(1000, 1000, 1, 24, 24)
    assert (a, b) == (1012, 988)
    a2, _ = rating.new_ratings(1000, 1400, 1, 24, 24)
    assert a2 - 1000 > 12                          # kuchliroq raqibni yengish ko'proq beradi
    a3, b3 = rating.new_ratings(1000, 1000, 0.5, 24, 24)
    assert (a3, b3) == (1000, 1000)


def test_password_policy_and_hash():
    assert security.password_problem("abc") == "password_short"
    assert security.password_problem("abcdefgh") == "password_weak"
    assert security.password_problem("12345678") == "password_weak"
    assert security.password_problem("abcd1234") is None
    h = security.hash_password("abcd1234")
    assert h != "abcd1234" and security.verify_password(h, "abcd1234")
    assert not security.verify_password(h, "wrong")


def test_settings_validation(db):
    with pytest.raises(ValueError):
        settings.set_value(db, "battle.questions", "abc")
    with pytest.raises(ValueError):
        settings.set_value(db, "no.such.key", 1)
    settings.set_value(db, "battle.questions", 8)
    assert settings.get(db, "battle.questions") == 8
