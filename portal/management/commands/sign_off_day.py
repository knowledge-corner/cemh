"""
Close a clinic day and send its report (KAN-48, KAN-49).

Exists so the clinic can schedule it — Task Scheduler on Windows, cron on a
server — and have the previous day closed and reported without anybody
remembering. There is no scheduler in this project, deliberately: one more
moving part on a clinic laptop is one more thing that can be quietly not
running, and its absence would be discovered only when a month of reports had
never been sent.

Reception can also sign the day off from the board, and the first person to
open the board after five in the morning triggers this same code. Whichever
happens first wins; the second is a no-op, because a day already signed off is
not signed off again.
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from appointments import signoff


class Command(BaseCommand):
    help = "Close a clinic day, email its report and record the sign-off."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="The clinic day to close, YYYY-MM-DD. Defaults to yesterday.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Sweep and record without sending. For a dry run on a copy.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even though sign-off is switched off for this clinic.",
        )

    def handle(self, *args, **options):
        if not signoff.is_enabled() and not options["force"]:
            # A schedule left in place after the feature was switched off would
            # otherwise keep emailing patient names and amounts to an address
            # nobody is expecting them at any more. Refused rather than run
            # quietly, and said plainly enough to act on.
            raise CommandError(
                "Day sign-off is switched off for this clinic "
                "(DAY_SIGN_OFF_ENABLED). Nothing was swept and no report was "
                "sent. Turn it on, or pass --force for a one-off run."
            )

        if options["date"]:
            try:
                day = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD.")
        else:
            day = timezone.localdate() - timedelta(days=1)

        record, created = signoff.sign_off(
            day, by_user=None, send=not options["no_email"]
        )

        if not created:
            self.stdout.write(
                f"{day:%d %b %Y} was already signed off at "
                f"{timezone.localtime(record.sent_at):%d %b %H:%M}. Nothing sent."
            )
            return

        self.stdout.write(
            f"{day:%d %b %Y}: {record.billed_count} billed, "
            f"{record.cancelled_count} cancelled, {record.no_show_count} no-show."
        )
        if record.delivery_error:
            # Written to stderr and not raised: the day IS closed, and a
            # non-zero exit would have a scheduler retrying a sign-off that
            # already happened.
            self.stderr.write(f"The report was not sent — {record.delivery_error}")
        else:
            self.stdout.write(f"Report sent to {record.sent_to}.")
