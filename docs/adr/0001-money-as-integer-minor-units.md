# 1. Money as integer minor units

## Status

Accepted (fixed by project convention in `CLAUDE.md`; implemented in `app/models.py`, `app/schemas.py`).

## Context

ExpenseFlow stores and manipulates monetary amounts: an expense's original amount, its converted base (INR) amount, and the FX rate used to convert between them. These values are persisted, returned over the API, summed/compared for approval logic, and will eventually be recomputed via a real FX conversion. We need one representation used consistently everywhere — storage, arithmetic, and the wire format — that doesn't introduce rounding drift or ambiguity about scale.

## Decision

Store every monetary amount as an **integer in the currency's smallest denomination** (minor units — paise for INR, cents for USD/EUR/GBP; JPY has no minor unit, so its "minor unit" is just whole yen). Concretely:

- `original_amount_minor` and `base_amount_minor` are `Integer` columns (`app/models.py`), with `CHECK` constraints (`original_amount_minor > 0`, `base_amount_minor >= 0`) enforced at the DB layer.
- Each currency's decimal places are tracked explicitly in `SUPPORTED_CURRENCIES: dict[str, int]` (`app/models.py`) — `INR/USD/EUR/GBP: 2`, `JPY: 0` — used to validate input and to convert to/from major units only at the UI boundary (`ui/app.py` multiplies by `10**decimals` when building the request, divides when displaying).
- FX rates are stored the same way: as an integer `fx_rate_scaled` alongside an integer `fx_rate_scale` (default `1_000_000`), never as a float, so `rate = fx_rate_scaled / fx_rate_scale` is reproducible exactly from stored data. The designed conversion (`docs/ARCHITECTURE.md`) is integer-only round-half-up: `base_amount_minor = (original_amount_minor * fx_rate_scaled + fx_rate_scale // 2) // fx_rate_scale`.
- Floats are never used for money, anywhere in the request/response/storage path (fixed explicitly in `CLAUDE.md`: "Money is stored as integer minor units, never float").

## Alternatives considered

1. **Float (Python `float` / SQL `REAL`).** Simplest to write, but binary floating point cannot represent common decimal fractions exactly (`0.1 + 0.2 != 0.3`). Errors compound across FX conversion and aggregation (e.g. summing expenses for insights), and can silently drift a stored balance over time. Rejected outright — this is the failure mode the integer convention exists to prevent.

2. **Arbitrary-precision decimal (Python `Decimal` / SQL `NUMERIC(precision, scale)`).** Exact, and arguably more "correct" in the abstract — no minor-unit bookkeeping needed. Rejected for this PoC because:
   - JSON has no native decimal type; `Decimal` values typically get serialized as strings or floats at the API boundary, pushing a parsing/precision decision onto every client instead of solving it once.
   - It requires consistent `Decimal` handling across pydantic, SQLAlchemy, and JSON serialization, which is more moving parts than a single small API needs.
   - Integer minor units are already the convention used by common real-world payment/accounting APIs (e.g. Stripe amounts in cents), so this aligns with familiar prior art rather than introducing a new pattern.

3. **String-encoded amounts (e.g. `"70.00"`).** Avoids float precision issues at the wire format, but defers all arithmetic to parsing at every consumer, and rules out DB-level `CHECK` constraints or `SUM`/comparison queries without casting first. Rejected — pushes complexity outward instead of removing it.

## Consequences

**Positive**

- No float rounding or representation error anywhere in the money path; Python `int` is arbitrary-precision and SQLite `INTEGER` is exact, so arithmetic and comparisons are unambiguous end to end.
- `CHECK` constraints on `original_amount_minor`/`base_amount_minor` catch invalid values (zero, negative) at the DB layer, not just in application code.
- A stored `fx_rate_scaled`/`fx_rate_scale` pair makes every conversion exactly reproducible later without re-calling the FX provider — useful for audits or disputes (see `docs/ARCHITECTURE.md` §1).

**Negative / things to watch**

- Every supported currency's decimal-place count must be tracked correctly and explicitly (`SUPPORTED_CURRENCIES`). Adding a currency with the wrong decimal count (e.g. treating JPY as 2 decimals instead of 0) silently produces amounts that are off by a factor of 100.
- API consumers must know amounts are minor units, not major units, since the API has no separate "display amount" field — this is a documentation/contract burden, and any client that forgets the `10**decimals` conversion will be off by 100x (or more, for non-2-decimal currencies).
- This convention only pays off if all arithmetic on the money fields stays integer-only. As of this writing, `create_expense` (`app/routes.py`) bypasses the intended rounding logic entirely — it hardcodes a 1:1 stub rate rather than calling the designed `convert_to_base`-style integer division. The representation is correct; the conversion logic that's supposed to use it isn't implemented yet. That gap is tracked in [`../HANDOFF.md`](../HANDOFF.md), not hidden by this ADR.
