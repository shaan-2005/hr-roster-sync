import uuid
from django.conf import settings
from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=120, unique=True)


class Office(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_tenant_office")]


class Membership(models.Model):
    EMPLOYEE, ADMIN, OPERATOR, HR = "employee", "admin", "operator", "hr"
    ROLES = [(x, x) for x in (EMPLOYEE, ADMIN, OPERATOR, HR)]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membership")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    role = models.CharField(max_length=12, choices=ROLES)
    employee_id = models.CharField(max_length=80, blank=True)


class Employee(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=80)
    source_version = models.BigIntegerField(default=-1)
    name = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=24, blank=True)
    manager_id = models.CharField(max_length=80, blank=True)
    office = models.ForeignKey(Office, null=True, on_delete=models.PROTECT)
    shift = models.CharField(max_length=80, blank=True)
    pickup_point = models.CharField(max_length=200, blank=True)
    special_assistance = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    hr_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "external_id"], name="unique_tenant_employee")]
        indexes = [models.Index(fields=["tenant", "active", "external_id"])]


class Booking(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    status = models.CharField(max_length=12, default="booked")
    seat_released = models.BooleanField(default=False)


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    kind = models.CharField(max_length=32)
    source_version = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=40, blank=True)

    class Meta:
        indexes = [models.Index(fields=["published_at", "next_attempt_at", "created_at"])]


class ProcessedEvent(models.Model):
    event = models.OneToOneField(OutboxEvent, primary_key=True, on_delete=models.CASCADE)
    processed_at = models.DateTimeField(auto_now_add=True)


class ImportRun(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default="loading")
    requires_approval = models.BooleanField(default=False)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    active_count = models.PositiveIntegerField(default=0)
    applied_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)


class StagedEmployee(models.Model):
    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=80)
    source_version = models.BigIntegerField()
    data = models.JSONField()
    hr_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "external_id"], name="unique_import_employee")]


class ImportError(models.Model):
    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE)
    row_number = models.PositiveIntegerField()
    employee_id = models.CharField(max_length=80, blank=True)
    code = models.CharField(max_length=40)
    field = models.CharField(max_length=40, blank=True)


class MetricCounter(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name = models.CharField(max_length=40)
    value = models.BigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="unique_tenant_metric")]
