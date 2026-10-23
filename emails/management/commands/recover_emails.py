from django.core.management.base import BaseCommand

from emails.services import recover_jobs


class Command(BaseCommand):
    help = "Republish due unfinished jobs (also run automatically by Celery Beat)."

    def handle(self, *args, **options):
        self.stdout.write(f"Republished {recover_jobs()} jobs")
