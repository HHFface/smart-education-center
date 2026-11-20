#!/usr/bin/env python3
"""Kodda va shablonlarda ishlatilgan tarjima kalitlarini yig'adi.

  python tools/i18n_scan.py            # kalitlar ro'yxati (statik + dinamik prefikslar)
  python tools/i18n_scan.py --missing  # tarjima fayllarida yo'q kalitlar
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "smartedu")

# t('key'), _('key'), flash_t("key"), t("key", ...) ; kalit: harf bilan boshlanuvchi, nuqta bilan ajratilgan
KEY = r"[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
STATIC = [
    re.compile(r"""\b(?:t|_|flash_t)\(\s*['"](%s)['"]""" % KEY),
    re.compile(r"""\bt\(\s*['"](%s)['"]""" % KEY),
]
# Python fayllaridagi to'g'ridan-to'g'ri yozilgan kalit literallari (masalan bot menyusi)
LITERAL = re.compile(r"""['"]((?:bot|msg|mail|auth|account|cert|landing|lbl|btn)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)['"]""")
# Shablon/kodda o'zgaruvchi orqali beriladigan kalitlar: ('bt.online'), section('bt.incoming', ...), nav.py
ANY_LITERAL = re.compile(r"""['"]((?:nav|bt|st|tc|ad|ae|pa)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)['"]""")
DYNAMIC = re.compile(r"""\bt\(\s*['"]([a-z][a-z0-9_.]*\.)['"]\s*~""")
ERR = re.compile(r"""AppError\(\s*['"]([a-z_0-9]+)['"]""")
NOTIFY = re.compile(r"""notify(?:_many|_parents)?\(\s*db\s*,\s*[^,]+,\s*['"]([a-z_]+)['"]""")
REASON = re.compile(r"""(?:award|revoke)\([^)]*?['"]([a-z_]+)['"]""")


def files():
    for base, _dirs, names in os.walk(PKG):
        for name in names:
            if name.endswith((".py", ".html", ".js")):
                yield os.path.join(base, name)


def scan():
    static, dynamic = {}, {}
    errors, notifs = set(), set()
    for path in files():
        text = open(path, encoding="utf-8").read()
        rel = os.path.relpath(path, ROOT)
        for rx in STATIC:
            for key in rx.findall(text):
                static.setdefault(key, set()).add(rel)
        for prefix in DYNAMIC.findall(text):
            dynamic.setdefault(prefix, set()).add(rel)
        for key in ANY_LITERAL.findall(text):
            static.setdefault(key, set()).add(rel)
        if path.endswith(".py"):
            errors.update(ERR.findall(text))
            notifs.update(NOTIFY.findall(text))
            for key in LITERAL.findall(text):
                static.setdefault(key, set()).add(rel)
    return static, dynamic, errors, notifs


def load_catalog():
    cat = {}
    folder = os.path.join(PKG, "translations")
    for code in ("uz", "uz_cyrl", "ru", "en", "ko"):
        path = os.path.join(folder, code + ".json")
        cat[code] = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}
    return cat


def main():
    static, dynamic, errors, notifs = scan()
    keys = set(k for k in static if not k.endswith(("_", ".")))  # oxiri "_"/"." — dinamik prefiks
    keys.update("err." + e for e in errors)
    keys.update("notif." + n for n in notifs)
    if "--missing" in sys.argv:
        cat = load_catalog()
        bad = 0
        for code, table in cat.items():
            missing = sorted(k for k in keys if k not in table)
            if missing:
                bad += len(missing)
                print("[%s] yo'q: %d" % (code, len(missing)))
                for key in missing[:60]:
                    print("   ", key)
        print("Jami kalit:", len(keys), "| yetishmayotgan:", bad)
        return 1 if bad else 0
    for key in sorted(keys):
        print(key)
    print("--- dinamik prefikslar:")
    for prefix, where in sorted(dynamic.items()):
        print(prefix, sorted(where)[:2])
    print("Jami statik kalit:", len(keys))


if __name__ == "__main__":
    sys.exit(main())
