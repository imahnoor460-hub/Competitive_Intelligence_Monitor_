# Competitive Intelligence Monitor — Full Implementation Plan

## Context

The repo at `competitor/` is currently a bare MVP: JWT auth, single-URL competitor CRUD, and a manual "Check now" button that fetches a page, strips noise, diffs it against the last snapshot, and stores a `ChangeLog` row. That's it — `ChangeLog.materiality_score`/`classification` exist as columns but nothing ever writes them, there's no LLM call anywhere in the codebase, no approval gate, no delivery, no scheduling, no multi-tenancy, and no Alembic migrations (schema is created via `Base.metadata.create_all`).

The target is the full product described in the user's own spec doc ("Competitive Intelligence Monitor" concept) and mocked up in a "Sentry Signal" dashboard: multi-surface monitoring per competitor, LLM materiality scoring + classification, cross-competitor synthesis, human-gated briefings/battlecards, Slack/email delivery, team workspaces, audit trail, billing, and production hardening.

User decisions locked in for this plan:
1. **Scope:** build everything in the spec (not just an MVP slice).
2. **LLM provider:** NVIDIA NIM, via its OpenAI-compatible chat completions API (`openai` SDK pointed at `https://integrate.api.nvidia.com/v1`), behind a provider-agnostic wrapper so swapping models/providers later is a config change.
3. **Scheduling:** in-process APScheduler inside FastAPI (no Redis/Celery) — accepted tradeoff: only correct for a single app instance. **Partly superseded:** background *job execution* moved to arq + Redis on a separate worker service (see `docs/QUEUE_AND_WORKER.md`); the scheduler itself still lives in the web process, so the single-instance constraint continues to apply to the web service alone.

This is a large, multi-session effort. The plan is ordered so each phase unblocks the next and schema/tenancy decisions don't need to be retrofitted later.

---

## Sequencing

```
0 Config+Alembic+tests
  -> 1 Workspaces & multi-tenancy        [must precede every content table]
    -> 2 Multi-surface competitors        [must precede scoring/scheduler/visual-diff]
      -> 3 LLM wrapper + scoring + budget logging
        -> 4 APScheduler wiring
        -> 5 Visual diff (Playwright)
        -> 6 Embeddings + synthesis (needs pgvector)
          -> 7 Briefing / Approval Queue / Audit Trail  [human-in-the-loop spine]
            -> 8 Battlecards + Response Library
            -> 9 Delivery connectors (Slack/email) + digest scheduling
              -> 10 Dashboard overhaul + company profiles
              -> 11 Exports (CSV/DOCX/PDF) + GDPR scaffolding
              -> 12 Budget enforcement + rate limiting
                -> 13 Stripe billing scaffold
                  -> 14 Cross-tenant isolation tests + CI
                    -> 15 Observability/polish
```

Hard rules that keep this from unraveling mid-build:
- **Delivery only ever fires from the approval decision handler.** Draft/generate and deliver stay separate functions — no code path lets LLM output reach Slack/email without an explicit approve.
- **Prompt-injection defense is written into the LLM wrapper on day one** (Phase 3), not bolted on later: crawled content goes in a delimited "untrusted data" block, output is JSON-schema-validated only, and these calls never get tool/function-calling access.
- **`workspace_id` is on every table from creation**, starting Phase 1 — no retrofitting tenancy after content models exist.
- Tests are added per-phase (pytest scaffolding starts Phase 0); Phase 14 is the isolation-test *sweep* + CI wiring, not first-time test authorship.

---

## Phase 0 — Config, Alembic bootstrap, test harness

- `backend/app/core/config.py`: `pydantic-settings` `Settings` (currently empty file) — `database_url`, `jwt_secret_key`, `jwt_algorithm`, `access_token_expire_minutes`, `nvidia_api_key`, `nvidia_base_url` (default `https://integrate.api.nvidia.com/v1`), `nvidia_chat_model`, `nvidia_embed_model`, `smtp_host/port/user/password/from_email`, `snapshot_storage_path`, `cors_origins`. Replace the scattered `os.getenv()` calls in `backend/app/database.py` and `backend/app/core/security.py` with `from app.core.config import settings`.
- Alembic: `alembic init` targeting `backend/app/alembic` (currently empty despite being in `requirements.txt`), `env.py` imports `Base` + all models + `settings.database_url`. Generate `0001_baseline` via `--autogenerate` against the current dev DB and verify it's a no-op diff. **Only then** remove `Base.metadata.create_all(bind=engine)` from `backend/app/main.py` — ordering matters, or schema can no longer be recreated from scratch.
- `backend/tests/conftest.py` + `pytest`/`pytest-cov`/`httpx` in `requirements.txt`, `TestClient` fixture overriding `get_db`.

## Phase 1 — Workspaces & multi-tenancy

- New models: `backend/app/models/workspace.py` (`Workspace(id, name, slug, created_at)`), `backend/app/models/workspace_member.py` (`WorkspaceMember(id, workspace_id FK, user_id FK, role: Enum[owner,editor,reviewer])`, unique on `(workspace_id, user_id)`).
- Migration `0002`: create both tables; backfill one `Workspace`+owner `WorkspaceMember` per existing `User`; add `competitors.workspace_id` (nullable→backfill→NOT NULL); rename `competitors.user_id` → `created_by_user_id` (keep for provenance).
- `backend/app/dependencies.py`: add `get_current_workspace(workspace_id, ...)` (404 if no membership) and `require_role(*roles)` factory.
- New router `backend/app/routers/workspace.py`: create/list workspaces, invite/list/change-role/remove members.
- Restructure `competitor.py`/`change_log.py` routes under `/workspaces/{workspace_id}/...`, filter by `workspace_id` not `user_id`.
- Frontend: `Workspace`/`WorkspaceMember` types in `frontend/lib/types.ts`, workspace switcher + `frontend/app/settings/team/page.tsx`, active workspace id stored alongside the JWT.

## Phase 2 — Multi-surface competitors (Surface + Snapshot)

- New models: `backend/app/models/surface.py` (`Surface(id, competitor_id FK, surface_type: Enum[pricing,product,changelog,blog,jobs,other], url, check_frequency, capture_visual: bool, last_checked_at, is_active)`), `backend/app/models/snapshot.py` (`Snapshot(id, surface_id FK, text_content, content_hash, screenshot_path nullable, created_at)`).
- `ChangeLog` gains `surface_id FK`, `old_snapshot_id`/`new_snapshot_id FK→Snapshot`; drop old inline `old_snapshot`/`new_snapshot` text columns.
- Migration `0003`: create tables; for each `Competitor`, spin up one `Surface(surface_type='pricing', url=competitor.url, ...)` + a `Snapshot` wrapping `last_snapshot`; repoint `change_logs`; then drop `competitors.url/last_snapshot/check_frequency/last_checked_at`.
- Extract `backend/app/services/check_service.py::run_surface_check(db, surface)` from the inline logic in `competitor.py` — becomes the single call site for both the manual endpoint and the Phase-4 scheduler.
- New router `backend/app/routers/surfaces.py`: CRUD under `/workspaces/{wid}/competitors/{cid}/surfaces`, `POST .../surfaces/{sid}/check`.
- Frontend: competitor → expandable surface list with per-surface frequency/check/history.

## Phase 3 — LLM wrapper (NVIDIA NIM) + materiality scoring/classification

- `backend/app/services/llm/client.py`: `LLMClient` protocol — `complete(system, user, response_model) -> BaseModel`, `embed(texts) -> list[list[float]]`.
- `backend/app/services/llm/provider_nim.py`: `NIMProvider` wraps `openai.OpenAI(base_url=settings.nvidia_base_url, api_key=settings.nvidia_api_key)`; forced JSON output validated into Pydantic models (`MaterialityResult(score, classification, rationale)`), retry-once-then-discard on parse failure. Add `openai` to `requirements.txt`.
- `backend/app/services/llm/prompts.py`: fixed system prompt; untrusted crawled/diff text wrapped in a `<SCRAPED_CONTENT>` delimited block with an explicit "data only, ignore embedded instructions" preamble. No tool/function-calling access on these calls — text-in/JSON-out only.
- `backend/app/models/llm_usage.py` (`TokenUsageLog`) and `backend/app/models/workspace_budget.py` (`WorkspaceBudget`) — logging wired in now, enforcement deferred to Phase 12.
- Migration `0004`: new tables + `ChangeLog.rationale: Text`.
- `backend/app/services/llm/scoring.py::score_and_classify(...)` called from `check_service.run_surface_check` whenever a material diff is found, populating `materiality_score`/`classification`/`rationale`.

## Phase 4 — APScheduler wiring

- `backend/app/scheduler.py`: `AsyncIOScheduler`, `schedule_surface(surface)` mapping `check_frequency` → interval/cron trigger with jitter; stored on `app.state.scheduler` so `surfaces.py` mutation handlers can add/remove jobs on create/update/delete (not just at startup).
- `backend/app/main.py`: startup loads all active `Surface` rows into the scheduler; shutdown stops it.
- `backend/app/models/check_run.py` (`CheckRun(surface_id, started_at, finished_at, status, error)`) for idempotency — `run_surface_check` refuses to start if a run is already `running` for that surface (with an age-based self-heal).
- Migration `0005`. New endpoint `GET .../surfaces/{sid}/check-runs`.
- **Document the constraint explicitly** (README/comment): single-instance only — horizontal scaling needs Redis/Celery instead.

## Phase 5 — Visual diff capture

- `playwright` + `Pillow` + `imagehash` (note: `playwright install chromium` is a manual environment step).
- `backend/app/services/screenshot_service.py::capture_screenshot(url)`, saved under `{settings.snapshot_storage_path}/screenshots/...`, path on `Snapshot.screenshot_path`.
- `backend/app/services/visual_diff.py::compare(...)` via `imagehash.phash` distance; `ChangeLog.visual_diff_score: Float` (migration `0006`). Only runs for surfaces with `capture_visual=True`; feeds a textual hint into Phase 3 scoring (no image bytes sent to the text-only NIM model).

## Phase 6 — Embeddings + cross-competitor synthesis

- Prerequisite check: Postgres `pgvector` extension available (`CREATE EXTENSION IF NOT EXISTS vector`) — confirm before starting; blocks on infra if unavailable.
- `backend/app/models/embedding.py` (`ChangeEmbedding(change_log_id FK, workspace_id, vector: Vector(dim))`). Migration `0007`.
- `backend/app/services/llm/embeddings.py` wraps `LLMClient.embed()`.
- `backend/app/services/synthesis.py`: `find_similar_changes(...)`, `generate_cross_competitor_summary(workspace_id, since)` (feeds Phase 9's weekly digest).
- Router: `GET /workspaces/{wid}/insights/trends`.

## Phase 7 — Briefing / Approval Queue / Audit Trail

The human-in-the-loop spine — everything else routes through this.

- Models: `backend/app/models/briefing.py` (`Briefing(workspace_id, audience: Enum[exec,sales,product,all], digest_type: Enum[urgent,daily,weekly], title, body_markdown, status: Enum[draft,pending_approval,approved,rejected,delivered], ...)`), association table to source `ChangeLog`s, `backend/app/models/approval_item.py` (`ApprovalItem(workspace_id, item_type: Enum[briefing,battlecard_update,crm_note], item_id, status, requested_at, decided_by, decided_at, decision_notes)` — one unified table so the queue UI is a single list), `backend/app/models/audit_log.py` (`AuditLog(workspace_id, actor_user_id, action, entity_type, entity_id, metadata JSON, created_at)`).
- Migration `0008`.
- `backend/app/services/briefing_service.py::generate_briefing(...)` — LLM call creates `Briefing(status='draft')` + `ApprovalItem(status='pending')`.
- `backend/app/services/approval_service.py::decide(approval_item_id, decision, actor, notes)` — the **only** place status flips to approved/rejected; on approval, writes `AuditLog` and calls Phase 9's `delivery_service.deliver(...)`.
- Routers: `briefings.py`, `approvals.py` (role-gated via Phase 1's `require_role`), `audit.py`.
- Frontend: `frontend/app/approvals/page.tsx` (queue + approve/reject drawer), `frontend/app/briefings/page.tsx`.

## Phase 8 — Battlecards + Response Library

- `backend/app/models/battlecard.py` (live approved version) + `battlecard_update.py` (proposed changes, also creates an `ApprovalItem`; approval applies via `battlecard_service.apply_approved_update()` and bumps version).
- `backend/app/models/response_library.py` — direct editor/owner CRUD, no approval gate for manual entries.
- Migration `0009`. Routers + frontend pages for both.

## Phase 9 — Delivery connectors + digest scheduling

- `backend/app/models/workspace_integration.py` (`provider: Enum[slack,email,crm]`, `config: JSON`, `enabled`).
- `backend/app/services/delivery/`: `base.py` (`DeliveryConnector` ABC), `slack_connector.py` (incoming webhook — no OAuth app needed), `email_connector.py` (`smtplib`), `crm_connector.py` (stub — real CRM OAuth is out of scope, see below).
- `delivery_service.py::deliver(approval_item)` — called only from Phase 7's `approval_service.decide()` on approval.
- `backend/app/scheduler.py::schedule_digest_jobs()` — cron per workspace bundling approved-but-undelivered non-urgent items into daily/weekly digests; `urgent` briefings bypass batching.
- Router `integrations.py` (CRUD + test-send). Frontend `settings/integrations` page.

## Phase 10 — Dashboard overhaul + company profiles

- Rebuild `frontend/app/page.tsx` into the "Sentry Signal"-style dashboard (stat tiles, change feed, approval-queue widget, charts). **Load the `dataviz` skill before writing any chart code.**
- `frontend/app/competitors/[id]/page.tsx` detail view.
- `backend/app/models/company_profile.py` (`CompanyProfile(competitor_id FK unique, industry, hq_location, employee_range, funding_stage, key_people JSON, notes_markdown)`). Migration `0010`.

## Phase 11 — Exports + GDPR scaffolding

- `backend/app/services/export_service.py` — CSV (stdlib), DOCX (`python-docx`), PDF (`reportlab`, avoids system-dependency issues).
- `backend/app/routers/exports.py`, `backend/app/routers/gdpr.py` (`/users/me/export`, `/users/me/delete`) — mechanics only; legal review of retention/DPA text is out of scope (TODO).

## Phase 12 — Budget enforcement + rate limiting

- Enforce `WorkspaceBudget` from Phase 3: every `LLMClient` call site checks `budget.check(workspace_id)` first; scheduler jobs log/skip on `BudgetExceededError`, user-triggered calls return HTTP 402.
- Simple in-process rate limiting keyed by workspace/user — same single-instance caveat as the scheduler.

## Phase 13 — Stripe billing scaffold

- `backend/app/models/subscription.py`, `backend/app/routers/billing.py` (checkout session + webhook receiver). Real Stripe keys/product catalog/webhook secret are manual dashboard steps — scaffold with placeholder env vars and TODOs.

## Phase 14 — Cross-tenant isolation tests + CI/CD

- `backend/tests/test_tenant_isolation.py` — parametrized across every workspace-scoped endpoint from Phases 1–13, asserting workspace A can't touch workspace B's data.
- `.github/workflows/ci.yml` — backend: install, `alembic upgrade head` against an ephemeral Postgres (pgvector image), pytest; frontend: `npm ci`, lint, build.

## Phase 15 — Observability/polish

- Structured logging, request-id middleware, `/healthz`, optional config-gated Sentry hook, simple `/metrics` counter. Actual Sentry/Prometheus infra provisioning is out of scope.

---

## Explicitly out of scope for implementation (scaffold + TODO only)

- Penetration testing / security audit sign-off.
- Legal document text (ToS, Privacy Policy, DPA) and legal review of GDPR/CCPA mechanics.
- Real Stripe production keys, product/price catalog, webhook secret (Stripe Dashboard is manual).
- Real OAuth app registration for Gmail and CRM (Salesforce/HubSpot) delivery — Slack is fully buildable via incoming webhook since it needs no OAuth app.
- Procuring/billing the NVIDIA API key itself.
- Email deliverability domain setup (SPF/DKIM/DMARC).
- `playwright install chromium` binary download and enabling Postgres `pgvector` — environment/infra steps to confirm before their phases, not blind code changes.

---

## Verification approach (per phase)

- Backend: `pytest` in `backend/` (new tests added per phase per the rule above); `alembic upgrade head` / `alembic downgrade -1` round-trip on each new migration against the dev DB.
- Manual smoke test after each phase: run `uvicorn app.main:app --reload` from `backend/`, `npm run dev` from `frontend/`, exercise the new endpoint(s)/page(s) through the browser.
- Phase 14 specifically: run the isolation-test suite and CI workflow end-to-end before considering multi-tenancy "done."
- Before writing any chart/dashboard code in Phase 10, load the `dataviz` skill.

---

## Status as of this writing

Phases 0 through 10 are implemented, tested (58 backend pytest tests passing), and verified live against the real Postgres DB and real NVIDIA NIM API. Notable deviations from the plan as originally written:

- **Phase 6 (embeddings):** the target Postgres instance does not have the `pgvector` extension installed. Implemented as a plain JSON column (`ChangeEmbedding.vector`) with cosine similarity computed in Python instead of an indexed vector column. Documented in the model's docstring as the thing to swap if this needs to scale.
- **Phase 3 (LLM model):** originally scaffolded against `meta/llama-3.1-70b-instruct`, later switched to `nvidia/nemotron-3-nano-30b-a3b` per user request. The embedding model (`nvidia/nv-embedqa-e5-v5`) required an additional `input_type: "passage"` parameter not anticipated in the original plan — NIM's asymmetric retrieval embedding models need this; wired into `provider_nim.py`.
- **Scheduler:** implemented as a module-level singleton in `app/scheduler.py` rather than `app.state.scheduler` as originally planned — functionally equivalent for a single-process deployment, simpler to test.
- **Phase 3 budget model:** `WorkspaceBudget` was initially skipped and added later during an audit pass to bring the codebase back in line with this document (still unused until Phase 12's enforcement lands).

Two real bugs were found and fixed during a full audit of Phases 0–6: `visual_diff.py::compare()` was returning a `numpy.float64` instead of a plain Python `float`, which crashed on insert against the real Postgres DB (psycopg2 has no adapter for numpy scalars) — silently passed unit tests because SQLite is more lenient; and `run_surface_check` only caught `FetchError`, leaving `CheckRun` rows stuck at `running` forever on any other unexpected exception. Both are fixed with regression tests added.

Phases 11, 12, and 14 are now also implemented, tested (94 backend pytest tests passing), and verified live. **Phase 13 (Stripe billing) was explicitly skipped per user direction** — no payment/billing system is wanted right now; revisit only if asked.

- **Phase 11 (Exports + GDPR):** `export_service.py` (CSV/DOCX via python-docx/PDF via reportlab), `routers/exports.py`, `routers/gdpr.py`. `DELETE /users/me` anonymizes the account (email/password/name scrubbed) rather than hard-deleting the row, since hard-deleting would orphan FKs on shared workspace content (competitors created, briefings generated, audit trail) that other members still rely on; blocks if the user is the sole owner of a workspace that still has other members.
- **Phase 12 (Budget + rate limiting):** `budget_service.py` enforces `WorkspaceBudget.monthly_cap_usd` (estimated from `TokenUsageLog` × a configurable `llm_cost_per_1k_tokens_usd`) at all 5 LLM call sites (scoring, briefing generation, battlecard drafting, cross-competitor synthesis, embeddings). User-triggered endpoints (briefings/battlecards) return HTTP 402 over budget; scheduler/automatic paths (surface-check scoring/embedding) degrade gracefully via the existing broad exception handling, matching Phase 3's pattern; the read-only insights/trends endpoint also degrades gracefully rather than erroring. New `GET/PUT /workspaces/{id}/budget/` (owner-only to set) since the budget was previously unreachable via API. `rate_limiter.py` adds a simple in-process fixed-window limiter (per-workspace, per-scope buckets) on the same LLM-triggering endpoints plus manual surface-check; both budget cap and rate limit live-verified against the running server (real 402s and 429s), not just pytest.
- **Phase 14 (Isolation + CI):** `tests/test_tenant_isolation.py` sweeps every workspace-scoped endpoint with sub-resource ids (competitors, surfaces, briefings, approvals, battlecards, response library, company profiles, members, insights, exports), proving a legitimate member of workspace B can't reach workspace A's resources by id. `.github/workflows/ci.yml` runs backend (`alembic upgrade head` + downgrade/upgrade round-trip against an ephemeral `pgvector/pgvector:pg16` Postgres service, then pytest) and frontend (`npm ci`, lint, build) jobs. **Building and locally verifying this workflow against a real Docker Postgres container caught a real bug**: `0001_baseline` had been autogenerated as a no-op against a dev DB that already had `users`/`competitors`/`change_logs` from the pre-Alembic `create_all` era, so a genuinely fresh database failed at migration 0002 with `relation "users" does not exist` — meaning fresh installs were completely broken. Fixed by filling in `0001_baseline`'s `upgrade()`/`downgrade()` with the actual original pre-migration-2/3 shape of those three tables (reconstructed from what later migrations alter/drop). This only affects fresh installs going forward — the existing dev DB is already past this migration in its recorded Alembic history, so editing the script doesn't touch it.

Remaining: Phase 15 (observability/polish — structured logging, request-id middleware, `/healthz`, `/metrics`) is not yet started. Phase 13 (Stripe billing) is skipped per user direction, not merely deferred.
