# ExpenseFlow — Handoff

Status: proof of concept, not production-hardened. This doc is for whoever picks this up next — engineering or deployment — to understand what exists, how it behaves, and what has not been built yet.

## What it does

ExpenseFlow supports one journey: **submit an expense → normalize it to a base amount → an approver approves or rejects it.**

- `POST /expenses` — submit an expense; created with `status="pending"`.
- `GET /expenses` / `GET /expenses/{id}` — list (optionally filtered by `status`/`category`) or fetch one.
- `POST /expenses/{id}/approve` / `POST /expenses/{id}/reject` — one-way transition out of `pending`.
- `GET /reports/insights` — Claude-generated natural-language summary over all stored expenses.
- A Streamlit UI (`ui/app.py`) drives all of the above over HTTP: submit form, expense table with color-coded status, approve/reject buttons, and an insights panel.

Full endpoint reference and request/response shapes are in [`../README.md`](../README.md). The original schema design intent is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## How it works

- **App structure**: a single FastAPI app (`app/main.py`) mounting one router (`app/routes.py`). No auth, no middleware beyond FastAPI defaults.
- **Data**: one table, `expenses`, via SQLAlchemy ORM (`app/models.py`) on a SQLite file (`expenseflow.db`, path hardcoded in `app/db.py` as `sqlite:///expenseflow.db`, relative to the process's working directory). `init_db()` runs `Base.metadata.create_all()` on startup — it creates missing tables but never alters existing ones.
- **Money**: stored as integer minor units end to end, never float — see [ADR-0001](adr/0001-money-as-integer-minor-units.md) for why.
- **FX conversion is currently stubbed.** `create_expense` (`app/routes.py`) sets `base_amount_minor = original_amount_minor` and `base_currency = "INR"` unconditionally, with a hardcoded `fx_rate_scaled = fx_rate_scale = 1_000_000` and `fx_source = "stub"`. There is a `TODO` in that function marking this. The schema is fully designed for a real FX lookup (`fx_rate_scaled`, `fx_rate_scale`, `fx_source`, `fx_fetched_at` all exist to record a real, reproducible rate), but no external FX API is called yet. **A non-INR expense today is recorded with the wrong base amount** (it copies the original amount as if the rate were always 1:1).
- **Insights are computed on demand, not cached.** Every call to `GET /reports/insights` re-queries every row in the table and makes a fresh call to the Anthropic Messages API (`app/insights.py`, model `claude-sonnet-4-6`, up to 2 attempts, 300 max output tokens). On any API error, timeout, or unparseable response, it degrades to a fixed fallback object rather than raising — the endpoint never 5xxs because of Claude.
- **The UI is a separate process** (`streamlit run ui/app.py`) that only talks to the API over HTTP (`API_BASE`, default `http://127.0.0.1:8000`). It holds no state of its own.

## What a deployment engineer needs to know

1. **Bind address/port are not fixed in code.** `python -m uvicorn app.main:app` defaults to `127.0.0.1:8000` (loopback only). You must pass `--host 0.0.0.0 --port <PORT>` explicitly to expose it beyond localhost, and drop `--reload` (it's a dev-only auto-restart flag) in any real deployment.

2. **Storage is a local SQLite file, not a network database.** `expenseflow.db` must live on a persistent volume, and the process must always run from the same working directory (the DB URL is a relative path). SQLite serializes writers — this does not scale to multiple app instances/workers writing concurrently. If this needs horizontal scaling, budget time to move to a networked DB (Postgres, etc.) before that becomes a bottleneck.

3. **No migrations.** Schema changes are not handled — `create_all()` only adds missing tables, it will not alter an existing `expenses` table if a column changes shape. Introduce a migration tool (e.g. Alembic) before making schema changes against data you need to keep.

4. **Secrets: only `ANTHROPIC_API_KEY`**, loaded from `.env` via `python-dotenv` + `os.environ`. Two things to fix before this repo is shared or pushed anywhere:
   - **There is currently no `.gitignore`.** `.env` (which holds a live key), `.venv/`, `expenseflow.db`, and `__pycache__/` are all untracked but not excluded — add a `.gitignore` before the first commit, or they will end up in version control.
   - If the key in the current `.env` has ever been exposed (committed, pasted, shared), rotate it.

5. **No authentication or authorization on any endpoint.** Anyone who can reach the port can submit, list, approve, or reject expenses. Put this behind an authenticating gateway/proxy before exposing it beyond localhost — it was explicitly scoped out of the brief ("no auth/user model," per `ARCHITECTURE.md`), not an oversight to silently patch.

6. **No CORS configuration.** `app/main.py` does not add `CORSMiddleware`. If a browser-based client (including a differently-hosted copy of the Streamlit UI, or any future SPA) is served from a different origin than the API, requests will be blocked by the browser until CORS is configured.

7. **No health-check endpoint.** There's no `/health` or `/ready`. Add one before wiring this into an orchestrator (Kubernetes, ECS, etc.) that expects liveness/readiness probes.

8. **Logging is not configured.** `app/insights.py` uses the stdlib `logging` module, but nothing in `app/main.py` sets handlers or levels. In practice only whatever the process host's default root logging config surfaces will show up. Configure logging explicitly if you need visibility into insight-generation failures in production.

9. **`GET /reports/insights` makes a live Anthropic API call on every request** — it is not cached or rate-limited. If this endpoint is exposed to real user traffic, consider caching (e.g. by expense-set fingerprint) or rate-limiting to control cost and latency (each call can take up to 2 attempts before falling back).

10. **No automated tests exist yet.** `python -m pytest -q` is the documented test command (see `CLAUDE.md`/`README.md`), but there are currently zero test files in the repo — there is no regression safety net for any change.

11. **The FX-conversion stub (point above, "How it works") is the biggest functional gap.** Anything downstream that assumes `base_amount_minor` reflects a real conversion — approvals, insights, reporting — is currently working off a 1:1 copy for non-INR currencies. Flag this loudly if this system is used for any real approval decision before it's fixed.

## Running it

See [`../README.md`](../README.md) for venv setup (including Windows), `.env` configuration, and the exact run/test commands.
