"""
Move every pending doctor straight to onboarded, skipping the emailed
invitation link.

A doctor sits in "Pending" (DoctorProfile.activated_at is None) until they
follow their invitation email and choose a password themselves — see
accounts.views.activate_doctor. That is the right flow for a real clinic,
but it is exactly the step testing wants to skip: nobody testing the doctor
portal has a working mailbox for a fake doctor account.

    python manage.py onboard_pending_doctors

Refuses to run when DEBUG is off unless --force is given, matching
seed_demo's guard — this bypasses the real onboarding flow and must never be
pointed at the clinic's live database by accident.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import DoctorInvitation, DoctorProfile

#: Same password seed_demo uses, so every test account in the database
#: answers to one password.
TEST_PASSWORD = "clinicdemo2026"


class Command(BaseCommand):
    help = "Activate every pending doctor with a known test password, skipping the invitation email."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password", default=TEST_PASSWORD,
            help=f"Password to set on every pending doctor (default: {TEST_PASSWORD}).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Allow running when DEBUG is off. Never use on the clinic's live database.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to onboard doctors with DEBUG off. Re-run with --force "
                "only if you are certain this is not the clinic's live database."
            )

        password = options["password"]
        pending = list(
            DoctorProfile.objects.filter(activated_at__isnull=True).select_related("user")
        )

        if not pending:
            self.stdout.write("No pending doctors — nothing to do.")
            return

        with transaction.atomic():
            for profile in pending:
                doctor = profile.user
                doctor.set_password(password)
                doctor.is_active = True
                doctor.save(update_fields=["password", "is_active"])

                profile.activated_at = timezone.now()
                profile.save(update_fields=["activated_at"])

                # Consumed rather than left open: the doctor is onboarded now,
                # and a stale "open" invitation link should not still work.
                DoctorInvitation.objects.filter(
                    user=doctor, used_at__isnull=True, revoked_at__isnull=True,
                ).update(used_at=timezone.now())

        self.stdout.write(self.style.SUCCESS(f"\nOnboarded {len(pending)} doctor(s)."))
        self.stdout.write(f"Password for each: {password}\n")
        for profile in pending:
            self.stdout.write(f"    {profile.user.username:20s} Dr. {profile.user.display_name}")
