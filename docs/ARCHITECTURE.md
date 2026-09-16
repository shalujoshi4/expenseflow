# ExpenseFlow — Architecture

This documents the agreed design for the one supported journey: **submit an expense → convert it to base currency (INR) → approve or reject it.** It follows the stack, layout, and money rules fixed in `CLAUDE.md` (integer minor units, never float; base currency INR, normalized on write).

Assumptions made where CLAUDE.md is silent:
- **FX provider**: a single function, `fetch_fx_rate()`, wraps one httpx call to an external rate API (URL/key via env vars, read with python-dotenv). Swappable without touching the rest of the design.
- **Supported currencies**: a small hardcoded dict in `app/models.py` mapping currency code → minor-unit decimal places (`INR:2, USD:2, EUR:2, GBP:2, JPY:0, ...`), used for input validation only.
- **Rate storage**: FX rate stored as an integer scaled by `1_000_000` (never a float), alongside the scale factor, so conversions are reproducible from stored data alone.
- **No auth/user model** — the brief has no login journey.

## 1. `expenses` table schema

Principle: freeze everything needed to reconstruct exactly what was submitted and exactly how it was converted, so a later-changing live FX rate never changes the meaning of a historical row.

| Column | Type | Why |
|---|---|---|
| `id` | `Integer`, PK autoincrement | Identifier for get/approve/reject. |
| `description` | `String(500)`, not null | Human-readable content; required for any list/detail view. |
| `original_amount_minor` | `Integer`, not null, `CHECK > 0` | Amount as submitted, in the *original* currency's minor units — kept separate from the base amount so the original claim survives disputes/re-display. |
| `original_currency` | `String(3)`, not null | ISO-4217 code needed to interpret `original_amount_minor`. |
| `base_amount_minor` | `Integer`, not null, `CHECK >= 0` | INR-paise value used for all approval/accounting decisions; computed once at submission ("normalized on write") and frozen. |
| `base_currency` | `String(3)`, not null, default `"INR"` | Explicit rather than assumed, so the row is self-describing. |
| `fx_rate_scaled` | `Integer`, not null | Exact rate used, as `rate * fx_rate_scale` — the audit trail; lets anyone recompute `base_amount_minor` from `original_amount_minor` without re-calling the FX API. |
| `fx_rate_scale` | `Integer`, not null, default `1000000` | Scale factor for `fx_rate_scaled`, stored explicitly rather than hardcoded. |
| `fx_source` | `String(100)`, not null | Which provider produced the rate — needed for disputes/debugging. |
| `fx_fetched_at` | `DateTime`, not null | When the rate was retrieved (point-in-time fact, distinct from row creation time). |
| `status` | `String(20)`, not null, default `"pending"`, `CHECK IN ('pending','approved','rejected')` | The one mutable field; everything else is write-once. |
| `expense_date` | `Date`, not null | Date the cost was incurred (submitter-supplied), distinct from `created_at`. |
| `created_at` | `DateTime`, not null, server default `func.now()` | Row-creation audit stamp; used for list ordering. |
| `updated_at` | `DateTime`, not null, server default `func.now()`, `onupdate=func.now()` | When the approve/reject decision landed — avoids needing a separate audit-log table for a PoC. |

Both `original_*` and `base_*` amounts are stored (not just one) because a submitter who entered "€50" must always be re-shown "€50," not only the derived "₹4,650" — otherwise disputes ("I submitted 50 EUR, not this INR figure") are unresolvable. No separate `fx_rates` table — one embedded rate per expense is a fact about that expense, not a shared entity worth normalizing in a PoC.

## 2. Endpoints

Exactly five, matching submit → convert → approve/reject. Nothing speculative added (no FX-preview endpoint, no auth).

| Method | Path | Request body | Response |
|---|---|---|---|
| `POST` | `/expenses` | `ExpenseCreate`: `{description, original_amount_minor, original_currency, expense_date}` | `201 ExpenseRead` (full row incl. `id`, both amount/currency pairs, fx fields, `status`, timestamps). `422` on bad currency/amount. `502` if the FX provider is unreachable/times out — no partial row is ever written. |
| `GET` | `/expenses/{id}` | — | `200 ExpenseRead`, `404` if missing. |
| `GET` | `/expenses?status=pending\|approved\|rejected` | — (optional query filter) | `200 list[ExpenseRead]`. Filter exists because an approver needs to see the pending queue — not speculative pagination. |
| `POST` | `/expenses/{id}/approve` | empty | `200 ExpenseRead` with `status="approved"`. `409` if `status != "pending"`. |
| `POST` | `/expenses/{id}/reject` | empty | `200 ExpenseRead` with `status="rejected"`. `409` if `status != "pending"`. |

Route function names: `create_expense`, `get_expense`, `list_expenses`, `approve_expense`, `reject_expense`.

## 3. File layout

Fixed by `CLAUDE.md` — no new files.

- **`app/db.py`** — `engine`, `SessionLocal`, `Base`, `get_db()` FastAPI dependency, `init_db()` (`Base.metadata.create_all`) called once from `main.py` startup. No Alembic (acceptable for a PoC).
- **`app/models.py`** — `Expense` ORM model (§1); `SUPPORTED_CURRENCIES: dict[str, int]` (code → decimal places), used by both validation and conversion.
- **`app/schemas.py`** — `ExpenseCreate`, `ExpenseRead` (`model_config = ConfigDict(from_attributes=True)`), `ExpenseStatus = Literal["pending","approved","rejected"]`.
- **`app/routes.py`** — the five endpoint functions (each with a docstring); `fetch_fx_rate(from_currency, to_currency) -> tuple[rate_scaled, rate_scale, source, fetched_at]` (httpx call + timeout handling); `convert_to_base(amount_minor, rate_scaled, rate_scale) -> int` as a standalone pure function, unit-testable in isolation.
- **`app/main.py`** — `FastAPI()` app, `include_router`, startup hook calling `init_db()`, `load_dotenv()` at import time.

`fetch_fx_rate`/`convert_to_base` live in `routes.py` rather than a new `app/fx.py`, to respect the fixed 5-file layout — revisit only if FX logic needs to be split out later.

## 4. Edge-case decisions

1. **External FX call fails/times out/is slow.** `fetch_fx_rate` uses an explicit `httpx.Client(timeout=5.0)`. On timeout/connection/HTTP error, the route raises `HTTPException(502)`. The DB insert only happens *after* a successful fetch, in the same request — so a failed call leaves zero trace (no half-written "fx-pending" row, no extra status value). Retry is left to the caller.

2. **Rounding across currencies with different decimal places (e.g. JPY 0 decimals vs INR 2).** All arithmetic is integer-only: `base_amount_minor = (original_amount_minor * rate_scaled + rate_scale // 2) // rate_scale` (round-half-up, using Python's arbitrary-precision ints — float never enters). The per-currency decimals table is used only to validate input, not the conversion math, since minor units are already integers on both sides. The stored `fx_rate_scaled`/`fx_rate_scale` make every conversion exactly reproducible later.

3. **Double-transition (approve/reject an already-decided expense).** Both `approve_expense` and `reject_expense` check `status == "pending"` before mutating; otherwise `409 Conflict` naming the current status. Backed by a DB-level `CHECK` constraint on `status` as defense in depth. No un-approve/reopen endpoint — the brief's transition is one-way.

*Honorable mention:* unsupported currency codes are rejected at `422` via pydantic validation before any FX call is made; an INR-to-INR submission takes the same code path as any other currency with `rate_scaled == rate_scale`, avoiding a special-cased second path that could drift from the general one.
