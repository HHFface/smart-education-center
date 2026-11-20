"""WSGI kirish nuqtasi: gunicorn wsgi:app  yoki  PythonAnywhere WSGI faylida `from wsgi import app as application`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartedu import create_app  # noqa: E402

app = create_app()
