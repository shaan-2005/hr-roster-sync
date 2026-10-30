import time
from django.core.management.base import BaseCommand
from roster.outbox import publish_once


class Command(BaseCommand):
    help = "Deliver pending outbox events; safe to restart."

    def handle(self, *args, **options):
        while True:
            if not publish_once():
                time.sleep(1)
