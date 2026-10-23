"""Demo settings showing how a host project integrates ``bank_statement_django``.

Configuration comes from environment variables (see ``.env.example``). These
defaults are for local development only; see the README for production advice.
"""

import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
# A random key per process if none is configured (sessions reset on restart). Always set one in production.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or get_random_secret_key()
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "bank_statement_django",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "demo_project.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "demo_project.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploads above this size are streamed to a temporary file that Django deletes after the request.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {"bank_statement_upload": os.environ.get("BSA_UPLOAD_RATE", "30/hour")},
}

BANK_STATEMENT_ANALYSIS = {
    # Private directory outside MEDIA_ROOT: reports are served only through the authenticated view.
    "STORAGE_DIR": os.environ.get("BSA_STORAGE_DIR") or BASE_DIR / "private_reports",
    "RETENTION_HOURS": int(os.environ.get("BSA_RETENTION_HOURS", "72")),
    "MAX_UPLOAD_BYTES": int(os.environ.get("BSA_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
    "MAX_PAGES": int(os.environ.get("BSA_MAX_PAGES", "600")),
    "STORE_TRANSACTIONS": False,
    "ENGINE_CONFIG": {
        "report": {"organization_name": os.environ.get("BSA_ORGANIZATION_NAME", "")},
        "ocr": {"mode": os.environ.get("BSA_OCR_MODE", "auto")},
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        # Engine and app loggers record only ids, counts and status codes — never statement content.
        "bank_statement_engine": {"handlers": ["console"], "level": "INFO"},
        "bank_statement_django": {"handlers": ["console"], "level": "INFO"},
    },
}

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
