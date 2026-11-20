#!/usr/bin/env python3
"""SMART EDUCATION CENTER boshqaruv buyrug'i. Faqat standart kutubxona + Flask talab qilinadi.

  python manage.py init-db            bazani yaratish
  python manage.py create-admin       administrator yaratish
  python manage.py seed-demo          DEMO ma'lumotlar (faqat sinash uchun)
  python manage.py run                mahalliy server (http://127.0.0.1:5000)
  python manage.py bot                Telegram botni polling rejimida ishga tushirish
  python manage.py set-webhook        Telegram webhook o'rnatish (hosting uchun, BASE_URL https bo'lishi shart)
  python manage.py tick               davriy ishlar (Battle eslatmalari, jarimalar, xabarlar navbati)
  python manage.py backup             bazaning zaxira nusxasi
  python manage.py restore FAYL       zaxiradan tiklash (oldingi baza avtomatik saqlanadi)
  python manage.py check              sozlamalar va ulanishlar holati
"""
import argparse
import getpass
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartedu import create_app  # noqa: E402
from smartedu import utils  # noqa: E402
from smartedu.db import get_db  # noqa: E402


def cmd_init_db(app, _args):
    with app.app_context():
        get_db().scalar("SELECT 1")
    print("Baza tayyor:", app.config["DATABASE_PATH"])


def cmd_create_admin(app, args):
    from smartedu import seed
    from smartedu.utils import AppError
    username = args.username or os.environ.get("ADMIN_USERNAME") or input("Login: ").strip()
    password = args.password or os.environ.get("ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Parol (kamida 8 belgi, harf va raqam): ")
        if getpass.getpass("Parolni takrorlang: ") != password:
            print("Parollar mos kelmadi.")
            return 1
    with app.app_context():
        try:
            seed.create_admin(get_db(), username, password)
        except AppError as exc:
            print("Xatolik:", exc.code)
            return 1
    print("Administrator yaratildi:", username)


def cmd_seed_demo(app, _args):
    from smartedu import seed
    with app.app_context():
        done = seed.seed_demo(get_db())
    if done:
        print("Demo ma'lumotlar yaratildi. Barcha demo foydalanuvchilar paroli: %s" % seed.DEMO_PASSWORD)
        print("Loginlar: demo_admin, demo_ali, demo_olga, demo_s1..demo_s6, demo_parent")
        print("DIQQAT: demo hisoblarni haqiqiy serverda ishlatmang!")
    else:
        print("Demo ma'lumotlar allaqachon mavjud.")


def cmd_run(app, args):
    app.run(host=args.host, port=args.port, debug=app.config["DEBUG"])


def cmd_bot(app, _args):
    from smartedu.bot.routes import run_polling
    try:
        run_polling(app)
    except KeyboardInterrupt:
        print("\nBot to'xtatildi.")
    except RuntimeError as exc:
        print(exc)
        return 1


def cmd_set_webhook(app, args):
    from smartedu.providers import telegram as tg
    client = tg.get_client(app)
    if client is None:
        print("TELEGRAM_BOT_TOKEN sozlanmagan.")
        return 1
    if args.delete:
        client.delete_webhook()
        print("Webhook o'chirildi.")
        return 0
    base = app.config["BASE_URL"]
    if not base.startswith("https://"):
        print("Webhook uchun BASE_URL https:// bilan boshlanishi shart (hozir: %s)." % base)
        return 1
    client.set_webhook(base + "/telegram/webhook", app.config.get("TELEGRAM_WEBHOOK_SECRET") or None)
    print("Webhook o'rnatildi:", base + "/telegram/webhook")


def cmd_tick(app, _args):
    from smartedu.services import scheduler
    with app.app_context():
        print(scheduler.run(get_db()))


def cmd_backup(app, _args):
    from smartedu.db import backup_database
    name = "smartedu-%s.db" % utils.now().strftime("%Y%m%d-%H%M%S")
    path = backup_database(app.config["DATABASE_PATH"], os.path.join(app.config["BACKUP_DIR"], name))
    print("Zaxira nusxa:", path)


def cmd_restore(app, args):
    from smartedu.db import restore_database
    try:
        safety = restore_database(args.file, app.config["DATABASE_PATH"], app.config["BACKUP_DIR"])
    except (ValueError, sqlite3.Error, OSError) as exc:
        print("Tiklab bo'lmadi:", exc)
        return 1
    print("Tiklandi. Oldingi baza saqlandi:", safety or "(yo'q edi)")
    print("Saytni qayta ishga tushiring.")


def cmd_check(app, _args):
    cfg = app.config
    ok = lambda flag: "OK " if flag else "-- "
    print("Muhit:", cfg["ENV_NAME"], "| Python", sys.version.split()[0])
    print(ok(True), "Baza:", cfg["DATABASE_PATH"])
    print(ok(bool(cfg["TELEGRAM_BOT_TOKEN"])), "Telegram bot", "(sozlangan)" if cfg["TELEGRAM_BOT_TOKEN"] else "(TELEGRAM_BOT_TOKEN yo'q)")
    print(ok(cfg["AI_PROVIDER"] != "none" and bool(cfg["AI_API_KEY"])), "AI:", cfg["AI_PROVIDER"])
    print(ok(cfg["EMAIL_PROVIDER"] == "smtp"), "Email:", cfg["EMAIL_PROVIDER"])
    print(ok(True), "To'lov:", cfg["PAYMENT_PROVIDER"])
    print(ok(bool(cfg["TICK_TOKEN"])), "Tashqi cron tokeni (TICK_TOKEN)")
    if cfg["ENV_NAME"] == "production":
        print(ok(cfg["BASE_URL"].startswith("https://")), "BASE_URL https")
        print(ok(bool(os.environ.get("SECRET_KEY"))), "SECRET_KEY muhit o'zgaruvchisida")
    with app.app_context():
        db = get_db()
        for table in ("users", "groups", "lessons", "questions", "battles"):
            print("   %-10s %d" % (table, db.scalar("SELECT COUNT(*) FROM %s" % table, (), 0)))


def main():
    parser = argparse.ArgumentParser(description="SMART EDUCATION CENTER")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p = sub.add_parser("create-admin")
    p.add_argument("--username")
    p.add_argument("--password")
    sub.add_parser("seed-demo")
    p = sub.add_parser("run")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    sub.add_parser("bot")
    p = sub.add_parser("set-webhook")
    p.add_argument("--delete", action="store_true")
    sub.add_parser("tick")
    sub.add_parser("backup")
    restore = sub.add_parser("restore")
    restore.add_argument("file", help="zaxira fayl yo'li")
    sub.add_parser("check")
    args = parser.parse_args()
    app = create_app()
    handler = globals()["cmd_" + args.cmd.replace("-", "_")]
    sys.exit(handler(app, args) or 0)


if __name__ == "__main__":
    main()
