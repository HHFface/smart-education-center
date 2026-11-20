"""Administrator o'zgartira oladigan sozlamalar (jarimalar, Battle qoidalari, ligalar va h.k.)."""
import json

from .. import utils

DEFAULTS = {
    # Davomat uchun Coin qoidalari
    "coin.attendance_present": 10,
    "coin.attendance_late": -5,
    "coin.attendance_excused": 0,
    "coin.attendance_absent": -20,
    # Uy vazifasi: 1-marta, 2-marta, 3-marta (va keyingilari oxirgi qiymat bilan)
    "hw.penalties": [0, -10, -20],
    "hw.reward_coin": 5,
    "hw.xp_divisor": 10,
    # Battle
    "battle.questions": 10,
    "practice.questions": 8,
    "battle.seconds_per_question": 20,
    "battle.gap_seconds": 3,
    "battle.countdown_seconds": 4,
    "battle.initial_rating": 1000,
    "battle.k_placement": 40,
    "battle.k_normal": 24,
    "battle.placement_games": 10,
    "battle.rating_windows": [100, 200, 350, 600],
    "battle.max_level_gap": 1,
    "battle.invite_ttl_minutes": 1440,
    "battle.quick_ttl_seconds": 90,
    "battle.invites_per_day": 10,
    "battle.pair_weekly_limit": 3,
    "battle.lobby_open_minutes": 10,
    "battle.wait_minutes": 10,
    "battle.online_seconds": 120,
    "battle.tiebreak_by_time": False,
    "battle.reward_win": {"xp": 15, "coin": 10},
    "battle.reward_draw": {"xp": 8, "coin": 5},
    "battle.reward_loss": {"xp": 5, "coin": 0},
    "battle.daily_reward_cap": 5,
    "battle.leagues": [["Bronze", 0], ["Silver", 1100], ["Gold", 1250],
                       ["Platinum", 1400], ["Diamond", 1550], ["Master", 1700]],
    # Reyting (faqat XP/Coin emas)
    "rating.weights": {"xp": 1.0, "attendance": 2.0, "homework": 2.0, "battle": 0.2},
    # Smart Shop
    "shop.monthly_limit": 3,
    # AI yordamchi (bepul tarif limitlariga mos)
    "ai.daily_limit": 20,
    # Ro'yxatdan o'tish
    "registration.open_for_students": True,
    # Uzrli sabab arizasini necha kun o'tgach ham yuborish mumkin
    "absence.late_days": 3,
}


def ensure_defaults(db):
    stamp = utils.fmt(utils.now())
    for key, value in DEFAULTS.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value), stamp),
        )


def get(db, key):
    row = db.one("SELECT value FROM settings WHERE key=?", (key,))
    if row is not None:
        try:
            return json.loads(row["value"])
        except ValueError:
            pass
    return DEFAULTS.get(key)


def get_all(db):
    result = dict(DEFAULTS)
    for row in db.all("SELECT key, value FROM settings"):
        try:
            result[row["key"]] = json.loads(row["value"])
        except ValueError:
            continue
    return result


def validate(key, value):
    """Qiymat turi standart qiymat turiga mos bo'lishi shart. Xatolikda ValueError."""
    if key not in DEFAULTS:
        raise ValueError("unknown_setting")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError("bad_value")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("bad_value")
    elif isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("bad_value")
    elif isinstance(default, list):
        if not isinstance(value, list) or not value:
            raise ValueError("bad_value")
    elif isinstance(default, dict):
        if not isinstance(value, dict) or set(value) != set(default):
            raise ValueError("bad_value")
    return value


def set_value(db, key, value):
    validate(key, value)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
        (key, json.dumps(value), utils.fmt(utils.now())),
    )
