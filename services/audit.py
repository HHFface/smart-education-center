"""Muhim harakatlar tarixi (audit)."""
import logging

from flask import has_request_context, request

from .. import utils

log = logging.getLogger(__name__)


def record(db, actor_id, action, entity=None, entity_id=None, detail=None):
    ip = None
    if has_request_context():
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = (forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or ""))[:45]
    try:
        db.insert(
            "INSERT INTO audit_log (actor_id, action, entity, entity_id, detail, ip, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (actor_id, action, entity, entity_id,
             utils.jdumps(detail) if detail is not None else None, ip, utils.fmt(utils.now())),
        )
    except Exception:  # audit hech qachon asosiy jarayonni to'xtatmasligi kerak
        log.exception("Audit yozilmadi: %s", action)
