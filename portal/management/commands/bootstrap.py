"""
Get a fresh install ready to use, without anybody typing a command.

Run automatically every time the container starts, so the person running the
clinic never has to open a terminal. Everything here is therefore **safe to run
repeatedly** — the second start must not undo the first day's work.

Two jobs:

  1. Make sure an administrator account exists, so there is a way in.
  2. Put the demo data in, but only into a genuinely empty database.

The second guard is the important one. ``seed_demo`` deletes what it finds
before it writes, which is right for a developer resetting their laptop and
catastrophic on a Tuesday morning at the clinic.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from patients.models import Patient


class Command(BaseCommand):
    help = "Create the first administrator and, on an empty database, demo data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-demo", action="store_true",
            help="Skip the demo data even on an empty database.",
        )

    def handle(self, *args, **options):
        self._ensure_admin()

        if options["no_demo"] or os.environ.get("SEED_DEMO", "1") != "1":
            self.stdout.write("Demo data skipped.")
            return

        if Patient.objects.exists():
            # Somebody's real patients are in here. seed_demo would delete them.
            self.stdout.write(
                f"{Patient.objects.count()} patients already registered — "
                f"leaving the database alone."
            )
            return

        self.stdout.write("Empty database: adding demo patients to look around with.")
        call_command("seed_demo", force=True)

    # ── The way in ────────────────────────────────────────────────────────────

    def _ensure_admin(self):
        """
        Create the administrator account if there is not one already.

        Never changes an existing account's password: someone who has already
        set their own password would otherwise find it silently reset to the
        default on the next restart.
        """
        User = get_user_model()

        username = os.environ.get("ADMIN_USERNAME", "admin")
        password = os.environ.get("ADMIN_PASSWORD", "clinicadmin2026")
        email = os.environ.get("ADMIN_EMAIL", "admin@clinic.local")

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Administrator account already exists.")
            return

        User.objects.create_superuser(
            username=username, email=email, password=password,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Created the administrator account '{username}'."
        ))
        if password == "clinicadmin2026":
            self.stdout.write(self.style.WARNING(
                "It is using the default password. Change it before the clinic "
                "goes live — Users, in the admin."
            ))
