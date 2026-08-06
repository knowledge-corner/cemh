"""Developer laptop settings. Never used on the clinic's server."""

from .base import *  # noqa: F401,F403

DEBUG = True

# Any host, so the clinic's own phones and tablets can reach this over the
# surgery Wi-Fi at http://<this machine's address>:8000.
#
# Not a list of addresses, because the one that matters cannot be written down
# here: the Host header a phone sends is the *laptop's* address on the local
# network, which changes with the router's lease, and which this process —
# inside a container, seeing only the Docker bridge — has no way to discover.
# A guessed list is a list that is wrong the first morning the router hands out
# a different number.
#
# Safe here and only here. prod.py refuses to start without an explicit
# ALLOWED_HOSTS, so nothing on the clinic's server inherits this.
#
# It does mean anybody already on that Wi-Fi can reach the system, and DEBUG is
# on, so a page that errors shows them a traceback. Fine on the surgery's own
# network; not something to do on café or hotel Wi-Fi.
ALLOWED_HOSTS = ["*"]

INTERNAL_IPS = ["127.0.0.1"]

# Manifest storage requires a collectstatic run; plain storage is friendlier in dev.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Long enough not to interrupt development; production keeps the 30-minute rule.
SESSION_COOKIE_AGE = 8 * 60 * 60
