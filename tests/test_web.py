import datetime
import json
import re

from conftest import csrf_of, login
from smartedu import utils
from smartedu.services import battle, access


def test_login_logout_and_wrong_password(app):
    client, resp = login(app, "demo_s1", "wrong-password")
    assert resp.status_code == 401                      # forma xato bilan qayta ko'rsatiladi
    client, resp = login(app, "demo_s1")
    assert resp.status_code in (302, 303)
    assert client.get("/student/").status_code == 200
    token = csrf_of(client, "/student/")
    client.post("/logout", data={"_csrf": token})
    assert client.get("/student/").status_code in (301, 302, 303, 401)


def test_lockout_after_failed_attempts(app):
    for _ in range(app.config["MAX_FAILED_LOGINS"] + 1):
        login(app, "demo_s3", "bad-password-1")
    client, resp = login(app, "demo_s3")                # to'g'ri parol ham bloklangan
    assert resp.status_code == 429
    assert client.get("/student/").status_code != 200


def test_csrf_required(app, client_for):
    client = client_for("demo_s1")
    resp = client.post("/battle/invite", data={"opponent_id": 2})   # tokensiz
    assert resp.status_code in (400, 403)


def test_role_isolation(client_for):
    student = client_for("demo_s1")
    assert student.get("/admin/").status_code in (302, 401, 403)
    assert student.get("/teacher/").status_code in (302, 401, 403)
    teacher = client_for("demo_ali")
    assert teacher.get("/admin/users").status_code in (302, 401, 403)
    admin = client_for("demo_admin")
    assert admin.get("/admin/").status_code == 200


def test_security_headers_and_csp(client_for):
    resp = client_for("demo_s1").get("/student/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_no_inline_handlers_in_pages(app, client_for):
    """CSP inline skriptni bloklaydi — shablonlarda on*= yoki <script> ichki kod bo'lmasin."""
    client = client_for("demo_s1")
    for path in ("/student/", "/battle/", "/student/shop", "/student/homework"):
        html = client.get(path).get_data(as_text=True)
        assert not re.search(r"\son(click|change|submit|load)=", html), path
        assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>\s*\S", html), path


def test_battle_pages_render(app, db, uid, client_for):
    a, b = uid["demo_s1"], uid["demo_s2"]
    subj = [s["id"] for s in access.student_subjects(db, a)][0]
    bid = battle.create_invite(db, a, b, subj, "vocabulary")
    ca, cb = client_for("demo_s1"), client_for("demo_s2")
    assert ca.get("/battle/%d" % bid).status_code == 200          # pending (challenger)
    assert cb.get("/battle/%d" % bid).status_code == 200          # pending (opponent)
    tok = csrf_of(cb, "/battle/%d" % bid)
    assert cb.post("/battle/%d/accept" % bid, data={"_csrf": tok}).status_code in (200, 302)
    assert ca.get("/battle/%d" % bid).status_code == 200          # ready
    for c in (ca, cb):
        r = c.post("/battle/%d/ready" % bid, data={"_csrf": csrf_of(c, "/battle/%d" % bid)})
        assert r.status_code in (200, 302)
    room = ca.get("/battle/%d" % bid)
    html = room.get_data(as_text=True)
    assert room.status_code == 200 and 'id="arena"' in html
    state = ca.get("/battle/api/%d/state" % bid).get_json()
    assert state["ok"] and state["status"] == "active"
    assert ca.get("/battle/%d/result" % bid).status_code in (200, 302)


def test_battle_forbidden_for_outsider(app, db, uid, client_for):
    a, b = uid["demo_s1"], uid["demo_s2"]
    subj = [s["id"] for s in access.student_subjects(db, a)][0]
    bid = battle.create_invite(db, a, b, subj, "vocabulary")
    outsider = client_for("demo_s4")
    assert outsider.get("/battle/%d" % bid).status_code in (302, 403, 404)


def test_registration_closed_setting(app, db):
    from smartedu.services import settings
    settings.set_value(db, "registration.open_for_students", False)
    client = app.test_client()
    page = client.get("/register")
    assert page.status_code == 200


def test_internal_tick_requires_token(app):
    client = app.test_client()
    assert client.get("/internal/tick").status_code in (401, 403, 404)
    assert client.get("/internal/tick?token=wrong").status_code in (401, 403, 404)


def test_language_and_theme_switch(client_for):
    client = client_for("demo_s1")
    for lang in ("uz", "uz_cyrl", "ru", "en", "ko"):
        client.post("/lang", data={"lang": lang, "_csrf": csrf_of(client, "/student/")})
        assert client.get("/student/").status_code == 200


def test_404_and_error_pages(client_for):
    client = client_for("demo_s1")
    resp = client.get("/no/such/page")
    assert resp.status_code == 404 and "<html" in resp.get_data(as_text=True).lower()


def test_templates_have_no_csp_violations():
    """Qat'iy CSP: shablonlarda inline on*=, style="" va <style> bo'lmasligi kerak."""
    import os
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "smartedu", "templates")
    bad = []
    for root, _d, files in os.walk(base):
        for name in files:
            text = open(os.path.join(root, name), encoding="utf-8").read()
            if re.search(r"\son[a-z]+=\"", text) or re.search(r"\sstyle=\"", text) or "<style" in text:
                bad.append(name)
            for _m in re.finditer(r"<script(?![^>]*\ssrc=)[^>]*>", text):
                bad.append(name + ":inline-script")
    assert not bad, bad
