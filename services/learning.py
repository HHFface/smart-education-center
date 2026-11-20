"""O'quv funksiyalari: lug'at (takrorlash), ko'nikmalar, imtihonlar, maqsadlar, klublar, sertifikatlar."""
import datetime
import secrets

from .. import utils
from ..utils import AppError
from . import access, audit, notifications, points

SKILLS = ("grammar", "vocabulary", "listening", "reading", "speaking", "writing")
EXAM_TYPES = ("internal", "IELTS", "CEFR", "TOPIK I", "TOPIK II", "TORFL")
# Leitner qutilari: qutichaga qarab keyingi takrorlash kunlari
INTERVALS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14, 6: 30}


# ---------------------------------------------------------------- lug'at
def add_word(db, subject_id, level, word, translation, pronunciation="", example="", audio_url=""):
    word, translation = utils.clean_text(word, 100), utils.clean_text(translation, 200)
    if not word or not translation:
        raise AppError("title_required")
    return db.insert(
        "INSERT INTO vocab_words (subject_id, level, word, translation, pronunciation, example, audio_url) "
        "VALUES (?,?,?,?,?,?,?)", (subject_id, level, word, translation, utils.clean_text(pronunciation, 100),
                                   utils.clean_text(example, 300), utils.clean_text(audio_url, 300)))


def next_words(db, student_id, subject_id, limit=10, now=None):
    """Avval takrorlash vaqti kelganlar, keyin yangi so'zlar."""
    now_s = utils.fmt(now or utils.now())
    due = db.all(
        "SELECT w.*, p.box FROM vocab_words w JOIN vocab_progress p ON p.word_id=w.id "
        "WHERE p.student_id=? AND w.subject_id=? AND p.next_review<=? ORDER BY p.next_review LIMIT ?",
        (student_id, subject_id, now_s, limit))
    words = list(due)
    if len(words) < limit:
        fresh = db.all(
            "SELECT w.*, 0 AS box FROM vocab_words w WHERE w.subject_id=? AND NOT EXISTS "
            "(SELECT 1 FROM vocab_progress p WHERE p.word_id=w.id AND p.student_id=?) ORDER BY w.id LIMIT ?",
            (subject_id, student_id, limit - len(words)))
        words.extend(fresh)
    return words


def answer_word(db, student_id, word_id, known, now=None):
    now = now or utils.now()
    if db.one("SELECT 1 FROM vocab_words WHERE id=?", (word_id,)) is None:
        raise AppError("not_found")
    row = db.one("SELECT * FROM vocab_progress WHERE student_id=? AND word_id=?", (student_id, word_id))
    box = row["box"] if row else 1
    box = min(6, box + 1) if known else 1
    nxt = utils.fmt(now + datetime.timedelta(days=INTERVALS[box]))
    db.execute("INSERT OR REPLACE INTO vocab_progress (student_id, word_id, box, reps, next_review) VALUES (?,?,?,?,?)",
               (student_id, word_id, box, (row["reps"] if row else 0) + 1, nxt))
    return box


def vocab_stats(db, student_id, subject_id):
    total = db.scalar("SELECT COUNT(*) FROM vocab_words WHERE subject_id=?", (subject_id,), 0)
    learned = db.scalar("SELECT COUNT(*) FROM vocab_progress p JOIN vocab_words w ON w.id=p.word_id "
                        "WHERE p.student_id=? AND w.subject_id=? AND p.box>=4", (student_id, subject_id), 0)
    started = db.scalar("SELECT COUNT(*) FROM vocab_progress p JOIN vocab_words w ON w.id=p.word_id "
                        "WHERE p.student_id=? AND w.subject_id=?", (student_id, subject_id), 0)
    return {"total": total, "learned": learned, "started": started}


# ---------------------------------------------------------------- ko'nikmalar
def current_period(now=None):
    return utils.to_local(now or utils.now()).strftime("%Y-%m")


def previous_period(period):
    year, month = int(period[:4]), int(period[5:7])
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return "%04d-%02d" % (year, month)


def record_skill(db, student_id, subject_id, skill, score, teacher, period=None, comment=""):
    if skill not in SKILLS:
        raise AppError("bad_skill")
    score = utils.to_int(score)
    if score is None or not 0 <= score <= 100:
        raise AppError("bad_score")
    period = period or current_period()
    db.execute("INSERT OR REPLACE INTO skill_scores (student_id, subject_id, skill, period, score, comment) "
               "VALUES (?,?,?,?,?,?)", (student_id, subject_id, skill, period, score, utils.clean_text(comment, 300)))


def skills_overview(db, student_id, subject_id):
    period = current_period()
    prev = previous_period(period)
    result = []
    for skill in SKILLS:
        now_row = db.one("SELECT score FROM skill_scores WHERE student_id=? AND subject_id=? AND skill=? "
                         "ORDER BY period DESC LIMIT 1", (student_id, subject_id, skill))
        prev_row = db.one("SELECT score FROM skill_scores WHERE student_id=? AND subject_id=? AND skill=? "
                          "AND period<? ORDER BY period DESC LIMIT 1", (student_id, subject_id, skill, period))
        result.append({"skill": skill, "score": now_row["score"] if now_row else None,
                       "previous": prev_row["score"] if prev_row else None})
    scored = [r for r in result if r["score"] is not None]
    weakest = min(scored, key=lambda r: r["score"])["skill"] if scored else None
    strongest = max(scored, key=lambda r: r["score"])["skill"] if scored else None
    return {"rows": result, "weakest": weakest, "strongest": strongest, "period": period, "prev": prev}


# ---------------------------------------------------------------- imtihonlar
def add_exam_result(db, student_id, subject_id, exam_type, title, score, max_score, taken_on, official, recorder, comment=""):
    if exam_type not in EXAM_TYPES:
        raise AppError("bad_exam_type")
    try:
        score, max_score = float(score), float(max_score)
    except (TypeError, ValueError):
        raise AppError("bad_score")
    if max_score <= 0 or score < 0 or score > max_score:
        raise AppError("bad_score")
    day = utils.parse_date(taken_on)
    if day is None:
        raise AppError("bad_date")
    return db.insert(
        "INSERT INTO exam_results (student_id, subject_id, exam_type, title, score, max_score, official, taken_on, "
        "comment, recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (student_id, subject_id, exam_type, utils.clean_text(title, 150), score, max_score,
         1 if (official or exam_type != "internal") else 0, day.isoformat(), utils.clean_text(comment, 300),
         recorder["id"]))


# ---------------------------------------------------------------- shaxsiy o'quv yo'li
def create_goal(db, student_id, subject_id, title, target_date, creator):
    title = utils.clean_text(title, 200)
    if not title:
        raise AppError("title_required")
    td = utils.parse_date(target_date)
    return db.insert("INSERT INTO goals (student_id, subject_id, title, target_date, created_by) VALUES (?,?,?,?,?)",
                     (student_id, subject_id, title, td.isoformat() if td else None, creator["id"]))


def add_goal_task(db, goal_id, title, due_on):
    title = utils.clean_text(title, 200)
    if not title:
        raise AppError("title_required")
    due = utils.parse_date(due_on)
    return db.insert("INSERT INTO goal_tasks (goal_id, title, due_on) VALUES (?,?,?)",
                     (goal_id, title, due.isoformat() if due else None))


def toggle_task(db, task_id, student_id):
    row = db.one("SELECT t.*, g.student_id FROM goal_tasks t JOIN goals g ON g.id=t.goal_id WHERE t.id=?", (task_id,))
    if row is None or row["student_id"] != student_id:
        raise AppError("forbidden")
    db.execute("UPDATE goal_tasks SET done=? WHERE id=?", (0 if row["done"] else 1, task_id))


def goals_for(db, student_id):
    items = []
    for goal in db.all("SELECT g.*, s.name AS subject_name FROM goals g LEFT JOIN subjects s ON s.id=g.subject_id "
                       "WHERE g.student_id=? ORDER BY g.id DESC", (student_id,)):
        tasks = db.all("SELECT * FROM goal_tasks WHERE goal_id=? ORDER BY done, due_on, id", (goal["id"],))
        done = sum(1 for t in tasks if t["done"])
        items.append({"goal": goal, "tasks": tasks, "done": done, "total": len(tasks),
                      "pct": round(100.0 * done / len(tasks)) if tasks else 0})
    return items


# ---------------------------------------------------------------- Speaking / Movie Club
def create_event(db, kind, subject_id, title, starts_at_utc, description, questions, new_words, xp, coin, creator):
    if kind not in ("speaking", "movie"):
        raise AppError("bad_kind")
    title = utils.clean_text(title, 150)
    if not title or starts_at_utc is None:
        raise AppError("title_required")
    return db.insert(
        "INSERT INTO club_events (kind, subject_id, title, starts_at, description, questions, new_words, reward_xp, "
        "reward_coin, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (kind, subject_id, title, utils.fmt(starts_at_utc), utils.clean_text(description, 1000),
         utils.clean_text(questions, 2000), utils.clean_text(new_words, 1000), max(0, xp), max(0, coin), creator["id"]))


def join_event(db, event_id, student_id):
    if db.one("SELECT 1 FROM club_events WHERE id=?", (event_id,)) is None:
        raise AppError("not_found")
    db.execute("INSERT OR IGNORE INTO club_attendees (event_id, student_id) VALUES (?,?)", (event_id, student_id))


def mark_attended(db, event_id, student_id, actor):
    ev = db.one("SELECT * FROM club_events WHERE id=?", (event_id,))
    if ev is None:
        raise AppError("not_found")
    with db.tx():
        db.execute("INSERT OR IGNORE INTO club_attendees (event_id, student_id) VALUES (?,?)", (event_id, student_id))
        row = db.one("SELECT attended FROM club_attendees WHERE event_id=? AND student_id=?", (event_id, student_id))
        if row["attended"]:
            return False
        db.execute("UPDATE club_attendees SET attended=1 WHERE event_id=? AND student_id=?", (event_id, student_id))
        if ev["reward_xp"]:
            points.award(db, student_id, "xp", ev["reward_xp"], "club", ref_type="club", ref_id=event_id,
                         actor_id=actor["id"], notify=False)
        if ev["reward_coin"]:
            points.award(db, student_id, "coin", ev["reward_coin"], "club", ref_type="club", ref_id=event_id,
                         actor_id=actor["id"], notify=False)
    notifications.notify(db, student_id, "club_reward",
                         {"title": ev["title"], "xp": ev["reward_xp"], "coin": ev["reward_coin"]})
    return True


# ---------------------------------------------------------------- sertifikatlar
def revoke_certificate(db, number, reason, actor):
    """Sertifikatni bekor qiladi: tekshiruv sahifasida endi haqiqiy deb ko'rsatilmaydi. Qayta tiklab bo'lmaydi."""
    reason = utils.clean_text(reason, 200)
    if not reason:
        raise AppError("review_note_required")
    row = db.one("SELECT id FROM certificates WHERE number=?", ((number or "").strip().upper(),))
    if row is None:
        raise AppError("not_found")
    if db.execute("UPDATE certificates SET revoked=1, revoked_at=?, revoked_reason=? WHERE id=? AND revoked=0",
                  (utils.fmt(utils.now()), reason, row["id"])).rowcount != 1:
        raise AppError("already_revoked")
    audit.record(db, actor["id"], "certificate.revoke", "certificate", row["id"], {"reason": reason})


def issue_certificate(db, student_id, subject_id, course, level, issuer):
    course = utils.clean_text(course, 150)
    if not course:
        raise AppError("title_required")
    year = utils.now().year
    for _ in range(5):
        number = "SEC-%d-%s" % (year, "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6)))
        if db.one("SELECT 1 FROM certificates WHERE number=?", (number,)) is None:
            break
    cert_id = db.insert(
        "INSERT INTO certificates (number, student_id, subject_id, course, level, issued_at, issued_by) "
        "VALUES (?,?,?,?,?,?,?)", (number, student_id, subject_id, course, level, utils.fmt(utils.now()), issuer["id"]))
    audit.record(db, issuer["id"], "certificate.issue", "certificate", cert_id, {"number": number})
    notifications.notify(db, student_id, "certificate_new", {"course": course}, link="/student/certificates")
    return number
