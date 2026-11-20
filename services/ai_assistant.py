"""AI yordamchi: kunlik limit bilan (bepul tarif kvotasini tejash uchun)."""
from flask import current_app

from .. import utils
from ..providers import ai
from ..utils import AppError
from . import settings


def available():
    return ai.get_provider(current_app.config).name != "none"


def ask(db, user, prompt):
    prompt = utils.clean_text(prompt, 1500)
    if not prompt:
        raise AppError("message_required")
    provider = ai.get_provider(current_app.config)
    if provider.name == "none":
        raise AppError("ai_disabled")
    day_start, _ = utils.local_day_bounds(utils.local_today())
    used = db.scalar("SELECT COUNT(*) FROM ai_usage WHERE user_id=? AND created_at>=?", (user["id"], day_start), 0)
    if used >= int(settings.get(db, "ai.daily_limit")):
        raise AppError("ai_limit")
    try:
        answer = provider.ask(prompt)
    except ai.AIError as exc:
        code = str(exc)
        raise AppError(code if code in ("ai_quota", "ai_disabled") else "ai_failed")
    db.insert("INSERT INTO ai_usage (user_id, created_at) VALUES (?,?)", (user["id"], utils.fmt(utils.now())))
    return answer
