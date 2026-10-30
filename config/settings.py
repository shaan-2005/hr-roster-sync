import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(32)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,app").split(",")
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages",
    "django.contrib.admin", "rest_framework", "rest_framework.authtoken", "roster",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [],
              "APP_DIRS": True, "OPTIONS": {"context_processors": [
                  "django.template.context_processors.debug",
                  "django.template.context_processors.request",
                  "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql",
                             "NAME": os.environ.get("POSTGRES_DB", "roster"),
                             "USER": os.environ.get("POSTGRES_USER", "roster"),
                             "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
                             "HOST": os.environ["POSTGRES_HOST"],
                             "PORT": os.environ.get("POSTGRES_PORT", "5432"),
                             "CONN_MAX_AGE": 60}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
REST_FRAMEWORK = {"DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
                  "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
                  "EXCEPTION_HANDLER": "roster.errors.safe_exception_handler"}
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "formatters": {"json": {"()": "roster.logging.JsonFormatter"}},
           "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
           "loggers": {"roster": {"handlers": ["console"], "level": "INFO", "propagate": False}}}
OUTBOX_INTERNAL_URL = os.environ.get("OUTBOX_INTERNAL_URL", "http://app:8000/api/internal/events/")
OUTBOX_INTERNAL_TOKEN = os.environ.get("OUTBOX_INTERNAL_TOKEN", "")
IMPORT_MISSING_THRESHOLD = float(os.environ.get("IMPORT_MISSING_THRESHOLD", "0.30"))
OFFICE_CACHE_SECONDS = int(os.environ.get("OFFICE_CACHE_SECONDS", "30"))
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                      "LOCATION": "roster-office-validation", "TIMEOUT": OFFICE_CACHE_SECONDS,
                      "OPTIONS": {"MAX_ENTRIES": 10000, "CULL_FREQUENCY": 3}}}
