"""Battle Mode: sahifalar va JSON API (server hamma narsani hisoblaydi)."""
from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, url_for

from .. import utils, web
from ..db import get_db
from ..security import roles_required
from ..services import practice, access, battle
from ..utils import AppError

bp = Blueprint("battle", __name__, url_prefix="/battle")
student_only = roles_required("student")


def _pick_subject(db, subjects):
    subject_id = utils.to_int(request.values.get("subject"))
    ids = [s["id"] for s in subjects]
    return subject_id if subject_id in ids else (ids[0] if ids else None)


@bp.route("/")
@student_only
def hub():
    db = get_db()
    uid = g.user["id"]
    battle.run_due(db)
    subjects = access.student_subjects(db, uid)
    subject_id = _pick_subject(db, subjects)
    ctx = {"subjects": subjects, "subject_id": subject_id, "rating": None, "opponents": {"online": [], "offline": []},
           "challenges": [], "kinds": battle.KINDS, "history": battle.history(db, uid, 8),
           "center": battle.center(db, uid)}
    if subject_id:
        row = battle.get_rating_readonly(db, uid, subject_id)
        ctx["rating"] = {"value": row["rating"], "league": battle.league(db, row["rating"]), "wins": row["wins"],
                         "losses": row["losses"], "draws": row["draws"], "games": row["games"],
                         "pct": round(100.0 * row["wins"] / row["games"]) if row["games"] else 0,
                         "level": battle.student_level(db, uid, subject_id)}
        ctx["opponents"] = battle.find_opponents(db, uid, subject_id)
        groups = access.student_group_ids(db, uid)
        marks = ",".join("?" * len(groups)) if groups else "NULL"
        ctx["challenges"] = db.all(
            "SELECT * FROM challenges WHERE subject_id=? AND (group_id IS NULL OR group_id IN (%s)) ORDER BY id DESC LIMIT 20"
            % marks, [subject_id] + list(groups))
        ctx["subject_code"] = next(s["code"] for s in subjects if s["id"] == subject_id)
    return render_template("battle/hub.html", **ctx)


@bp.route("/invite", methods=["POST"])
@student_only
def invite():
    db = get_db()
    form = request.form
    battle_id = battle.create_invite(
        db, g.user["id"], utils.to_int(form.get("opponent_id")), utils.to_int(form.get("subject_id")),
        form.get("kind") or "mixed", quick=form.get("quick") == "1", challenge_id=utils.to_int(form.get("challenge_id")))
    web.flash_t("msg.battle_invite_sent")
    return redirect(url_for("battle.room", battle_id=battle_id))


@bp.route("/quick", methods=["POST"])
@student_only
def quick():
    """«Raqib topish»: mos onlayn raqibga tezkor taklif. Topilmasa - ro'yxatdan taklif yuborishga yo'naltiradi."""
    db = get_db()
    subject_id = utils.to_int(request.form.get("subject_id"))
    kind = request.form.get("kind") or "mixed"
    subjects = access.student_subjects(db, g.user["id"])
    if subject_id not in [s["id"] for s in subjects]:
        raise AppError("not_in_subject")
    candidates = battle.find_opponents(db, g.user["id"], subject_id)["online"]
    last_error = None
    for cand in candidates:
        if battle.active_battle_for(db, cand["id"]):
            continue
        try:
            battle_id = battle.create_invite(db, g.user["id"], cand["id"], subject_id, kind, quick=True,
                                             challenge_id=utils.to_int(request.form.get("challenge_id")))
        except AppError as exc:
            last_error = exc
            continue
        web.flash_t("msg.opponent_found", name=cand["name"])
        return redirect(url_for("battle.room", battle_id=battle_id))
    if last_error is not None and last_error.code in ("not_enough_questions", "invite_limit", "already_in_battle"):
        raise last_error
    web.flash_t("msg.no_online_opponent", "info")
    return redirect(url_for("battle.hub", subject=subject_id))


@bp.route("/center")
@student_only
def center():
    return render_template("battle/center.html", center=battle.center(get_db(), g.user["id"]))


@bp.route("/history")
@student_only
def history():
    return render_template("battle/history.html", items=battle.history(get_db(), g.user["id"], 100))


@bp.route("/<int:battle_id>")
@student_only
def room(battle_id):
    db = get_db()
    b = battle.tick_battle(db, battle_id)
    if b is None or g.user["id"] not in (b["challenger_id"], b["opponent_id"]):
        abort(404)
    if b["status"] == "finished":
        return redirect(url_for("battle.result", battle_id=battle_id))
    opp_id = battle.other_id(b, g.user["id"])
    opp = db.one("SELECT * FROM users WHERE id=?", (opp_id,))
    opp_rating = battle.get_rating_readonly(db, opp_id, b["subject_id"])
    slots = db.all("SELECT * FROM battle_slots WHERE battle_id=? ORDER BY starts_at", (battle_id,))
    subject = db.one("SELECT * FROM subjects WHERE id=?", (b["subject_id"],))
    state = battle.state_for(db, battle_id, g.user["id"])
    return render_template("battle/room.html", b=b, opp=opp, opp_name=access.user_name(opp), opp_rating=opp_rating,
                           opp_league=battle.league(db, opp_rating["rating"]), slots=slots, subject=subject, state=state,
                           me_is_challenger=(b["challenger_id"] == g.user["id"]),
                           lobby_open=battle.lobby_open(db, b, utils.now_precise()))


def _action(battle_id, fn, *args, flash_key=None):
    db = get_db()
    fn(db, battle_id, g.user["id"], *args)
    if flash_key:
        web.flash_t(flash_key)
    return redirect(url_for("battle.room", battle_id=battle_id))


@bp.route("/<int:battle_id>/accept", methods=["POST"])
@student_only
def accept(battle_id):
    return _action(battle_id, battle.accept)


@bp.route("/<int:battle_id>/decline", methods=["POST"])
@student_only
def decline(battle_id):
    _action(battle_id, battle.decline)
    return redirect(url_for("battle.center"))


@bp.route("/<int:battle_id>/cancel", methods=["POST"])
@student_only
def cancel(battle_id):
    _action(battle_id, battle.cancel)
    return redirect(url_for("battle.center"))


@bp.route("/<int:battle_id>/ready", methods=["POST"])
@student_only
def ready(battle_id):
    return _action(battle_id, battle.set_ready)


@bp.route("/<int:battle_id>/propose", methods=["POST"])
@student_only
def propose(battle_id):
    slots = [utils.parse_local_input(request.form.get("slot%d" % i)) if request.form.get("slot%d" % i) else None
             for i in (1, 2, 3)]
    try:
        return _action(battle_id, battle.propose_slots, slots, flash_key="msg.slots_sent")
    except ValueError:
        raise AppError("bad_slots")


@bp.route("/<int:battle_id>/choose", methods=["POST"])
@student_only
def choose(battle_id):
    return _action(battle_id, battle.choose_slot, utils.to_int(request.form.get("slot_id")), flash_key="msg.battle_scheduled")


@bp.route("/<int:battle_id>/dispute", methods=["POST"])
@student_only
def dispute(battle_id):
    battle.open_dispute(get_db(), g.user["id"], battle_id, request.form.get("message"))
    web.flash_t("msg.dispute_sent")
    return redirect(url_for("battle.result", battle_id=battle_id))


@bp.route("/<int:battle_id>/result")
@student_only
def result(battle_id):
    db = get_db()
    data = battle.result_for(db, battle_id, g.user["id"])
    b = data["battle"]
    if b["status"] not in ("finished", "void", "no_show", "expired", "declined", "cancelled"):
        return redirect(url_for("battle.room", battle_id=battle_id))
    subject = db.one("SELECT * FROM subjects WHERE id=?", (b["subject_id"],))
    return render_template("battle/result.html", subject=subject, me_id=g.user["id"], **data)


# ---------------------------------------------------------------- JSON API
@bp.route("/api/<int:battle_id>/state")
@student_only
def api_state(battle_id):
    return jsonify(battle.state_for(get_db(), battle_id, g.user["id"]))


@bp.route("/api/<int:battle_id>/answer", methods=["POST"])
@student_only
def api_answer(battle_id):
    data = request.get_json(silent=True) or {}
    q_index = utils.to_int(data.get("q_index"))
    choice = data.get("choice")
    if q_index is None or (choice is not None and not isinstance(choice, int)):
        raise AppError("bad_choice")
    return jsonify(battle.answer(get_db(), battle_id, g.user["id"], q_index, choice))


@bp.route("/api/quick-incoming")
@student_only
def api_quick_incoming():
    """Tezkor takliflar: sahifa ochiq turgan o'quvchiga 'Sizga mos raqib topildi' xabarini ko'rsatish uchun."""
    return jsonify({"ok": True, "items": battle.incoming_quick(get_db(), g.user["id"])})


# ---------------------------------------------------------------- Mashq rejimi (reytingga ta'sir qilmaydi)
@bp.route("/practice", methods=["GET", "POST"])
@student_only
def practice_home():
    db = get_db()
    uid = g.user["id"]
    if request.method == "POST":
        sid = practice.start(db, uid, utils.to_int(request.form.get("subject_id")), request.form.get("kind"))
        return redirect(url_for("battle.practice_room", session_id=sid))
    return render_template("battle/practice.html", session_id=None, subjects=access.student_subjects(db, uid),
                           kinds=battle.QTYPES + ["mixed"], history=practice.history(db, uid))


@bp.route("/practice/<int:session_id>")
@student_only
def practice_room(session_id):
    db = get_db()
    practice.state(db, session_id, g.user["id"])          # egasi ekanini tekshiradi
    return render_template("battle/practice.html", session_id=session_id, subjects=[], kinds=[], history=[])


@bp.route("/api/practice/<int:session_id>/state")
@student_only
def api_practice_state(session_id):
    return jsonify(practice.state(get_db(), session_id, g.user["id"]))


@bp.route("/api/practice/<int:session_id>/answer", methods=["POST"])
@student_only
def api_practice_answer(session_id):
    data = request.get_json(silent=True) or {}
    index = utils.to_int(data.get("index"))
    choice = data.get("choice")
    if index is None or (choice is not None and (not isinstance(choice, int) or isinstance(choice, bool))):
        raise AppError("bad_choice")
    return jsonify(practice.answer(get_db(), session_id, g.user["id"], index, choice))
