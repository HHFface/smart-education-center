"""Ko'p tillilik. Tarjimalar smartedu/translations/*.json fayllarida (kodda emas)."""
import json
import logging
from pathlib import Path

from flask import g, has_request_context

log = logging.getLogger(__name__)

# (kod, o'z nomi). Yangi til qo'shish: ro'yxatga qo'shing + translations/<kod>.json yarating.
LANGS = [
    ("uz", "O‘zbekcha"),
    ("uz_cyrl", "Ўзбекча"),
    ("ru", "Русский"),
    ("en", "English"),
    ("ko", "한국어"),
]
LANG_CODES = [code for code, _ in LANGS]
FALLBACKS = ["uz", "en"]
_CATALOG = {}
_DIR = Path(__file__).with_name("translations")


def load_catalog():
    _CATALOG.clear()
    for code in LANG_CODES:
        path = _DIR / ("%s.json" % code)
        try:
            _CATALOG[code] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("Tarjima fayli o'qilmadi: %s (%s)", path, exc)
            _CATALOG[code] = {}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{%s}" % key


def current_lang():
    if has_request_context():
        return getattr(g, "lang", None) or "uz"
    return "uz"


def t(key, lang=None, **params):
    """Kalit bo'yicha tarjima. Topilmasa: uz -> en -> kalitning o'zi."""
    if not _CATALOG:
        load_catalog()
    lang = lang or current_lang()
    text = None
    for code in [lang] + FALLBACKS:
        text = _CATALOG.get(code, {}).get(key)
        if text is not None:
            break
    if text is None:
        return key
    if params:
        try:
            return text.format_map(_SafeDict(params))
        except (ValueError, IndexError):
            return text
    return text


def has_key(key):
    if not _CATALOG:
        load_catalog()
    return any(key in _CATALOG.get(code, {}) for code in LANG_CODES)


def pick_lang(candidate):
    return candidate if candidate in LANG_CODES else None


def best_from_header(header):
    """Accept-Language sarlavhasidan mos tilni tanlaydi."""
    if not header:
        return None
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()
        base = code.split("-")[0]
        if base in ("uz", "ru", "en", "ko"):
            if base == "uz" and "cyrl" in code:
                return "uz_cyrl"
            return base
    return None
