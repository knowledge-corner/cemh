"""Developer laptop settings. Never used on the clinic's server."""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]

INTERNAL_IPS = ["127.0.0.1"]

# Manifest storage requires a collectstatic run; plain storage is friendlier in dev.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Long enough not to interrupt development; production keeps the 30-minute rule.
SESSION_COOKIE_AGE = 8 * 60 * 60
