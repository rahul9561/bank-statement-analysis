import tempfile
from pathlib import Path

BASE_DIR = Path(tempfile.mkdtemp(prefix="bsa-test-"))
SECRET_KEY = "test-only-not-secret"
DEBUG = False
ALLOWED_HOSTS = ["testserver"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "bank_statement_django",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "test_urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.BasicAuthentication"],
    "DEFAULT_THROTTLE_RATES": {"bank_statement_upload": "1000/hour"},
    "UNAUTHENTICATED_USER": None,
}
BANK_STATEMENT_ANALYSIS = {
    "STORAGE_DIR": BASE_DIR / "reports",
    "RETENTION_HOURS": 1,
    "MAX_UPLOAD_BYTES": 2 * 1024 * 1024,
    "ENGINE_CONFIG": {"ocr": {"mode": "off"}},
}
