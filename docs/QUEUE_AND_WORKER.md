# Background jobs: the queue and the worker

Every slow job in this system — a surface check, a briefing, a battlecard
update, page discovery — used to run inside the web process via FastAPI's
`BackgroundTasks`. That works, but it means a Chromium launch and a chain of
NIM calls compete with request handling in the same process, and a deploy or a
container restart silently drops whatever was mid-flight.

Those jobs now go through [arq](https://arq-docs.helpmanual.io/) (Redis) to a
separate worker service.

## The one rule

**Postgres is the source of truth. Redis only carries the message.**

A job's status, result and error live in its Postgres row (`check_runs`,
`briefing_jobs`, `battlecard_update_jobs`, `competitor_discovery_jobs`,
`site_summary_jobs`, `check_sweeps`). Redis holds nothing that cannot be
reconstructed.

Losing Redis therefore costs throughput, never a record. Rows whose message
never arrived are re-enqueued by `services/job_reconciler.py`; rows whose
worker died mid-flight are marked failed by the same pass.

## Running it

Two services, **one image** — the worker needs the identical Chromium install
and the identical NIM configuration, so sharing the image is the point rather
than an economy.

| Service | Command |
| --- | --- |
| web | `python -m uvicorn app.main:app --host 0.0.0.0 --port 8080` (the Dockerfile `CMD`) |
| worker | `arq app.worker.WorkerSettings` (override the `CMD`) |

Both must be given the **same `REDIS_URL` and the same `DATABASE_URL`**, and
the worker must be given the same `NIM_*` variables as the web service. A
divergent embedding model is the dangerous case: `find_similar_changes`
compares only embeddings whose `model` matches, so a worker writing vectors
under a different model name produces rows that are silently invisible to
similarity search — no error, no failed job. The worker logs both resolved
model names on every boot (`worker.on_startup`) so this is diagnosable from
its first line of output.

### Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `REDIS_URL` | unset | Queue transport. **Unset means no queue** — see below. |
| `ARQ_MAX_JOBS` | `2` | Concurrent jobs per worker. Bounded by memory, not CPU: each job can hold a Chromium process (~300MB resident). |
| `ARQ_JOB_TIMEOUT` | `900` | Seconds. Generous because a site-summary fan-out across a 40-surface competitor is minutes of browser work by design. |

### Running without Redis

With `REDIS_URL` unset, `app/queue.py` falls back to `BackgroundTasks` —
exactly what every call site did before arq existed. This is not a degraded
mode bolted on for convenience; it is what keeps the test suite and a local
dev machine behaving the way they always have. No worker process is needed,
and nothing in `tests/` knows arq exists.

## How a job flows

1. A router (or the scheduler) commits the job's row as `queued` and calls
   `dispatch_job` / `dispatch_jobs` from `app/queue.py`.
2. `app/queue.py` enqueues to arq, or falls back to `BackgroundTasks`.
3. `app/worker.py` receives the task and runs the existing **synchronous** job
   runner in `asyncio.to_thread`, so blocking DB/browser/HTTP work never
   occupies the worker's event loop.
4. The runner writes its own terminal status to its Postgres row.
5. The frontend polls that row (`lib/job-poller.ts`) until it is terminal.

If the enqueue itself fails, the row stays `queued` and the reconciler picks it
up. There is deliberately **no in-process fallback** once a queue is
configured: running the job inline at that moment would put a browser-and-LLM
pipeline back in the web process exactly when the system is already unhealthy,
and a fanned-out sweep would do it dozens of times over.

## Duplicate protection

Two independent layers, either of which is sufficient:

- **Database.** `enqueue_surface_check` refuses to create a second run while
  one is `queued` or `running` for that surface. Transactional and
  authoritative.
- **Queue.** arq refuses to enqueue a job whose `_job_id` already exists, so a
  double-clicked button collapses to one job before the database is consulted.

Job ids are keyed on the **row**, not the subject (`check:run:{id}`, not
`check:surface:{id}`). A subject-scoped key looks like better deduplication but
is actively wrong: arq refuses an id whose *result* still exists, and results
live for `keep_result` seconds after completion, so a surface could not be
re-checked for five minutes.

## Retries are off (`max_tries = 1`)

arq re-invokes a task from the top, and these runners spend real money: a
retried briefing or check re-runs materiality scoring, the baseline summary and
the site summary, re-billing NIM tokens against the workspace budget for work
that may have already partly succeeded. Each runner instead catches its own
failures and records them on its job row, matching the graceful-degradation
behaviour the pipeline already had.

Note the interaction with `job_timeout`: arq enforces it by cancelling the
coroutine, and a thread running blocking code cannot be cancelled. On timeout
the job is marked failed and the worker moves on, but the thread finishes its
work in the background. That is survivable precisely because each runner writes
its own terminal status, and because the per-surface database guard stops a
late finisher from colliding with a retry.

## The reconciler

`services/job_reconciler.py` runs every 5 minutes, **in the web process** — the
case it exists to catch is precisely the one where no worker is consuming, so a
reconciler living in the worker would be asleep exactly when it is needed.

| Situation | Threshold | Action |
| --- | --- | --- |
| Row `queued`, message never delivered | 10 min | Re-enqueue (runners are idempotent; arq refuses the id if the original is alive) |
| Row `running`, worker died holding it | 30 min | Mark failed — comfortably beyond `ARQ_JOB_TIMEOUT` so a merely slow job is never killed |
| Sweep open, every child resolved | 45 min | Close it |

## Scaling note

The APScheduler constraint from the original plan still stands: the **web**
service must run a single instance, because the scheduler lives in its
lifespan. The worker is the part that scales — run as many as memory allows.
Moving the scheduler itself onto the queue is the next step if the web service
ever needs more than one replica.
