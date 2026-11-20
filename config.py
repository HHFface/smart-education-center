"""Sozlamalar. Hamma narsa muhit o'zgaruvchilari (.env) orqali boshqariladi.

Bepul rejim uchun standart qiymatlar tanlangan: SQLite, konsol xabarlari,
AI o'chiq. Pulli xizmatlarga o'tish uchun faqat .env faylini o'zgartirish kifoya.
"""
import os
import re
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv_file(path):
    """Oddiy .env o'quvchi (python-dotenv shart emas). Mavjud o'zgaruvchilarni bosib o'tmaydi."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()   # qator oxiridagi izohni olib tashlaymiz
        os.environ.setdefault(key, value)


def _bool(value, default=False):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _secret_key(instance_dir):
    """SECRET_KEY berilmasa, bir marta yaratib instance/secret_key fayliga saqlaydi."""
    env_value = os.environ.get("SECRET_KEY", "").strip()
    if env_value:
        return env_value
    key_file = Path(instance_dir) / "secret_key"
    try:
        if key_file.is_file():
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        key_file.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_hex(32)
        key_file.write_text(generated, encoding="utf-8")
        try:
            os.chmod(str(key_file), 0o600)
        except OSError:
            pass
        return generated
    except OSError:
        # Fayl tizimiga yozib bo'lmasa, vaqtinchalik kalit (sessiyalar qayta ishga tushganda tugaydi)
        return secrets.token_hex(32)


def build_config(overrides=None):
    load_dotenv_file(BASE_DIR / ".env")
    home = Path(os.environ.get("SMARTEDU_HOME", str(BASE_DIR / "instance"))).resolve()

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url.startswith("sqlite:///"):
        db_path = db_url[len("sqlite:///"):]
    elif db_url and "://" in db_url:
        raise RuntimeError(
            "DATABASE_URL hozircha faqat sqlite:/// ni qo'llaydi. "
            "PostgreSQL hozircha qo'llab-quvvatlanmaydi."
        )
    elif db_url:
        db_path = db_url
    else:
        db_path = str(home / "smartedu.db")

    env = os.environ.get("APP_ENV", "development").strip().lower()
    base_url = os.environ.get("BASE_URL", "http://127.0.0.1:5000").strip().rstrip("/")

    cfg = {
        "APP_NAME": os.environ.get("APP_NAME", "Smart Education Center"),
        "ENV_NAME": env,
        # production'da DEBUG hech qachon yoqilmaydi (Werkzeug debugger — masofadan kod bajarish xavfi)
        "DEBUG": env != "production" and _bool(os.environ.get("DEBUG"), env == "development"),
        "SECRET_KEY": _secret_key(home),
        "HOME_DIR": str(home),
        "DATABASE_PATH": db_path,
        "UPLOAD_DIR": os.environ.get("UPLOAD_DIR", str(home / "uploads")),
        "LOG_DIR": os.environ.get("LOG_DIR", str(home / "logs")),
        "BACKUP_DIR": os.environ.get("BACKUP_DIR", str(home / "backups")),
        "MAX_UPLOAD_MB": _int(os.environ.get("MAX_UPLOAD_MB"), 8),
        "TZ_OFFSET_HOURS": os.environ.get("TZ_OFFSET_HOURS", "5"),
        "DEFAULT_LANG": os.environ.get("DEFAULT_LANG", "uz"),
        "BASE_URL": base_url,
        "SESSION_COOKIE_SECURE": _bool(os.environ.get("SESSION_COOKIE_SECURE"),
                                       env == "production" and base_url.startswith("https")),
        "MAX_FAILED_LOGINS": _int(os.environ.get("MAX_FAILED_LOGINS"), 5),
        "LOCK_MINUTES": _int(os.environ.get("LOCK_MINUTES"), 10),
        # IP bo'yicha limit (bir IP dan 15 daqiqada nechta muvaffaqiyatsiz urinish) va ishonchli proksi soni
        "LOGIN_IP_LIMIT": _int(os.environ.get("LOGIN_IP_LIMIT"), 30),
        "TRUSTED_PROXIES": _int(os.environ.get("TRUSTED_PROXIES"), 0),
        "SESSION_MAX_DAYS": _int(os.environ.get("SESSION_MAX_DAYS"), 30),
        "SESSION_IDLE_DAYS": _int(os.environ.get("SESSION_IDLE_DAYS"), 14),
        # --- Telegram ---
        "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_BOT_USERNAME": os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
        "TELEGRAM_WEBHOOK_SECRET": os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip(),
        # --- Tashqi cron (bepul cron-job.org) uchun himoya tokeni ---
        "TICK_TOKEN": os.environ.get("TICK_TOKEN", "").strip(),
        "OPPORTUNISTIC_TICK": _bool(os.environ.get("OPPORTUNISTIC_TICK"), True),
        # --- AI: none | gemini | groq | openai_compat | anthropic ---
        "AI_PROVIDER": os.environ.get("AI_PROVIDER", "none").strip().lower(),
        "AI_API_KEY": os.environ.get("AI_API_KEY", "").strip(),
        "AI_MODEL": os.environ.get("AI_MODEL", "").strip(),
        "AI_BASE_URL": os.environ.get("AI_BASE_URL", "").strip(),
        # --- Email: console | smtp ---
        "EMAIL_PROVIDER": os.environ.get("EMAIL_PROVIDER", "console").strip().lower(),
        "SMTP_HOST": os.environ.get("SMTP_HOST", "").strip(),
        "SMTP_PORT": _int(os.environ.get("SMTP_PORT"), 587),
        "SMTP_USER": os.environ.get("SMTP_USER", "").strip(),
        "SMTP_PASSWORD": os.environ.get("SMTP_PASSWORD", ""),
        "SMTP_FROM": os.environ.get("SMTP_FROM", "").strip(),
        # --- To'lov: manual | click | payme (keyingi bosqich) ---
        "PAYMENT_PROVIDER": os.environ.get("PAYMENT_PROVIDER", "manual").strip().lower(),
        "PAYMENT_MERCHANT_ID": os.environ.get("PAYMENT_MERCHANT_ID", "").strip(),
        "PAYMENT_SECRET": os.environ.get("PAYMENT_SECRET", "").strip(),
    }
    cfg["MAX_CONTENT_LENGTH"] = cfg["MAX_UPLOAD_MB"] * 1024 * 1024
    if overrides:
        cfg.update(overrides)
    return cfg
