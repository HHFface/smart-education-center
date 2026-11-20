"""Vaqt bo'yicha ishlar (tick): Battle eslatmalari, uy vazifasi jarimalari, to'lov eslatmalari, xabarlar navbati.

Bepul hostingda doimiy fon jarayoni bo'lmaganda ham ishlashi uchun 3 usul qo'llab-quvvatlanadi:
  1) GET /internal/tick?token=...  (cron-job.org kabi bepul tashqi cron, har 1-5 daqiqada)
  2) python manage.py tick         (server cron yoki PythonAnywhere "Scheduled task")
  3) Saytga kirishlar paytida har 60 soniyada bir marta (OPPORTUNISTIC_TICK)
"""
import logging

from .. import utils
from . import battle, finance, homework, notifications

log = logging.getLogger(__name__)
TICK_KEY = "sys.last_tick"
MIN_INTERVAL = 60


def run(db, now=None):
    """Hamma davriy ishlarni bajaradi. Har biri alohida himoyalangan."""
    summary = {}
    for name, job in (
        ("battles", lambda: battle.run_due(db)),
        ("homework", lambda: homework.process_overdue(db)),
        ("payments", lambda: finance.due_reminders(db)),
        ("outbox", lambda: notifications.flush_outbox(db)),
    ):
        try:
            summary[name] = job()
        except Exception:
            log.exception("tick: '%s' bajarilmadi", name)
            summary[name] = "error"
    return summary


def run_if_due(db):
    """Oxirgi tick 60 soniyadan oldin bo'lgan bo'lsagina bajaradi (parallel so'rovlarda faqat bittasi)."""
    now = utils.now()
    stamp = utils.fmt(now)
    threshold = utils.fmt(now - __import__("datetime").timedelta(seconds=MIN_INTERVAL))
    db.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?,?,?)",
               (TICK_KEY, "1970-01-01 00:00:00", stamp))
    claimed = db.execute("UPDATE settings SET value=?, updated_at=? WHERE key=? AND value<=?",
                         (stamp, stamp, TICK_KEY, threshold)).rowcount
    if claimed:
        return run(db, now)
    return None
