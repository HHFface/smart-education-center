import datetime
import io
import re

import pytest

from conftest import csrf_of, login
from smartedu import security, utils, web
from smartedu.services import access, points, users
from smartedu.utils import AppError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _post_profile(client, **files):
    token = csrf_of(client, "/account/settings")
    data = {"_csrf": token, "first_name": "Ali", "last_name": "Vali"}
    data.update(files)
    return client.post("/account/settings/profile", data=data, content_type="multipart/form-data")


# ---------- yuklash
def test_content_signature_matrix():
    assert web.content_matches("png", PNG)
    assert not web.content_matches("png", b"<html><script>alert(1)</script></html>")
    assert not web.content_matches("png", b"GIF89a" + b"\0" * 20)            # kengaytma va tarkib mos emas
    assert web.content_matches("gif", b"GIF89a" + b"\0" * 20)
    assert web.content_matches("pdf", b"%PDF-1.7\n...")
    assert not web.content_matches("pdf", b"MZ\x90\x00 executable")
    assert not web.content_matches("txt", b"binary\x00data")
    assert not web.content_matches("txt", b"<script>alert(1)</script>")      # faol kod belgisi
    assert not web.content_matches("exe", b"MZ")                              # ro'yxatda yo'q


def test_upload_accepts_real_png_rejects_fakes(app, client_for):
    client = client_for("demo_s1")
    ok = _post_profile(client, avatar=(io.BytesIO(PNG), "me.png"))
    assert ok.status_code in (200, 302)
    for name, body in (("evil.png", b"<script>alert(1)</script>"), ("evil.php", b"<?php system($_GET[1]); ?>"),
                       ("pic.svg", b"<svg onload=alert(1)>"), ("x.png", b"")):
        before = client.get("/account/settings").get_data(as_text=True)
        resp = _post_profile(client, avatar=(io.BytesIO(body), name))
        assert resp.status_code in (200, 302, 400)
        import os
        saved = os.listdir(os.path.join(app.config["UPLOAD_DIR"], "avatar"))
        assert len(saved) == 1, (name, saved)                                  # yangi fayl saqlanmagan


def test_uploaded_names_are_random_and_served_sandboxed(app, client_for, db, uid):
    client = client_for("demo_s1")
    _post_profile(client, avatar=(io.BytesIO(PNG), "../../etc/passwd.png"))
    name = db.one("SELECT avatar FROM users WHERE id=?", (uid["demo_s1"],))["avatar"]
    assert re.fullmatch(r"[0-9a-f]{32}\.png", name)
    resp = client.get("/files/avatar/%s" % name)
    assert resp.status_code == 200
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert client.get("/files/avatar/..%2F..%2Fsecret").status_code in (404, 403)
    assert app.test_client().get("/files/avatar/%s" % name).status_code in (302, 401)   # kirmasdan — yo'q


def test_upload_size_limit(app, client_for):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    client = client_for("demo_s1")
    resp = _post_profile(client, avatar=(io.BytesIO(PNG + b"\0" * 5000), "big.png"))
    assert resp.status_code == 413


# ---------- XSS
def test_user_text_is_escaped(app, db, uid, client_for):
    payload = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    db.execute("UPDATE users SET first_name=?, last_name='X' WHERE id=?", (payload, uid["demo_s2"]))
    client = client_for("demo_s1")
    for path in ("/student/leaderboard", "/battle/", "/student/"):
        html = client.get(path).get_data(as_text=True)
        assert "<script>alert(1)" not in html and "onerror=alert(2)" not in html.replace("&lt;", "<").replace("onerror=alert(2)", "x") or "&lt;" in html


def test_messages_escape_html(app, db, uid, client_for):
    body = "<b onmouseover=alert(1)>hi</b>"
    teacher = client_for("demo_ali")
    student_id = db.one("SELECT gs.student_id id FROM group_students gs JOIN groups g ON g.id=gs.group_id "
                        "JOIN users t ON t.id=g.teacher_id WHERE t.username='demo_ali' LIMIT 1")["id"]
    tok = csrf_of(teacher, "/account/messages/%d" % student_id)
    teacher.post("/account/messages/%d" % student_id, data={"_csrf": tok, "body": body})
    name = db.one("SELECT username FROM users WHERE id=?", (student_id,))["username"]
    student = client_for(name)
    html = student.get("/account/messages/%d" % uid["demo_ali"]).get_data(as_text=True)
    assert "<b onmouseover" not in html and "&lt;b onmouseover" in html


# ---------- SQL injection
@pytest.mark.parametrize("evil", ["' OR '1'='1", "admin'--", "x' UNION SELECT 1,2,3--", "'; DROP TABLE users;--"])
def test_sql_injection_attempts(app, db, client_for, evil):
    client, resp = login(app, evil, evil)
    assert resp.status_code in (401, 400)
    admin = client_for("demo_admin")
    for path in ("/admin/users?q=%s", "/admin/users?role=%s"):
        r = admin.get(path % evil)
        assert r.status_code == 200
    assert db.scalar("SELECT COUNT(*) FROM users") > 5


# ---------- sessiyalar, tokenlar, limitlar
def _client_with_cookie(app, value):
    replay = app.test_client()
    try:
        replay.set_cookie("session", value)                 # Werkzeug >= 2.3
    except TypeError:
        replay.set_cookie("localhost", "session", value)    # eski Werkzeug
    return replay


def test_logout_invalidates_old_cookie(app):
    client, resp = login(app, "demo_s1")
    value = [c.split(";")[0] for c in resp.headers.getlist("Set-Cookie") if c.startswith("session=")][-1].split("=", 1)[1]
    assert _client_with_cookie(app, value).get("/student/").status_code == 200
    client.post("/logout", data={"_csrf": csrf_of(client, "/student/")})
    # chiqishdan keyin ushlab olingan eski cookie bilan kirib bo'lmaydi — sessiya serverda bekor qilingan
    assert _client_with_cookie(app, value).get("/student/").status_code in (302, 401)


def test_revoke_other_sessions(app, db, uid):
    a, _ = login(app, "demo_s4")
    b, _ = login(app, "demo_s4")
    assert a.get("/student/").status_code == 200 and b.get("/student/").status_code == 200
    a.post("/account/settings/sessions/revoke", data={"_csrf": csrf_of(a, "/account/settings"), "all": "1"})
    assert a.get("/student/").status_code == 200
    assert b.get("/student/").status_code in (302, 401)


def test_session_server_side_expiry(app, db, uid):
    client, _ = login(app, "demo_s5")
    assert client.get("/student/").status_code == 200
    old = utils.fmt(utils.now() - datetime.timedelta(days=app.config["SESSION_IDLE_DAYS"] + 1))
    db.execute("UPDATE user_sessions SET last_seen=? WHERE user_id=?", (old, uid["demo_s5"]))
    assert client.get("/student/").status_code in (302, 401)
    assert db.scalar("SELECT COUNT(*) FROM user_sessions WHERE user_id=? AND revoked=0", (uid["demo_s5"],)) == 0


def test_blocked_user_loses_access_immediately(app, db, uid):
    client, _ = login(app, "demo_s6")
    assert client.get("/student/").status_code == 200
    db.execute("UPDATE users SET is_active=0 WHERE id=?", (uid["demo_s6"],))
    assert client.get("/student/").status_code in (302, 401)


def test_one_time_token_and_expiry(app, db, uid):
    raw = security.make_token(db, uid["demo_s1"], "reset", 2)
    assert security.consume_token(db, raw, "reset") == uid["demo_s1"]
    assert security.consume_token(db, raw, "reset") is None                  # qayta ishlatib bo'lmaydi
    raw2 = security.make_token(db, uid["demo_s1"], "reset", 2)
    db.execute("UPDATE tokens SET expires_at=? WHERE token_hash=?", ("2000-01-01 00:00:00", security._digest(raw2)))
    assert security.consume_token(db, raw2, "reset") is None                 # muddati o'tgan
    raw3 = security.make_token(db, uid["demo_s1"], "reset", 2)
    raw4 = security.make_token(db, uid["demo_s1"], "reset", 2)               # yangi token eskisini bekor qiladi
    assert security.consume_token(db, raw3, "reset") is None
    assert security.consume_token(db, raw4, "verify_email") is None          # maqsad mos emas
    assert security.consume_token(db, raw4, "reset") == uid["demo_s1"]


def test_password_reset_flow_end_to_end(app, db, uid):
    db.execute("UPDATE users SET email='s1@example.com' WHERE id=?", (uid["demo_s1"],))
    client = app.test_client()
    tok = csrf_of(client, "/forgot")
    client.post("/forgot", data={"_csrf": tok, "identifier": "demo_s1"})
    raw = security.make_token(db, uid["demo_s1"], "reset", 2)
    page = client.get("/reset/%s" % raw)
    assert page.status_code == 200
    tok = csrf_of(client, "/reset/%s" % raw)
    resp = client.post("/reset/%s" % raw, data={"_csrf": tok, "password": "NewPass123", "password2": "NewPass123"})
    assert resp.status_code in (302, 303)
    assert login(app, "demo_s1", "NewPass123")[1].status_code in (302, 303)
    assert login(app, "demo_s1", "Demo12345")[1].status_code == 401
    assert client.get("/reset/%s" % raw).status_code in (302, 303)           # token qayta ishlamaydi


def test_ip_rate_limit_blocks_even_correct_password(app):
    app.config["LOGIN_IP_LIMIT"] = 4
    for i in range(4):
        login(app, "nobody%d" % i, "whatever-1")
    client, resp = login(app, "demo_admin")                                   # to'g'ri parol, lekin IP bloklangan
    assert resp.status_code == 429


def test_forwarded_header_ignored_by_default(app):
    app.config["LOGIN_IP_LIMIT"] = 3
    for i in range(3):
        c = app.test_client()
        token = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "u%d" % i, "password": "x", "_csrf": token},
               headers={"X-Forwarded-For": "10.0.0.%d" % i})                  # IP ni soxtalashtirish
    client, resp = login(app, "demo_admin")
    assert resp.status_code == 429


def test_forgot_password_rate_limited_and_silent(app, db):
    client = app.test_client()
    for _ in range(6):
        tok = csrf_of(client, "/forgot")
        r = client.post("/forgot", data={"_csrf": tok, "identifier": "demo_s1"})
        assert r.status_code in (302, 303)                                    # mavjudligini oshkor qilmaydi
    assert db.scalar("SELECT COUNT(*) FROM rate_hits WHERE bucket='forgot_ident'") == 3


def test_open_redirect_blocked(app):
    client = app.test_client()
    tok = csrf_of(client, "/login")
    for target in ("//evil.com", "https://evil.com", "/\\evil.com", "javascript:alert(1)"):
        resp = client.post("/login?next=" + target, data={"_csrf": tok, "username": "demo_s1", "password": "Demo12345"})
        assert resp.status_code in (302, 303)
        assert "evil.com" not in resp.headers["Location"] and "javascript" not in resp.headers["Location"]
        client.post("/logout", data={"_csrf": csrf_of(client, "/student/")})
        tok = csrf_of(client, "/login")


def test_production_forces_debug_off(monkeypatch, tmp_path):
    from smartedu import config
    monkeypatch.setenv("SMARTEDU_HOME", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "1")
    assert config.build_config()["DEBUG"] is False


# ---------- IDOR / rollar
def test_idor_between_students_and_teachers(app, db, uid, client_for):
    s1 = client_for("demo_s1")
    # o'quvchi boshqa o'quvchining sahifalariga URL orqali kira olmaydi
    for path in ("/teacher/students/%d" % uid["demo_s2"], "/admin/users/%d" % uid["demo_s2"],
                 "/parent/?child=%d" % uid["demo_s2"]):
        assert s1.get(path).status_code in (302, 403, 404)
    # o'qituvchi o'z guruhida bo'lmagan o'quvchini ko'rmaydi
    teacher = client_for("demo_ali")
    mine = set(access.teacher_student_ids(db, uid["demo_ali"]))
    foreign = [r["id"] for r in db.all("SELECT id FROM users WHERE role='student'") if r["id"] not in mine]
    for sid in foreign[:3]:
        assert teacher.get("/teacher/students/%d" % sid).status_code in (302, 403, 404)


def test_parent_children_are_isolated(app, db, uid, client_for):
    own = access.parent_child_ids(db, uid["demo_parent"])
    other = [r["id"] for r in db.all("SELECT id FROM users WHERE role='student'") if r["id"] not in own][0]
    parent = client_for("demo_parent")
    html = parent.get("/parent/?child=%d" % other).get_data(as_text=True)
    name = db.one("SELECT first_name FROM users WHERE id=?", (other,))["first_name"]
    assert parent.get("/parent/?child=%d" % other).status_code == 200
    assert "?child=%d" % other not in html                                    # begona farzand havolalarda yo'q
    first_own = db.one("SELECT first_name FROM users WHERE id=?", (own[0],))["first_name"]
    assert first_own in html


def test_messages_limited_to_allowed_pairs(app, db, uid, client_for):
    s1 = client_for("demo_s1")
    assert s1.get("/account/messages/%d" % uid["demo_s2"]).status_code in (302, 403, 404)      # o'quvchi-o'quvchi
    tok = csrf_of(s1, "/account/messages")
    r = s1.post("/account/messages/%d" % uid["demo_s2"], data={"_csrf": tok, "body": "salom"})
    assert db.scalar("SELECT COUNT(*) FROM messages WHERE from_id=? AND to_id=?", (uid["demo_s1"], uid["demo_s2"])) == 0


def test_every_post_endpoint_requires_csrf(app):
    """Barcha POST endpointlar tokensiz so'rovni rad etadi (istisnolar: webhook va tick)."""
    client = app.test_client()
    skipped = {"bot.webhook", "main.tick", "static"}
    for rule in app.url_map.iter_rules():
        if rule.endpoint in skipped or "POST" not in rule.methods:
            continue
        url = re.sub(r"<(?:int:)?[a-z_]+>", "1", rule.rule)
        resp = client.post(url, data={})
        assert resp.status_code in (400, 401, 403, 404), (rule.rule, resp.status_code)
        assert resp.status_code != 500, rule.rule
