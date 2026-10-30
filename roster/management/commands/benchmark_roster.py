import csv
import json
import platform
import statistics
import tempfile
import time
import uuid
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from rest_framework.authtoken.models import Token
from roster.imports import apply_import, stage_csv
from roster.models import Membership, Office, Tenant


class Command(BaseCommand):
    help = "Measure import throughput and applied-event API p50/p99; use --rows 200000 for target."

    def add_arguments(self, parser):
        parser.add_argument("--rows", type=int, default=200000)
        parser.add_argument("--samples", type=int, default=200)

    def handle(self, *args, **options):
        count = options["rows"]
        if count < 1 or options["samples"] < 1:
            raise ValueError("rows and samples must be positive")
        tenant = Tenant.objects.create(name=f"Benchmark {uuid.uuid4().hex[:12]}")
        Office.objects.get_or_create(tenant=tenant, code="BLR", defaults={"name": "Bengaluru"})
        user, _ = get_user_model().objects.get_or_create(username="benchmark-operator")
        Membership.objects.update_or_create(user=user, defaults={"tenant": tenant, "role": "operator"})
        token, _ = Token.objects.get_or_create(user=user)
        stream = tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="")
        writer = csv.writer(stream)
        writer.writerow(["employee_id", "source_version", "name", "email", "phone", "manager_id", "office_code", "shift"])
        for index in range(count):
            writer.writerow([f"bench-{index}", 1, "Synthetic Person", "", "", "", "BLR", "day"])
        stream.seek(0)
        start = time.perf_counter()
        run = stage_csv(tenant.id, stream)
        stage_seconds = time.perf_counter() - start
        if run.status != "ready":
            raise RuntimeError(f"Benchmark import requires review: run {run.id}")
        start = time.perf_counter()
        apply_import(run)
        apply_seconds = time.perf_counter() - start
        client = Client(HTTP_AUTHORIZATION=f"Token {token.key}", HTTP_HOST="localhost")
        latencies = []
        api_start = time.perf_counter()
        for index in range(options["samples"]):
            body = json.dumps({"records": [{"employee_id": f"bench-api-{index}", "source_version": 1,
                                            "name": "Synthetic Person", "office_code": "BLR", "shift": "day"}]})
            started = time.perf_counter()
            response = client.post("/api/hr/events/", data=body, content_type="application/json")
            if response.status_code != 200 or response.json()["results"][0]["result"] != "applied":
                raise RuntimeError(f"Unexpected response {response.status_code}")
            latencies.append((time.perf_counter()-started)*1000)
        api_seconds = time.perf_counter() - api_start
        latencies.sort()
        result = {"database": connection.vendor,
                  "machine": platform.platform(), "python": platform.python_version(),
                  "rows": count, "stage_seconds": round(stage_seconds, 3),
                  "apply_seconds": round(apply_seconds, 3),
                  "total_seconds": round(stage_seconds+apply_seconds, 3),
                  "rows_per_second": round(count/(stage_seconds+apply_seconds), 1),
                  "peak_process_rss_mb": peak_rss_mb(),
                  "api_samples": len(latencies), "api_events_per_second": round(len(latencies)/api_seconds, 1),
                  "api_p50_ms": round(statistics.median(latencies), 3),
                  "api_p99_ms": round(latencies[min(len(latencies)-1, int(len(latencies)*0.99))], 3)}
        self.stdout.write(json.dumps(result, indent=2))
        stream.close()


def peak_rss_mb():
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
    except ImportError:
        return None
