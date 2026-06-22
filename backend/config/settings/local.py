from .base import *  # noqa: F401,F403


SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-secret-key")  # noqa: F405
DEBUG = env("DJANGO_DEBUG", default=True)  # noqa: F405
AUTH_ALLOW_DEV_USER = env("AUTH_ALLOW_DEV_USER", default=True)  # noqa: F405

DATABASES = build_database_config(allow_sqlite_fallback=True)  # noqa: F405
