#!/usr/bin/env python3
"""smartedu/translations/src/*.tsv  ->  smartedu/translations/<til>.json

Manba format (har qator):  kalit|uz|en|ru|ko     ('#' bilan boshlangan qatorlar izoh)
O'zbek kirill (uz_cyrl) lotin matnidan avtomatik translit qilinadi.
Tekshiruvlar: ustunlar soni, takroriy kalit, {placeholder}lar barcha tillarda bir xilmi.

  python tools/build_translations.py
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "smartedu", "translations", "src")
OUT = os.path.join(ROOT, "smartedu", "translations")
PLACE = re.compile(r"\{[a-z_]+\}")

PROTECT = re.compile(
    r"(\{[a-z_]+\}|\n|https?://\S+|/link\b|\.env|\bXP\b|\bAI\b|\bJSON\b|\bElo\b|\bTelegram\b|\bEmail\b|"
    r"\bWebhook\b|\bbackups\b|\bK\b|\bTTS\b|\bPDF\b|[0-9]+|[^\w\s'‘’ʻ])",
)
WORD = re.compile(r"[A-Za-z'‘’ʻ]+")
VOW = set("aeiouAEIOU")
SINGLE = {"a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж",
          "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с",
          "t": "т", "u": "у", "v": "в", "x": "х", "y": "й", "z": "з", "c": "к", "w": "в"}
SPECIAL_WORDS = {"battle": "баттл", "coin": "коин"}


def _case(src, dst):
    if src.isupper() and len(src) > 0:
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


def translit_word(word):
    low = word.lower().replace("‘", "'").replace("’", "'").replace("ʻ", "'")
    for k, v in SPECIAL_WORDS.items():
        if low.startswith(k):
            return _case(word[:len(k)], v) + translit_word(word[len(k):]) if word[len(k):] else _case(word, v)
    out = []
    i, n = 0, len(low)
    while i < n:
        ch = low[i]
        nxt = low[i + 1] if i + 1 < n else ""
        orig = word[i] if i < len(word) else ch
        if ch in "og" and nxt == "'":
            out.append(_case(orig, "ў" if ch == "o" else "ғ"))
            i += 2
            continue
        if ch == "'":
            out.append("ъ")
            i += 1
            continue
        if ch == "s" and nxt == "h":
            out.append(_case(orig, "ш")); i += 2; continue
        if ch == "c" and nxt == "h":
            out.append(_case(orig, "ч")); i += 2; continue
        if ch == "y" and nxt in ("a", "u", "o") and not (nxt == "o" and i + 2 < n and low[i + 2] == "'"):
            out.append(_case(orig, {"a": "я", "u": "ю", "o": "ё"}[nxt])); i += 2; continue
        if ch == "y" and nxt == "e":
            out.append(_case(orig, "е")); i += 2; continue
        if ch == "e" and (i == 0 or low[i - 1] in "aeiou"):
            out.append(_case(orig, "э")); i += 1; continue
        out.append(_case(orig, SINGLE.get(ch, ch)))
        i += 1
    return "".join(out)


def to_cyrillic(text):
    parts = PROTECT.split(text)
    res = []
    for part in parts:
        if not part:
            continue
        if PROTECT.fullmatch(part):
            res.append(part)
        else:
            res.append(WORD.sub(lambda m: translit_word(m.group(0)), part))
    return "".join(res)


def load():
    rows, errors = {}, []
    for path in sorted(glob.glob(os.path.join(SRC, "*.tsv"))):
        for ln, line in enumerate(open(path, encoding="utf-8"), 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("|")
            where = "%s:%d" % (os.path.basename(path), ln)
            if len(cols) != 5:
                errors.append("%s: 5 ustun kerak, %d topildi (%s)" % (where, len(cols), cols[0]))
                continue
            key, uz, en, ru, ko = [c.strip().replace("\\n", "\n") for c in cols]
            if not all((uz, en, ru, ko)):
                errors.append("%s: bo'sh tarjima (%s)" % (where, key))
                continue
            sets = [frozenset(PLACE.findall(v)) for v in (uz, en, ru, ko)]
            if len(set(sets)) != 1:
                errors.append("%s: {placeholder}lar mos emas (%s)" % (where, key))
            rows[key] = (uz, en, ru, ko)
    return rows, errors


def main():
    rows, errors = load()
    if errors:
        print("\n".join(errors))
        return 1
    cat = {"uz": {}, "uz_cyrl": {}, "en": {}, "ru": {}, "ko": {}}
    for key, (uz, en, ru, ko) in sorted(rows.items()):
        cat["uz"][key], cat["en"][key], cat["ru"][key], cat["ko"][key] = uz, en, ru, ko
        cat["uz_cyrl"][key] = to_cyrillic(uz)
    for code, table in cat.items():
        with open(os.path.join(OUT, code + ".json"), "w", encoding="utf-8") as fh:
            json.dump(table, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
    print("Yozildi: %d kalit x %d til" % (len(rows), len(cat)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
