import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTEDU_HOME", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("OPPORTUNISTIC_TICK", "0")
    for var in ("TELEGRAM_BOT_TOKEN", "AI_PROVIDER", "EMAIL_PROVIDER", "PAYMENT_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    from smartedu import create_app, seed, utils
    from smartedu.db import DB
    application = create_app({"TESTING": True, "PROPAGATE_EXCEPTIONS": True})
    db = DB(application.config["DATABASE_PATH"])
    seed.seed_demo(db)
    db.close()
    yield application
    utils.set_now(None)


@pytest.fixture()
def db(app):
    from smartedu.db import DB
    conn = DB(app.config["DATABASE_PATH"])
    yield conn
    conn.close()


@pytest.fixture()
def uid(db):
    """username -> id"""
    return {r["username"]: r["id"] for r in db.all("SELECT id, username FROM users")}


def login(app, username, password="Demo12345"):
    client = app.test_client()
    page = client.get("/login")
    token = re.search(r'name="_csrf" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    resp = client.post("/login", data={"username": username, "password": password, "_csrf": token})
    return client, resp


def csrf_of(client, path="/"):
    page = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', page) or re.search(r'name="csrf-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


@pytest.fixture()
def client_for(app):
    def make(username):
        client, resp = login(app, username)
        assert resp.status_code in (302, 303), resp.status_code
        return client
    return make
