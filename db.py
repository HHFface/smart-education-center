"""Ma'lumotlar bazasi qatlami (SQLite, standart kutubxona).

DIQQAT: butun loyiha bazaga faqat shu modul orqali murojaat qiladi.
Baza faqat SQLite (PostgreSQL hozircha qo'llab-quvvatlanmaydi: SQL SQLite xususiyatlaridan foydalanadi).
"""
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

log = logging.getLogger("smartedu.db")

# --- Migratsiyalar -----------------------------------------------------------
# schema.sql — har doim ENG YANGI sxema (toza o'rnatish uchun).
# Mavjud baza yangilanganda quyidagi ro'yxatdagi, hali bajarilmagan migratsiyalar tartib bilan ishlaydi.
# Migratsiya faqat MAVJUD jadvallarni o'zgartiradi (ALTER/UPDATE); yangi jadval va indekslarni
# schema.sql (IF NOT EXISTS) o'zi qo'shadi. Har bir migratsiya bitta tranzaksiyada bajariladi va
# oldidan bazaning zaxira nusxasi olinadi. Yangi migratsiya qo'shish:  (versiya, "tavsif", [SQL, ...]).
MIGRATIONS = [
    (1, "boshlang'ich sxema", []),
    (2, "uzrli kelmaslik arizasiga hujjat biriktirish", [
        "ALTER TABLE absence_requests ADD COLUMN file_name TEXT",
    ]),
    (3, "qisman to'lovlar (payment_transactions) va sertifikatni bekor qilish", [
        "ALTER TABLE certificates ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE certificates ADD COLUMN revoked_at TEXT",
        "ALTER TABLE certificates ADD COLUMN revoked_reason TEXT",
        """CREATE TABLE IF NOT EXISTS payment_transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id  INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
            amount      INTEGER NOT NULL CHECK (amount > 0),
            method      TEXT NOT NULL DEFAULT 'manual',
            external_id TEXT,
            created_at  TEXT NOT NULL,
            recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            voided      INTEGER NOT NULL DEFAULT 0
        )""",
        # eski "to'langan" to'lovlar uchun bitta tranzaksiya yaratamiz (daromad hisoboti buzilmasin)
        "INSERT INTO payment_transactions (payment_id, amount, method, external_id, created_at, recorded_by) "
        "SELECT id, amount - discount, method, NULL, COALESCE(paid_at, due_date || ' 00:00:00'), recorded_by "
        "FROM payments WHERE status='paid' AND amount - discount > 0",
    ]),
]
SCHEMA_VERSION = MIGRATIONS[-1][0] if MIGRATIONS else 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class DB:
    """sqlite3 ustidagi yupqa qobiq: bitta joyda tranzaksiya va so'rovlar."""

    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 15000")
        try:   # WAL: o'qish yozishni bloklamaydi (bir nechta foydalanuvchi uchun). Fayl tizimi qo'llamasa — jim o'tadi.
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            pass
        self._depth = 0

    # -- so'rovlar
    def execute(self, sql, args=()):
        return self.conn.execute(sql, args)

    def insert(self, sql, args=()):
        return self.conn.execute(sql, args).lastrowid

    def one(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def scalar(self, sql, args=(), default=None):
        row = self.conn.execute(sql, args).fetchone()
        if row is None or row[0] is None:
            return default
        return row[0]

    # -- tranzaksiya (ichma-ich chaqirish mumkin: ichki qatlam SAVEPOINT bilan o'raladi)
    @contextmanager
    def tx(self):
        savepoint = None
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            savepoint = "sp%d" % self._depth
            self.conn.execute("SAVEPOINT " + savepoint)
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            try:
                if savepoint:
                    # faqat shu qatlamdagi yozuvlar bekor qilinadi; tashqi tranzaksiya davom etishi mumkin
                    self.conn.execute("ROLLBACK TO " + savepoint)
                    self.conn.execute("RELEASE " + savepoint)
                else:
                    self.conn.execute("ROLLBACK")
            except sqlite3.Error:
                log.exception("rollback muvaffaqiyatsiz")
            raise
        else:
            self._depth -= 1
            self.conn.execute("RELEASE " + savepoint if savepoint else "COMMIT")

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


def get_db():
    if "db" not in g:
        g.db = DB(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None):
    database = g.pop("db", None)
    if database is not None:
        database.close()


def _has_table(database, name):
    return database.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


def backup_database(src_path, dest_path):
    """SQLite backup API: ish paytida ham izchil nusxa oladi (WAL bilan ham)."""
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(dest_path)


def restore_database(backup_path, target_path, safety_dir=None):
    """Zaxiradan tiklaydi. Avval nusxaning butunligi tekshiriladi, joriy baza esa saqlab qo'yiladi."""
    probe = sqlite3.connect(str(backup_path))
    try:
        if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup_corrupt")
        if probe.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone() is None:
            raise ValueError("backup_not_smartedu")
    finally:
        probe.close()
    safety = None
    if os.path.exists(str(target_path)):
        folder = safety_dir or os.path.join(os.path.dirname(str(target_path)), "backups")
        safety = backup_database(target_path, os.path.join(
            folder, "before-restore-%s.db" % datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")))
    src = sqlite3.connect(str(backup_path))
    dst = sqlite3.connect(str(target_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return safety


def migrate(database):
    """Mavjud bazani yangi versiyaga o'tkazadi. Ma'lumotlar saqlanadi; xato bo'lsa hammasi qaytariladi."""
    current = database.scalar("PRAGMA user_version", default=0)
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return current
    if current > SCHEMA_VERSION:
        raise RuntimeError("Baza (v%d) dastur (v%d) dan yangi. Dasturni yangilang." % (current, SCHEMA_VERSION))
    if database.path != ":memory:" and os.path.exists(database.path):
        folder = os.path.join(os.path.dirname(os.path.abspath(database.path)), "backups")
        backup_database(database.path, os.path.join(folder, "before-migrate-v%d.db" % current))
    for version, description, statements in pending:
        log.info("migratsiya v%d: %s", version, description)
        with database.tx():
            for sql in statements:
                try:
                    database.execute(sql)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):     # ustun allaqachon bor — xavfsiz
                        raise
            database.execute("PRAGMA user_version = %d" % version)   # SQLite'da PRAGMA tranzaksiyaga kiradi
    return database.scalar("PRAGMA user_version", default=0)


def init_db(database):
    """Toza bazani yaratadi yoki mavjudini migratsiya qiladi (qayta chaqirish xavfsiz)."""
    from .services import settings as settings_service

    fresh = not _has_table(database, "users")
    if not fresh:
        migrate(database)                       # eski jadvallarga yangi ustunlar
    database.conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))   # yangi jadval/indekslar
    if fresh:
        database.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    settings_service.ensure_defaults(database)
    for code, name in (("en", "English"), ("ru", "Russian"), ("ko", "Korean")):
        database.execute("INSERT OR IGNORE INTO subjects (code, name) VALUES (?, ?)", (code, name))


def init_app(app):
    app.teardown_appcontext(close_db)
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    database = DB(app.config["DATABASE_PATH"])
    try:
        init_db(database)
    finally:
        database.close()
