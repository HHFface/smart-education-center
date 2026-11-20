import pytest

from smartedu.bot import handlers
from smartedu.providers import telegram as tg
from smartedu.services import users


class FakeClient:
    def __init__(self):
        self.sent, self.edited, self.alerts = [], [], []

    def send_message(self, chat_id, text, markup=None):
        self.sent.append((chat_id, text, markup))

    def edit_message_text(self, chat_id, message_id, text, markup=None):
        self.edited.append((chat_id, message_id, text, markup))

    def answer_callback_query(self, cb_id, text=None):
        self.alerts.append(text)


@pytest.fixture()
def fake(app, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tg, "get_client", lambda _app: client)
    return client


def _buttons(markup):
    out = []
    for row in (markup or {}).get("inline_keyboard", []):
        for b in row:
            if "callback_data" in b:
                out.append(b["callback_data"])
    return out


def _link(app, db, username, chat_id):
    uid = db.one("SELECT id FROM users WHERE username=?", (username,))["id"]
    code = users.new_link_code(db, uid)
    with app.test_request_context():
        handlers.handle_update(app, {"message": {"chat": {"id": chat_id, "type": "private"}, "text": "/link " + code,
                                                 "from": {"language_code": "uz"}}})
    return uid


def test_not_linked_message(app, fake):
    with app.test_request_context():
        handlers.handle_update(app, {"message": {"chat": {"id": 5, "type": "private"}, "text": "/start"}})
    assert fake.sent and "link" in fake.sent[-1][1].lower()


def test_bad_code(app, fake):
    with app.test_request_context():
        handlers.handle_update(app, {"message": {"chat": {"id": 5, "type": "private"}, "text": "/link ABCD1234"}})
    assert fake.sent


@pytest.mark.parametrize("username", ["demo_s1", "demo_ali", "demo_parent", "demo_admin"])
def test_walk_every_button(app, db, fake, username):
    chat = 1000
    _link(app, db, username, chat)
    assert fake.sent, "ulanishdan keyin menyu yuborilishi kerak"
    seen, queue = set(), _buttons(fake.sent[-1][2])
    steps = 0
    while queue and steps < 150:
        data = queue.pop(0)
        if data in seen:
            continue
        seen.add(data)
        # xaridni/tasdiqlashni o'tkazib yuboramiz: bu ma'lumotni o'zgartiradi, alohida testlanadi
        with app.test_request_context():
            handlers.handle_update(app, {"callback_query": {"id": "x", "data": data, "from": {"language_code": "uz"},
                                                            "message": {"chat": {"id": chat}, "message_id": 1}}})
        steps += 1
        assert fake.edited or fake.sent
        last = fake.edited[-1] if fake.edited else None
        if last:
            assert last[2] and "Traceback" not in last[2]
            queue.extend(_buttons(last[3]))
    assert not any(a and "xatolik" in a.lower() for a in fake.alerts)
    assert steps >= 3


def test_language_switch_via_bot(app, db, fake):
    uid = _link(app, db, "demo_s1", 2000)
    with app.test_request_context():
        handlers.handle_update(app, {"callback_query": {"id": "x", "data": "lg:ru", "from": {},
                                                        "message": {"chat": {"id": 2000}, "message_id": 1}}})
    assert db.one("SELECT lang FROM users WHERE id=?", (uid,))["lang"] == "ru"
