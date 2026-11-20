import os
import sqlite3
import threading

import pytest

from smartedu import db as dbmod, utils
from smartedu.db import DB
from smartedu.services import points
from smartedu.utils import AppError

BASE = dbmod.SCHEMA_VERSION      # testlar haqiqiy joriy versiyadan boshlab bitta ortiq soxta migratsiya qo'shadi


def _student(db):
    return db.one("SELECT id FROM users WHERE role='student' ORDER BY id")["id"]


# ---------- ballar
def test_sum_between_ignores_revoked(db):
    sid = _student(db)
    start = "2000-01-01 00:00:00"
    end = "2100-01-01 00:00:00"
    base = points.sum_between(db, sid, "xp", start, end)
    log = points.award(db, sid, "xp", 40, "teacher_award", actor_id=1)
    assert points.sum_between(db, sid, "xp", start, end) == base + 40
    points.revoke(db, log, 1, "xato")
    assert points.sum_between(db, sid, "xp", start, end) == base      # bekor qilingan hisobga olinmaydi


def test_revoke_rules(db):
    sid = _student(db)
    log = points.award(db, sid, "coin", 30, "teacher_award", actor_id=1)
    points.revoke(db, log, 1)
    with pytest.raises(AppError):
        points.revoke(db, log, 1)
    rev = db.one("SELECT id FROM points_log WHERE reason_code='revoked' AND ref_id=?", (log,))["id"]
    with pytest.raises(AppError):
        points.revoke(db, rev, 1)                      # teskari yozuvni bekor qilib bo'lmaydi
    with pytest.raises(AppError):
        points.revoke(db, 987654, 1)


def test_coin_never_negative_and_xp_not_reducible(db):
    sid = _student(db)
    points.award(db, sid, "coin", -10 ** 6, "homework_missed", actor_id=None)
    assert points.stats(db, sid)["coins"] == 0
    with pytest.raises(ValueError):
        points.award(db, sid, "xp", -5, "homework_missed")
    xp = points.stats(db, sid)["xp"]
    points.award(db, sid, "xp", -3, "admin_adjust", actor_id=1)        # faqat administrator tuzatishi
    assert points.stats(db, sid)["xp"] == max(0, xp - 3)


def test_history_has_reason_actor_date(db):
    sid = _student(db)
    points.award(db, sid, "coin", 5, "teacher_award", "yaxshi", actor_id=1)
    row = points.history(db, sid, "coin", 1)[0]
    assert row["reason_code"] == "teacher_award" and row["created_by"] == 1 and row["created_at"]


def test_revoke_by_ref_only_once(db):
    sid = _student(db)
    points.award(db, sid, "coin", 10, "attendance_present", actor_id=1, ref_type="attendance", ref_id=777)
    assert points.revoke_by_ref(db, "attendance", 777, 1) == 1
    assert points.revoke_by_ref(db, "attendance", 777, 1) == 0


def test_concurrent_revoke_only_one_wins(app, db):
    """Ikki parallel so'rov bir yozuvni bekor qilsa — balans faqat bir marta o'zgaradi."""
    sid = _student(db)
    log = points.award(db, sid, "coin", 50, "teacher_award", actor_id=1)
    before = points.stats(db, sid)["coins"]
    results = []

    def worker():
        conn = DB(app.config["DATABASE_PATH"])
        try:
            points.revoke(conn, log, 1)
            results.append("ok")
        except AppError as exc:
            results.append(exc.code)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("already_revoked") == 5
    assert points.stats(db, sid)["coins"] == before - 50


# ---------- tranzaksiyalar
def test_tx_rolls_back_everything(db):
    sid = _student(db)
    before = points.stats(db, sid)["coins"]
    with pytest.raises(RuntimeError):
        with db.tx():
            points.award(db, sid, "coin", 100, "teacher_award", actor_id=1, notify=False)
            raise RuntimeError("boom")
    assert points.stats(db, sid)["coins"] == before


def test_nested_tx_savepoint_partial_rollback(db):
    sid = _student(db)
    before = points.stats(db, sid)["coins"]
    with db.tx():
        points.award(db, sid, "coin", 10, "teacher_award", actor_id=1, notify=False)
        try:
            with db.tx():                                   # ichki qatlam xato bilan tugaydi
                points.award(db, sid, "coin", 1000, "teacher_award", actor_id=1, notify=False)
                raise RuntimeError("inner")
        except RuntimeError:
            pass                                            # tashqi qatlam xatoni ushladi va davom etadi
        points.award(db, sid, "coin", 5, "teacher_award", actor_id=1, notify=False)
    assert points.stats(db, sid)["coins"] == before + 15    # ichki +1000 qaytarilgan, qolganlari saqlangan


# ---------- baza: sxema, zaxira, tiklash, migratsiya
def test_schema_integrity_and_foreign_keys(db):
    assert db.scalar("PRAGMA integrity_check") == "ok"
    assert db.all("PRAGMA foreign_key_check") == []
    assert db.scalar("PRAGMA foreign_keys") == 1
    assert db.scalar("PRAGMA user_version") == dbmod.SCHEMA_VERSION


def test_unique_constraints(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('demo_s1','x','student','2020-01-01 00:00:00')")


def test_backup_and_restore_roundtrip(app, db, tmp_path):
    sid = _student(db)
    path = dbmod.backup_database(app.config["DATABASE_PATH"], tmp_path / "b" / "copy.db")
    marker = points.award(db, sid, "coin", 77, "teacher_award", actor_id=1, notify=False)
    assert db.one("SELECT 1 FROM points_log WHERE id=?", (marker,))
    safety = dbmod.restore_database(path, app.config["DATABASE_PATH"], str(tmp_path / "safe"))
    assert os.path.exists(safety)
    conn = DB(app.config["DATABASE_PATH"])
    try:
        assert conn.one("SELECT 1 FROM points_log WHERE id=?", (marker,)) is None      # tiklashdan keyin yo'q
        assert conn.scalar("SELECT COUNT(*) FROM users") > 0                            # foydalanuvchilar saqlangan
    finally:
        conn.close()


def test_restore_rejects_garbage(app, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite")
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        dbmod.restore_database(bad, app.config["DATABASE_PATH"], str(tmp_path))
    other = tmp_path / "other.db"
    c = sqlite3.connect(str(other)); c.execute("CREATE TABLE x (a)"); c.commit(); c.close()
    with pytest.raises(ValueError):
        dbmod.restore_database(other, app.config["DATABASE_PATH"], str(tmp_path))


def test_migration_keeps_data(app, db, tmp_path, monkeypatch):
    """Eski (v1) bazaga yangi migratsiya qo'llanganda foydalanuvchilar va ballar saqlanadi."""
    before_users = db.scalar("SELECT COUNT(*) FROM users")
    migs = list(dbmod.MIGRATIONS) + [(dbmod.SCHEMA_VERSION + 1, "test", ["ALTER TABLE shop_items ADD COLUMN test_col TEXT DEFAULT 'z'"])]
    monkeypatch.setattr(dbmod, "MIGRATIONS", migs)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", BASE + 1)
    db.execute("PRAGMA user_version = %d" % BASE)
    dbmod.init_db(db)
    assert db.scalar("PRAGMA user_version") == BASE + 1
    assert db.scalar("SELECT COUNT(*) FROM users") == before_users
    assert db.scalar("SELECT test_col FROM shop_items LIMIT 1") == "z"
    assert os.path.exists(os.path.join(os.path.dirname(app.config["DATABASE_PATH"]), "backups", "before-migrate-v%d.db" % BASE))
    dbmod.init_db(db)                                       # qayta ishga tushirish xavfsiz
    assert db.scalar("PRAGMA user_version") == BASE + 1


def test_failed_migration_rolls_back(app, db, monkeypatch):
    migs = list(dbmod.MIGRATIONS) + [(dbmod.SCHEMA_VERSION + 1, "bad", ["ALTER TABLE shop_items ADD COLUMN ok_col TEXT", "THIS IS NOT SQL"])]
    monkeypatch.setattr(dbmod, "MIGRATIONS", migs)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", BASE + 1)
    db.execute("PRAGMA user_version = %d" % BASE)
    with pytest.raises(sqlite3.Error):
        dbmod.migrate(db)
    assert db.scalar("PRAGMA user_version") == BASE
    cols = [r["name"] for r in db.all("PRAGMA table_info(shop_items)")]
    assert "ok_col" not in cols


def test_newer_db_than_app_refused(db, monkeypatch):
    db.execute("PRAGMA user_version = 99")
    monkeypatch.setattr(dbmod, "MIGRATIONS", list(dbmod.MIGRATIONS) + [(100, "x", [])])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 5)
    with pytest.raises(RuntimeError):
        dbmod.migrate(db)
    db.execute("PRAGMA user_version = %d" % dbmod.SCHEMA_VERSION)


def test_concurrent_writers_do_not_corrupt(app, db):
    sid = _student(db)
    before = points.stats(db, sid)["coins"]
    errors = []

    def worker():
        conn = DB(app.config["DATABASE_PATH"])
        try:
            for _ in range(10):
                points.award(conn, sid, "coin", 1, "teacher_award", actor_id=1, notify=False)
        except Exception as exc:      # noqa
            errors.append(repr(exc))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert points.stats(db, sid)["coins"] == before + 50


def test_old_v1_database_upgrades_with_data(tmp_path):
    """Haqiqiy eski (v1) baza: yangi ustun/jadvallar yo'q — migratsiya qo'shadi, ma'lumot (jumladan to'langan to'lov) saqlanadi."""
    path = str(tmp_path / "old.db")
    conn = DB(path)
    schema = dbmod.SCHEMA_FILE.read_text(encoding="utf-8")
    schema = schema.replace(",\n    file_name   TEXT\n);", "\n);")
    schema = schema.replace(",\n    revoked    INTEGER NOT NULL DEFAULT 0,\n    revoked_at TEXT,\n    revoked_reason TEXT\n);", "\n);")
    start = schema.index("CREATE TABLE IF NOT EXISTS payment_transactions")
    end = schema.index("CREATE TABLE IF NOT EXISTS expenses")
    schema = schema[:start] + schema[end:]
    conn.conn.executescript(schema)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('old_user','x','student','2020-01-01 00:00:00')")
    uid = conn.one("SELECT id FROM users WHERE username='old_user'")["id"]
    conn.execute("INSERT INTO payments (student_id, amount, discount, due_date, paid_at, status, method) "
                 "VALUES (?,1000,100,'2024-01-01','2024-01-02 10:00:00','paid','cash')", (uid,))
    assert "file_name" not in [r["name"] for r in conn.all("PRAGMA table_info(absence_requests)")]
    dbmod.init_db(conn)
    assert "file_name" in [r["name"] for r in conn.all("PRAGMA table_info(absence_requests)")]
    assert "revoked" in [r["name"] for r in conn.all("PRAGMA table_info(certificates)")]
    assert conn.one("SELECT 1 FROM users WHERE username='old_user'")
    assert conn.scalar("SELECT SUM(amount) FROM payment_transactions") == 900       # eski to'lov tranzaksiyaga o'tdi
    assert conn.scalar("PRAGMA user_version") == dbmod.SCHEMA_VERSION
    assert conn.scalar("PRAGMA integrity_check") == "ok"
    conn.close()
