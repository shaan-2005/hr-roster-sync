import csv
import io
import json
import threading
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from .imports import apply_import, stage_csv
from .models import Booking, Employee, Membership, Office, OutboxEvent, ProcessedEvent, Tenant
from .outbox import process_event, publish_once
from .services import apply_record
from .validation import RecordError, validate_record


def record(employee_id="e1", version=1, office="BLR", **changes):
    data = {"employee_id": employee_id, "source_version": version, "name": "Test Person",
            "email": "test@example.test", "phone": "+919000000000", "office_code": office,
            "manager_id": "m1", "shift": "day"}
    data.update(changes)
    return data


class BaseRosterTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="T1")
        self.other = Tenant.objects.create(name="T2")
        Office.objects.create(tenant=self.tenant, code="BLR", name="Bengaluru")
        Office.objects.create(tenant=self.other, code="BLR", name="Other Bengaluru")
        self.user = get_user_model().objects.create_user("operator", password="test")
        Membership.objects.create(user=self.user, tenant=self.tenant, role="operator")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Token " + Token.objects.create(user=self.user).key)


class RulesTests(BaseRosterTest):
    def test_newer_duplicate_stale_and_conflicting_versions(self):
        first = validate_record(record(version=7, shift="late"), self.tenant.id)
        self.assertEqual(apply_record(self.tenant.id, first), "applied")
        self.assertEqual(apply_record(self.tenant.id, first), "duplicate")
        self.assertEqual(apply_record(self.tenant.id, validate_record(record(version=6), self.tenant.id)), "stale")
        self.assertEqual(apply_record(self.tenant.id, validate_record(record(version=7, shift="day"), self.tenant.id)), "conflict")
        self.assertEqual(Employee.objects.get(tenant=self.tenant, external_id="e1").shift, "late")
        self.assertEqual(OutboxEvent.objects.count(), 1)

    def test_hr_does_not_overwrite_transport_field(self):
        apply_record(self.tenant.id, validate_record(record(), self.tenant.id))
        response = self.client.patch("/api/employees/e1/transport/",
                                     {"pickup_point": "Gate 2", "special_assistance": "Ramp"}, format="json")
        self.assertEqual(response.status_code, 200)
        apply_record(self.tenant.id, validate_record(record(version=2, shift="night"), self.tenant.id))
        employee = Employee.objects.get(tenant=self.tenant, external_id="e1")
        self.assertEqual((employee.pickup_point, employee.special_assistance), ("Gate 2", "Ramp"))

    def test_bad_record_does_not_fail_batch(self):
        response = self.client.post("/api/hr/events/", {"records": [record(phone="bad"), record("e2")]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([x["result"] for x in response.data["results"]], ["invalid", "applied"])
        self.assertEqual(Employee.objects.count(), 1)

    def test_cross_tenant_read_and_employee_scope(self):
        apply_record(self.other.id, validate_record(record(), self.other.id))
        self.assertEqual(self.client.get("/api/employees/e1/").status_code, 404)
        self.assertEqual(self.client.patch("/api/employees/e1/pickup/", {"pickup_point": "X"}).status_code, 404)
        self.user.membership.role = "employee"
        self.user.membership.employee_id = "e1"
        self.user.membership.save()
        self.assertEqual(self.client.get("/api/employees/e2/").status_code, 403)

    def test_unknown_office_and_private_error(self):
        with self.assertRaises(RecordError):
            validate_record(record(office="NOPE"), self.tenant.id)
        response = self.client.post("/api/hr/events/", {"records": [record(phone="secret-bad")]}, format="json")
        self.assertNotIn("secret-bad", json.dumps(response.data))

    def test_replay_feed_no_effect(self):
        body = {"records": [record("e1"), record("e2")]}
        self.client.post("/api/hr/events/", body, format="json")
        again = self.client.post("/api/hr/events/", body, format="json")
        self.assertEqual([x["result"] for x in again.data["results"]], ["duplicate", "duplicate"])
        self.assertEqual(OutboxEvent.objects.count(), 2)


class ImportTests(BaseRosterTest):
    def csv_stream(self, rows):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(record().keys()))
        writer.writeheader()
        writer.writerows(rows)
        stream.seek(0)
        return stream

    def test_safety_brake_prevents_mass_deactivation(self):
        for i in range(10):
            apply_record(self.tenant.id, validate_record(record(f"e{i}"), self.tenant.id))
        run = stage_csv(self.tenant.id, self.csv_stream([record("e0")]))
        self.assertEqual(run.status, "needs_review")
        self.assertEqual(run.missing_count, 9)
        self.assertEqual(Employee.objects.filter(tenant=self.tenant, active=True).count(), 10)

    def test_complete_import_deactivates_absent_employee(self):
        apply_record(self.tenant.id, validate_record(record("e1"), self.tenant.id))
        apply_record(self.tenant.id, validate_record(record("e2"), self.tenant.id))
        run = stage_csv(self.tenant.id, self.csv_stream([record("e1", version=2)]))
        self.assertEqual(run.status, "needs_review")
        apply_import(run, approved_by=self.user)
        self.assertFalse(Employee.objects.get(tenant=self.tenant, external_id="e2").active)

    def test_invalid_record_is_reported_without_pii(self):
        run = stage_csv(self.tenant.id, self.csv_stream([record("e1", phone="private-invalid"), record("e2")]))
        self.assertEqual(run.invalid_rows, 1)
        self.assertEqual(run.valid_rows, 1)
        detail = self.client.get(f"/api/imports/{run.id}/")
        self.assertNotIn("private-invalid", json.dumps(detail.data))

    def test_equal_version_different_snapshot_requires_review(self):
        apply_record(self.tenant.id, validate_record(record("e1"), self.tenant.id))
        run = stage_csv(self.tenant.id, self.csv_stream([record("e1", shift="night")]))
        self.assertEqual(run.status, "needs_review")
        self.assertEqual(run.invalid_rows, 1)
        self.assertEqual(run.importerror_set.first().code, "version_conflict")


class OutboxTests(BaseRosterTest):
    def test_crash_after_commit_then_recovery_and_idempotent_delivery(self):
        apply_record(self.tenant.id, validate_record(record(), self.tenant.id))
        employee = Employee.objects.get(tenant=self.tenant, external_id="e1")
        Booking.objects.create(tenant=self.tenant, employee=employee, starts_at=timezone.now()+timedelta(days=1))
        apply_record(self.tenant.id, validate_record({"employee_id": "e1", "source_version": 2,
                                                     "operation": "DEACTIVATE"}, self.tenant.id))
        event = OutboxEvent.objects.get(kind="EMPLOYEE_DEACTIVATED")
        self.assertIsNone(event.published_at)  # Simulated crash before publishing.
        self.assertEqual(process_event(event), "processed")
        self.assertEqual(process_event(event), "duplicate")
        booking = Booking.objects.get(employee=employee)
        self.assertEqual((booking.status, booking.seat_released), ("cancelled", True))
        self.assertEqual(ProcessedEvent.objects.filter(event=event).count(), 1)

    @override_settings(OUTBOX_INTERNAL_URL="http://invalid.local/")
    def test_delivery_timeout_is_retried(self):
        apply_record(self.tenant.id, validate_record(record(), self.tenant.id))
        with patch("roster.outbox.urllib.request.urlopen", side_effect=TimeoutError):
            self.assertTrue(publish_once())
        event = OutboxEvent.objects.get()
        self.assertEqual(event.attempts, 1)
        self.assertIsNone(event.published_at)
        self.assertIsNotNone(event.next_attempt_at)


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("row-lock concurrency test requires PostgreSQL")
        self.tenant = Tenant.objects.create(name="Concurrent")
        Office.objects.create(tenant=self.tenant, code="BLR", name="Bengaluru")
        apply_record(self.tenant.id, validate_record(record(version=1), self.tenant.id))

    def test_two_versions_race_and_newest_wins(self):
        barrier = threading.Barrier(2)
        failures = []

        def update(version):
            close_old_connections()
            try:
                candidate = validate_record(record(version=version, shift=f"s{version}"), self.tenant.id)
                barrier.wait(timeout=5)
                apply_record(self.tenant.id, candidate)
            except Exception as error:
                failures.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=update, args=(version,)) for version in (2, 3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(failures)
        employee = Employee.objects.get(tenant=self.tenant, external_id="e1")
        self.assertEqual((employee.source_version, employee.shift), (3, "s3"))

class SubmissionBoundaryTests(BaseRosterTest):
    def test_roles_separate_hr_and_transport_writes(self):
        self.user.membership.role = "admin"
        self.user.membership.save(update_fields=["role"])
        response = self.client.post("/api/hr/events/", {"records": [record()]}, format="json")
        self.assertEqual(response.status_code, 403)
        self.user.membership.role = "hr"
        self.user.membership.save(update_fields=["role"])
        response = self.client.post("/api/hr/events/", {"records": [record()]}, format="json")
        self.assertEqual(response.data["results"][0]["result"], "applied")
        response = self.client.patch("/api/employees/e1/transport/", {"pickup_point": "Gate 2"}, format="json")
        self.assertEqual(response.status_code, 403)

    @override_settings(OUTBOX_INTERNAL_TOKEN="test-internal-secret")
    def test_internal_event_authentication_and_repeat_delivery(self):
        apply_record(self.tenant.id, validate_record(record(), self.tenant.id))
        employee = Employee.objects.get(tenant=self.tenant, external_id="e1")
        Booking.objects.create(tenant=self.tenant, employee=employee,
                               starts_at=timezone.now() + timedelta(days=1))
        apply_record(self.tenant.id, validate_record(
            {"employee_id": "e1", "source_version": 2, "operation": "DEACTIVATE"}, self.tenant.id))
        event = OutboxEvent.objects.get(kind="EMPLOYEE_DEACTIVATED")
        payload = {"event_id": str(event.id), "tenant_id": self.tenant.id,
                   "employee_id": "e1", "kind": event.kind, "source_version": 2}
        self.assertEqual(self.client.post("/api/internal/events/", payload, format="json").status_code, 403)
        first = self.client.post("/api/internal/events/", payload, format="json",
                                 HTTP_X_INTERNAL_TOKEN="test-internal-secret")
        again = self.client.post("/api/internal/events/", payload, format="json",
                                 HTTP_X_INTERNAL_TOKEN="test-internal-secret")
        self.assertEqual((first.json()["result"], again.json()["result"]), ("processed", "duplicate"))
        booking = Booking.objects.get(employee=employee)
        self.assertEqual((booking.status, booking.seat_released), ("cancelled", True))

    def test_postgres_duplicate_snapshot_row_is_reported(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL bulk staging behavior")
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(record().keys()))
        writer.writeheader()
        writer.writerows([record("e1", version=1), record("e1", version=2)])
        stream.seek(0)
        run = stage_csv(self.tenant.id, stream)
        self.assertEqual((run.valid_rows, run.invalid_rows, run.status), (1, 1, "needs_review"))
        self.assertEqual(run.importerror_set.first().code, "duplicate_employee")
        self.assertEqual(run.stagedemployee_set.get().source_version, 2)
