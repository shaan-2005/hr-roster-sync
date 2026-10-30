from django.core.management.base import BaseCommand, CommandError
from roster.imports import apply_import
from roster.models import ImportRun


class Command(BaseCommand):
    help = "Resume a failed/interrupted import after confirming no apply process is still running."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=int)

    def handle(self, *args, **options):
        run = ImportRun.objects.filter(id=options["run_id"]).first()
        if not run or run.status not in ("failed", "applying"):
            raise CommandError("Import is not failed or interrupted")
        if run.requires_approval and not run.approved_by_id:
            raise CommandError("Operator approval is required before recovery")
        run.status = "needs_review" if run.requires_approval else "ready"
        run.save(update_fields=["status"])
        apply_import(run, approved_by=run.approved_by)
        self.stdout.write(f"Import {run.id} completed; applied {run.applied_count} changes")
