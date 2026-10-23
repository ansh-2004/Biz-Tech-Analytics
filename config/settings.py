import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# This project runs locally. Set a stable secret before using it outside the demo.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "web", "testserver"]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "silk",
    "orders",
    "emails",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "silk.middleware.SilkyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "assessment"),
        "USER": os.environ.get("POSTGRES_USER", "assessment"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
if os.environ.get("USE_SQLITE") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "/admin/login/"
SILKY_AUTHENTICATION = True
SILKY_AUTHORISATION = True
SILKY_META = False
SILKY_MAX_REQUEST_BODY_SIZE = 0
SILKY_MAX_RESPONSE_BODY_SIZE = 0
SILKY_IGNORE_PATHS = ["/health/"]

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
RATE_REDIS_URL = os.environ.get("RATE_REDIS_URL", "redis://redis:6379/2")
RATE_KEY = os.environ.get("RATE_KEY", "assessment:email-provider:window")
EMAIL_RATE_LIMIT = 200
EMAIL_RATE_WINDOW_MS = 60_000
EMAIL_MAX_FAILURES = 4
EMAIL_RETRY_BASE_SECONDS = 2
JOB_REDIS_LOCK_SECONDS = 90
JOB_REDISPATCH_SECONDS = 120

CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_QUEUE", "emails")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_ETA_TASK_LIMIT = 100
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True
CELERY_TASK_SOFT_TIME_LIMIT = 30
CELERY_TASK_TIME_LIMIT = 45
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 300}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {"visibility_timeout": 300}
CELERY_VISIBILITY_TIMEOUT = 300
CELERY_RESULT_EXPIRES = 3600
CELERY_BEAT_SCHEDULE = {
    "recover-unfinished-emails": {
        "task": "emails.tasks.recover_pending_jobs",
        "schedule": 15.0,
    },
}
