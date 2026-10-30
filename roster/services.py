from django.db import IntegrityError, transaction
from .models import Employee, Office, OutboxEvent


def apply_record(tenant_id, record):
    """Return applied, duplicate, stale, or conflict. Caller validates first."""
    for attempt in range(2):
        try:
            with transaction.atomic():
                employee = Employee.objects.select_for_update().filter(
                    tenant_id=tenant_id, external_id=record.external_id).first()
                if employee is None:
                    if record.operation == "DEACTIVATE":
                        return "stale"
                    employee = Employee(tenant_id=tenant_id, external_id=record.external_id)
                else:
                    if record.source_version < employee.source_version:
                        return "stale"
                    if record.source_version == employee.source_version:
                        return "duplicate" if record.digest() == employee.hr_hash else "conflict"
                employee.source_version = record.source_version
                employee.hr_hash = record.digest()
                if record.operation == "DEACTIVATE":
                    employee.active = False
                else:
                    office = Office.objects.get(tenant_id=tenant_id, code=record.office_code)
                    employee.name = record.name
                    employee.email = record.email
                    employee.phone = record.phone
                    employee.manager_id = record.manager_id
                    employee.office = office
                    employee.shift = record.shift
                    employee.active = True
                employee.save()
                OutboxEvent.objects.create(
                    tenant_id=tenant_id, employee=employee, source_version=record.source_version,
                    kind="EMPLOYEE_DEACTIVATED" if record.operation == "DEACTIVATE" else "EMPLOYEE_CHANGED")
                return "applied"
        except IntegrityError:
            if attempt:
                raise
    raise RuntimeError("unreachable")
