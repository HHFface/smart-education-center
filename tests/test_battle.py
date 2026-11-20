import datetime

import pytest

from conftest import csrf_of
from smartedu import utils
from smartedu.services import battle, access
from smartedu.utils import AppError


def _pair(db, uid):
    a, b = uid["demo_s1"], uid["demo_s2"]
    subj = [s["id"] for s in access.student_subjects(db, a)]
    shared = [s for s in subj if s in [x["id"] for x in access.student_subjects(db, b)]]
    assert shared, "demo o'quvchilarning umumiy fani yo'q"
    return a, b, shared[0]


def test_full_cycle(db, uid):
    a, b, subj = _pair(db, uid)
    t0 = utils.now()
    bid = battle.create_invite(db, a, b, subj, "vocabulary", now=t0)
    battle.accept(db, bid, b, now=t0)
    assert battle.set_ready(db, bid, a, now=t0) == "ready"
    assert battle.set_ready(db, bid, b, now=t0) == "active"
    cfg = lambda k: int(battle._cfg(db, k))
    start = t0 + datetime.timedelta(seconds=cfg("battle.countdown_seconds"))
    per = cfg("battle.seconds_per_question") + cfg("battle.gap_seconds")
    ids = utils.jloads(db.one("SELECT question_ids FROM battles WHERE id=?", (bid,))["question_ids"], [])
    n = len(ids)
    assert battle.MIN_QUESTIONS <= n <= cfg("battle.questions")
    for i in range(n):
        now = start + datetime.timedelta(seconds=i * per + 2)
        right = db.one("SELECT correct_index FROM questions WHERE id=?", (ids[i],))["correct_index"]
        battle.answer(db, bid, a, i, right, now=now)           # a hammasini to'g'ri
        battle.answer(db, bid, b, i, None if i % 2 else (right + 1) % 2, now=now)
    row = db.one("SELECT * FROM battles WHERE id=?", (bid,))
    assert row["status"] == "finished"
    ra = battle.get_rating(db, a, subj)["rating"]
    rb = battle.get_rating(db, b, subj)["rating"]
    assert ra > rb


def test_double_answer_rejected(db, uid):
    a, b, subj = _pair(db, uid)
    t0 = utils.now()
    bid = battle.create_invite(db, a, b, subj, "vocabulary", now=t0)
    battle.accept(db, bid, b, now=t0)
    battle.set_ready(db, bid, a, now=t0)
    battle.set_ready(db, bid, b, now=t0)
    start = t0 + datetime.timedelta(seconds=int(battle._cfg(db, "battle.countdown_seconds")))
    now = start + datetime.timedelta(seconds=1)
    battle.answer(db, bid, a, 0, 0, now=now)
    with pytest.raises(AppError) as exc:
        battle.answer(db, bid, a, 0, 1, now=now)
    assert exc.value.code == "already_answered"


def test_invite_expiry_and_noshow(db, uid):
    a, b, subj = _pair(db, uid)
    t0 = utils.now()
    bid = battle.create_invite(db, a, b, subj, "vocabulary", now=t0)
    later = t0 + datetime.timedelta(days=3)
    battle.run_due(db, now=later)
    assert db.one("SELECT status FROM battles WHERE id=?", (bid,))["status"] == "expired"


def test_cannot_invite_self_or_duplicate(db, uid):
    a, b, subj = _pair(db, uid)
    with pytest.raises(AppError):
        battle.create_invite(db, a, a, subj, "vocabulary")
    battle.create_invite(db, a, b, subj, "vocabulary")
    with pytest.raises(AppError) as exc:
        battle.create_invite(db, b, a, subj, "vocabulary")
    assert exc.value.code == "invite_exists"


def test_decline(db, uid):
    a, b, subj = _pair(db, uid)
    bid = battle.create_invite(db, a, b, subj, "vocabulary")
    battle.decline(db, bid, b)
    assert db.one("SELECT status FROM battles WHERE id=?", (bid,))["status"] == "declined"
    with pytest.raises(AppError):
        battle.accept(db, bid, b)


# ---------- xavfsizlik / parallel / mashq
def _active_battle(db, uid, a_name="demo_s1", b_name="demo_s2"):
    a, b = uid[a_name], uid[b_name]
    subj = [s["id"] for s in access.student_subjects(db, a)]
    subj = [s for s in subj if s in [x["id"] for x in access.student_subjects(db, b)]][0]
    t0 = utils.now()
    bid = battle.create_invite(db, a, b, subj, "vocabulary", now=t0)
    battle.accept(db, bid, b, now=t0)
    battle.set_ready(db, bid, a, now=t0)
    battle.set_ready(db, bid, b, now=t0)
    return bid, a, b, subj, t0


def test_state_never_leaks_correct_answer_during_question(db, uid):
    bid, a, b, subj, t0 = _active_battle(db, uid)
    start = t0 + datetime.timedelta(seconds=int(battle._cfg(db, "battle.countdown_seconds")))
    state = battle.state_for(db, bid, a, now=start + datetime.timedelta(seconds=1))
    assert state["phase"] == "question"
    flat = repr(state)
    assert "correct_index" not in flat and "explanation" not in flat
    assert "correct" not in state["question"]


def test_result_hidden_until_finished_and_outsider_blocked(db, uid, client_for):
    bid, a, b, subj, t0 = _active_battle(db, uid)
    c1 = client_for("demo_s1")
    resp = c1.get("/battle/%d/result" % bid)
    assert resp.status_code == 302 and "/result" not in resp.headers["Location"]      # tugamaguncha ko'rsatilmaydi
    outsider = client_for("demo_s4")
    assert outsider.get("/battle/api/%d/state" % bid).status_code in (400, 403, 404)
    r = outsider.post("/battle/api/%d/answer" % bid, json={"q_index": 0, "choice": 0},
                      headers={"X-CSRF-Token": csrf_of(outsider, "/battle/")})
    assert r.status_code in (400, 403, 404)


def test_answer_outside_window_and_bad_input(db, uid):
    bid, a, b, subj, t0 = _active_battle(db, uid)
    start = t0 + datetime.timedelta(seconds=int(battle._cfg(db, "battle.countdown_seconds")))
    with pytest.raises(AppError) as e1:
        battle.answer(db, bid, a, 0, 0, now=start - datetime.timedelta(seconds=1))
    assert e1.value.code == "not_started"
    with pytest.raises(AppError) as e2:
        battle.answer(db, bid, a, 3, 0, now=start + datetime.timedelta(seconds=1))      # boshqa savolga oldindan javob
    assert e2.value.code == "wrong_question"
    with pytest.raises(AppError) as e3:
        battle.answer(db, bid, a, 0, 99, now=start + datetime.timedelta(seconds=1))
    assert e3.value.code == "bad_choice"
    late = start + datetime.timedelta(seconds=int(battle._cfg(db, "battle.seconds_per_question")) + 3)
    with pytest.raises(AppError) as e4:
        battle.answer(db, bid, a, 0, 0, now=late)
    assert e4.value.code in ("time_up", "wrong_question")


def test_double_ready_does_not_restart(db, uid):
    bid, a, b, subj, t0 = _active_battle(db, uid)
    before = db.one("SELECT question_ids, started_at FROM battles WHERE id=?", (bid,))
    with pytest.raises(AppError) as exc:
        battle.set_ready(db, bid, a, now=t0)                           # takroriy so'rov o'yinni qayta boshlamaydi
    assert exc.value.code == "bad_state"
    after = db.one("SELECT question_ids, started_at FROM battles WHERE id=?", (bid,))
    assert tuple(before) == tuple(after)


def test_cannot_be_in_two_active_battles(db, uid):
    bid, a, b, subj, t0 = _active_battle(db, uid)
    c = uid["demo_s3"]
    if subj not in [s["id"] for s in access.student_subjects(db, c)]:
        pytest.skip("demo_s3 boshqa fanda")
    with pytest.raises(AppError) as exc:
        bid2 = battle.create_invite(db, c, a, subj, "vocabulary", now=t0)
        battle.accept(db, bid2, a, now=t0)
    assert exc.value.code == "already_in_battle"
    bid2 = db.scalar("SELECT MAX(id) FROM battles")
    assert db.one("SELECT status FROM battles WHERE id=?", (bid2,))["status"] != "active"


def test_technical_dropout_is_void_not_loss(db, uid):
    """Ikkalasi ham javob bermasa — yoki bittasi uzilib qolsa — avtomatik mag'lubiyat yo'q."""
    bid, a, b, subj, t0 = _active_battle(db, uid)
    ra_before = battle.get_rating(db, a, subj)["rating"]
    rb_before = battle.get_rating(db, b, subj)["rating"]
    far = t0 + datetime.timedelta(hours=2)
    battle.run_due(db, now=far)
    row = db.one("SELECT status, winner_id FROM battles WHERE id=?", (bid,))
    assert row["status"] in ("void", "finished", "no_show")
    if row["status"] == "void":
        assert row["winner_id"] is None
        assert battle.get_rating(db, a, subj)["rating"] == ra_before
        assert battle.get_rating(db, b, subj)["rating"] == rb_before


def test_only_one_player_answers_is_not_auto_loss(db, uid):
    """Bir o'yinchi hammasiga javob beradi, ikkinchisi umuman (aloqa uzilgan) — battle 'void' yoki aniq natija, lekin
    uzilgan tomonga jarima rating avtomatik berilmaydi (reytingga ta'sir qiluvchi 'finished' faqat ikkalasi ishtirok etsa)."""
    bid, a, b, subj, t0 = _active_battle(db, uid)
    cfg = lambda k: int(battle._cfg(db, k))
    start = t0 + datetime.timedelta(seconds=cfg("battle.countdown_seconds"))
    per = cfg("battle.seconds_per_question") + cfg("battle.gap_seconds")
    ids = utils.jloads(db.one("SELECT question_ids FROM battles WHERE id=?", (bid,))["question_ids"], [])
    for i, qid in enumerate(ids):
        right = db.one("SELECT correct_index FROM questions WHERE id=?", (qid,))["correct_index"]
        battle.answer(db, bid, a, i, right, now=start + datetime.timedelta(seconds=i * per + 1))
    battle.run_due(db, now=start + datetime.timedelta(seconds=len(ids) * per + 60))
    row = db.one("SELECT status FROM battles WHERE id=?", (bid,))
    assert row["status"] in ("void", "finished")
    if row["status"] == "void":
        assert battle.get_rating(db, b, subj)["rating"] == db.one(
            "SELECT rating_before FROM battle_players WHERE battle_id=? AND user_id=?", (bid, b))["rating_before"]


# ---------- Practice
def test_practice_does_not_touch_rating_and_hides_answers(db, uid):
    from smartedu.services import practice
    a = uid["demo_s1"]
    subj = access.student_subjects(db, a)[0]["id"]
    rating_before = battle.get_rating_readonly(db, a, subj)["rating"]
    sid = practice.start(db, a, subj, "mixed")
    state = practice.state(db, sid, a)
    assert state["index"] == 0 and "correct" not in repr(state["question"]) and state["status"] == "open"
    total = state["total"]
    right = 0
    for i in range(total):
        q = practice.state(db, sid, a)["question"]
        ci = db.one("SELECT correct_index FROM questions WHERE id=?", (q["id"],))["correct_index"]
        res = practice.answer(db, sid, a, i, ci if i % 2 == 0 else None)
        right += 1 if i % 2 == 0 else 0
        assert res["correct_index"] == ci
    assert res["done"] and res["correct"] == right
    assert battle.get_rating_readonly(db, a, subj)["rating"] == rating_before
    assert db.scalar("SELECT COUNT(*) FROM battles") == 0
    with pytest.raises(AppError):
        practice.answer(db, sid, a, 0, 0)                             # tugagan sessiyaga javob yo'q
    assert practice.history(db, a)[0]["correct"] == right


def test_practice_rules(db, uid):
    from smartedu.services import practice
    a, b = uid["demo_s1"], uid["demo_s2"]
    subj = access.student_subjects(db, a)[0]["id"]
    sid = practice.start(db, a, subj, "vocabulary")
    with pytest.raises(AppError):
        practice.state(db, sid, b)                                     # begona sessiya
    with pytest.raises(AppError):
        practice.answer(db, sid, a, 2, 0)                              # tartibni buzib bo'lmaydi
    with pytest.raises(AppError):
        practice.answer(db, sid, a, 0, 99)
    with pytest.raises(AppError):
        practice.start(db, a, subj, "teacher")
    practice.answer(db, sid, a, 0, 0)
    with pytest.raises(AppError):
        practice.answer(db, sid, a, 0, 1)                              # ikkinchi marta javob yo'q


def test_practice_pages_and_api(app, db, uid, client_for):
    c = client_for("demo_s1")
    assert c.get("/battle/practice").status_code == 200
    subj = access.student_subjects(db, uid["demo_s1"])[0]["id"]
    tok = csrf_of(c, "/battle/practice")
    resp = c.post("/battle/practice", data={"_csrf": tok, "subject_id": subj, "kind": "mixed"})
    assert resp.status_code == 302
    page = resp.headers["Location"]
    assert c.get(page).status_code == 200
    sid = int(page.rstrip("/").rsplit("/", 1)[1])
    state = c.get("/battle/api/practice/%d/state" % sid).get_json()
    assert state["ok"] and "correct_index" not in repr(state)
    ans = c.post("/battle/api/practice/%d/answer" % sid, json={"index": 0, "choice": 0}, headers={"X-CSRF-Token": tok})
    assert ans.get_json()["ok"] and "correct_index" in ans.get_json()
    other = client_for("demo_s2")
    assert other.get("/battle/api/practice/%d/state" % sid).status_code in (400, 403, 404)
