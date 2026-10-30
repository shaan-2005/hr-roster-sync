import logging
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Subquery
from django.utils import timezone
from .models import Employee, ImportError, ImportRun, OutboxEvent, StagedEmployee
from .services import apply_record
from .sources import CsvHRSource
from .validation import RecordError, safe_employee_id, validate_record

log = logging.getLogger("roster")


def stage_csv(tenant_id, stream):
    run = ImportRun.objects.create(tenant_id=tenant_id)
    if connection.vendor == "postgresql":
        from .pg_staging import stage_postgres
        stage_postgres(run, tenant_id, stream)
    else:
        batch = []
        for row_number, row in CsvHRSource(stream).records():
            run.total_rows += 1
            try:
                row["source_version"] = int(row.get("source_version", ""))
                row["operation"] = "UPSERT"
                record = validate_record(row, tenant_id)
                batch.append(StagedEmployee(run=run, external_id=record.external_id,
                                            source_version=record.source_version,
                                            data=record.as_data(), hr_hash=record.digest()))
            except (RecordError, ValueError, TypeError) as error:
                run.invalid_rows += 1
                ImportError.objects.create(run=run, row_number=row_number,
                                           employee_id=safe_employee_id(row),
                                           code=getattr(error, "code", "invalid_version"),
                                           field=getattr(error, "field", "source_version"))
            if len(batch) >= 1000:
                StagedEmployee.objects.bulk_create(batch, ignore_conflicts=True)
                batch.clear()
        if batch:
            StagedEmployee.objects.bulk_create(batch, ignore_conflicts=True)
    run.valid_rows = StagedEmployee.objects.filter(run=run).count()
    # Equal source versions with different payloads are conflicts, not duplicates.
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT s.external_id FROM roster_stagedemployee s
            JOIN roster_employee e ON e.tenant_id = %s AND e.external_id = s.external_id
            WHERE s.run_id = %s AND s.source_version = e.source_version AND s.hr_hash <> e.hr_hash
        """, [tenant_id, run.id])
        while True:
            conflicts = cursor.fetchmany(1000)
            if not conflicts:
                break
            ImportError.objects.bulk_create([
                ImportError(run=run, row_number=0, employee_id=employee_id,
                            code="version_conflict", field="source_version")
                for (employee_id,) in conflicts])
            run.invalid_rows += len(conflicts)
    run.active_count = Employee.objects.filter(tenant_id=tenant_id, active=True).count()
    staged_ids = StagedEmployee.objects.filter(run=run).values("external_id")
    run.missing_count = Employee.objects.filter(tenant_id=tenant_id, active=True).exclude(
        external_id__in=Subquery(staged_ids)).count()
    ratio = run.missing_count / max(run.active_count, 1)
    run.requires_approval = ratio >= settings.IMPORT_MISSING_THRESHOLD or bool(run.invalid_rows)
    run.status = "needs_review" if run.requires_approval else "ready"
    run.save()
    log.info("import_staged run_id=%s status=%s rows=%s missing=%s", run.id, run.status,
             run.total_rows, run.missing_count)
    return run


def apply_import(run, approved_by=None):
    if run.status not in ("ready", "needs_review"):
        raise ValueError("invalid_import_state")
    if run.status == "needs_review" and approved_by is None:
        raise ValueError("approval_required")
    run.status = "applying"
    run.approved_by = approved_by
    run.save(update_fields=["status", "approved_by"])
    try:
        if connection.vendor == "postgresql":
            run.applied_count = _apply_postgres(run)
        else:
            run.applied_count = _apply_portable(run)
        run.status = "completed"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "applied_count"])
    except Exception:
        run.status = "failed"
        run.save(update_fields=["status", "applied_count"])
        raise
    return run


def _apply_portable(run):
    count = 0
    for staged in StagedEmployee.objects.filter(run=run).order_by("id").iterator(chunk_size=1000):
        from .validation import HRRecord
        if apply_record(run.tenant_id, HRRecord(**staged.data)) == "applied":
            count += 1
    staged_ids = StagedEmployee.objects.filter(run=run).values("external_id")
    missing = Employee.objects.filter(tenant=run.tenant, active=True).exclude(
        external_id__in=Subquery(staged_ids)).values_list("id", flat=True)
    for employee_id in missing.iterator(chunk_size=1000):
        with transaction.atomic():
            employee = Employee.objects.select_for_update().get(id=employee_id)
            if employee.active:
                employee.active = False
                employee.save(update_fields=["active"])
                OutboxEvent.objects.create(tenant=run.tenant, employee=employee,
                                           kind="EMPLOYEE_DEACTIVATED", source_version=employee.source_version)
                count += 1
    return count


def _apply_postgres(run):
    """Set-based import: one transaction for changes and matching outbox rows."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("""
            WITH changed AS (
                INSERT INTO roster_employee
                    (tenant_id, external_id, source_version, name, email, phone, manager_id,
                     office_id, shift, pickup_point, special_assistance, active, hr_hash)
                SELECT %s, s.external_id, s.source_version,
                       s.data->>'name', s.data->>'email', s.data->>'phone',
                       s.data->>'manager_id', o.id, s.data->>'shift', '', '', TRUE, s.hr_hash
                FROM roster_stagedemployee s
                JOIN roster_office o ON o.tenant_id = %s AND o.code = s.data->>'office_code'
                WHERE s.run_id = %s
                ON CONFLICT (tenant_id, external_id) DO UPDATE SET
                    source_version = EXCLUDED.source_version, name = EXCLUDED.name,
                    email = EXCLUDED.email, phone = EXCLUDED.phone,
                    manager_id = EXCLUDED.manager_id, office_id = EXCLUDED.office_id,
                    shift = EXCLUDED.shift, active = TRUE, hr_hash = EXCLUDED.hr_hash
                WHERE roster_employee.source_version < EXCLUDED.source_version
                RETURNING id, tenant_id, source_version
            ), emitted AS (
                INSERT INTO roster_outboxevent
                    (id, tenant_id, employee_id, kind, source_version, created_at,
                     published_at, attempts, next_attempt_at, last_error_code)
                SELECT gen_random_uuid(), tenant_id, id, 'EMPLOYEE_CHANGED', source_version,
                       NOW(), NULL, 0, NULL, '' FROM changed RETURNING id
            ) SELECT COUNT(*) FROM emitted
        """, [run.tenant_id, run.tenant_id, run.id])
        changed_count = cursor.fetchone()[0]
        deactivated_count = 0
        if run.missing_count:
            cursor.execute("""
                WITH deactivated AS (
                    UPDATE roster_employee e SET active = FALSE
                    WHERE e.tenant_id = %s AND e.active = TRUE AND NOT EXISTS (
                        SELECT 1 FROM roster_stagedemployee s
                        WHERE s.run_id = %s AND s.external_id = e.external_id)
                    RETURNING e.id, e.tenant_id, e.source_version
                ), emitted AS (
                    INSERT INTO roster_outboxevent
                        (id, tenant_id, employee_id, kind, source_version, created_at,
                         published_at, attempts, next_attempt_at, last_error_code)
                    SELECT gen_random_uuid(), tenant_id, id, 'EMPLOYEE_DEACTIVATED',
                           source_version, NOW(), NULL, 0, NULL, '' FROM deactivated RETURNING id
                ) SELECT COUNT(*) FROM emitted
            """, [run.tenant_id, run.id])
            deactivated_count = cursor.fetchone()[0]
    return changed_count + deactivated_count
