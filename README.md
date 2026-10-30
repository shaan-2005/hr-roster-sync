# HR Roster Sync

Django implementation of MoveInSync Case Study 5. It accepts versioned HR events, reconciles nightly rosters, preserves transport-admin fields, and delivers downstream changes through a transactional outbox.

Python and Django were chosen for fast, readable API and data-layer development; PostgreSQL performs the 200,000-row reconciliation with set-based SQL. Core validation and version rules remain in plain classes and functions so they can be tested directly.

## Run

```sh
.\start.ps1  # Windows PowerShell
# ./start.sh  # macOS/Linux
```

The API is at `http://localhost:8000/api/health/`. In another terminal, create a demo tenant, office, and operator token:

```sh
docker compose exec app python manage.py create_demo
```

The starter creates a local `.env` with random database, Django, and internal-event credentials; `.env` is excluded from Git. Serve the token-authenticated API over HTTPS for any shared deployment. Docker Compose starts PostgreSQL, the Django API, and the outbox worker; the API container applies migrations. The Compose stack was started and verified on Windows 11 with Docker Desktop and WSL 2.

## Example API calls

Use the printed operator token as `TOKEN`:

```sh
curl -X POST http://localhost:8000/api/hr/events/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{"records":[{"employee_id":"e-101","source_version":1,"name":"Example Person","office_code":"BLR","shift":"day"}]}'
curl http://localhost:8000/api/employees/e-101/ -H "Authorization: Token $TOKEN"
curl -X PATCH http://localhost:8000/api/employees/e-101/transport/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{"pickup_point":"Gate 2"}'
curl -F "file=@roster.csv" http://localhost:8000/api/imports/ -H "Authorization: Token $TOKEN"
curl http://localhost:8000/api/imports/1/ -H "Authorization: Token $TOKEN"
curl -X POST http://localhost:8000/api/imports/1/apply/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{"approve_missing":true}'
curl http://localhost:8000/api/metrics/ -H "Authorization: Token $TOKEN"
```

Nightly CSV columns: `employee_id,source_version,name,email,phone,manager_id,office_code,shift`. Each row is a **complete HR-owned snapshot** for that employee. A missing row is interpreted as a deactivation only after the export passes the configurable safety check (`IMPORT_MISSING_THRESHOLD`, default 0.30), or an operator explicitly approves it. A file with invalid rows also requires review before any reconciliation is applied.

## Tests and benchmark

```sh
docker compose exec app python manage.py test roster
docker compose exec app python manage.py benchmark_roster --rows 200000 --samples 200
```

The benchmark prints machine details, stage and apply time, rows per second, peak process resident memory, and applied-event API throughput and p50/p99 latency. It creates a fresh synthetic tenant. Run `python manage.py cleanup_benchmarks --yes` afterward to remove generated rows.

**Measured PostgreSQL result (Windows 11 host, Intel Core i5-9300H (8 logical processors), 15.8 GiB RAM, Docker Desktop/WSL 2, Linux kernel 6.6.87.2, Python 3.13.18):** 200,000 rows staged in 12.018 s and applied in 18.045 s, **30.063 s total** (6,652.6 rows/s), with 68.41 MiB peak process RSS. The 200 applied API samples measured 5.1 events/s, p50 175.573 ms, and p99 872.478 ms. The outbox worker was paused during the import benchmark to isolate reconciliation cost. This meets the case study's under-60-second target on this machine. The API latency sample is sequential and is not a concurrent load test. A repeat run measured 33.45 s total (12.03 s staging, 21.42 s applying), 68.43 MiB peak RSS, API p50 200.397 ms and p99 595.919 ms.

The PostgreSQL import uses COPY into a temporary table followed by set-based upserts and matching outbox inserts. SQLite remains a portable local-test fallback. A smaller SQLite smoke run is not used to assess the PostgreSQL target.

The PostgreSQL-only concurrency test starts two threads updating the same employee and verifies that the higher source version wins. A separate-process failure test commits a deactivation and exits before event publication; recovery then cancels the future booking. Another injected-crash test verifies that approval of a flagged import survives a crash before apply begins. SQLite skips the PostgreSQL concurrency and process-crash tests because its locking model is different. Other tests cover replay, stale updates, field ownership, invalid records, tenant isolation, mass-deactivation protection, and delivery timeout.

After confirming an interrupted import is no longer running, an operator can resume it with `docker compose exec app python manage.py recover_import RUN_ID`. A flagged import must have prior operator approval. Reapplying already committed employee versions is idempotent; the worker also restarts automatically after a crash.

## Demo

The [demo guide](DEMO.md) explains the live script and links to a short MP4 captured from real API and worker-failure output.

## Rules and trade-offs

- **Version key:** `(tenant, employee_id, source_version)`. A lower version is stale; an equal version with identical HR data is a duplicate; an equal version with different HR data is a conflict. A higher version applies.
- **Class design:** `HRSource` defines a streaming input contract; `ApiHRSource` and `CsvHRSource` implement it. Immutable `HRRecord` encapsulates validated HR data and its digest. API views, validation, sync service, import service, and outbox delivery have separate responsibilities.
- **Field ownership:** HR owns name, email, phone, manager, office, shift, and active status. Transport admins own pickup point and special assistance. HR writes never modify the latter fields.
- **Durability:** employee changes and outbox rows commit together. The worker delivers at least once; consumers deduplicate by outbox event ID. An API may return before booking cancellation completes. Outbox age and retries expose that delay.
- **Safety:** a nightly export with at least 30% of active employees missing requires explicit operator approval before any deactivation. Invalid rows also force review. This favors safety over unattended completion.
- **Caching:** office-code validation uses a per-process cache with a 30-second TTL, a 10,000-entry limit and one-third culling when full, plus local invalidation on edits. A different process may retain an old code for at most 30 seconds. Employee versions are never cached for writes.
- **Scale:** staging and comparison are set-based in PostgreSQL. Import processing is expected O(N) in rows plus indexed lookups; the largest costs are database I/O, indexes, and outbox writes. The Python CSV reader retains batches of at most 1,000 rows. The API caps batches at 1,000 records. At larger volume, partitioning staging/outbox tables and separate import workers would be needed.
- **Observability:** JSON logs contain event codes and identifiers, never HR payloads. The operator metrics endpoint reports ingest outcomes and error ratio, pending outbox count, cumulative retry count, oldest pending age, and imports requiring review.

## Known limits

The Compose stack, PostgreSQL test suite, and 200,000-row target have been verified on the machine above. Import apply is synchronous and could exceed the API timeout for a large file; move the apply step to a durable job worker before production use. The internal event receiver demonstrates idempotent booking cancellation; a production integration would deliver the same outbox event ID to the actual booking service. The local demo API token should be rotated before any shared deployment.

See [DESIGN.md](DESIGN.md) for architecture, the hardest sequence, and failure behavior.
