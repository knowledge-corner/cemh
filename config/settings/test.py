"""Test settings — fast hashing, no real email, no static manifest."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = []

# The suite has broken twice because a test built a visit at "now + 2 hours"
# and the clock happened to be near midnight. Overridable so that case can be
# reproduced deliberately: TEST_TIME_ZONE=Asia/Bangkok runs the suite as if it
# were late evening.
TIME_ZONE = os.environ.get("TEST_TIME_ZONE", TIME_ZONE)  # noqa: F405
