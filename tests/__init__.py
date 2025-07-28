import os

import django

# django setup must occur before importing models
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
django.setup()

from .django_setup import destroy_db, init_db  # noqa: E402


def setup():
    """Initialize database for pytest"""
    init_db()


def teardown():
    destroy_db()
