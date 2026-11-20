"""Reyting jadvallari, o'quvchi xulosasi va administrator hisobotlari."""
import datetime

from .. import utils
from . import access, attendance, battle, finance, homework, points, settings


def _student_ids(db, subject_id=None, group_id=None):
    if group_id:
        rows = db.all("SELECT u.id FROM users u JOIN group_students gs ON gs.student_id=u.id "
                      "WHERE gs.group_id=? AND u.is_active=1", (group_id,))
    elif subject_id:
        rows = db.all("SELECT DISTINCT u.id FROM users u JOIN group_students gs ON gs.student_id=u.id "
                      "JOIN groups g ON g.id=gs.group_id JOIN courses c ON c.id=g.course_id "
                      "WHERE c.subject_id=? AND u.is_active=1", (subject_id,))
    else:
        rows = db.all("SELECT id FROM users WHERE role='student' AND is_active=1")
    return [r["id"] for r in rows]


def best_battle_rating(db, student_id, subject_id=None):
    if subject_id:
        return battle.rating_value(db, student_id, subject_id)
    row = db.one("SELECT MAX(rating) AS r FROM battle_ratings WHERE student_id=?", (student_id,))
    return row["r"] if row and row["r"] else int(settings.get(db, "battle.initial_rating"))


def composite_score(db, student_id, subject_id=None):
    """Umumiy ball faqat XP/Coin emas: bilim (XP), intizom (davomat, uy vazifasi) va musobaqa (Battle)."""
    w = settings.get(db, "rating.weights")
    st = points.stats(db, student_id)
    att = attendance.stats(db, student_id)["pct"] or 0
    hw = homework.student_stats(db, student_id)["pct"] or 0
    base = int(settings.get(db, "battle.initial_rating"))
    battle_part = max(0, best_battle_rating(db, student_id, subject_id) - base)
    return round(w["xp"] * st["xp"] + w["attendance"] * att + w["homework"] * hw + w["battle"] * battle_part, 1)


def week_bounds(today):
    monday = today - datetime.timedelta(days=today.weekday())
    return monday, monday + datetime.timedelta(days=7)


def month_bounds(today):
    first = today.replace(day=1)
    nxt = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return first, nxt


def leaderboard(db, scope="overall", subject_id=None, group_id=None, limit=50):
    """scope: overall | subject | group | weekly | monthly | battle. Saralangan ro'yxat (rank bilan)."""
    ids = _student_ids(db, subject_id if scope in ("subject", "battle", "weekly", "monthly") else None,
                       group_id if scope == "group" else None)
    users = {}
    if ids:
        marks = ",".join("?" * len(ids))
        users = {r["id"]: r for r in db.all("SELECT * FROM users WHERE id IN (%s)" % marks, ids)}
    today = utils.local_today()
    rows = []
    for sid in ids:
        st = points.stats(db, sid)
        item = {"id": sid, "name": access.user_name(users[sid]), "xp": st["xp"], "coins": st["coins"]}
        if scope == "weekly" or scope == "monthly":
            a, b = week_bounds(today) if scope == "weekly" else month_bounds(today)
            start, _ = utils.local_day_bounds(a)
            end, _ = utils.local_day_bounds(b)
            item["value"] = points.sum_between(db, sid, "xp", start, end)
        elif scope == "battle":
            rt = battle.get_rating_readonly(db, sid, subject_id) if subject_id else None
            item["value"] = rt["rating"] if rt else best_battle_rating(db, sid)
            if rt:
                item["extra"] = "%d-%d-%d" % (rt["wins"], rt["losses"], rt["draws"])
                item["league"] = battle.league(db, rt["rating"])
        else:
            item["value"] = composite_score(db, sid, subject_id if scope == "subject" else None)
        rows.append(item)
    rows.sort(key=lambda r: (-r["value"], -r["xp"], r["name"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows[:limit] if limit else rows


def position(rows, user_id):
    for row in rows:
        if row["id"] == user_id:
            return row["rank"]
    return None


def student_summary(db, student_id):
    st = points.stats(db, student_id)
    overall = leaderboard(db, "overall", limit=0)
    return {
        "xp": st["xp"], "coins": st["coins"],
        "rank": position(overall, student_id), "ranked_total": len(overall),
        "attendance": attendance.stats(db, student_id),
        "homework": homework.student_stats(db, student_id),
    }


def admin_dashboard(db):
    now = utils.now()
    month_ago = utils.fmt(now - datetime.timedelta(days=30))
    week_ago = utils.fmt(now - datetime.timedelta(days=7))
    local = utils.to_local(now)
    data = {
        "students": db.scalar("SELECT COUNT(*) FROM users WHERE role='student'", (), 0),
        "active_students": db.scalar("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE created_at>=?",
                                     (month_ago,), 0),
        "teachers": db.scalar("SELECT COUNT(*) FROM users WHERE role='teacher' AND is_active=1", (), 0),
        "groups": db.scalar("SELECT COUNT(*) FROM groups WHERE is_active=1", (), 0),
        "new_users": db.scalar("SELECT COUNT(*) FROM users WHERE created_at>=?", (week_ago,), 0),
        "battles_week": db.scalar("SELECT COUNT(*) FROM battles WHERE status='finished' AND finished_at>=?",
                                  (week_ago,), 0),
        "pending_disputes": db.scalar("SELECT COUNT(*) FROM disputes WHERE status='open'", (), 0),
        "pending_absences": db.scalar("SELECT COUNT(*) FROM absence_requests WHERE status='pending'", (), 0),
        "pending_orders": db.scalar("SELECT COUNT(*) FROM shop_orders WHERE status='pending'", (), 0),
        "suspicious": db.scalar("SELECT COUNT(*) FROM battles WHERE suspicious IS NOT NULL", (), 0),
        "by_subject": db.all(
            "SELECT s.id, s.name, s.code, COUNT(DISTINCT gs.student_id) AS students FROM subjects s "
            "LEFT JOIN courses c ON c.subject_id=s.id LEFT JOIN groups g ON g.course_id=c.id "
            "LEFT JOIN group_students gs ON gs.group_id=g.id GROUP BY s.id ORDER BY s.id"),
        "finance": finance.month_summary(db, local.year, local.month),
        "top": leaderboard(db, "overall", limit=5),
    }
    att = db.one("SELECT SUM(status IN ('present','late')) AS ok, SUM(status='absent') AS bad, COUNT(*) AS total "
                 "FROM attendance WHERE created_at>=?", (month_ago,))
    attended, absent = (att["ok"] or 0), (att["bad"] or 0)
    data["attendance_pct"] = round(100.0 * attended / (attended + absent)) if (attended + absent) else None
    hw_total = db.scalar("SELECT COUNT(*) FROM submissions WHERE submitted_at>=?", (month_ago,), 0)
    hw_miss = db.scalar("SELECT COUNT(*) FROM homework_misses WHERE created_at>=? AND action<>'excused'",
                        (month_ago,), 0)
    data["homework_pct"] = round(100.0 * hw_total / (hw_total + hw_miss)) if (hw_total + hw_miss) else None
    return data
