"""Telegram bot mantiqi: inline tugmali menyular. Har bir ekran (text, tugmalar) juftligini qaytaradi.

Bot sessiya holatini saqlamaydi: ko'p bosqichli jarayonlar (masalan, «Darsga kela olmayman»)
callback_data ichida holatni olib yuradi, shuning uchun webhook ham, polling ham, bir nechta jarayon ham ishlayveradi.
"""
import datetime
import logging
import re

from .. import i18n, utils
from ..db import get_db
from ..providers import telegram as tg
from ..services import (access, analytics, attendance, battle, finance, homework, notifications, points, schedule,
                        shop, users)
from ..utils import AppError

log = logging.getLogger(__name__)
CODE_RE = re.compile(r"^[A-Za-z0-9]{8}$")


def _btn(text, data):
    if data.startswith("http"):
        return {"text": text, "url": data}
    return {"text": text, "callback_data": data}


def keyboard(rows):
    return {"inline_keyboard": [[_btn(t, d) for t, d in row] for row in rows]}


class Bot:
    def __init__(self, app, client, db):
        self.app, self.client, self.db = app, client, db

    # -- yuborish
    def send(self, chat_id, text, markup=None):
        try:
            self.client.send_message(chat_id, text, markup)
        except tg.TelegramError as exc:
            log.warning("bot send failed: %s", exc)

    def show(self, chat_id, message_id, text, markup=None):
        """Mavjud xabarni yangilaydi (bo'lmasa yangi xabar yuboradi)."""
        try:
            if message_id:
                self.client.edit_message_text(chat_id, message_id, text, markup)
                return
        except tg.TelegramError as exc:
            log.info("bot edit failed, sending new: %s", exc)
        self.send(chat_id, text, markup)

    def site_button(self, path=""):
        base = self.app.config["BASE_URL"]
        if base.startswith("https://"):
            return [(i18n.t("bot.open_site", self.lang), base + path)]
        return None

    lang = "uz"


# ---------------------------------------------------------------- kirish nuqtasi
def handle_update(app, update):
    client = tg.get_client(app)
    if client is None or not isinstance(update, dict):
        return
    bot = Bot(app, client, get_db())
    if "message" in update:
        _on_message(bot, update["message"])
    elif "callback_query" in update:
        _on_callback(bot, update["callback_query"])


def _guess_lang(code):
    return i18n.best_from_header(code or "") or "uz"


def _user_for_chat(db, chat_id):
    return db.one("SELECT * FROM users WHERE telegram_chat_id=? AND is_active=1", (str(chat_id),))


def _on_message(bot, msg):
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    guess = _guess_lang((msg.get("from") or {}).get("language_code"))
    code = None
    command = None
    if text.startswith("/"):
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        if len(parts) > 1:
            code = parts[1]
    elif CODE_RE.match(text):
        code = text
    if code and (command in (None, "/start", "/link")) and CODE_RE.match(code):
        linked = users.link_telegram(bot.db, code, chat_id)
        if linked is not None:
            bot.lang = linked["lang"]
            bot.send(chat_id, i18n.t("bot.linked", bot.lang, name=access.user_name(linked)))
            text_, markup = screen_main(bot, linked)
            bot.send(chat_id, text_, markup)
        else:
            bot.send(chat_id, i18n.t("bot.bad_code", guess))
        return
    user = _user_for_chat(bot.db, chat_id)
    if user is None:
        bot.send(chat_id, i18n.t("bot.not_linked", guess, site=bot.app.config["BASE_URL"]))
        return
    bot.lang = user["lang"]
    if command == "/help":
        bot.send(chat_id, i18n.t("bot.help", bot.lang))
    text_, markup = screen_main(bot, user)
    bot.send(chat_id, text_, markup)


def _on_callback(bot, cb):
    message = cb.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    data = cb.get("data") or ""
    if chat_id is None:
        return
    user = _user_for_chat(bot.db, chat_id)
    guess = _guess_lang((cb.get("from") or {}).get("language_code"))
    if user is None:
        bot.client.answer_callback_query(cb.get("id"))
        bot.send(chat_id, i18n.t("bot.not_linked", guess, site=bot.app.config["BASE_URL"]))
        return
    bot.lang = user["lang"]
    alert = None
    try:
        text, markup, alert = _dispatch(bot, user, data)
    except AppError as exc:
        alert = i18n.t("err.%s" % exc.code, bot.lang, **exc.params)
        text, markup = screen_main(bot, user)
    except Exception:
        log.exception("bot callback failed: %s", data)
        alert = i18n.t("http.server_error", bot.lang)
        text, markup = screen_main(bot, user)
    bot.client.answer_callback_query(cb.get("id"), alert)
    bot.show(chat_id, message_id, text[:4000], markup)


def _dispatch(bot, user, data):
    """data -> (text, markup, alert). Tuzilma: 'amal:arg1:arg2'."""
    action, _, rest = data.partition(":")
    args = rest.split(":") if rest else []
    role = user["role"]
    alert = None
    if action == "lg":
        if args and args[0] in i18n.LANG_CODES:
            bot.db.execute("UPDATE users SET lang=? WHERE id=?", (args[0], user["id"]))
            user = bot.db.one("SELECT * FROM users WHERE id=?", (user["id"],))
            bot.lang = user["lang"]
            alert = i18n.t("bot.lang_set", bot.lang)
        text, markup = screen_main(bot, user)
    elif action == "lang":
        text, markup = screen_language(bot)
    elif action == "nt":
        text, markup = screen_notifications(bot, user, mark=(args[:1] == ["read"]))
    elif role == "student" and action in STUDENT_SCREENS:
        text, markup, alert = STUDENT_SCREENS[action](bot, user, args)
    elif role == "parent" and action == "kids":
        text, markup = screen_kids(bot, user)
    elif role == "teacher" and action in TEACHER_SCREENS:
        text, markup, alert = TEACHER_SCREENS[action](bot, user, args)
    elif role == "admin" and action == "as":
        text, markup = screen_admin(bot, user)
    else:
        text, markup = screen_main(bot, user)
    return text, markup, alert


# ---------------------------------------------------------------- umumiy ekranlar
def T(bot, key, **params):
    return i18n.t(key, bot.lang, **params)


def _back(bot):
    return [(T(bot, "bot.back"), "m")]


def _subject_label(bot, code, name):
    key = "subject.%s" % code
    return i18n.t(key, bot.lang) if i18n.has_key(key) else name


def _reason_label(bot, code, reason):
    key = "reason.%s" % code
    return i18n.t(key, bot.lang) if i18n.has_key(key) else (reason or code)


def screen_main(bot, user):
    role = user["role"]
    name = access.user_name(user)
    title = T(bot, "bot.menu_title", name=name)
    if role == "student":
        rows = [
            [(T(bot, "bot.btn_schedule"), "sch"), (T(bot, "bot.btn_homework"), "hw")],
            [(T(bot, "bot.btn_points"), "pts"), (T(bot, "bot.btn_rank"), "rank")],
            [(T(bot, "bot.btn_battle"), "bt"), (T(bot, "bot.btn_shop"), "shop")],
            [(T(bot, "bot.btn_absence"), "abs"), (T(bot, "bot.btn_notifications"), "nt")],
            [(T(bot, "bot.btn_language"), "lang")],
        ]
    elif role == "parent":
        rows = [[(T(bot, "bot.btn_kids"), "kids")], [(T(bot, "bot.btn_notifications"), "nt"),
                                                     (T(bot, "bot.btn_language"), "lang")]]
    elif role == "teacher":
        rows = [[(T(bot, "bot.btn_today"), "tl"), (T(bot, "bot.btn_requests"), "ta")],
                [(T(bot, "bot.btn_notifications"), "nt"), (T(bot, "bot.btn_language"), "lang")]]
    else:
        rows = [[(T(bot, "bot.btn_stats"), "as")], [(T(bot, "bot.btn_notifications"), "nt"),
                                                    (T(bot, "bot.btn_language"), "lang")]]
    site = bot.site_button()
    if site:
        rows.append(site)
    return title, keyboard(rows)


def screen_language(bot):
    rows = [[(label, "lg:%s" % code)] for code, label in i18n.LANGS]
    rows.append(_back(bot))
    return T(bot, "bot.choose_language"), keyboard(rows)


def screen_notifications(bot, user, mark=False):
    db = bot.db
    if mark:
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    items = notifications.list_for(db, user["id"], bot.lang, limit=8)
    if not items:
        body = T(bot, "bot.empty")
    else:
        lines = []
        for it in items:
            dot = "• " if it["is_read"] else "🔵 "
            lines.append("%s%s  (%s)" % (dot, it["text"], utils.fmt_local(it["created_at"], "%d.%m %H:%M")))
        body = "\n\n".join(lines)
    rows = [[(T(bot, "bot.mark_read"), "nt:read")], _back(bot)]
    return "%s\n\n%s" % (T(bot, "bot.notifications_title"), body), keyboard(rows)


# ---------------------------------------------------------------- o'quvchi ekranlari
def screen_schedule(bot, user, args):
    now = utils.now()
    lessons = schedule.student_lessons(bot.db, user["id"], now - datetime.timedelta(hours=2), now + datetime.timedelta(days=7))
    if not lessons:
        body = T(bot, "bot.empty")
    else:
        body = "\n".join("📅 %s — %s (%s)%s" % (
            utils.fmt_local(l["starts_at"], "%d.%m %H:%M"),
            l["group_name"], _subject_label(bot, l["subject_code"], l["subject_name"]),
            "  ❌" if l["status"] == "cancelled" else "") for l in lessons[:15])
    return "%s\n\n%s" % (T(bot, "bot.schedule_title"), body), keyboard([_back(bot)]), None


def screen_homework(bot, user, args):
    db = bot.db
    rows = db.all(
        "SELECT h.title, h.due_at FROM homework h JOIN group_students gs ON gs.group_id=h.group_id "
        "WHERE gs.student_id=? AND h.due_at>? AND NOT EXISTS (SELECT 1 FROM submissions s WHERE s.homework_id=h.id AND s.student_id=?) "
        "ORDER BY h.due_at LIMIT 8", (user["id"], utils.fmt(utils.now()), user["id"]))
    stats = homework.student_stats(db, user["id"])
    body = "\n".join("📝 %s — %s" % (r["title"], utils.fmt_local(r["due_at"], "%d.%m %H:%M")) for r in rows) or T(bot, "bot.empty")
    pct = "—" if stats["pct"] is None else "%d%%" % stats["pct"]
    text = "%s\n\n%s\n\n%s" % (T(bot, "bot.homework_title"), body, T(bot, "bot.homework_stats", pct=pct, misses=stats["misses"]))
    rows_kb = []
    site = bot.site_button("/student/homework")
    if site:
        rows_kb.append(site)
    rows_kb.append(_back(bot))
    return text, keyboard(rows_kb), None


def screen_points(bot, user, args):
    st = points.stats(bot.db, user["id"])
    log_rows = points.history(bot.db, user["id"], limit=6)
    lines = []
    for r in log_rows:
        sign = "+" if r["amount"] > 0 else ""
        icon = "⭐" if r["kind"] == "xp" else "🪙"
        lines.append("%s %s%d — %s" % (icon, sign, r["amount"], _reason_label(bot, r["reason_code"], r["reason"])))
    text = "%s\n\n⭐ XP: %d\n🪙 Coin: %d\n\n%s" % (T(bot, "bot.points_title"), st["xp"], st["coins"], "\n".join(lines))
    return text, keyboard([_back(bot)]), None


def screen_rank(bot, user, args):
    rows = analytics.leaderboard(bot.db, "overall", limit=0)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["%s %d. %s — %s" % (medals.get(r["rank"], "▫️"), r["rank"], r["name"], r["value"]) for r in rows[:10]]
    me = analytics.position(rows, user["id"])
    text = "%s\n\n%s\n\n%s" % (T(bot, "bot.rank_title"), "\n".join(lines), T(bot, "bot.rank_me", rank=me or "—", total=len(rows)))
    return text, keyboard([_back(bot)]), None


def screen_battle(bot, user, args):
    db = bot.db
    battle.run_due(db)
    parts = []
    for s in access.student_subjects(db, user["id"]):
        rt = battle.get_rating_readonly(db, user["id"], s["id"])
        parts.append("⚔️ %s: %d (%s) — %d/%d/%d" % (_subject_label(bot, s["code"], s["name"]), rt["rating"],
                                                    battle.league(db, rt["rating"]), rt["wins"], rt["losses"], rt["draws"]))
    rows_kb = []
    incoming = db.all("SELECT b.*, u.first_name, u.last_name, u.username FROM battles b JOIN users u ON u.id=b.challenger_id "
                      "WHERE b.opponent_id=? AND b.status='pending' ORDER BY b.id DESC LIMIT 5", (user["id"],))
    for b in incoming:
        name = access.user_name(b)
        parts.append("📩 " + T(bot, "bot.battle_invite_line", name=name, kind=T(bot, "battle.kind.%s" % b["kind"])))
        rows_kb.append([(T(bot, "bot.accept") + " · " + name, "bta:%d" % b["id"]), (T(bot, "bot.decline"), "btd:%d" % b["id"])])
    sched = db.all("SELECT * FROM battles WHERE (challenger_id=? OR opponent_id=?) AND status IN ('scheduled','ready') "
                   "ORDER BY id DESC LIMIT 5", (user["id"], user["id"]))
    for b in sched:
        when = utils.fmt_local(b["scheduled_at"]) if b["scheduled_at"] else T(bot, "bot.now")
        parts.append("🗓 %s: %s" % (T(bot, "bot.battle_scheduled_line"), when))
    site = bot.site_button("/battle/")
    if site:
        rows_kb.append(site)
    rows_kb.append(_back(bot))
    return "%s\n\n%s" % (T(bot, "bot.battle_title"), "\n".join(parts) or T(bot, "bot.empty")), keyboard(rows_kb), None


def act_battle_accept(bot, user, args):
    battle.accept(bot.db, int(args[0]), user["id"])
    text, markup, _ = screen_battle(bot, user, [])
    return text, markup, T(bot, "bot.battle_accepted")


def act_battle_decline(bot, user, args):
    battle.decline(bot.db, int(args[0]), user["id"])
    text, markup, _ = screen_battle(bot, user, [])
    return text, markup, T(bot, "bot.battle_declined")


def screen_shop(bot, user, args):
    db = bot.db
    st = points.stats(db, user["id"])
    items = db.all("SELECT * FROM shop_items WHERE is_active=1 AND (stock IS NULL OR stock>0) ORDER BY price_coins LIMIT 8")
    lines, rows_kb = [], []
    for it in items:
        lines.append("🛍 %s — %d 🪙" % (it["name"], it["price_coins"]))
        rows_kb.append([("%s · %d 🪙" % (it["name"], it["price_coins"]), "shc:%d" % it["id"])])
    rows_kb.append(_back(bot))
    text = "%s\n🪙 %d\n\n%s" % (T(bot, "bot.shop_title"), st["coins"], "\n".join(lines) or T(bot, "bot.empty"))
    return text, keyboard(rows_kb), None


def act_shop_confirm(bot, user, args):
    item = bot.db.one("SELECT * FROM shop_items WHERE id=? AND is_active=1", (int(args[0]),))
    if item is None:
        raise AppError("not_found")
    text = T(bot, "bot.shop_confirm", item=item["name"], price=item["price_coins"])
    return text, keyboard([[(T(bot, "bot.yes"), "shb:%d" % item["id"]), (T(bot, "bot.no"), "shop")]]), None


def act_shop_buy(bot, user, args):
    shop.buy(bot.db, user["id"], int(args[0]))
    text, markup, _ = screen_shop(bot, user, [])
    return text, markup, T(bot, "msg.purchased")


# -- «Darsga kela olmayman»: callback_data ichida holat
def screen_absence_dates(bot, user, args):
    today = utils.local_today()
    rows = []
    for offset in range(0, 4):
        day = today + datetime.timedelta(days=offset)
        label = T(bot, "bot.today") if offset == 0 else (T(bot, "bot.tomorrow") if offset == 1 else day.strftime("%d.%m"))
        rows.append((label + " · " + day.strftime("%d.%m"), "abd:%s" % day.strftime("%Y%m%d")))
    kb = [[r] for r in rows]
    kb.append(_back(bot))
    return T(bot, "bot.absence_pick_date"), keyboard(kb), None


def screen_absence_subject(bot, user, args):
    day = args[0]
    subjects = access.student_subjects(bot.db, user["id"])
    if not subjects:
        raise AppError("not_in_subject")
    if len(subjects) == 1:
        return screen_absence_reason(bot, user, [str(subjects[0]["id"]), day])
    rows = [[(_subject_label(bot, s["code"], s["name"]), "abs2:%d:%s" % (s["id"], day))] for s in subjects]
    rows.append(_back(bot))
    return T(bot, "bot.absence_pick_subject"), keyboard(rows), None


def screen_absence_reason(bot, user, args):
    subject_id, day = args[0], args[1]
    rows = [[(T(bot, "absence.reason.%s" % r), "abr:%s:%s:%d" % (subject_id, day, i))]
            for i, r in enumerate(attendance.ABSENCE_REASONS)]
    rows.append(_back(bot))
    return T(bot, "bot.absence_pick_reason"), keyboard(rows), None


def act_absence_submit(bot, user, args):
    subject_id, day, idx = int(args[0]), args[1], int(args[2])
    if not 0 <= idx < len(attendance.ABSENCE_REASONS):
        raise AppError("bad_reason")
    date_iso = datetime.datetime.strptime(day, "%Y%m%d").date().isoformat()
    attendance.submit_absence(bot.db, user["id"], subject_id, date_iso, attendance.ABSENCE_REASONS[idx], "via telegram")
    return T(bot, "bot.absence_sent"), keyboard([_back(bot)]), T(bot, "msg.absence_sent")


STUDENT_SCREENS = {
    "m": lambda bot, user, args: (*screen_main(bot, user), None),
    "sch": screen_schedule, "hw": screen_homework, "pts": screen_points, "rank": screen_rank,
    "bt": screen_battle, "bta": act_battle_accept, "btd": act_battle_decline,
    "shop": screen_shop, "shc": act_shop_confirm, "shb": act_shop_buy,
    "abs": screen_absence_dates, "abd": screen_absence_subject, "abs2": screen_absence_reason, "abr": act_absence_submit,
}


# ---------------------------------------------------------------- ota-ona, o'qituvchi, administrator
def screen_kids(bot, user):
    db = bot.db
    parts = []
    for kid in access.parent_child_ids(db, user["id"]):
        row = db.one("SELECT * FROM users WHERE id=?", (kid,))
        s = analytics.student_summary(db, kid)
        debt = finance.student_payments(db, kid)["debt"]
        att = "—" if s["attendance"]["pct"] is None else "%d%%" % s["attendance"]["pct"]
        hw = "—" if s["homework"]["pct"] is None else "%d%%" % s["homework"]["pct"]
        parts.append("👤 %s\n⭐ %d  🪙 %d  🏆 #%s\n✅ %s  📝 %s%s" % (
            access.user_name(row), s["xp"], s["coins"], s["rank"] or "—", att, hw,
            ("\n💳 %s: %s" % (T(bot, "bot.debt"), "{:,}".format(debt).replace(",", " "))) if debt else ""))
    text = "%s\n\n%s" % (T(bot, "bot.kids_title"), "\n\n".join(parts) or T(bot, "bot.empty"))
    return text, keyboard([_back(bot)])


def screen_teacher_today(bot, user, args):
    today = utils.local_today()
    s, _ = utils.local_day_bounds(today)
    e, _ = utils.local_day_bounds(today + datetime.timedelta(days=2))
    lessons = schedule.teacher_lessons(bot.db, user["id"], utils.parse(s), utils.parse(e))
    body = "\n".join("📅 %s — %s" % (utils.fmt_local(l["starts_at"], "%d.%m %H:%M"), l["group_name"]) for l in lessons) or T(bot, "bot.empty")
    return "%s\n\n%s" % (T(bot, "bot.today_title"), body), keyboard([_back(bot)]), None


def screen_teacher_requests(bot, user, args):
    db = bot.db
    rows = db.all(
        "SELECT DISTINCT a.*, u.first_name, u.last_name, u.username FROM absence_requests a JOIN users u ON u.id=a.student_id "
        "JOIN group_students gs ON gs.student_id=a.student_id JOIN groups g ON g.id=gs.group_id "
        "JOIN courses c ON c.id=g.course_id AND c.subject_id=a.subject_id WHERE g.teacher_id=? AND a.status='pending' "
        "ORDER BY a.id LIMIT 5", (user["id"],))
    lines, kb = [], []
    for r in rows:
        reason = T(bot, "absence.reason.%s" % r["reason"])
        lines.append("🚫 %s — %s (%s)" % (access.user_name(r), utils.parse_date(r["lesson_date"]).strftime("%d.%m"), reason))
        kb.append([("✅ %s" % access.user_name(r), "tav:%d:a" % r["id"]), ("❌", "tav:%d:r" % r["id"])])
    kb.append(_back(bot))
    return "%s\n\n%s" % (T(bot, "bot.requests_title"), "\n".join(lines) or T(bot, "bot.empty")), keyboard(kb), None


def act_teacher_review(bot, user, args):
    status = "approved" if args[1] == "a" else "rejected"
    attendance.review_absence(bot.db, int(args[0]), status, user, "")
    text, markup, _ = screen_teacher_requests(bot, user, [])
    return text, markup, T(bot, "msg.saved")


TEACHER_SCREENS = {
    "m": lambda bot, user, args: (*screen_main(bot, user), None),
    "tl": screen_teacher_today, "ta": screen_teacher_requests, "tav": act_teacher_review,
}


def screen_admin(bot, user):
    d = analytics.admin_dashboard(bot.db)
    fin = d["finance"]
    text = T(bot, "bot.stats_text", students=d["students"], teachers=d["teachers"], groups=d["groups"],
             absences=d["pending_absences"], orders=d["pending_orders"], disputes=d["pending_disputes"],
             income="{:,}".format(fin["income"]).replace(",", " "), debt="{:,}".format(fin["debt"]).replace(",", " "))
    return "%s\n\n%s" % (T(bot, "bot.stats_title"), text), keyboard([_back(bot)])
