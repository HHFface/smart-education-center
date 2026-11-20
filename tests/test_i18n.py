import json
import os
import re
import subprocess
import sys

import pytest

from smartedu import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("uz", "uz_cyrl", "ru", "en", "ko")
PLACE = re.compile(r"\{[a-z_]+\}")


def _cat():
    base = os.path.join(ROOT, "smartedu", "translations")
    return {c: json.load(open(os.path.join(base, c + ".json"), encoding="utf-8")) for c in LANGS}


def test_same_keys_everywhere():
    cat = _cat()
    ref = set(cat["uz"])
    for code in LANGS:
        assert set(cat[code]) == ref, code


def test_placeholders_match():
    cat = _cat()
    for key in cat["uz"]:
        ref = set(PLACE.findall(cat["uz"][key]))
        for code in LANGS:
            assert set(PLACE.findall(cat[code][key])) == ref, (code, key)


def test_cyrillic_has_no_latin_words_left():
    cat = _cat()
    allowed = {"XP", "AI", "JSON", "Elo", "Telegram", "Email", "Webhook", "backups", "env", "link", "K", "TTS", "PDF", "vs", "KOD"}
    for key, text in cat["uz_cyrl"].items():
        clean = PLACE.sub("", text)
        for word in re.findall(r"[A-Za-z]{2,}", clean):
            assert word in allowed, (key, word)


def test_scanner_finds_no_missing_keys():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "i18n_scan.py"), "--missing"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_dynamic_families_defined():
    cat = _cat()["uz"]
    from smartedu.services import notifications, settings, attendance, learning
    wanted = []
    wanted += ["notif.%s" % k for k in notifications.KINDS]
    wanted += ["notif_name.%s" % k for k in notifications.KINDS]
    wanted += ["ad.set.%s" % k for k in settings.DEFAULTS]
    wanted += ["ad.section.%s" % k.split(".")[0] for k in settings.DEFAULTS]
    wanted += ["st.reason_%s" % r for r in attendance.ABSENCE_REASONS]
    wanted += ["tc.reason.%s" % r for r in attendance.ABSENCE_REASONS]
    wanted += ["st.skill.%s" % s for s in learning.SKILLS]
    wanted += ["role.%s" % r for r in ("admin", "teacher", "student", "parent")]
    wanted += ["theme.%s" % r for r in ("system", "light", "dark")]
    wanted += ["weekday.%d" % d for d in range(7)]
    wanted += ["bt.kind.%s" % k for k in ("vocabulary", "grammar", "translation", "listening", "reading", "mixed", "teacher")]
    wanted += ["bt.end.%s" % k for k in ("void", "no_show", "expired", "declined", "cancelled")]
    wanted += ["st.scope.%s" % k for k in ("overall", "subject", "group", "weekly", "monthly", "battle")]
    wanted += ["ae.tab_%s" % k for k in ("pending", "debtors", "paid", "cancelled")]
    wanted += ["ae.qtype_%s" % k for k in ("vocabulary", "grammar", "translation", "listening", "reading")]
    wanted += ["st.kind.%s" % k for k in ("text", "image", "document", "audio", "video", "test")]
    wanted += ["reason.%s" % k for k in ("admin_adjust", "battle_reward", "club", "homework_checked", "homework_done",
               "homework_missed", "shop_purchase", "shop_refund", "teacher_award", "revoked",
               "attendance_present", "attendance_late", "attendance_excused", "attendance_absent")]
    missing = [k for k in wanted if k not in cat]
    assert not missing, missing


def test_every_notification_renders_in_every_language(app):
    from smartedu.services import notifications
    payload = {"title": "T", "score": 5, "time": "10:00", "status": "present", "group": "G", "amount": -10,
               "code": "homework_missed", "reason": "r", "seq": 1, "name": "N", "kind": "vocabulary", "minutes": 5,
               "result": "win", "delta": 12, "course": "C", "date": "01.01", "item": "I", "xp": 1, "coin": 2, "text": "x"}
    with app.app_context():
        for lang in LANGS:
            for kind in notifications.KINDS:
                text = notifications.render(kind, payload, lang)
                assert text and "{" not in text, (lang, kind, text)
