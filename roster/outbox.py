import json
import logging
import random
import urllib.error
import urllib.request
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Booking, OutboxEvent, ProcessedEvent

log = logging.getLogger("roster")


def process_event(event):
    with transaction.atomic():
        if ProcessedEvent.objects.filter(event=event).exists():
            return "duplicate"
        if event.kind == "EMPLOYEE_DEACTIVATED":
            Booking.objects.filter(tenant=event.tenant, employee=event.employee,
                                   status="booked", starts_at__gt=timezone.now()).update(
                                       status="cancelled", seat_released=True)
        ProcessedEvent.objects.create(event=event)
    return "processed"


def publish_once():
    now = timezone.now()
    event = OutboxEvent.objects.filter(published_at__isnull=True).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).order_by("created_at").first()
    if event is None:
        return False
    payload = json.dumps({"event_id": str(event.id), "tenant_id": event.tenant_id,
                          "employee_id": event.employee.external_id, "kind": event.kind,
                          "source_version": event.source_version}).encode()
    request = urllib.request.Request(settings.OUTBOX_INTERNAL_URL, data=payload,
                                     headers={"Content-Type": "application/json",
                                              "X-Internal-Token": settings.OUTBOX_INTERNAL_TOKEN}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise urllib.error.HTTPError(request.full_url, response.status, "delivery_failed", {}, None)
        event.published_at = timezone.now()
        event.last_error_code = ""
        event.save(update_fields=["published_at", "last_error_code"])
    except (urllib.error.URLError, TimeoutError) as error:
        event.attempts += 1
        delay = min(300, 2 ** min(event.attempts, 8)) + random.uniform(0, 1)
        event.next_attempt_at = timezone.now() + timedelta(seconds=delay)
        event.last_error_code = "delivery_failed"
        event.save(update_fields=["attempts", "next_attempt_at", "last_error_code"])
        log.warning("outbox_retry event_id=%s attempt=%s", event.id, event.attempts)
    return True
