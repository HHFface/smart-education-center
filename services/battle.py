"""Battle Mode: 1 vs 1 bilim bellashuvi.

Asosiy g'oyalar
 * Hamma hisob-kitob SERVERDA: javob to'g'riligi, vaqt, ball va reyting. Brauzer faqat tanlovni yuboradi.
 * Vaqt chizig'i serverdagi started_at dan hisoblanadi (savol i: started_at + i*(sekund+tanaffus)).
   Shu sabab internet uzilib qayta ulanganda holat avtomatik tiklanadi.
 * WebSocket o'rniga qisqa so'rov (polling) ishlatiladi: bepul hostinglarda ham ishlaydi.
 * Onlayn raqib bo'lmasa: taklif yuborish -> vaqt kelishish -> rejalashtirilgan Battle.
 * Texnik nosozlikda avtomatik mag'lubiyat yo'q: javob bermagan tomon bilan o'yin 'void' (reytingsiz) bo'ladi.
"""
import datetime
import random
import sqlite3

from .. import utils
from ..utils import AppError
from . import access, audit, notifications, points, rating, settings

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
QTYPES = ["vocabulary", "grammar", "translation", "listening", "reading"]
KINDS = QTYPES + ["mixed", "teacher"]
OPEN_STATUSES = ("pending", "counter", "scheduled", "ready", "active")
MIN_QUESTIONS = 3


def _cfg(db, key):
    return settings.get(db, key)


def level_index(level):
    return LEVELS.index(level) if level in LEVELS else 0


# ---------------------------------------------------------------- reyting va daraja
def rating_value(db, student_id, subject_id):
    row = db.one("SELECT rating FROM battle_ratings WHERE student_id=? AND subject_id=?", (student_id, subject_id))
    return row["rating"] if row else int(_cfg(db, "battle.initial_rating"))


def get_rating(db, student_id, subject_id):
    db.execute("INSERT OR IGNORE INTO battle_ratings (student_id, subject_id, rating) VALUES (?,?,?)",
               (student_id, subject_id, int(_cfg(db, "battle.initial_rating"))))
    return db.one("SELECT * FROM battle_ratings WHERE student_id=? AND subject_id=?", (student_id, subject_id))


def league(db, rating_val):
    return rating.league_for(rating_val, _cfg(db, "battle.leagues"))


def student_level(db, student_id, subject_id):
    row = db.one("SELECT level FROM student_levels WHERE student_id=? AND subject_id=?", (student_id, subject_id))
    if row:
        return row["level"]
    rows = db.all(
        "SELECT c.level FROM courses c JOIN groups g ON g.course_id=c.id JOIN group_students gs ON gs.group_id=g.id "
        "WHERE gs.student_id=? AND c.subject_id=?", (student_id, subject_id))
    if rows:
        return max((r["level"] for r in rows), key=level_index)
    return "A1"


def is_online(last_seen, online_seconds, now):
    seen = utils.parse(last_seen) if last_seen else None
    return bool(seen and (now - seen).total_seconds() <= online_seconds)


# ---------------------------------------------------------------- raqib qidirish
def find_opponents(db, user_id, subject_id, now=None):
    """Mos raqiblar: bir fan, daraja farqi chegarada, reyting yaqinligi bo'yicha saralangan.

    Qidiruv bosqichma-bosqich kengayadi (battle.rating_windows): 'tier' qanchalik kichik bo'lsa,
    reyting shunchalik yaqin. Daraja farqi juda katta o'quvchilar hech qachon ro'yxatga kirmaydi.
    """
    now = now or utils.now()
    windows = list(_cfg(db, "battle.rating_windows"))
    gap = int(_cfg(db, "battle.max_level_gap"))
    online_sec = int(_cfg(db, "battle.online_seconds"))
    mine = rating_value(db, user_id, subject_id)
    my_level = level_index(student_level(db, user_id, subject_id))
    rows = db.all(
        "SELECT DISTINCT u.id, u.first_name, u.last_name, u.username, u.avatar, u.last_seen FROM users u "
        "JOIN group_students gs ON gs.student_id=u.id JOIN groups g ON g.id=gs.group_id "
        "JOIN courses c ON c.id=g.course_id WHERE c.subject_id=? AND u.role='student' AND u.is_active=1 AND u.id<>?",
        (subject_id, user_id))
    result = {"online": [], "offline": []}
    for row in rows:
        level = student_level(db, row["id"], subject_id)
        if abs(level_index(level) - my_level) > gap:
            continue
        rt = get_rating_readonly(db, row["id"], subject_id)
        diff = abs(rt["rating"] - mine)
        tier = len(windows)
        for index, window in enumerate(windows):
            if diff <= window:
                tier = index
                break
        online = is_online(row["last_seen"], online_sec, now)
        item = {
            "id": row["id"], "name": access.user_name(row), "avatar": row["avatar"],
            "rating": rt["rating"], "league": league(db, rt["rating"]), "wins": rt["wins"],
            "games": rt["games"], "level": level, "diff": diff, "tier": tier, "online": online,
        }
        result["online" if online else "offline"].append(item)
    for key in result:
        result[key].sort(key=lambda it: (it["tier"], it["diff"], it["name"]))
    return result


def get_rating_readonly(db, student_id, subject_id):
    row = db.one("SELECT * FROM battle_ratings WHERE student_id=? AND subject_id=?", (student_id, subject_id))
    if row:
        return row
    return {"rating": int(_cfg(db, "battle.initial_rating")), "games": 0, "wins": 0, "losses": 0, "draws": 0}


# ---------------------------------------------------------------- savollar
def _question_pool(db, subject_id, level, kind, challenge_id, spread):
    if kind == "teacher":
        return db.all("SELECT id, qtype FROM questions WHERE challenge_id=? AND is_active=1", (challenge_id,))
    idx = level_index(level)
    levels = LEVELS[max(0, idx - spread): idx + spread + 1]
    types = QTYPES if kind == "mixed" else [kind]
    marks_l = ",".join("?" * len(levels))
    marks_t = ",".join("?" * len(types))
    return db.all(
        "SELECT id, qtype FROM questions WHERE subject_id=? AND is_active=1 AND challenge_id IS NULL "
        "AND level IN (%s) AND qtype IN (%s)" % (marks_l, marks_t), [subject_id] + levels + types)


def available_questions(db, subject_id, level, kind, challenge_id=None):
    need = int(_cfg(db, "battle.questions"))
    for spread in (0, 1, 2):
        pool = _question_pool(db, subject_id, level, kind, challenge_id, spread)
        if len(pool) >= need:
            return len(pool)
        if kind == "teacher":
            break
    return len(pool)


def _recent_question_ids(db, user_ids, limit=5):
    seen = set()
    for uid in user_ids:
        rows = db.all(
            "SELECT b.question_ids FROM battles b JOIN battle_players bp ON bp.battle_id=b.id "
            "WHERE bp.user_id=? AND b.question_ids IS NOT NULL ORDER BY b.id DESC LIMIT ?", (uid, limit))
        for row in rows:
            seen.update(utils.jloads(row["question_ids"], []))
    return seen


def pick_questions(db, battle):
    """Ikkala o'quvchiga bir xil savollar to'plami. Yaqinda ishlatilganlar imkon qadar takrorlanmaydi."""
    need = int(_cfg(db, "battle.questions"))
    pool = []
    for spread in (0, 1, 2):
        pool = _question_pool(db, battle["subject_id"], battle["level"], battle["kind"],
                              battle["challenge_id"], spread)
        if len(pool) >= need or battle["kind"] == "teacher":
            break
    if len(pool) < MIN_QUESTIONS:
        raise AppError("not_enough_questions")
    recent = _recent_question_ids(db, [battle["challenger_id"], battle["opponent_id"]])
    fresh = [q["id"] for q in pool if q["id"] not in recent]
    stale = [q["id"] for q in pool if q["id"] in recent]
    random.shuffle(fresh)
    random.shuffle(stale)
    chosen = (fresh + stale)[:need]
    random.shuffle(chosen)
    return chosen


def question_payload(db, question_id):
    q = db.one("SELECT * FROM questions WHERE id=?", (question_id,))
    listening = q["qtype"] == "listening"
    return {
        "id": q["id"], "type": q["qtype"], "text": q["text"],
        # Listening: matn brauzerning ovoz sintezi (bepul) orqali o'qiladi; reading: matn ko'rsatiladi.
        "passage": None if listening else q["passage"],
        "tts": q["passage"] if listening and not q["audio_url"] else None,
        "audio_url": q["audio_url"], "options": utils.jloads(q["options"], []),
    }


# ---------------------------------------------------------------- taklif yaratish
def _open_between(db, a, b):
    marks = ",".join("?" * len(OPEN_STATUSES))
    return db.one(
        "SELECT id FROM battles WHERE status IN (%s) AND ((challenger_id=? AND opponent_id=?) "
        "OR (challenger_id=? AND opponent_id=?))" % marks, list(OPEN_STATUSES) + [a, b, b, a])


def active_battle_for(db, user_id, exclude_id=None):
    sql = ("SELECT b.id FROM battles b WHERE b.status='active' AND (b.challenger_id=? OR b.opponent_id=?)")
    args = [user_id, user_id]
    if exclude_id:
        sql += " AND b.id<>?"
        args.append(exclude_id)
    return db.one(sql, args)


def create_invite(db, challenger_id, opponent_id, subject_id, kind, quick=False, challenge_id=None, now=None):
    now = now or utils.now()
    if kind not in KINDS:
        raise AppError("bad_kind")
    if challenger_id == opponent_id:
        raise AppError("bad_opponent")
    users = {r["id"]: r for r in db.all(
        "SELECT * FROM users WHERE id IN (?,?) AND role='student' AND is_active=1", (challenger_id, opponent_id))}
    if len(users) != 2:
        raise AppError("bad_opponent")
    for uid in (challenger_id, opponent_id):
        if subject_id not in [s["id"] for s in access.student_subjects(db, uid)]:
            raise AppError("not_in_subject")
    lvl_a = level_index(student_level(db, challenger_id, subject_id))
    lvl_b = level_index(student_level(db, opponent_id, subject_id))
    if abs(lvl_a - lvl_b) > int(_cfg(db, "battle.max_level_gap")):
        raise AppError("level_gap")
    day_start, _ = utils.local_day_bounds(utils.to_local(now).date())
    sent_today = db.scalar("SELECT COUNT(*) FROM battles WHERE challenger_id=? AND created_at>=?",
                           (challenger_id, day_start), 0)
    if sent_today >= int(_cfg(db, "battle.invites_per_day")):
        raise AppError("invite_limit")
    if _open_between(db, challenger_id, opponent_id):
        raise AppError("invite_exists")
    week_ago = utils.fmt(now - datetime.timedelta(days=7))
    pair_count = db.scalar(
        "SELECT COUNT(*) FROM battles WHERE status IN ('finished','active') AND created_at>=? AND "
        "((challenger_id=? AND opponent_id=?) OR (challenger_id=? AND opponent_id=?))",
        (week_ago, challenger_id, opponent_id, opponent_id, challenger_id), 0)
    if pair_count >= int(_cfg(db, "battle.pair_weekly_limit")):
        raise AppError("pair_limit")
    if quick and active_battle_for(db, challenger_id):
        raise AppError("already_in_battle")
    level = LEVELS[(lvl_a + lvl_b) // 2]
    if kind == "teacher":
        challenge = db.one("SELECT * FROM challenges WHERE id=? AND subject_id=?", (challenge_id, subject_id))
        if challenge is None:
            raise AppError("bad_challenge")
        level = challenge["level"]
    else:
        challenge_id = None
    if available_questions(db, subject_id, level, kind, challenge_id) < MIN_QUESTIONS:
        raise AppError("not_enough_questions")
    if quick:
        expires = now + datetime.timedelta(seconds=int(_cfg(db, "battle.quick_ttl_seconds")))
    else:
        expires = now + datetime.timedelta(minutes=int(_cfg(db, "battle.invite_ttl_minutes")))
    with db.tx():
        battle_id = db.insert(
            "INSERT INTO battles (subject_id, kind, level, challenger_id, opponent_id, challenge_id, status, quick, "
            "expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (subject_id, kind, level, challenger_id, opponent_id, challenge_id, "pending", 1 if quick else 0,
             utils.fmt(expires), utils.fmt(now)))
        for uid in (challenger_id, opponent_id):
            db.execute("INSERT INTO battle_players (battle_id, user_id) VALUES (?,?)", (battle_id, uid))
    notifications.notify(db, opponent_id, "battle_invite",
                         {"name": access.user_name(users[challenger_id]), "kind": kind},
                         link="/battle/%d" % battle_id)
    return battle_id


# ---------------------------------------------------------------- holat o'tishlari
def _get(db, battle_id):
    b = db.one("SELECT * FROM battles WHERE id=?", (battle_id,))
    if b is None:
        raise AppError("not_found")
    return b


def _participant(b, user_id):
    if user_id not in (b["challenger_id"], b["opponent_id"]):
        raise AppError("forbidden")


def other_id(b, user_id):
    return b["opponent_id"] if user_id == b["challenger_id"] else b["challenger_id"]


def _name(db, user_id):
    return access.user_name(db.one("SELECT * FROM users WHERE id=?", (user_id,)))


def accept(db, battle_id, user_id, now=None):
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    if b["opponent_id"] != user_id:
        raise AppError("forbidden")
    if b["status"] != "pending":
        raise AppError("bad_state")
    if active_battle_for(db, user_id):
        raise AppError("already_in_battle")
    wait = 2 if b["quick"] else int(_cfg(db, "battle.wait_minutes"))
    deadline = now + datetime.timedelta(minutes=wait)
    db.execute("UPDATE battles SET status='ready', lobby_deadline=?, expires_at=NULL WHERE id=?",
               (utils.fmt(deadline), battle_id))
    notifications.notify(db, b["challenger_id"], "battle_ready", {"name": _name(db, user_id)},
                         link="/battle/%d" % battle_id)


def decline(db, battle_id, user_id, now=None):
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    if user_id != b["opponent_id"]:
        raise AppError("forbidden")
    if b["status"] not in ("pending", "counter"):
        raise AppError("bad_state")
    db.execute("UPDATE battles SET status='declined' WHERE id=?", (battle_id,))
    notifications.notify(db, b["challenger_id"], "battle_declined", {"name": _name(db, user_id)})


def cancel(db, battle_id, user_id, now=None):
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    _participant(b, user_id)
    if b["status"] not in ("pending", "counter", "scheduled", "ready"):
        raise AppError("bad_state")
    if b["status"] == "pending" and user_id != b["challenger_id"]:
        raise AppError("forbidden")
    db.execute("UPDATE battles SET status='cancelled' WHERE id=?", (battle_id,))
    notifications.notify(db, other_id(b, user_id), "battle_declined", {"name": _name(db, user_id)})


def propose_slots(db, battle_id, user_id, slots, now=None):
    """Boshqa vaqt taklif qilish. slots: UTC datetime ro'yxati (1-3 ta)."""
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    _participant(b, user_id)
    allowed = (b["status"] == "pending" and user_id == b["opponent_id"]) or \
              (b["status"] == "counter" and user_id != b["proposed_by"])
    if not allowed:
        raise AppError("bad_state")
    slots = [s for s in slots if s is not None]
    if not 1 <= len(slots) <= 3:
        raise AppError("bad_slots")
    for slot in slots:
        if slot < now + datetime.timedelta(minutes=5) or slot > now + datetime.timedelta(days=30):
            raise AppError("bad_slots")
    ttl = datetime.timedelta(minutes=int(_cfg(db, "battle.invite_ttl_minutes")))
    with db.tx():
        db.execute("DELETE FROM battle_slots WHERE battle_id=?", (battle_id,))
        for slot in sorted(set(slots)):
            db.execute("INSERT INTO battle_slots (battle_id, proposed_by, starts_at) VALUES (?,?,?)",
                       (battle_id, user_id, utils.fmt(slot)))
        db.execute("UPDATE battles SET status='counter', proposed_by=?, expires_at=? WHERE id=?",
                   (user_id, utils.fmt(now + ttl), battle_id))
    notifications.notify(db, other_id(b, user_id), "battle_counter", {"name": _name(db, user_id)},
                         link="/battle/%d" % battle_id)


def choose_slot(db, battle_id, user_id, slot_id, now=None):
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    _participant(b, user_id)
    if b["status"] != "counter" or user_id == b["proposed_by"]:
        raise AppError("bad_state")
    slot = db.one("SELECT * FROM battle_slots WHERE id=? AND battle_id=?", (slot_id, battle_id))
    if slot is None:
        raise AppError("bad_slots")
    start = utils.parse(slot["starts_at"])
    if start < now:
        raise AppError("bad_slots")
    wait = datetime.timedelta(minutes=int(_cfg(db, "battle.wait_minutes")))
    db.execute("UPDATE battles SET status='scheduled', scheduled_at=?, lobby_deadline=?, expires_at=NULL WHERE id=?",
               (slot["starts_at"], utils.fmt(start + wait), battle_id))
    for uid in (b["challenger_id"], b["opponent_id"]):
        notifications.notify(db, uid, "battle_scheduled",
                             {"name": _name(db, other_id(b, uid)), "time": utils.fmt_local(slot["starts_at"])},
                             link="/battle/%d" % battle_id)


def lobby_open(db, b, now):
    if b["status"] == "ready":
        return True
    if b["status"] == "scheduled" and b["scheduled_at"]:
        opens = utils.parse(b["scheduled_at"]) - datetime.timedelta(minutes=int(_cfg(db, "battle.lobby_open_minutes")))
        return now >= opens
    return False


def set_ready(db, battle_id, user_id, now=None):
    now = now or utils.now()
    tick_battle(db, battle_id, now)
    b = _get(db, battle_id)
    _participant(b, user_id)
    if b["status"] not in ("ready", "scheduled"):
        raise AppError("bad_state")
    if not lobby_open(db, b, now):
        raise AppError("lobby_not_open")
    with db.tx():
        fresh = db.one("SELECT status FROM battles WHERE id=?", (battle_id,))
        if fresh is None or fresh["status"] not in ("ready", "scheduled"):
            return fresh["status"] if fresh else b["status"]        # allaqachon boshlangan — qayta boshlamaymiz
        db.execute("UPDATE battle_players SET ready=1 WHERE battle_id=? AND user_id=?", (battle_id, user_id))
        ready = db.scalar("SELECT COUNT(*) FROM battle_players WHERE battle_id=? AND ready=1", (battle_id,), 0)
        if ready == 2:
            for uid in (b["challenger_id"], b["opponent_id"]):
                if active_battle_for(db, uid, exclude_id=battle_id):
                    raise AppError("already_in_battle")
            ids = pick_questions(db, b)
            started = now + datetime.timedelta(seconds=int(_cfg(db, "battle.countdown_seconds")))
            ra = get_rating(db, b["challenger_id"], b["subject_id"])
            rb = get_rating(db, b["opponent_id"], b["subject_id"])
            db.execute("UPDATE battle_players SET rating_before=? WHERE battle_id=? AND user_id=?",
                       (ra["rating"], battle_id, b["challenger_id"]))
            db.execute("UPDATE battle_players SET rating_before=? WHERE battle_id=? AND user_id=?",
                       (rb["rating"], battle_id, b["opponent_id"]))
            db.execute("UPDATE battles SET status='active', question_ids=?, started_at=? WHERE id=?",
                       (utils.jdumps(ids), utils.fmt(started), battle_id))
            return "active"
    return b["status"]


# ---------------------------------------------------------------- vaqt chizig'i
def timeline(db, b):
    n = len(utils.jloads(b["question_ids"], []))
    sec = int(_cfg(db, "battle.seconds_per_question"))
    gap = int(_cfg(db, "battle.gap_seconds"))
    started = utils.parse(b["started_at"])
    return {"n": n, "sec": sec, "gap": gap, "per": sec + gap, "started": started,
            "end": started + datetime.timedelta(seconds=n * (sec + gap))}


def _answered_counts(db, battle_id):
    rows = db.all("SELECT user_id, COUNT(*) AS n FROM battle_answers WHERE battle_id=? GROUP BY user_id", (battle_id,))
    return {r["user_id"]: r["n"] for r in rows}


def tick_battle(db, battle_id, now=None):
    """Vaqt o'tishi bilan holatni yangilaydi (muddati o'tgan takliflar, kelmaganlar, tugagan o'yinlar)."""
    now = now or utils.now_precise()
    b = db.one("SELECT * FROM battles WHERE id=?", (battle_id,))
    if b is None:
        return None
    now_s = utils.fmt(now)
    status = b["status"]
    if status in ("pending", "counter") and b["expires_at"] and now_s > b["expires_at"]:
        db.execute("UPDATE battles SET status='expired' WHERE id=? AND status IN ('pending','counter')", (battle_id,))
        notifications.notify(db, b["challenger_id"], "battle_expired", {"name": _name(db, b["opponent_id"])})
    elif status in ("ready", "scheduled") and b["lobby_deadline"] and now_s > b["lobby_deadline"]:
        _no_show(db, b)
    elif status == "active":
        tl = timeline(db, b)
        answered = _answered_counts(db, battle_id)
        both_done = tl["n"] > 0 and all(answered.get(uid, 0) >= tl["n"]
                                        for uid in (b["challenger_id"], b["opponent_id"]))
        if now >= tl["end"] or both_done:
            finalize(db, battle_id, now)
    return db.one("SELECT * FROM battles WHERE id=?", (battle_id,))


def _no_show(db, b):
    players = db.all("SELECT user_id, ready FROM battle_players WHERE battle_id=?", (b["id"],))
    absent = [p["user_id"] for p in players if not p["ready"]]
    absent_user = absent[0] if len(absent) == 1 else None
    changed = db.execute("UPDATE battles SET status='no_show', no_show_user_id=? WHERE id=? AND status IN ('ready','scheduled')",
                         (absent_user, b["id"])).rowcount
    if changed:
        for uid in (b["challenger_id"], b["opponent_id"]):
            notifications.notify(db, uid, "battle_noshow", {"name": _name(db, other_id(b, uid))})


def run_due(db, now=None):
    """Hamma ochiq Battlelarni tekshiradi va eslatmalarni yuboradi (tick/cron uchun)."""
    now = now or utils.now_precise()
    for row in db.all("SELECT id FROM battles WHERE status IN ('pending','counter','ready','scheduled','active')"):
        tick_battle(db, row["id"], now)
    send_reminders(db, now)


def send_reminders(db, now):
    rows = db.all("SELECT * FROM battles WHERE status='scheduled' AND scheduled_at>?", (utils.fmt(now),))
    for b in rows:
        start = utils.parse(b["scheduled_at"])
        for uid in (b["challenger_id"], b["opponent_id"]):
            user = db.one("SELECT reminder_minutes FROM users WHERE id=?", (uid,))
            minutes = sorted({m for m in (utils.to_int(x) for x in (user["reminder_minutes"] or "").split(",")) if m},
                             reverse=True)
            due = [m for m in minutes if now >= start - datetime.timedelta(minutes=m)]
            fresh = [m for m in due if db.one(
                "SELECT 1 FROM battle_reminders_sent WHERE battle_id=? AND user_id=? AND minutes=?", (b["id"], uid, m)) is None]
            if not fresh:
                continue
            for m in due:
                db.execute("INSERT OR IGNORE INTO battle_reminders_sent (battle_id, user_id, minutes) VALUES (?,?,?)",
                           (b["id"], uid, m))
            left = max(0, int((start - now).total_seconds() // 60))
            notifications.notify(db, uid, "battle_reminder",
                                 {"name": _name(db, other_id(b, uid)), "minutes": left,
                                  "time": utils.fmt_local(b["scheduled_at"])}, link="/battle/%d" % b["id"])


# ---------------------------------------------------------------- o'yin holati va javoblar
def state_for(db, battle_id, user_id, now=None):
    now = now or utils.now_precise()
    b = tick_battle(db, battle_id, now)
    if b is None:
        raise AppError("not_found")
    _participant(b, user_id)
    opp_id = other_id(b, user_id)
    opp = db.one("SELECT id, first_name, last_name, username, avatar FROM users WHERE id=?", (opp_id,))
    players = {p["user_id"]: p for p in db.all("SELECT * FROM battle_players WHERE battle_id=?", (battle_id,))}
    data = {
        "ok": True, "battle_id": battle_id, "status": b["status"], "kind": b["kind"], "phase": b["status"],
        "opponent": {"id": opp_id, "name": access.user_name(opp)},
        "me_ready": bool(players[user_id]["ready"]), "opp_ready": bool(players[opp_id]["ready"]),
        "scheduled_at": b["scheduled_at"], "lobby_open": lobby_open(db, b, now),
    }
    if b["status"] != "active":
        return data
    tl = timeline(db, b)
    elapsed = (now - tl["started"]).total_seconds()
    data["total"] = tl["n"]
    mine = {r["q_index"]: r for r in db.all("SELECT * FROM battle_answers WHERE battle_id=? AND user_id=?",
                                            (battle_id, user_id))}
    opp_done = db.scalar("SELECT COUNT(*) FROM battle_answers WHERE battle_id=? AND user_id=?", (battle_id, opp_id), 0)
    data["me_answered"] = len(mine)
    data["opp_answered"] = opp_done
    if elapsed < 0:
        data.update(phase="countdown", seconds=round(-elapsed, 2))
        return data
    idx = int(elapsed // tl["per"])
    if idx >= tl["n"]:
        data["phase"] = "finishing"
        return data
    offset = elapsed - idx * tl["per"]
    ids = utils.jloads(b["question_ids"], [])
    data["index"] = idx
    if offset < tl["sec"]:
        data.update(phase="question", seconds_left=round(tl["sec"] - offset, 2), seconds_total=tl["sec"],
                    question=question_payload(db, ids[idx]), answered=idx in mine,
                    my_choice=mine[idx]["choice"] if idx in mine else None)
    else:
        q = db.one("SELECT correct_index, explanation FROM questions WHERE id=?", (ids[idx],))
        data.update(phase="gap", seconds_left=round(tl["per"] - offset, 2), correct_index=q["correct_index"],
                    question=question_payload(db, ids[idx]),
                    explanation=q["explanation"] or "", my_choice=mine[idx]["choice"] if idx in mine else None,
                    was_correct=bool(mine[idx]["is_correct"]) if idx in mine else False)
    return data


def answer(db, battle_id, user_id, q_index, choice, now=None):
    now = now or utils.now_precise()
    b = tick_battle(db, battle_id, now)
    if b is None:
        raise AppError("not_found")
    _participant(b, user_id)
    if b["status"] != "active":
        raise AppError("bad_state")
    tl = timeline(db, b)
    elapsed = (now - tl["started"]).total_seconds()
    if elapsed < 0:
        raise AppError("not_started")
    idx = int(elapsed // tl["per"])
    if q_index != idx or idx >= tl["n"]:
        raise AppError("wrong_question")
    offset = elapsed - idx * tl["per"]
    if offset > tl["sec"] + 1.0:  # 1 soniya tarmoq kechikishi uchun imtiyoz
        raise AppError("time_up")
    ids = utils.jloads(b["question_ids"], [])
    q = db.one("SELECT correct_index, options FROM questions WHERE id=?", (ids[idx],))
    options = utils.jloads(q["options"], [])
    if choice is not None and not (isinstance(choice, int) and 0 <= choice < len(options)):
        raise AppError("bad_choice")
    correct = 1 if choice is not None and choice == q["correct_index"] else 0
    time_ms = int(min(offset, tl["sec"]) * 1000)
    try:
        with db.tx():
            db.execute(
                "INSERT INTO battle_answers (battle_id, user_id, q_index, choice, is_correct, time_ms, answered_at) "
                "VALUES (?,?,?,?,?,?,?)", (battle_id, user_id, idx, choice, correct, time_ms, utils.fmt(now)))
            if correct:
                db.execute("UPDATE battle_players SET correct=correct+1, total_time_ms=total_time_ms+? "
                           "WHERE battle_id=? AND user_id=?", (time_ms, battle_id, user_id))
            else:
                db.execute("UPDATE battle_players SET wrong=wrong+1, total_time_ms=total_time_ms+? "
                           "WHERE battle_id=? AND user_id=?", (time_ms, battle_id, user_id))
    except sqlite3.IntegrityError:
        raise AppError("already_answered")
    tick_battle(db, battle_id, now)  # ikkalasi ham tugatgan bo'lsa, darhol yakunlaydi
    return {"ok": True}


# ---------------------------------------------------------------- yakunlash
def _reward_for(db, outcome):
    key = {"win": "battle.reward_win", "draw": "battle.reward_draw", "loss": "battle.reward_loss"}[outcome]
    return _cfg(db, key)


def finalize(db, battle_id, now=None):
    now = now or utils.now_precise()
    result = None
    with db.tx():
        changed = db.execute("UPDATE battles SET status='finished', finished_at=? WHERE id=? AND status='active'",
                             (utils.fmt(now), battle_id)).rowcount
        if not changed:
            return None
        b = _get(db, battle_id)
        a_id, b_id = b["challenger_id"], b["opponent_id"]
        players = {p["user_id"]: p for p in db.all("SELECT * FROM battle_players WHERE battle_id=?", (battle_id,))}
        answered = _answered_counts(db, battle_id)
        if answered.get(a_id, 0) == 0 or answered.get(b_id, 0) == 0:
            # Kimdir umuman javob bermagan: texnik nosozlik bo'lishi mumkin -> reytingsiz bekor qilinadi.
            silent = [u for u in (a_id, b_id) if answered.get(u, 0) == 0]
            db.execute("UPDATE battles SET status='void', no_show_user_id=? WHERE id=?",
                       (silent[0] if len(silent) == 1 else None, battle_id))
            result = "void"
        else:
            ca, cb = players[a_id]["correct"], players[b_id]["correct"]
            ta, tb = players[a_id]["total_time_ms"], players[b_id]["total_time_ms"]
            if ca > cb:
                score_a = 1.0
            elif cb > ca:
                score_a = 0.0
            elif _cfg(db, "battle.tiebreak_by_time") and abs(ta - tb) > 2000:
                score_a = 1.0 if ta < tb else 0.0
            else:
                score_a = 0.5
            row_a = get_rating(db, a_id, b["subject_id"])
            row_b = get_rating(db, b_id, b["subject_id"])
            pg, kp, kn = (int(_cfg(db, "battle.placement_games")), int(_cfg(db, "battle.k_placement")),
                          int(_cfg(db, "battle.k_normal")))
            new_a, new_b = rating.new_ratings(row_a["rating"], row_b["rating"], score_a,
                                              rating.k_factor(row_a["games"], pg, kp, kn),
                                              rating.k_factor(row_b["games"], pg, kp, kn))
            winner = a_id if score_a == 1.0 else (b_id if score_a == 0.0 else None)
            for uid, old, new, out_ in ((a_id, row_a, new_a, _outcome(score_a)),
                                        (b_id, row_b, new_b, _outcome(1.0 - score_a))):
                db.execute(
                    "UPDATE battle_ratings SET rating=?, games=games+1, wins=wins+?, losses=losses+?, draws=draws+? "
                    "WHERE student_id=? AND subject_id=?",
                    (new, 1 if out_ == "win" else 0, 1 if out_ == "loss" else 0, 1 if out_ == "draw" else 0,
                     uid, b["subject_id"]))
                db.execute("UPDATE battle_players SET rating_before=?, rating_after=? WHERE battle_id=? AND user_id=?",
                           (old["rating"], new, battle_id, uid))
            db.execute("UPDATE battles SET winner_id=? WHERE id=?", (winner, battle_id))
            _rewards(db, b, a_id, b_id, score_a, now)
            flags = _suspicious(db, b, players, answered, score_a, now)
            if flags:
                db.execute("UPDATE battles SET suspicious=? WHERE id=?", (",".join(flags), battle_id))
            result = "finished"
    if result == "finished":
        b = _get(db, battle_id)
        for uid in (b["challenger_id"], b["opponent_id"]):
            me = db.one("SELECT * FROM battle_players WHERE battle_id=? AND user_id=?", (battle_id, uid))
            opp = db.one("SELECT * FROM battle_players WHERE battle_id=? AND user_id=?", (battle_id, other_id(b, uid)))
            outcome = "draw" if b["winner_id"] is None else ("win" if b["winner_id"] == uid else "loss")
            notifications.notify(db, uid, "battle_result",
                                 {"name": _name(db, other_id(b, uid)), "result": outcome,
                                  "score": "%d:%d" % (me["correct"], opp["correct"]),
                                  "delta": (me["rating_after"] or 0) - (me["rating_before"] or 0)},
                                 link="/battle/%d/result" % battle_id)
    elif result == "void":
        for uid in (a_id, b_id):
            notifications.notify(db, uid, "battle_noshow", {"name": _name(db, other_id(b, uid))})
    return result


def _outcome(score):
    return "win" if score == 1.0 else ("loss" if score == 0.0 else "draw")


def _rewards(db, b, a_id, b_id, score_a, now):
    cap = int(_cfg(db, "battle.daily_reward_cap"))
    day_start, _ = utils.local_day_bounds(utils.to_local(now).date())
    for uid, out_ in ((a_id, _outcome(score_a)), (b_id, _outcome(1.0 - score_a))):
        given = db.scalar("SELECT COUNT(DISTINCT ref_id) FROM points_log WHERE student_id=? AND ref_type='battle' "
                          "AND reason_code='battle_reward' AND created_at>=?", (uid, day_start), 0)
        if given >= cap:
            continue
        reward = _reward_for(db, out_)
        for kind, key in (("xp", "xp"), ("coin", "coin")):
            amount = int(reward.get(key, 0))
            if amount > 0:
                points.award(db, uid, kind, amount, "battle_reward", ref_type="battle", ref_id=b["id"], notify=False)


def _suspicious(db, b, players, answered, score_a, now):
    """Ataylab yutqazish yoki kelishilgan natijalar belgilari (administrator ko'rib chiqadi)."""
    flags = []
    n = len(utils.jloads(b["question_ids"], []))
    for uid, p in players.items():
        got = answered.get(uid, 0)
        if got >= max(3, n // 2) and p["correct"] == 0 and p["total_time_ms"] / max(1, got) < 1500:
            flags.append("fast_all_wrong:%d" % uid)
    week_ago = utils.fmt(now - datetime.timedelta(days=7))
    repeats = db.scalar(
        "SELECT COUNT(*) FROM battles WHERE status='finished' AND created_at>=? AND "
        "((challenger_id=? AND opponent_id=?) OR (challenger_id=? AND opponent_id=?))",
        (week_ago, b["challenger_id"], b["opponent_id"], b["opponent_id"], b["challenger_id"]), 0)
    if repeats >= int(_cfg(db, "battle.pair_weekly_limit")):
        flags.append("repeat_pair")
    return flags


# ---------------------------------------------------------------- natijalar, tarix, e'tiroz
def result_for(db, battle_id, user_id, now=None):
    b = tick_battle(db, battle_id, now)
    if b is None:
        raise AppError("not_found")
    _participant(b, user_id)
    players = []
    for uid in (b["challenger_id"], b["opponent_id"]):
        p = db.one("SELECT * FROM battle_players WHERE battle_id=? AND user_id=?", (battle_id, uid))
        players.append({"id": uid, "name": _name(db, uid), "correct": p["correct"], "wrong": p["wrong"],
                        "total": len(utils.jloads(b["question_ids"], [])),
                        "time_ms": p["total_time_ms"], "before": p["rating_before"], "after": p["rating_after"]})
    review = []
    ids = utils.jloads(b["question_ids"], [])
    mine = {r["q_index"]: r for r in db.all("SELECT * FROM battle_answers WHERE battle_id=? AND user_id=?",
                                            (battle_id, user_id))}
    for index, qid in enumerate(ids):
        ans = mine.get(index)
        if ans is not None and ans["is_correct"]:
            continue
        q = db.one("SELECT * FROM questions WHERE id=?", (qid,))
        review.append({"index": index + 1, "text": q["text"], "options": utils.jloads(q["options"], []),
                       "correct": q["correct_index"], "choice": ans["choice"] if ans else None,
                       "explanation": q["explanation"] or ""})
    started = utils.parse(b["started_at"]) if b["started_at"] else None
    finished = utils.parse(b["finished_at"]) if b["finished_at"] else None
    duration = int((finished - started).total_seconds()) if started and finished else None
    return {"battle": b, "players": players, "review": review, "duration": duration,
            "winner_id": b["winner_id"]}


def history(db, user_id, limit=50):
    rows = db.all(
        "SELECT b.*, s.name AS subject_name FROM battles b JOIN subjects s ON s.id=b.subject_id "
        "WHERE (b.challenger_id=? OR b.opponent_id=?) AND b.status IN ('finished','void','no_show') "
        "ORDER BY b.id DESC LIMIT ?", (user_id, user_id, limit))
    items = []
    for b in rows:
        opp = other_id(b, user_id)
        me = db.one("SELECT * FROM battle_players WHERE battle_id=? AND user_id=?", (b["id"], user_id))
        op = db.one("SELECT * FROM battle_players WHERE battle_id=? AND user_id=?", (b["id"], opp))
        if b["status"] == "finished":
            outcome = "draw" if b["winner_id"] is None else ("win" if b["winner_id"] == user_id else "loss")
        else:
            outcome = b["status"]
        items.append({
            "id": b["id"], "date": b["finished_at"] or b["created_at"], "subject": b["subject_name"],
            "kind": b["kind"], "opponent_id": opp, "opponent": _name(db, opp), "outcome": outcome,
            "score": "%d:%d" % (me["correct"], op["correct"]),
            "delta": (me["rating_after"] - me["rating_before"]) if me["rating_after"] is not None else 0,
            "subject_id": b["subject_id"],
        })
    return items


def open_dispute(db, user_id, battle_id, message):
    b = _get(db, battle_id)
    _participant(b, user_id)
    if b["status"] != "finished":
        raise AppError("bad_state")
    message = utils.clean_text(message, 1000)
    if not message:
        raise AppError("message_required")
    if db.one("SELECT 1 FROM disputes WHERE student_id=? AND ref_type='battle' AND ref_id=? AND status='open'",
              (user_id, battle_id)):
        raise AppError("dispute_exists")
    return db.insert("INSERT INTO disputes (student_id, ref_type, ref_id, message, created_at) VALUES (?,?,?,?,?)",
                     (user_id, "battle", battle_id, message, utils.fmt(utils.now())))


def incoming_quick(db, user_id, now=None):
    now = now or utils.now()
    rows = db.all("SELECT * FROM battles WHERE opponent_id=? AND status='pending' AND quick=1 AND expires_at>?",
                  (user_id, utils.fmt(now)))
    return [{"id": r["id"], "from": _name(db, r["challenger_id"]), "kind": r["kind"]} for r in rows]


def center(db, user_id):
    """Takliflar markazi: kelgan, yuborilgan, tasdiqlangan, rad etilgan va muddati tugaganlar."""
    run_due(db)
    base = ("SELECT b.*, s.name AS subject_name, cu.first_name AS cf, cu.last_name AS cl, cu.username AS cn, "
            "ou.first_name AS of_, ou.last_name AS ol, ou.username AS on_ FROM battles b "
            "JOIN subjects s ON s.id=b.subject_id JOIN users cu ON cu.id=b.challenger_id "
            "JOIN users ou ON ou.id=b.opponent_id ")

    def fetch(where, args):
        out = []
        for r in db.all(base + "WHERE " + where + " ORDER BY b.id DESC LIMIT 50", args):
            out.append({"b": r, "challenger": ("%s %s" % (r["cf"], r["cl"])).strip() or r["cn"],
                        "opponent": ("%s %s" % (r["of_"], r["ol"])).strip() or r["on_"]})
        return out

    return {
        "incoming": fetch("b.opponent_id=? AND b.status IN ('pending','counter','ready')", [user_id]),
        "outgoing": fetch("b.challenger_id=? AND b.status IN ('pending','counter','ready')", [user_id]),
        "scheduled": fetch("(b.challenger_id=? OR b.opponent_id=?) AND b.status IN ('scheduled','active')",
                           [user_id, user_id]),
        "declined": fetch("(b.challenger_id=? OR b.opponent_id=?) AND b.status IN ('declined','cancelled')",
                          [user_id, user_id]),
        "expired": fetch("(b.challenger_id=? OR b.opponent_id=?) AND b.status IN ('expired','no_show')",
                         [user_id, user_id]),
    }
