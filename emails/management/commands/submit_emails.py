from django.core.management.base import BaseCommand, CommandError

from emails.services import enqueue_email


class Command(BaseCommand):
    help = "Submit a batch to the local fake email provider. No real email is sent."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=250)
        parser.add_argument("--fail-first", type=int, default=1)

    def handle(self, *args, **options):
        if not 1 <= options["count"] <= 2000 or not 0 <= options["fail_first"] <= 10:
            raise CommandError("Use count 1..2000 and fail-first 0..10")
        for i in range(options["count"]):
            enqueue_email(
                recipient=f"customer-{i}@example.com",
                subject="Order confirmation",
                body="Your order was received.",
                fail_first=options["fail_first"] if i == 0 else 0,
            )
        self.stdout.write(self.style.SUCCESS(f"Submitted {options['count']} mock email jobs."))
