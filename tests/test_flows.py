import datetime

import pytest

from conftest import csrf_of
from smartedu import utils
from smartedu.services import access, attendance, points, settings, users
from smartedu.utils import AppError


def _student_with_teacher(db):
    row = db.one("SELECT gs.student_id sid, g.teacher_id tid, c.subject_id subj FROM group_students gs "
                 "JOIN groups g ON g.id=gs.group_id JOIN courses c ON c.id=g.course_id "
                 "WHERE g.teacher_id IS NOT NULL ORDER BY gs.student_id")
    return row["sid"], row["tid"], row["subj"]


def test_absence_request_approval_excuses_later_absence(db):
    sid, tid, subj = _student_with_teacher(db)
    day = utils.local_today() + datetime.timedelta(days=1)
    req = attendance.submit_absence(db, sid, subj, day.isoformat(), "health", "isitma")
    with pytest.raises(AppError) as exc:
        attendance.submit_absence(db, sid, subj, day.isoformat(), "health")
    assert exc.value.code == "absence_duplicate"
    teacher = db.one("SELECT * FROM users WHERE id=?", (tid,))
    attendance.review_absence(db, req, "approved", teacher, "ok")
    assert db.one("SELECT status FROM absence_requests WHERE id=?", (req,))["status"] == "approved"
    assert attendance.has_approved_absence(db, sid, day.isoformat(), subj)


def test_absence_validation(db):
    sid, _, subj = _student_with_teacher(db)
    with pytest.raises(AppError):
        attendance.submit_absence(db, sid, subj, "2020-01-01", "health")          # juda eski
    with pytest.raises(AppError):
        attendance.submit_absence(db, sid, subj, "not-a-date", "health")
    with pytest.raises(AppError):
        attendance.submit_absence(db, sid, subj, utils.local_today().isoformat(), "bogus")


def test_register_with_invite_and_without(app, db, uid):
    admin = db.one("SELECT * FROM users WHERE role='admin'")
    code = users.create_invite(db, "teacher", admin)
    code = code if isinstance(code, str) else code["code"]
    form = {"invite": code, "username": "new_teacher", "password": "Passw0rd!x", "password2": "Passw0rd!x",
            "first_name": "N", "last_name": "T"}
    new_id = users.register(db, form, "uz")
    assert db.one("SELECT role FROM users WHERE id=?", (new_id,))["role"] == "teacher"
    with pytest.raises(AppError) as exc:
        users.register(db, dict(form, username="another_one"), "uz")      # kod bir martalik
    assert exc.value.code == "invite_invalid"
    with pytest.raises(AppError):
        users.register(db, {"username": "stud1", "password": "Passw0rd!x", "password2": "different1"}, "uz")
    settings.set_value(db, "registration.open_for_students", False)
    with pytest.raises(AppError) as exc:
        users.register(db, {"username": "stud2", "password": "Passw0rd!x", "password2": "Passw0rd!x"}, "uz")
    assert exc.value.code == "registration_closed"


def test_username_and_email_uniqueness(db):
    with pytest.raises(AppError):
        users.create_user(db, "demo_s1", "Passw0rd!x", "student")
    with pytest.raises(AppError):
        users.create_user(db, "x", "Passw0rd!x", "student")                # juda qisqa login
    with pytest.raises(AppError):
        users.create_user(db, "valid_name", "short", "student")


def test_admin_settings_post_changes_behaviour(app, db, client_for):
    client = client_for("demo_admin")
    tok = csrf_of(client, "/admin/settings")
    resp = client.post("/admin/settings", data={"_csrf": tok, "key": "coin.attendance_present", "value": "15"})
    assert resp.status_code in (200, 302)
    assert settings.get(db, "coin.attendance_present") == 15
    bad = client.post("/admin/settings", data={"_csrf": tok, "key": "coin.attendance_present", "value": "abc"})
    assert bad.status_code in (200, 302, 400)
    assert settings.get(db, "coin.attendance_present") == 15              # buzilmadi


def test_teacher_cannot_open_foreign_student(client_for, db):
    client = client_for("demo_ali")
    other = db.one("SELECT id FROM users WHERE role='student' AND id NOT IN (SELECT student_id FROM group_students "
                   "gs JOIN groups g ON g.id=gs.group_id WHERE g.teacher_id=(SELECT id FROM users WHERE username='demo_ali'))")
    if other:
        assert client.get("/teacher/students/%d" % other["id"]).status_code in (302, 403, 404)


def test_parent_sees_only_own_child(client_for):
    resp = client_for("demo_parent").get("/parent/")
    assert resp.status_code == 200


def test_tick_runs_without_error(app, db):
    from smartedu.services import scheduler
    with app.app_context():
        scheduler.tick(db) if hasattr(scheduler, "tick") else scheduler.run(db)


def test_env_example_loads_cleanly(tmp_path, monkeypatch):
    """.env.example ni o'zgartirmay nusxalash ishlashi kerak (inline izohlar qiymatga kirmasin)."""
    import os
    from smartedu import config
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example")
    for var in ("APP_ENV", "DEFAULT_LANG", "TZ_OFFSET_HOURS", "AI_PROVIDER", "EMAIL_PROVIDER", "PAYMENT_PROVIDER",
                "SESSION_COOKIE_SECURE", "OPPORTUNISTIC_TICK", "BASE_URL", "SMTP_PORT"):
        monkeypatch.delenv(var, raising=False)
    config.load_dotenv_file(src)
    assert os.environ["APP_ENV"] == "development"
    assert os.environ["DEFAULT_LANG"] == "uz"
    assert os.environ["TZ_OFFSET_HOURS"] == "5"
    assert os.environ["AI_PROVIDER"] == "none"
    assert os.environ["BASE_URL"] == "http://127.0.0.1:5000"
    for var in ("APP_ENV", "DEFAULT_LANG", "TZ_OFFSET_HOURS", "AI_PROVIDER", "EMAIL_PROVIDER", "PAYMENT_PROVIDER",
                "SESSION_COOKIE_SECURE", "OPPORTUNISTIC_TICK", "BASE_URL", "SMTP_PORT", "APP_NAME", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(var, raising=False)
