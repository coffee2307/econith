# Docker state and audit volume migration (Phase 0)

**Status:** reviewable design on draft PR #28, **not** a live deployment
instruction. Do not apply to an existing production instance until the
operator has verified a backup and accepted the data-migration plan.

## Why this change exists

In disposable Linux Docker CI the service is run as non-root uid 1000.
A brand-new host bind directory such as `./logs:/app/logs` is often
created by Docker as root. A real HTTP request crashed before
authentication with `PermissionError: /app/logs/econith_audit.log`.
After repairing audit storage, FastAPI startup failed because
`StateRecorder` created `datasets/econith.sqlite` inside another
unwritable host bind path.

These are **real non-root filesystem regressions**, not a missing test
assertion. Disabling audit, changing to root, or editing host data owners
recursively would be a poor fix.

## New configuration

| Path inside container | New storage | Previous data preserved |
| --- | --- | --- |
| `/app/logs` | `auditlogs` named Docker volume, writable by uid 1000 | Original host `./logs` (dev) or `./runtime/logs` (prod) is mounted **read-only** at `/app/legacy-host-logs`. |
| `/app/runtime-state/econith.sqlite` | `recorderstate` named Docker volume | Original `./datasets/econith.sqlite` or `./runtime/datasets/econith.sqlite` is unchanged. |
| `/app/datasets` | **Existing host directory bind** | Original datasets stay where they are, without copy or delete. |
| SQLAlchemy primary database, dev/prod | PostgreSQL service | New dev Compose `DATABASE_URL` explicitly points to its already configured Postgres; production already used Postgres. |
| Research/model registry | Existing bind paths | Unchanged. |

Named volumes survive `docker compose stop`, `restart`, and normal
`docker compose down`. **DO NOT RUN `docker compose down -v` on
production**: that intentionally destroys named volumes, including
`auditlogs`, `recorderstate`, Postgres and Redis. The CI smoke uses
`down -v` ONLY on its disposable runner and dummy databases.

The full `config/database.py` Postgres failover path still requires
a separate strict fail-closed review; this Phase 0 storage change alone
does not certify authoritative account ledger recovery.

## BEFORE moving any existing deployment

1. Stop the application gracefully and preserve its on-disk state.
   Verify existing `./datasets/`, `./logs/`, `./runtime/`,
   SQLite journal and database volumes; hash and back up them **outside**
   the active Compose stack.
2. Confirm the current recorder journal's SQLite integrity and latest
   tick/Sentinel records. The new named volume starts empty unless
   the journal is **explicitly migrated**. Starting fresh is not an
   implicit recovery from previously persisted Sentinel state.
3. Verify that the owner/operator can read the historical log files
   and has a tested path to restore both the old bind files and new
   named volumes. Do **not** overwrite the old journal automatically.
4. Inventory every independent component that writes under
   `/app/datasets`. The host datasets mount is still a writable bind
   and may need proper host permissions, but no recursive chown
   or unreviewed directory replacement should be attempted.
5. Rebuild and test with synthetic data, no trading credentials and
   a disconnected/demo execution path. Confirm health, HTTP 401 on
   unauthorized control mutation, durable audit record, SQLite
   `PRAGMA quick_check`, and shutdown/restart data retention.
6. Only then consider applying to a non-production staging host.
   Production promotion remains separately gated and live trading
   is explicitly out of scope.

## Safe inspection commands (do not change state)

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
docker volume ls
docker compose -f docker-compose.prod.yml ps
```

**Important:** these commands do not migrate historical state. Use a
separately approved migration with an explicit reversible restore procedure.

## CI evidence

- Initial Docker smoke: audit log `PermissionError` on an unowned host bind.
- Second Docker smoke: `sqlite3.OperationalError: unable to open database file`
  from `StateRecorder` in host-mounted datasets after audit was fixed.
- `.github/workflows/container-smoke.yml` now checks mock-market
  provenance, rejected unauthenticated control requests, audit DENY
  persistence and SQLite journal integrity in a disposable, uncredentialed
  Linux Docker stack. Do not declare that test green unless its GitHub
  Actions run is actually successful.

Root source-tree cleanup and this Docker migration remain unmerged.
