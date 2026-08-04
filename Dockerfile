# Two stages from one file.
#
#   docker compose up            → the `dev` stage: runserver, pytest available
#   docker compose -f docker-compose.prod.yml up  → the `prod` stage: gunicorn
#
# They share every layer up to the requirements split, so building both is cheap.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/

# The boot script is baked into the image rather than read from the bind mount,
# with any carriage returns stripped on the way in.
#
# Both halves matter. A checkout on Windows hands the container a script with
# CRLF endings, and `set -e\r` is not `set -e`: the shell refuses the first
# line, the entrypoint dies, and the container restarts forever while
# `docker compose` reports "Started". .gitattributes now prevents that at
# source, but a repository can be unzipped, copied off a USB stick or saved by
# an editor that knows better, and none of those consult .gitattributes.
# Whether the clinic system starts in the morning should not rest on it.
#
# Application code stays bind mounted and live. This is the one file that does
# not, so changing it needs `docker compose up -d --build`.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh


# ── Development ───────────────────────────────────────────────────────────────
#
# Includes pytest, so `docker compose exec web pytest` works. The source is bind
# mounted by docker-compose.yml rather than copied, so edits are live and no
# rebuild is needed to run a changed test.

FROM base AS dev

RUN pip install -r requirements/dev.txt

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# ── Production ────────────────────────────────────────────────────────────────

FROM base AS prod

RUN pip install -r requirements/prod.txt

COPY . .

# Collected at build time so the container starts without touching the database.
# A dummy SECRET_KEY is enough for collectstatic and never reaches runtime.
#
# This step is strict: CompressedManifestStaticFilesStorage rewrites URL
# references inside CSS and JS and refuses to resolve a file that is missing, so
# a dangling reference fails the build here. tests/test_static_build.py runs the
# same command, so that failure surfaces in the suite rather than in a deploy.
RUN SECRET_KEY=build-time-only \
    ALLOWED_HOSTS=localhost \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    python manage.py collectstatic --no-input

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
