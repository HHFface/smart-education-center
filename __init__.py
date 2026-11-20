"""SMART EDUCATION CENTER: Flask ilovasi fabrikasi."""
import datetime
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for

from . import config as config_mod
from . import db as db_mod
from . import i18n, nav, security, utils, web
from .utils import AppError

__version__ = "1.0.0"


def create_app(overrides=None):
    cfg = config_mod.build_config(overrides)
    app = Flask(__name__, instance_path=cfg["HOME_DIR"])
    app.config.update(cfg)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=14),
        JSON_AS_ASCII=False,
    )
    for key in ("HOME_DIR", "UPLOAD_DIR", "LOG_DIR", "BACKUP_DIR"):
        try:
            Path(app.config[key]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    _setup_logging(app)
    i18n.load_catalog()
    db_mod.init_app(app)
    _register_hooks(app)
    _register_template_helpers(app)
    _register_errors(app)

    from .blueprints import account, admin, auth, battle, main, parent, student, teacher
    from .bot import routes as bot_routes
    for module in (main, auth, account, student, battle, teacher, parent, admin, bot_routes):
        app.register_blueprint(module.bp)
    return app


def _setup_logging(app):
    level = logging.DEBUG if app.config["DEBUG"] else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    marker = "_smartedu_handlers"
    if getattr(root, marker, False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        file_handler = RotatingFileHandler(os.path.join(app.config["LOG_DIR"], "app.log"),
                                           maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass
    setattr(root, marker, True)


def _register_hooks(app):
    @app.before_request
    def before():
        if request.endpoint == "static":
            return None
        security.load_user()
        user = g.user
        lang = None
        if user is not None:
            lang = user["lang"]
        else:
            lang = i18n.pick_lang(session.get("lang")) or i18n.best_from_header(
                request.headers.get("Accept-Language"))
        g.lang = lang if lang in i18n.LANG_CODES else app.config["DEFAULT_LANG"]
        theme = user["theme"] if user is not None else session.get("theme", "system")
        g.theme = theme if theme in ("light", "dark", "system") else "system"
        security.check_csrf()
        if app.config["OPPORTUNISTIC_TICK"] and request.method == "GET":
            try:
                from .services import scheduler
                scheduler.run_if_due(db_mod.get_db())
            except Exception:
                app.logger.exception("opportunistic tick failed")
        return None

    @app.after_request
    def after(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "media-src 'self' https:; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if request.endpoint != "static" and "text/html" in (response.content_type or ""):
            response.headers["Cache-Control"] = "no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response


def _register_template_helpers(app):
    @app.context_processor
    def inject():
        user = g.get("user")
        items, common = nav.for_role(user["role"]) if user else ([], [])
        unread = 0
        if user is not None:
            from .services import notifications
            unread = notifications.unread_count(db_mod.get_db(), user["id"])
        return {
            "_": i18n.t, "t": i18n.t, "csrf_token": security.csrf_token, "current_user": user,
            "lang": g.get("lang", "uz"), "theme": g.get("theme", "system"), "LANGS": i18n.LANGS,
            "app_name": app.config["APP_NAME"], "nav_items": items, "nav_common": common,
            "unread_count": unread, "app_version": __version__,
        }

    @app.template_filter("dt")
    def _dt(value, pattern="%d.%m.%Y %H:%M"):
        return utils.fmt_local(value, pattern) if value else ""

    @app.template_filter("dated")
    def _dated(value):
        if not value:
            return ""
        d = utils.parse_date(value)
        return d.strftime("%d.%m.%Y") if d else str(value)

    @app.template_filter("money")
    def _money(value):
        try:
            return "{:,}".format(int(value)).replace(",", " ")
        except (TypeError, ValueError):
            return value

    @app.template_filter("subject")
    def _subject(value, code=None):
        """Fan nomini joriy tilda ko'rsatadi (kod bo'yicha tarjima, topilmasa bazadagi nom)."""
        if code:
            key = "subject.%s" % code
            if i18n.has_key(key):
                return i18n.t(key)
        return value


def _register_errors(app):
    def wants_json():
        return request.path.startswith("/battle/api") or request.path.startswith("/telegram") or request.is_json

    def page(code, key):
        if wants_json():
            return jsonify({"ok": False, "error": key}), code
        return render_template("common/error.html", code=code, message=i18n.t("http.%s" % key)), code

    @app.errorhandler(400)
    def e400(_e):
        return page(400, "bad_request")

    @app.errorhandler(403)
    def e403(_e):
        return page(403, "forbidden")

    @app.errorhandler(404)
    def e404(_e):
        return page(404, "not_found")

    @app.errorhandler(413)
    def e413(_e):
        return page(413, "too_large")

    @app.errorhandler(500)
    def e500(_e):
        return page(500, "server_error")

    @app.errorhandler(AppError)
    def app_error(exc):
        message = i18n.t("err.%s" % exc.code, **exc.params)
        if wants_json():
            return jsonify({"ok": False, "error": exc.code, "message": message}), 400
        if request.method == "GET":
            status = {"not_found": 404, "forbidden": 403}.get(exc.code, 400)
            return render_template("common/error.html", code=status, message=message), status
        flash(message, "error")
        return redirect(web.back("main.index"))
