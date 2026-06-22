from .base import *  # noqa: F401,F403


SECRET_KEY = required_env("DJANGO_SECRET_KEY")  # noqa: F405
DEBUG = False
AUTH_ALLOW_DEV_USER = False
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS = False

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])  # noqa: F405
if not ALLOWED_HOSTS:
    raise RuntimeError("Missing required environment variable: DJANGO_ALLOWED_HOSTS")

MSAL_TENANT_ID = required_env("MSAL_TENANT_ID")  # noqa: F405
MSAL_AUDIENCE = env("MSAL_AUDIENCE", default="")  # noqa: F405
MSAL_AUDIENCES = build_msal_audiences()  # noqa: F405
if not MSAL_AUDIENCES:
    raise RuntimeError("Missing required environment variable: MSAL_AUDIENCE or MSAL_AUDIENCES")

DATABASES = build_database_config(required_env("DATABASE_URL"), allow_sqlite_fallback=False)  # noqa: F405
