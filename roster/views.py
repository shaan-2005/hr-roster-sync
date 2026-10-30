import io
import csv
import json
import logging
from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from .imports import apply_import, stage_csv
from .models import Employee, ImportError, ImportRun, MetricCounter, OutboxEvent
from .outbox import process_event
from .services import apply_record
from .sources import ApiHRSource
from .validation import RecordError, safe_employee_id, validate_record

log = logging.getLogger("roster")


def increment_metric(tenant_id, name, amount=1):
    if not amount:
        return
    counter, _ = MetricCounter.objects.get_or_create(tenant_id=tenant_id, name=name)
    MetricCounter.objects.filter(pk=counter.pk).update(value=F("value") + amount)


def membership(request):
    return getattr(request.user, "membership", None)


def deny_unless(request, *roles):
    member = membership(request)
    return member if member and member.role in roles else None


class BatchView(APIView):
    def post(self, request):
        member = deny_unless(request, "hr", "operator")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        records = request.data.get("records")
        if not isinstance(records, list) or len(records) > 1000:
            return Response({"code": "invalid_batch"}, status=400)
        results = []
        counts = {"applied": 0, "duplicate": 0, "stale": 0, "conflict": 0, "invalid": 0}
        for index, raw in ApiHRSource(records).records():
            employee_id = safe_employee_id(raw)
            try:
                record = validate_record(raw, member.tenant_id)
                result = apply_record(member.tenant_id, record)
                counts[result] += 1
                results.append({"index": index, "employee_id": employee_id, "result": result})
            except RecordError as error:
                counts["invalid"] += 1
                results.append({"index": index, "employee_id": employee_id,
                                "result": "invalid", "code": error.code, "field": error.field})
        for name, count in counts.items():
            increment_metric(member.tenant_id, name, count)
        increment_metric(member.tenant_id, "records", len(records))
        return Response({"results": results})


class EmployeeView(APIView):
    def get(self, request, employee_id):
        member = membership(request)
        if not member or (member.role == "employee" and member.employee_id != employee_id):
            return Response({"code": "forbidden"}, status=403)
        employee = Employee.objects.filter(tenant_id=member.tenant_id, external_id=employee_id).first()
        if not employee:
            return Response({"code": "not_found"}, status=404)
        data = {"employee_id": employee.external_id, "office": employee.office.code if employee.office else None,
                "shift": employee.shift, "active": employee.active, "pickup_point": employee.pickup_point}
        if member.role in ("hr", "operator", "admin"):
            data["source_version"] = employee.source_version
        return Response(data)


class TransportView(APIView):
    def patch(self, request, employee_id):
        member = deny_unless(request, "admin", "operator")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        changes = {}
        for field, limit in (("pickup_point", 200), ("special_assistance", 2000)):
            if field in request.data:
                value = request.data[field]
                if not isinstance(value, str) or len(value) > limit:
                    return Response({"code": "invalid_transport_field", "field": field}, status=400)
                changes[field] = value
        if not changes:
            return Response({"code": "no_fields"}, status=400)
        updated = Employee.objects.filter(tenant_id=member.tenant_id, external_id=employee_id).update(**changes)
        return Response({"updated": bool(updated)}, status=200 if updated else 404)


class ImportView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        member = deny_unless(request, "hr", "operator")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        upload = request.FILES.get("file")
        if not upload:
            return Response({"code": "file_required"}, status=400)
        try:
            stream = io.TextIOWrapper(upload.file, encoding="utf-8-sig", newline="")
            run = stage_csv(member.tenant_id, stream)
        except (UnicodeError, ValueError, csv.Error):
            return Response({"code": "invalid_csv"}, status=400)
        return Response({"run_id": run.id, "status": run.status, "rows": run.total_rows,
                         "invalid": run.invalid_rows, "missing": run.missing_count}, status=201)


class ImportDetailView(APIView):
    def get(self, request, run_id):
        member = deny_unless(request, "hr", "operator", "admin")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        run = ImportRun.objects.filter(id=run_id, tenant_id=member.tenant_id).first()
        if not run:
            return Response({"code": "not_found"}, status=404)
        errors = list(ImportError.objects.filter(run=run).values("row_number", "employee_id", "code", "field")[:100])
        return Response({"run_id": run.id, "status": run.status, "rows": run.total_rows,
                         "valid": run.valid_rows, "invalid": run.invalid_rows,
                         "missing": run.missing_count, "applied": run.applied_count, "errors": errors})


class ImportApplyView(APIView):
    def post(self, request, run_id):
        member = deny_unless(request, "operator")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        with transaction.atomic():
            run = ImportRun.objects.select_for_update().filter(id=run_id, tenant_id=member.tenant_id).first()
            if not run:
                return Response({"code": "not_found"}, status=404)
            if run.status not in ("ready", "needs_review"):
                return Response({"code": "invalid_import_state"}, status=409)
            if run.status == "needs_review" and request.data.get("approve_missing") is not True:
                return Response({"code": "approval_required", "missing": run.missing_count}, status=409)
            # Claim the run before releasing the row lock. The worker can resume a failed run.
            run.status = "applying"
            run.save(update_fields=["status"])
        run.status = "needs_review" if request.data.get("approve_missing") is True else "ready"
        apply_import(run, approved_by=request.user if run.status == "needs_review" else None)
        return Response({"run_id": run.id, "status": run.status, "applied": run.applied_count})


@csrf_exempt
def internal_event(request):
    if request.method != "POST" or not settings.OUTBOX_INTERNAL_TOKEN or request.headers.get("X-Internal-Token") != settings.OUTBOX_INTERNAL_TOKEN:
        return JsonResponse({"code": "forbidden"}, status=403)
    try:
        payload = json.loads(request.body)
        event = OutboxEvent.objects.get(id=payload["event_id"])
        if event.tenant_id != payload["tenant_id"] or event.employee.external_id != payload["employee_id"]:
            return JsonResponse({"code": "mismatch"}, status=400)
        outcome = process_event(event)
        return JsonResponse({"result": outcome})
    except (ValueError, KeyError, OutboxEvent.DoesNotExist):
        return JsonResponse({"code": "invalid_event"}, status=400)


def health(request):
    return JsonResponse({"status": "ok"})


class MetricsView(APIView):
    def get(self, request):
        member = deny_unless(request, "operator")
        if not member:
            return Response({"code": "forbidden"}, status=403)
        pending = OutboxEvent.objects.filter(tenant_id=member.tenant_id, published_at__isnull=True)
        oldest = pending.order_by("created_at").first()
        counters = {item.name: item.value for item in MetricCounter.objects.filter(tenant_id=member.tenant_id)}
        rejected = counters.get("invalid", 0) + counters.get("conflict", 0)
        lines = [f"roster_outbox_pending {pending.count()}",
                 f"roster_outbox_retries {OutboxEvent.objects.filter(tenant_id=member.tenant_id).aggregate(total=Sum('attempts'))['total'] or 0}",
                 f"roster_outbox_oldest_age_seconds {(timezone.now()-oldest.created_at).total_seconds() if oldest else 0}",
                 f"roster_import_needs_review {ImportRun.objects.filter(tenant_id=member.tenant_id, status='needs_review').count()}",
                 f"roster_ingest_error_ratio {rejected / max(counters.get('records', 0), 1):.6f}"]
        for name, value in counters.items():
            lines.append(f"roster_ingest_{name} {value}")
        return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
