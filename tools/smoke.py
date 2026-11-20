#!/usr/bin/env python3
"""Barcha GET sahifalarni demo foydalanuvchilar bilan ochib, 500 xatoliklarni topadi.

  python tools/smoke.py                # hamma sahifalar
  python tools/smoke.py student/ battle # faqat yo'li shu prefikslardan boshlanganlar
"""
import os
import re
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

home = tempfile.mkdtemp(prefix="smartedu-smoke-")
os.environ["SMARTEDU_HOME"] = home
os.environ["SECRET_KEY"] = "smoke"
os.environ["OPPORTUNISTIC_TICK"] = "0"

from smartedu import create_app, seed  # noqa: E402
from smartedu.db import DB  # noqa: E402

ROLE_LOGIN = {"student": "demo_s1", "teacher": "demo_ali", "parent": "demo_parent", "admin": "demo_admin"}
PREFIX_ROLE = {"/student": "student", "/teacher": "teacher", "/parent": "parent", "/admin": "admin"}


def main():
    prefixes = sys.argv[1:]
    app = create_app({"PROPAGATE_EXCEPTIONS": True, "TESTING": True})
    db = DB(app.config["DATABASE_PATH"])
    seed.seed_demo(db)
    db.close()
    failures = 0
    checked = 0
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        path = re.sub(r"<(?:int:)?[a-z_]+>", "1", rule.rule)
        path = re.sub(r"<(?:string:)?[a-z_]+>", "demo", path)
        if prefixes and not any(path.lstrip("/").startswith(p.strip("/")) for p in prefixes):
            continue
        if path.startswith("/internal") or path.startswith("/telegram") or path.startswith("/logout"):
            continue
        roles = [PREFIX_ROLE[k] for k in PREFIX_ROLE if path.startswith(k)]
        if not roles:
            roles = ["student", "teacher", "parent", "admin"]
        if path.startswith("/battle"):
            roles = ["student"]
        for role in roles:
            client = app.test_client()
            page = client.get("/login")
            token = re.search(r'name="_csrf" value="([^"]+)"', page.get_data(as_text=True)).group(1)
            client.post("/login", data={"username": ROLE_LOGIN[role], "password": seed.DEMO_PASSWORD, "_csrf": token})
            checked += 1
            try:
                resp = client.get(path)
                status = resp.status_code
            except Exception:
                failures += 1
                print("FAIL  %-8s %-40s" % (role, path))
                print("      " + traceback.format_exc().strip().splitlines()[-1])
                continue
            if status >= 500:
                failures += 1
                print("FAIL  %-8s %-40s -> %s" % (role, path, status))
    print("\nTekshirildi: %d, xatolik: %d" % (checked, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
