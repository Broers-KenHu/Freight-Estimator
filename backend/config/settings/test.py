from .base import *  # noqa: F401,F403


SECRET_KEY = "test-secret-key"
DEBUG = False
AUTH_ALLOW_DEV_USER = True
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
