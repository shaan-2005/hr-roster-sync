"""Run a real local demo and save redacted evidence for a short video."""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOWS_COMPOSE = Path(r"D:\Docker\resources\bin\docker-compose.exe")
BASE = "http://127.0.0.1:8000"
STEPS = []


def compose(*args):
    env = dict(os.environ)
    if shutil.which("docker"):
        command = [shutil.which("docker"), "compose"]
    elif shutil.which("docker-compose"):
        command = [shutil.which("docker-compose")]
    elif WINDOWS_COMPOSE.exists():
        command = [str(WINDOWS_COMPOSE)]
        env["PATH"] = str(WINDOWS_COMPOSE.parent) + os.pathsep + env.get("PATH", "")
    else:
        raise RuntimeError("Docker Compose is not installed or on PATH")
    result = subprocess.run([*command, *args], cwd=ROOT, env=env,
                            text=True, capture_output=True, check=True)
    return result.stdout.strip()


def emit(title, command, result):
    step = {"title": title, "command": command, "result": result}
    STEPS.append(step)
    print(f"\n[{len(STEPS)}] {title}\n$ {command}\n{result}", flush=True)


def api(method, path, token, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(BASE + path, data=payload, method=method,
                                     headers={"Authorization": "Token " + token,
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def upload_empty_csv(token):
    boundary = "demo-roster-boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"incomplete.csv\"\r\n"
            "Content-Type: text/csv\r\n\r\n"
            "employee_id,source_version,name,email,phone,manager_id,office_code,shift\r\n"
            f"--{boundary}--\r\n").encode()
    request = urllib.request.Request(BASE + "/api/imports/", data=body, method="POST",
                                     headers={"Authorization": "Token " + token,
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def django_shell(code):
    return compose("exec", "-T", "app", "python", "manage.py", "shell", "-c", code)


def main():
    demo_id = "demo-" + datetime.now().strftime("%Y%m%d%H%M%S")
    token_output = compose("exec", "-T", "app", "python", "manage.py", "create_demo")
    match = re.search(r"Operator API token: (\S+)", token_output)
    if not match:
        raise RuntimeError("Could not obtain demo API token")
    token = match.group(1)
    emit("Stack health", "GET /api/health/", json.dumps(json.loads(urllib.request.urlopen(BASE + "/api/health/").read())))
    first = {"employee_id": demo_id, "source_version": 1, "name": "Synthetic Demo Employee",
             "office_code": "BLR", "shift": "day"}
    result = api("POST", "/api/hr/events/", token, {"records": [first]})
    emit("New employee", "POST /api/hr/events/ version=1", json.dumps(result))
    result = api("POST", "/api/hr/events/", token, {"records": [first]})
    emit("Duplicate feed is harmless", "POST /api/hr/events/ version=1 again", json.dumps(result))
    stale = dict(first, source_version=0, shift="night")
    result = api("POST", "/api/hr/events/", token, {"records": [stale]})
    emit("Older update is ignored", "POST /api/hr/events/ version=0", json.dumps(result))
    api("PATCH", f"/api/employees/{demo_id}/transport/", token, {"pickup_point": "Gate 2"})
    newer = dict(first, source_version=2, shift="evening")
    api("POST", "/api/hr/events/", token, {"records": [newer]})
    employee = api("GET", f"/api/employees/{demo_id}/", token)
    assert employee["shift"] == "evening" and employee["pickup_point"] == "Gate 2"
    emit("Transport field survives HR sync", "PATCH pickup; POST version=2; GET employee", json.dumps(employee))
    run = upload_empty_csv(token)
    employee = api("GET", f"/api/employees/{demo_id}/", token)
    assert run["status"] == "needs_review" and employee["active"]
    emit("Broken nightly file is stopped", "POST /api/imports/ header-only CSV", json.dumps(run) + " | employee active=true")
    code = ("from roster.models import Employee,Booking; from django.utils import timezone; "
            "from datetime import timedelta; "
            f"e=Employee.objects.get(external_id='{demo_id}'); "
            "Booking.objects.create(tenant=e.tenant,employee=e,starts_at=timezone.now()+timedelta(days=1)); "
            "print('future booking created')")
    django_shell(code)
    compose("stop", "worker")
    emit("Failure injected", "docker compose stop worker", "Outbox worker stopped before deactivation")
    try:
        deactivation = {"employee_id": demo_id, "source_version": 3, "operation": "DEACTIVATE"}
        result = api("POST", "/api/hr/events/", token, {"records": [deactivation]})
        code = ("from roster.models import Employee,Booking,OutboxEvent; "
                f"e=Employee.objects.get(external_id='{demo_id}'); "
                "print('booking=' + Booking.objects.get(employee=e).status); "
                "print('pending=' + str(OutboxEvent.objects.filter(employee=e,published_at__isnull=True).count()))")
        state = django_shell(code)
        emit("Change commits while worker is down", "POST deactivation version=3; inspect database",
             json.dumps(result) + " | " + state.replace("\n", " | "))
    finally:
        compose("up", "-d", "--no-deps", "worker")
    for attempt in range(30):
        code = ("from roster.models import Employee,Booking,OutboxEvent; "
                f"e=Employee.objects.get(external_id='{demo_id}'); "
                "b=Booking.objects.get(employee=e); "
                "print('booking=' + b.status + ', seat_released=' + str(b.seat_released)); "
                "print('pending=' + str(OutboxEvent.objects.filter(employee=e,published_at__isnull=True).count()))")
        state = django_shell(code)
        if "booking=cancelled, seat_released=True" in state and "pending=0" in state:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Worker did not recover within 30 seconds")
    emit("Worker recovers safely", "docker compose up -d worker; inspect booking and outbox",
         state.replace("\n", " | "))
    output = ROOT / "demo" / "capture.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"demo_employee_id": demo_id, "steps": STEPS}, indent=2), encoding="utf-8")
    print(f"\nSaved redacted demo evidence: {output}")


if __name__ == "__main__":
    main()
