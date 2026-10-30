# Demo: HR roster sync

The [submission video](demo/hr_roster_sync_demo.mp4) is the project owner's screen recording of a live run in PowerShell. It runs `python demo_capture.py --record` against the local Docker Compose stack. The recording begins in a clean terminal and does not display the local API token. Its machine-readable results are saved in [capture.json](demo/capture.json).

To repeat the demonstration with the stack running:

```powershell
cd D:\hr_roster_sync
python demo_capture.py --record
```

The script pauses between steps and runs these live checks:

1. Health check.
2. Apply a new employee at version 1.
3. Replay version 1 and show `duplicate`.
4. Send version 0 and show `stale`.
5. Update a transport-owned pickup point, then update HR-owned shift at version 2; the pickup point remains unchanged.
6. Upload an incomplete nightly file; the import enters `needs_review` and the employee stays active.
7. Create a future booking and stop the outbox worker.
8. Deactivate the employee while the worker is down; the roster change commits, the booking stays booked, and an outbox event remains pending.
9. Restart the worker; the booking is cancelled, the seat is released, and no event remains pending.

The script uses a fresh synthetic employee ID on each run. The flagged import is left unapproved. The worker restart is in a `finally` block so it is attempted even if the demonstration fails midway. Docker Desktop, the Compose stack, and host Python are required; the script uses only Python's standard library.

The video demonstrates selected safety and recovery behaviors. The [automated tests](README.md#tests-and-benchmark) cover the complete rule set, including concurrency, retries, and delivery timeouts. The 200,000-row benchmark result is in the README; it is a separate run and is not shown in this short video.
