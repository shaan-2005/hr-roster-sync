"""PostgreSQL bulk staging for a validated nightly roster."""

import csv
import json
import tempfile

from django.db import connection, transaction

from .models import ImportError, Office
from .sources import CsvHRSource
from .validation import RecordError, safe_employee_id, validate_record


def stage_postgres(run, tenant_id, stream):
    office_codes = set(Office.objects.filter(tenant_id=tenant_id).values_list("code", flat=True))
    errors = tempfile.SpooledTemporaryFile(max_size=1048576, mode="w+", encoding="utf-8", newline="")
    error_writer = csv.writer(errors)
    with transaction.atomic():
        with connection.connection.cursor() as cursor:
            cursor.execute("""
                CREATE TEMP TABLE tmp_roster_stage (
                    row_number bigint, external_id text, source_version bigint,
                    data jsonb, hr_hash text
                ) ON COMMIT DROP
            """)
            with cursor.copy("COPY tmp_roster_stage (row_number, external_id, source_version, data, hr_hash) FROM STDIN") as writer:
                for row_number, row in CsvHRSource(stream).records():
                    run.total_rows += 1
                    try:
                        row["source_version"] = int(row.get("source_version", ""))
                        row["operation"] = "UPSERT"
                        record = validate_record(row, tenant_id, known_offices=office_codes)
                        writer.write_row((row_number, record.external_id, record.source_version,
                                          json.dumps(record.as_data()), record.digest()))
                    except (RecordError, ValueError, TypeError) as error:
                        run.invalid_rows += 1
                        error_writer.writerow([row_number, safe_employee_id(row),
                                               getattr(error, "code", "invalid_version"),
                                               getattr(error, "field", "source_version")])
            cursor.execute("""
                INSERT INTO roster_stagedemployee (run_id, external_id, source_version, data, hr_hash)
                SELECT %s, external_id, source_version, data, hr_hash FROM (
                    SELECT DISTINCT ON (external_id) external_id, source_version, data, hr_hash
                    FROM tmp_roster_stage
                    ORDER BY external_id, source_version DESC, row_number DESC
                ) latest
            """, [run.id])
            cursor.execute("""
                INSERT INTO roster_importerror (run_id, row_number, employee_id, code, field)
                SELECT %s, row_number, external_id, 'duplicate_employee', 'employee_id'
                FROM (
                    SELECT row_number, external_id,
                           ROW_NUMBER() OVER (PARTITION BY external_id
                                              ORDER BY source_version DESC, row_number DESC) AS rank
                    FROM tmp_roster_stage
                ) ranked WHERE rank > 1
            """, [run.id])
            run.invalid_rows += cursor.rowcount
        errors.seek(0)
        batch = []
        for row_number, employee_id, code, field in csv.reader(errors):
            batch.append(ImportError(run=run, row_number=int(row_number), employee_id=employee_id,
                                     code=code, field=field))
            if len(batch) >= 1000:
                ImportError.objects.bulk_create(batch)
                batch.clear()
        if batch:
            ImportError.objects.bulk_create(batch)
    errors.close()
