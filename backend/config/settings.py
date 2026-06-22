from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, True),
    AUTH_ALLOW_DEV_USER=(bool, True),
    MSAL_ALLOW_UNVERIFIED_DEV_TOKENS=(bool, False),
    LOCAL_ACCESS_TOKEN_HOURS=(int, 12),
    POSTGRES_CONNECT_TIMEOUT=(int, 8),
    POSTGRES_CONN_MAX_AGE=(int, 120),
    POSTGRES_CONN_HEALTH_CHECKS=(bool, True),
    POSTGRES_STATEMENT_TIMEOUT_MS=(int, 90000),
    POSTGRES_LOCK_TIMEOUT_MS=(int, 10000),
    POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS=(int, 60000),
    POSTGRES_APPLICATION_NAME=(str, "freight-intelligence"),
    DJANGO_DISABLE_SERVER_SIDE_CURSORS=(bool, False),
)
env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-secret-key")

DEBUG = env("DJANGO_DEBUG")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

AUTH_ALLOW_DEV_USER = env("AUTH_ALLOW_DEV_USER")
MSAL_TENANT_ID = env("MSAL_TENANT_ID", default="")
MSAL_AUDIENCE = env("MSAL_AUDIENCE", default="")
MSAL_AUDIENCES = [value for value in env.list("MSAL_AUDIENCES", default=[]) if value]
if MSAL_AUDIENCE and MSAL_AUDIENCE not in MSAL_AUDIENCES:
    MSAL_AUDIENCES.insert(0, MSAL_AUDIENCE)
MSAL_ALLOW_UNVERIFIED_DEV_TOKENS = env("MSAL_ALLOW_UNVERIFIED_DEV_TOKENS")
LOCAL_ACCESS_TOKEN_HOURS = env("LOCAL_ACCESS_TOKEN_HOURS")


# Application definition

INSTALLED_APPS = [
    'corsheaders',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_filters',
    'rest_framework',
    'drf_spectacular',
    'freight',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

if env.bool("USE_SQLITE", default=False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
    if DATABASES["default"].get("ENGINE") in {"django.db.backends.postgresql", "django.db.backends.postgresql_psycopg2"}:
        database_options = DATABASES["default"].setdefault("OPTIONS", {})
        database_options.setdefault("connect_timeout", env("POSTGRES_CONNECT_TIMEOUT"))
        database_options.setdefault("application_name", env("POSTGRES_APPLICATION_NAME"))
        database_options.setdefault(
            "options",
            " ".join(
                [
                    f"-c statement_timeout={env('POSTGRES_STATEMENT_TIMEOUT_MS')}",
                    f"-c lock_timeout={env('POSTGRES_LOCK_TIMEOUT_MS')}",
                    f"-c idle_in_transaction_session_timeout={env('POSTGRES_IDLE_IN_TRANSACTION_TIMEOUT_MS')}",
                ]
            ),
        )
        DATABASES["default"].setdefault("CONN_MAX_AGE", env("POSTGRES_CONN_MAX_AGE"))
        DATABASES["default"].setdefault("CONN_HEALTH_CHECKS", env("POSTGRES_CONN_HEALTH_CHECKS"))

DISABLE_SERVER_SIDE_CURSORS = env("DJANGO_DISABLE_SERVER_SIDE_CURSORS")


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Australia/Sydney'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = 'static/'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173"],
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["freight.authentication.EntraOrDevAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "freight.permissions.HasFreightPermission",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "freight.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "AU Freight Estimator API",
    "DESCRIPTION": "Multi-carrier freight quoting, legacy rate import, and audit API.",
    "VERSION": "1.0.0",
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
