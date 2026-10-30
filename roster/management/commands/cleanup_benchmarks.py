"""Remove only synthetic tenants created by benchmark_roster."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Delete synthetic Benchmark <12 hex> tenants and their generated data."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Pass --yes to delete synthetic benchmark data")
        if connection.vendor != "postgresql":
            raise CommandError("This cleanup command requires PostgreSQL")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT id FROM roster_tenant WHERE name ~ '^Benchmark [0-9a-f]{12}$'")
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                self.stdout.write("No synthetic benchmark tenants found")
                return
            marks = ",".join(["%s"] * len(ids))
            statements = [
                f"DELETE FROM roster_processedevent WHERE event_id IN (SELECT id FROM roster_outboxevent WHERE tenant_id IN ({marks}))",
                f"DELETE FROM roster_outboxevent WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_booking WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_importerror WHERE run_id IN (SELECT id FROM roster_importrun WHERE tenant_id IN ({marks}))",
                f"DELETE FROM roster_stagedemployee WHERE run_id IN (SELECT id FROM roster_importrun WHERE tenant_id IN ({marks}))",
                f"DELETE FROM roster_importrun WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_metriccounter WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_membership WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_employee WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_office WHERE tenant_id IN ({marks})",
                f"DELETE FROM roster_tenant WHERE id IN ({marks})",
            ]
            for statement in statements:
                cursor.execute(statement, ids)
        self.stdout.write(f"Removed {len(ids)} synthetic benchmark tenants")
