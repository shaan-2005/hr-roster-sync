import hashlib
import json
import re
from dataclasses import dataclass
from django.conf import settings
from django.core.cache import cache
from .models import Office


class RecordError(Exception):
    def __init__(self, code, field=""):
        self.code, self.field = code, field
        super().__init__(code)


@dataclass(frozen=True)
class HRRecord:
    external_id: str
    source_version: int
    operation: str
    name: str = ""
    email: str = ""
    phone: str = ""
    manager_id: str = ""
    office_code: str = ""
    shift: str = ""

    def digest(self):
        values = {k: v for k, v in self.__dict__.items() if k != "source_version"}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def as_data(self):
        return dict(self.__dict__)


def validate_record(raw, tenant_id, known_offices=None):
    if not isinstance(raw, dict):
        raise RecordError("invalid_record")
    employee_id = raw.get("employee_id")
    if not isinstance(employee_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", employee_id):
        raise RecordError("invalid_employee_id", "employee_id")
    version = raw.get("source_version")
    if type(version) is not int or version < 0:
        raise RecordError("invalid_version", "source_version")
    operation = raw.get("operation", "UPSERT")
    if operation not in ("UPSERT", "DEACTIVATE"):
        raise RecordError("invalid_operation", "operation")
    if operation == "DEACTIVATE":
        return HRRecord(employee_id, version, operation)
    limits = {"name": 160, "email": 254, "phone": 24, "manager_id": 80, "office_code": 40, "shift": 80}
    values = {}
    for field, maximum in limits.items():
        value = raw.get(field, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise RecordError("invalid_field", field)
        values[field] = value.strip()
    if not values["name"]:
        raise RecordError("required_field", "name")
    if values["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["email"]):
        raise RecordError("invalid_email", "email")
    if values["phone"] and not re.fullmatch(r"\+?[0-9]{7,15}", values["phone"]):
        raise RecordError("invalid_phone", "phone")
    if not values["office_code"]:
        raise RecordError("required_field", "office_code")
    if known_offices is not None:
        exists = values["office_code"] in known_offices
    else:
        key = f"office:{tenant_id}:{values['office_code']}"
        exists = cache.get(key)
        if exists is None:
            exists = Office.objects.filter(tenant_id=tenant_id, code=values["office_code"]).exists()
            cache.set(key, exists, timeout=settings.OFFICE_CACHE_SECONDS)
    if not exists:
        raise RecordError("unknown_office", "office_code")
    return HRRecord(employee_id, version, operation, **values)


def safe_employee_id(raw):
    value = raw.get("employee_id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else ""
