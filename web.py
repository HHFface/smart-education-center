"""Veb qatlami uchun umumiy yordamchilar: xabarlar, qaytish, fayl yuklash."""
import os
import uuid
from urllib.parse import urlparse

from flask import current_app, flash, request, url_for

from . import i18n
from .utils import AppError

UPLOAD_KINDS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp"},
    "document": {"pdf", "doc", "docx", "txt", "xls", "xlsx", "ppt", "pptx"},
    "audio": {"mp3", "wav", "ogg", "m4a"},
    "video": {"mp4", "webm", "mov"},
}
INLINE_KINDS = {"image"}


HOME = {"student": "student.dashboard", "teacher": "teacher.dashboard",
        "parent": "parent.dashboard", "admin": "admin.dashboard"}


def home_for(user):
    return url_for(HOME.get(user["role"], "main.index"))


def flash_t(key, category="success", **params):
    flash(i18n.t(key, **params), category)


def flash_error(exc):
    flash(i18n.t("err.%s" % exc.code, **exc.params), "error")


def is_safe_target(target):
    if not target or "\\" in target or any(ord(ch) < 32 for ch in target):
        return False                      # "/\\evil.com" kabi brauzer "//" deb o'qiydigan yo'nalishlar
    host = urlparse(request.host_url)
    ref = urlparse(target)
    return (not ref.netloc or ref.netloc == host.netloc) and (ref.scheme in ("", "http", "https"))


def back(default_endpoint, **values):
    """Oldingi sahifaga qaytish (faqat shu sayt ichida), aks holda standart sahifaga."""
    ref = request.referrer
    if ref and is_safe_target(ref):
        return ref
    return url_for(default_endpoint, **values)


def kind_of(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for kind, exts in UPLOAD_KINDS.items():
        if ext in exts:
            return kind, ext
    return None, ext


_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SIGNATURES = {
    "png": lambda h: h.startswith(b"\x89PNG\r\n\x1a\n"),
    "jpg": lambda h: h.startswith(b"\xff\xd8\xff"),
    "jpeg": lambda h: h.startswith(b"\xff\xd8\xff"),
    "gif": lambda h: h[:6] in (b"GIF87a", b"GIF89a"),
    "webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
    "pdf": lambda h: h.lstrip()[:5] == b"%PDF-",
    "docx": lambda h: h[:4] == b"PK\x03\x04",
    "xlsx": lambda h: h[:4] == b"PK\x03\x04",
    "pptx": lambda h: h[:4] == b"PK\x03\x04",
    "doc": lambda h: h.startswith(_OLE),
    "xls": lambda h: h.startswith(_OLE),
    "ppt": lambda h: h.startswith(_OLE),
    "txt": lambda h: b"\x00" not in h,
    "mp3": lambda h: h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0),
    "wav": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WAVE",
    "ogg": lambda h: h[:4] == b"OggS",
    "m4a": lambda h: h[4:8] == b"ftyp",
    "mp4": lambda h: h[4:8] == b"ftyp",
    "mov": lambda h: h[4:8] in (b"ftyp", b"moov", b"wide", b"free", b"mdat"),
    "webm": lambda h: h[:4] == b"\x1a\x45\xdf\xa3",
}
# Aniq tur HTML/skript sifatida talqin qilinishi mumkin bo'lgan belgilar (yashirin kod): rasm/hujjat boshida bo'lmasligi kerak
_ACTIVE = (b"<script", b"<html", b"<?php", b"<svg", b"<!doctype html")


def content_matches(ext, head):
    """Fayl boshidagi baytlar kengaytmaga mos keladimi (kengaytmani soxtalashtirishga qarshi)."""
    check = _SIGNATURES.get(ext)
    if check is None or not check(head):
        return False
    return not any(marker in head[:512].lower() for marker in _ACTIVE)


def save_upload(file_storage, bucket, allowed_kinds=None):
    """Yuklangan faylni xavfsiz nom bilan saqlaydi. (saqlangan_nom, tur) qaytaradi."""
    if file_storage is None or not file_storage.filename:
        return None, None
    kind, ext = kind_of(file_storage.filename)
    if kind is None or (allowed_kinds and kind not in allowed_kinds):
        raise AppError("bad_file_type")
    stream = file_storage.stream
    head = stream.read(512)
    stream.seek(0)
    if not head or not content_matches(ext, head):
        raise AppError("bad_file_type")
    folder = os.path.join(current_app.config["UPLOAD_DIR"], bucket)
    os.makedirs(folder, exist_ok=True)
    name = "%s.%s" % (uuid.uuid4().hex, ext)
    file_storage.save(os.path.join(folder, name))
    return name, kind


def upload_path(bucket, name):
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = os.path.join(current_app.config["UPLOAD_DIR"], bucket, name)
    return path if os.path.isfile(path) else None
