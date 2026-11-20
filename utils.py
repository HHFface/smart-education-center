"""Umumiy yordamchi funksiyalar: vaqt, JSON, kodlar."""
import datetime as _dt
import json
import secrets

FMT = "%Y-%m-%d %H:%M:%S"
DATE_FMT = "%Y-%m-%d"
_now_override = None


class AppError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xatolik. code -> tarjima kaliti 'err.<code>'."""

    def __init__(self, code, **params):
        super().__init__(code)
        self.code = code
        self.params = params


# ---------------------------------------------------------------- vaqt
def set_now(value):
    """Testlar uchun: hozirgi vaqtni qo'lda belgilash (None -> haqiqiy vaqt)."""
    global _now_override
    _now_override = value


def now():
    """UTC vaqti (tz-naive). Bazada hamma vaqt UTC saqlanadi."""
    if _now_override is not None:
        return _now_override
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def now_precise():
    """Mikrosekundli UTC vaqt (Battle vaqt hisobi uchun). Testda now() bilan bir xil."""
    if _now_override is not None:
        return _now_override
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def fmt(dt):
    return dt.strftime(FMT)


def parse(value):
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value
    text = str(value).strip().replace("T", " ")
    for pattern in (FMT, "%Y-%m-%d %H:%M"):
        try:
            return _dt.datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise ValueError("Noto'g'ri sana: %r" % (value,))


def tz_offset():
    try:
        from flask import current_app
        hours = float(current_app.config.get("TZ_OFFSET_HOURS", 5))
    except Exception:
        hours = 5.0
    return _dt.timedelta(hours=hours)


def to_local(value):
    dt = parse(value)
    return dt + tz_offset() if dt else None


def parse_local_input(value):
    """<input type=datetime-local> qiymatini (mahalliy vaqt) UTC datetime'ga aylantiradi."""
    dt = parse(value)
    return dt - tz_offset() if dt else None


def fmt_local(value, pattern="%d.%m.%Y %H:%M"):
    try:
        dt = to_local(value)
    except ValueError:
        return str(value)
    return dt.strftime(pattern) if dt else ""


def local_today():
    return to_local(now()).date()


def local_day_bounds(day):
    """Mahalliy kun (date) uchun UTC chegaralari: (start_str, end_str)."""
    start_local = _dt.datetime(day.year, day.month, day.day)
    start = start_local - tz_offset()
    return fmt(start), fmt(start + _dt.timedelta(days=1))


def local_date_of(value):
    dt = to_local(value)
    return dt.date() if dt else None


def parse_date(value):
    if not value:
        return None
    try:
        return _dt.datetime.strptime(str(value).strip(), DATE_FMT).date()
    except ValueError:
        return None


# ---------------------------------------------------------------- JSON va kodlar
def jloads(value, default=None):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def jdumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_code(length=8):
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value, limit=2000):
    return (value or "").strip()[:limit]
