# Demo: HR roster sync

The [40-second MP4](demo/hr_roster_sync_demo.mp4) replays a redacted capture of a real local run. Its source evidence is [capture.json](demo/capture.json). The video contains synthetic employee IDs only and does not show the API token. For an interactive demonstration, run the script below while the Compose stack is healthy.

```powershell
cd D:\hr_roster_sync
python demo_capture.py --record
```

Recording mode pauses three seconds after each step so the terminal output is readable. The script obtains a local demo operator token internally and performs these steps against the running API:

1. Health check.
2. Create an employee (version 1).
3. Replay version 1 to show `duplicate`.
4. Send version 0 to show `stale`.
5. Set a transport-owned pickup point, then update HR-owned shift at version 2 to show field ownership.
6. Upload a header-only nightly file to show `needs_review` with no deactivation.
7. Create a future booking, stop the outbox worker, and deactivate the employee at version 3.
8. Confirm the employee update committed while the booking remains booked and the event is pending.
9. Restart the worker and confirm the booking is cancelled, its seat is released, and the event is no longer pending.

The worker restart is in a `finally` block, so the script attempts to restore it even if the API step fails. Each run uses a new synthetic employee ID. The flagged import remains unapproved. The script requires Docker Desktop, the running Compose stack, and Python on the host; it does not require any extra Python packages.

This is a controlled worker-outage demonstration. The automated tests also inject a delivery timeout and verify retry and consumer idempotency. The MP4 is a terminal-style rendering of actual captured output, while the script performs the steps live.
