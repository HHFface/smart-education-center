"""Mashq rejimi (Practice): tayyor savollar bilan yakka mashq. Rasmiy Battle reytingiga ta'sir QILMAYDI.

Javob to'g'riligi serverda tekshiriladi; to'g'ri javob faqat javob berilgandan keyin qaytariladi.
"""
import random

from .. import utils
from ..utils import AppError
from . import access, battle, settings

MAX_HISTORY = 10


def start(db, user_id, subject_id, kind):
    if kind not in battle.QTYPES + ["mixed"]:
        raise AppError("bad_kind")
    if subject_id not in [s["id"] for s in access.student_subjects(db, user_id)]:
        raise AppError("not_in_subject")
    level = battle.student_level(db, user_id, subject_id)
    need = int(settings.get(db, "practice.questions"))
    pool = []
    for spread in (0, 1, 2):
        pool = battle._question_pool(db, subject_id, level, kind, None, spread)
        if len(pool) >= need:
            break
    if len(pool) < 1:
        raise AppError("not_enough_questions")
    ids = [q["id"] for q in pool]
    random.shuffle(ids)
    return db.insert(
        "INSERT INTO practice_sessions (user_id, subject_id, kind, level, question_ids, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, subject_id, kind, level, utils.jdumps(ids[:need]), utils.fmt(utils.now())))


def _get(db, session_id, user_id):
    row = db.one("SELECT * FROM practice_sessions WHERE id=? AND user_id=?", (session_id, user_id))
    if row is None:
        raise AppError("not_found")          # boshqa foydalanuvchining sessiyasi ham "topilmadi"
    return row


def state(db, session_id, user_id):
    """Joriy holat: keyingi savol (to'g'ri javobsiz) yoki yakuniy natija."""
    row = _get(db, session_id, user_id)
    ids = utils.jloads(row["question_ids"], [])
    answers = utils.jloads(row["answers"], {})
    data = {"ok": True, "total": len(ids), "answered": len(answers), "correct": row["correct"],
            "status": row["status"]}
    if row["status"] == "open" and len(answers) < len(ids):
        index = len(answers)
        data["index"] = index
        data["question"] = battle.question_payload(db, ids[index])
    return data


def answer(db, session_id, user_id, index, choice):
    with db.tx():
        row = _get(db, session_id, user_id)
        ids = utils.jloads(row["question_ids"], [])
        answers = utils.jloads(row["answers"], {})
        if row["status"] != "open":
            raise AppError("bad_state")
        if index != len(answers) or index >= len(ids):
            raise AppError("wrong_question")           # faqat navbatdagi savolga, bir martadan javob
        q = db.one("SELECT correct_index, options, explanation FROM questions WHERE id=?", (ids[index],))
        options = utils.jloads(q["options"], [])
        if choice is not None and not (isinstance(choice, int) and not isinstance(choice, bool)
                                       and 0 <= choice < len(options)):
            raise AppError("bad_choice")
        is_correct = choice is not None and choice == q["correct_index"]
        answers[str(index)] = choice
        done = len(answers) >= len(ids)
        db.execute("UPDATE practice_sessions SET answers=?, correct=correct+?, status=?, finished_at=? WHERE id=?",
                   (utils.jdumps(answers), 1 if is_correct else 0, "done" if done else "open",
                    utils.fmt(utils.now()) if done else None, session_id))
        total_correct = row["correct"] + (1 if is_correct else 0)
    return {"ok": True, "is_correct": bool(is_correct), "correct_index": q["correct_index"],
            "explanation": q["explanation"] or "", "done": done, "correct": total_correct, "total": len(ids)}


def history(db, user_id, limit=MAX_HISTORY):
    rows = db.all("SELECT p.*, s.name AS subject_name, s.code AS subject_code FROM practice_sessions p "
                  "JOIN subjects s ON s.id=p.subject_id WHERE p.user_id=? AND p.status='done' "
                  "ORDER BY p.id DESC LIMIT ?", (user_id, limit))
    out = []
    for row in rows:
        item = dict(row)
        item["total"] = len(utils.jloads(row["question_ids"], []))
        out.append(item)
    return out
