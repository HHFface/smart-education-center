"""Dars jadvali: guruh, xona va o'qituvchi to'qnashuvlarini tekshiradi."""
import datetime

from .. import utils
from ..utils import AppError
from . import access, audit, notifications


def find_conflicts(db, group_id, starts, ends, room, exclude_id=None):
    """starts/ends - UTC datetime. To'qnashuvlar ro'yxati: [('group'|'room'|'teacher', lesson_row)]."""
    group = db.one("SELECT * FROM groups WHERE id=?", (group_id,))
    if group is None:
        raise AppError("not_found")
    s, e = utils.fmt(starts), utils.fmt(ends)
    base = ("SELECT l.*, g.name AS group_name FROM lessons l JOIN groups g ON g.id=l.group_id "
            "WHERE l.status='planned' AND l.starts_at < ? AND l.ends_at > ?")
    args = [e, s]
    if exclude_id:
        base += " AND l.id<>?"
        args.append(exclude_id)
    found = []
    for row in db.all(base + " AND l.group_id=?", args + [group_id]):
        found.append(("group", row))
    if room:
        for row in db.all(base + " AND l.room=? AND l.group_id<>?", args + [room, group_id]):
            found.append(("room", row))
    if group["teacher_id"]:
        for row in db.all(base + " AND g.teacher_id=? AND l.group_id<>?", args + [group["teacher_id"], group_id]):
            found.append(("teacher", row))
    return found


def create_lesson(db, group_id, starts, ends, room, topic, actor):
    if ends <= starts:
        raise AppError("bad_time_range")
    group = db.one("SELECT * FROM groups WHERE id=?", (group_id,))
    if group is None:
        raise AppError("not_found")
    room = utils.clean_text(room or group["room"], 50)
    conflicts = find_conflicts(db, group_id, starts, ends, room)
    if conflicts:
        raise AppError("schedule_conflict", what=conflicts[0][0])
    lid = db.insert("INSERT INTO lessons (group_id, starts_at, ends_at, room, topic) VALUES (?,?,?,?,?)",
                    (group_id, utils.fmt(starts), utils.fmt(ends), room, utils.clean_text(topic, 150)))
    audit.record(db, actor["id"], "lesson.create", "lesson", lid)
    return lid


def create_weekly(db, group_id, weekdays, start_hhmm, duration_min, first_day, weeks, room, actor):
    """Haftalik takrorlanuvchi darslar. (yaratilgan, o'tkazib yuborilgan) sonlarini qaytaradi."""
    try:
        hour, minute = [int(x) for x in start_hhmm.split(":")]
    except (ValueError, AttributeError):
        raise AppError("bad_time_range")
    duration = utils.to_int(duration_min)
    weeks = utils.to_int(weeks)
    if not duration or not 15 <= duration <= 360 or not weeks or not 1 <= weeks <= 26 or not weekdays:
        raise AppError("bad_time_range")
    if first_day is None:
        raise AppError("bad_date")
    created = skipped = 0
    for offset in range(weeks * 7):
        day = first_day + datetime.timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        local_start = datetime.datetime(day.year, day.month, day.day, hour, minute)
        starts = local_start - utils.tz_offset()
        ends = starts + datetime.timedelta(minutes=duration)
        try:
            create_lesson(db, group_id, starts, ends, room, "", actor)
            created += 1
        except AppError as exc:
            if exc.code != "schedule_conflict":
                raise
            skipped += 1
    return created, skipped


def reschedule(db, lesson_id, starts, ends, room, actor):
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lesson_id,))
    if lesson is None:
        raise AppError("not_found")
    if ends <= starts:
        raise AppError("bad_time_range")
    conflicts = find_conflicts(db, lesson["group_id"], starts, ends, room, exclude_id=lesson_id)
    if conflicts:
        raise AppError("schedule_conflict", what=conflicts[0][0])
    db.execute("UPDATE lessons SET starts_at=?, ends_at=?, room=? WHERE id=?",
               (utils.fmt(starts), utils.fmt(ends), room, lesson_id))
    audit.record(db, actor["id"], "lesson.reschedule", "lesson", lesson_id)
    _notify_group(db, lesson["group_id"], "lesson_changed", {"time": utils.fmt_local(starts)})


def cancel_lesson(db, lesson_id, actor):
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lesson_id,))
    if lesson is None:
        raise AppError("not_found")
    db.execute("UPDATE lessons SET status='cancelled' WHERE id=?", (lesson_id,))
    audit.record(db, actor["id"], "lesson.cancel", "lesson", lesson_id)
    _notify_group(db, lesson["group_id"], "lesson_changed", {"time": utils.fmt_local(lesson["starts_at"]) + " ✖"})


def _notify_group(db, group_id, kind, payload):
    ids = [r["student_id"] for r in db.all("SELECT student_id FROM group_students WHERE group_id=?", (group_id,))]
    teacher = db.scalar("SELECT teacher_id FROM groups WHERE id=?", (group_id,))
    if teacher:
        ids.append(teacher)
    for sid in list(ids):
        for parent in db.all("SELECT parent_id FROM parent_students WHERE student_id=?", (sid,)):
            ids.append(parent["parent_id"])
    notifications.notify_many(db, ids, kind, payload, link="/student/schedule")


def student_lessons(db, student_id, start, end):
    return db.all(
        "SELECT l.*, g.name AS group_name, c.name AS course_name, s.name AS subject_name, s.code AS subject_code "
        "FROM lessons l JOIN groups g ON g.id=l.group_id JOIN courses c ON c.id=g.course_id "
        "JOIN subjects s ON s.id=c.subject_id JOIN group_students gs ON gs.group_id=g.id "
        "WHERE gs.student_id=? AND l.starts_at>=? AND l.starts_at<? ORDER BY l.starts_at",
        (student_id, utils.fmt(start), utils.fmt(end)))


def teacher_lessons(db, teacher_id, start, end):
    return db.all(
        "SELECT l.*, g.name AS group_name, c.name AS course_name FROM lessons l JOIN groups g ON g.id=l.group_id "
        "JOIN courses c ON c.id=g.course_id WHERE g.teacher_id=? AND l.starts_at>=? AND l.starts_at<? "
        "ORDER BY l.starts_at", (teacher_id, utils.fmt(start), utils.fmt(end)))
