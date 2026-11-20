"""Savollar banki (Battle uchun): qo'shish, tekshirish, ommaviy import."""
import json

from .. import utils
from ..utils import AppError
from . import battle


def add_question(db, subject_id, level, qtype, text, options, correct_index, explanation="", passage="",
                 audio_url="", challenge_id=None, created_by=None):
    if level not in battle.LEVELS:
        raise AppError("bad_level")
    if qtype not in battle.QTYPES:
        raise AppError("bad_kind")
    text = utils.clean_text(text, 500)
    options = [utils.clean_text(o, 200) for o in (options or []) if utils.clean_text(o, 200)]
    if not text:
        raise AppError("title_required")
    if not 2 <= len(options) <= 6 or len(set(o.lower() for o in options)) != len(options):
        raise AppError("bad_options")
    correct_index = utils.to_int(correct_index)
    if correct_index is None or not 0 <= correct_index < len(options):
        raise AppError("bad_options")
    if db.one("SELECT 1 FROM subjects WHERE id=?", (subject_id,)) is None:
        raise AppError("not_found")
    return db.insert(
        "INSERT INTO questions (subject_id, level, qtype, text, passage, audio_url, options, correct_index, "
        "explanation, challenge_id, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (subject_id, level, qtype, text, utils.clean_text(passage, 3000), utils.clean_text(audio_url, 300),
         utils.jdumps(options), correct_index, utils.clean_text(explanation, 500), challenge_id, created_by))


def import_json(db, raw, created_by=None):
    """JSON ro'yxat: [{subject: 'en', level: 'A1', type: 'vocabulary', text, options: [], correct: 0, explanation}]
    Yaroqli savollar soni va xatoliklar ro'yxati qaytariladi."""
    try:
        items = json.loads(raw)
    except ValueError:
        raise AppError("bad_json")
    if not isinstance(items, list):
        raise AppError("bad_json")
    added, errors = 0, []
    for index, item in enumerate(items, start=1):
        try:
            subject = db.one("SELECT id FROM subjects WHERE code=?", (str(item.get("subject", "")).lower(),))
            if subject is None:
                raise AppError("not_found")
            add_question(db, subject["id"], item.get("level", "A1"), item.get("type", "vocabulary"), item.get("text"),
                         item.get("options"), item.get("correct"), item.get("explanation", ""),
                         item.get("passage", ""), item.get("audio_url", ""), None, created_by)
            added += 1
        except (AppError, AttributeError) as exc:
            errors.append("#%d: %s" % (index, getattr(exc, "code", "bad_json")))
    return added, errors
