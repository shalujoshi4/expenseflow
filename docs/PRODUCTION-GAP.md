# Production Gap Analysis

ExpenseFlow is a PoC (per `CLAUDE.md`). This audits the current code (`app/`, `ui/`) against a production bar. Each finding is grounded in what the code actually does today, not the design intent documented in `docs/ARCHITECTURE.md`.

"Blocking" = should not go live handling real expense/financial data without this. "Deferrable" = acceptable to ship without, for a first controlled rollout (e.g. internal, trusted network, low stakes), but should be tracked.

## Summary

| Area | Gap severity | Rough effort |
|---|---|---|
| Authentication and key rotation | Blocking | 3.5–6 days |
| Input validation | Blocking (lengths) / Deferrable (category/date rules) | 1–1.5 days |
| Rate limiting | Blocking (`/reports/insights`) / Deferrable (CRUD, if trusted network) | 1–3 days |
| Observability and logging | Blocking | 3.5–6 days |
| Error handling | Deferrable (edge case) / Blocking (global handler) | 1 day |
| DB migrations and pooling | Blocking (migrations) / Deferrable (pooling, until networked DB) | 1–2 days now, 3–5 days later |
| Secrets management | Blocking | 0.5–3 days |
| Tests and coverage | Blocking | 2–3 days (baseline) |
| Deployment and health checks | Blocking | 3.25–5.25 days |
| Data privacy for expense data | Blocking (encryption/permissions) / Deferrable (retention/deletion) | 0.5–3 days |

**Total for the blocking items alone, minimum viable form: roughly 3–4.5 engineer-weeks.**

---

## 1. Authentication and key rotation

**Gap.** No endpoint in `app/routes.py` has an auth dependency — every route (`create_expense`, `list_expenses`, `get_expense`, `approve_expense`, `reject_expense`, `get_insights`) is open to anyone who can reach the port. There is no user/role model at all, so there's no distinction between "submitter" and "approver" — the same anonymous caller can submit an expense and immediately approve it. The one real secret in the system, `ANTHROPIC_API_KEY`, has no rotation mechanism: rotating it today means manually editing `.env` and restarting the process, with no dual-key/versioned rollover window.

**Classification.** Blocking. Approval workflows are inherently authorization-sensitive; shipping this open is not viable once real money/approval decisions are involved.

**Effort.**
- Minimal API-key or JWT/OAuth2 auth via FastAPI security dependencies, plus a submitter/approver role check on the approve/reject routes: 3–5 days (excludes integrating an actual org identity provider, which is separate scope).
- Documented key-rotation process for `ANTHROPIC_API_KEY` (dual-key window, restart procedure): 0.5–1 day.

## 2. Input validation

**Gap.** `ExpenseCreate` (`app/schemas.py`) validates `original_amount_minor > 0` and `original_currency` against `SUPPORTED_CURRENCIES`, but `description` and `category` have no `max_length` (or any) validation. The DB columns declare `String(500)`/`String(100)` (`app/models.py`), but SQLite's type affinity does not enforce `VARCHAR` length limits — a description of unbounded length can be submitted and stored today. There's also no bound on `expense_date` (arbitrary past or future dates are accepted), and `category` is free text despite being used as an exact-match filter in `GET /expenses`, so typos silently fragment reporting.

**Classification.** Blocking for the unbounded string lengths (storage/DoS risk with zero enforcement anywhere in the stack). Deferrable for `category` allow-listing and `expense_date` bounds — data-quality issues, not a security gap.

**Effort.**
- Add `Field(max_length=...)` on `description`/`category` in `ExpenseCreate` (matching the DB column sizes): 0.5 day.
- `category` enum/allow-list and `expense_date` range validation: 0.5–1 day.

## 3. Rate limiting

**Gap.** No rate limiting exists anywhere — no middleware, no per-IP/per-key throttling. `GET /reports/insights` is the sharpest edge: every call triggers a live, paid Anthropic API call (`app/insights.py`) with no cap, no cache, and no per-caller limit — a scripted loop against this single endpoint is an open-ended cost and upstream-abuse vector. `POST /expenses` can also be flooded to grow the DB unbounded.

**Classification.** Blocking for `/reports/insights` specifically, given the direct, uncapped cost exposure to a third-party API. Deferrable for the CRUD endpoints only if the first deployment sits behind an authenticated, trusted network — otherwise also blocking.

**Effort.**
- Basic in-process rate limiting (e.g. `slowapi` or a Starlette middleware) on all endpoints, tighter on `/reports/insights`: 1 day.
- Distributed rate limiting (Redis-backed), required once more than one app instance runs: +1–2 days.

## 4. Observability and logging

**Gap.** `app/main.py` never calls `logging.basicConfig` or configures any handler/formatter/level — the only logger in the codebase is the module-level one in `app/insights.py`, which logs Anthropic call failures via `logger.error`/`logger.warning` but has nowhere configured to actually surface those. Beyond Uvicorn's default access log line, there is no structured (JSON) logging, no request/correlation IDs, no metrics endpoint (e.g. Prometheus `/metrics`), no tracing, and no alerting hooks. Operationally, the service is close to a black box today.

**Classification.** Blocking. An unmonitored service handling approval decisions cannot be operated safely — you'd have no way to know insight generation is silently failing, or that the DB is growing unbounded, until a user complains.

**Effort.**
- Structured logging + request-ID middleware: 1–2 days.
- Metrics + a basic dashboard (assumes an existing metrics stack, e.g. Prometheus/Grafana or a SaaS APM): 2–3 days.
- Alerting rules on top of the above: 0.5–1 day.

## 5. Error handling

**Gap.** There is no global exception handler registered on the FastAPI app. `approve_expense`/`reject_expense` and `get_expense` explicitly raise `HTTPException` for the 404/409 cases they anticipate, but anything unanticipated — e.g. a SQLAlchemy `IntegrityError` from a `CHECK` constraint violation that somehow bypasses pydantic's pre-validation — would propagate as FastAPI's default unhandled-exception 500 rather than a clean, consistent error response. There is also no single error-response schema: `HTTPException` errors return `{"detail": "..."}`, while pydantic 422s return FastAPI's separate auto-generated validation-error shape. The Anthropic-call failure path in `app/insights.py` is the only place in the codebase with a deliberate try/except-and-fallback strategy.

**Classification.** The specific `IntegrityError` edge case is deferrable (unlikely in practice, since pydantic validates first). The lack of a global exception handler (needed to guarantee no raw traceback ever leaks if debug mode is left on) is closer to blocking.

**Effort.** Global exception handler + consistent error envelope across all endpoints: 1 day.

## 6. Database migrations and pooling

**Gap.** There is no migration tool. `init_db()` (`app/db.py`) only calls `Base.metadata.create_all()`, which creates tables that don't exist yet but never alters existing ones — any future column/constraint change requires either a hand-written `ALTER TABLE` or dropping and recreating data. On pooling: the engine is created with no explicit pool configuration, so SQLAlchemy defaults apply (`QueuePool`, size 5, verified directly against this codebase's `create_engine` call) — no `max_overflow`, `pool_timeout`, `pool_pre_ping`, or `pool_recycle` are set. This is largely moot today because SQLite itself serializes writers regardless of pool size; it becomes a real concern only once/if the DB moves to a networked engine, where `pool_pre_ping` (to survive DB restarts) and pool sizing under concurrent load matter.

**Classification.** Migrations are blocking — no schema change can be shipped safely without one. Pooling tuning is deferrable until a networked DB is adopted.

**Effort.**
- Introduce Alembic + a baseline migration capturing the current schema: 1–2 days.
- Move off SQLite to a networked DB (if/when concurrency requires it) with proper pool tuning: 3–5 days (connection string/env plumbing, data migration, load testing).

## 7. Secrets management

**Gap.** The only secret, `ANTHROPIC_API_KEY`, is read from a plain `.env` file via `python-dotenv`. There is **no `.gitignore` in the repository at all** — `.env` (which currently contains a live key), `.venv/`, and `expenseflow.db` are untracked purely by accident, not by policy; a single `git add -A` would commit the live key. There is no secrets-manager integration (Vault, AWS/GCP Secrets Manager, etc.), and no environment separation — dev/staging/prod would all read secrets the same ad hoc way.

**Classification.** Blocking. This is a live, currently-exposed key with no guardrail against being committed, not a hypothetical risk.

**Effort.**
- Add `.gitignore`, rotate the current key, document per-environment secret injection via platform env vars/CI secrets: 0.5 day.
- Full secrets-manager integration: 1–3 days depending on target platform.

## 8. Tests and coverage

**Gap.** There are zero test files in the repository — `.pytest_cache/v/cache/nodeids` shows an empty list, confirming no tests have ever been collected, despite `pytest` being installed and documented as the test command in `CLAUDE.md`/`README.md`. There is no coverage of the money/rounding logic, the approve/reject state machine, currency validation, or the insights fallback behavior. Coverage is 0% by definition.

**Classification.** Blocking. No production sign-off should happen with zero automated coverage on money-handling and one-way state-transition logic.

**Effort.** Baseline unit tests (schema validators, approve/reject transitions including the 409 double-transition case, currency/amount validation) plus a couple of integration tests against a test DB: 2–3 days for a meaningful (not exhaustive) baseline. Ongoing coverage maintenance is continuous, not a one-time cost.

## 9. Deployment and health checks

**Gap.** There is no `Dockerfile` or other container definition, no CI/CD configuration, and no health or readiness endpoint (`/health`) anywhere in `app/routes.py`. The only documented run command uses `uvicorn --reload`, which is a dev-only auto-restart flag, not a production process manager. The default bind is loopback-only (`127.0.0.1:8000`), so an explicit `--host`/`--port` override is required for the service to be reachable at all outside the host it runs on. There is no documented graceful-shutdown behavior beyond FastAPI's default lifespan hook.

**Classification.** Blocking. This cannot be wired into any orchestrator or load balancer without a health endpoint, and there is currently no repeatable build/deploy artifact at all.

**Effort.**
- Health endpoint: 0.25 day.
- `Dockerfile` + basic CI (lint/test/build): 1–2 days.
- Full CD pipeline (environment promotion, rollback): +2–3 days depending on target platform.

## 10. Data privacy for expense data

**Gap.** Expense records — including the free-text `description` field, which can carry personal or business-sensitive content — are stored unencrypted in a local SQLite file. In this environment, `expenseflow.db` and `.env` are both currently `-rw-r--r--` (group/other readable at the OS level). There is no encryption at rest, no field-level encryption, no data-retention policy, and no deletion/export endpoint (no "right to erasure" support) — which is compounded by there being no user/auth model at all (item 1), so there's no concept of data ownership to enforce against.

On the positive side: `app/insights.py`'s `_build_summary` deliberately sends only `amount_base_minor`, `category`, and `status` to the Anthropic API for `/reports/insights` — it does **not** send the free-text `description` — which limits (though doesn't eliminate, since categories and amounts can still be sensitive in aggregate) third-party exposure of expense data.

**Classification.** Blocking for encryption at rest and file-permission hardening — not defensible for real financial/personal data. Deferrable for retention/deletion tooling until a concrete compliance driver (e.g. GDPR/local data-protection law) defines the requirement, though it should be scoped alongside the auth work in item 1 since deletion needs an owner concept to be meaningful.

**Effort.**
- File-permission hardening + encrypted-volume storage at the infra layer: 0.5–1 day (mostly infra config, assuming the target platform supports encrypted volumes).
- Retention/deletion tooling: 1–2 days, contingent on requirements being defined.
