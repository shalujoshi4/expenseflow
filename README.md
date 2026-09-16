# ExpenseFlow

A small expense submission and approval API (proof of concept, not production).

The supported journey: submit an expense → it's normalized to a base amount → an approver approves or rejects it. A Streamlit front end and a Claude-powered spending-insights endpoint are layered on top of that core API.

## What it does

- `POST /expenses` — submit an expense in any supported currency. It is stored in the database with `status="pending"`.
- `GET /expenses` — list expenses, optionally filtered by `status` and/or `category`.
- `GET /expenses/{id}` — fetch a single expense.
- `POST /expenses/{id}/approve` / `POST /expenses/{id}/reject` — move a pending expense to `approved` or `rejected`. Only valid from `pending`.
- `GET /reports/insights` — ask Claude for a short natural-language summary of spending across all stored expenses.
- A Streamlit UI (`ui/app.py`) that submits expenses, lists them with color-coded status, lets an approver approve/reject pending ones, and shows generated insights.

**Note on FX conversion:** the code currently stores `base_amount_minor` as a 1:1 stub of `original_amount_minor` with `base_currency` fixed to `"INR"` and `fx_source="stub"` (see the `TODO` in `app/routes.py::create_expense`). It does not yet call a real FX rate API, even though `httpx` is in the stack for that purpose and the schema (`fx_rate_scaled`, `fx_rate_scale`, `fx_source`, `fx_fetched_at`) is already designed to record a real rate.

## Stack

- Python 3.12
- FastAPI + Uvicorn (ASGI server)
- SQLAlchemy ORM on SQLite (`expenseflow.db`)
- pydantic v2 request/response models
- httpx (used by the Streamlit UI to call the API; reserved for the real FX call)
- anthropic SDK (`app/insights.py`) for the `/reports/insights` endpoint
- Streamlit + pandas for the front end (`ui/app.py`)
- python-dotenv for loading environment variables
- pytest for tests

## Project layout

```
app/
  main.py      FastAPI app, lifespan hook that calls init_db()
  db.py        engine, SessionLocal, Base, get_db(), init_db()
  models.py    Expense ORM model, SUPPORTED_CURRENCIES
  schemas.py   ExpenseCreate, ExpenseRead, ExpenseStatus
  routes.py    all API endpoints
  insights.py  Claude-backed spending insight generation
ui/
  app.py       Streamlit front end
docs/
  ARCHITECTURE.md   design notes for the expenses table and endpoints
```

## Setup on Windows

Requires Python 3.12 on your PATH (`py -3.12 --version` to check).

```powershell
# from the project root, e.g. C:\path\to\ExpenseFlow
py -3.12 -m venv .venv
.venv\Scripts\activate

pip install --upgrade pip
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic python-dotenv httpx anthropic streamlit pandas pytest
```

There is no `requirements.txt` in the repo yet — the command above installs exactly the packages the code imports directly (`app/*.py` and `ui/app.py`).

To leave the virtual environment later: `deactivate`.

On macOS/Linux the equivalent is:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" sqlalchemy pydantic python-dotenv httpx anthropic streamlit pandas pytest
```

## Configuring `.env`

Create a `.env` file in the project root (never commit it — it holds a live secret):

```
ANTHROPIC_API_KEY=sk-ant-...
```

- `ANTHROPIC_API_KEY` — read by `app/insights.py` for the `/reports/insights` endpoint. If it's missing or invalid, the endpoint doesn't crash: `generate_insight()` catches the Anthropic API error and returns a fallback object (`{"summary": "Insights are unavailable right now. Please try again later.", "bullets": []}`).
- `API_BASE` (optional, not read from `.env` by default but honored if set as an environment variable) — base URL the Streamlit UI uses to reach the API. Defaults to `http://127.0.0.1:8000` if unset.

`load_dotenv()` is called at import time in both `app/main.py` and `app/insights.py`, so `.env` is picked up automatically as long as you run commands from the project root.

## Running the server

Start the API (from the project root, venv activated):

```bash
python -m uvicorn app.main:app --reload
```

This serves the API at `http://127.0.0.1:8000` and interactive docs at `http://127.0.0.1:8000/docs`. On startup, `init_db()` creates the `expenses` table in `expenseflow.db` if it doesn't already exist.

Optionally, run the Streamlit UI in a second terminal (with the API already running):

```bash
streamlit run ui/app.py
```

By default it talks to `http://127.0.0.1:8000`; set `API_BASE` before launching it to point elsewhere.

## Running the tests

```bash
python -m pytest -q
```

At the time of writing there are no test files in the repo (`.pytest_cache` shows zero collected node IDs) — this is the command to use once tests are added.

## API endpoint reference

All request/response bodies are JSON. Money fields are integer minor units (e.g. paise for INR, cents for USD; JPY has 0 decimal places).

Supported currency codes (`app/models.py: SUPPORTED_CURRENCIES`): `INR` (2 decimals), `USD` (2), `EUR` (2), `GBP` (2), `JPY` (0).

`ExpenseRead` shape returned by every endpoint below that returns an expense:

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `description` | string | |
| `category` | string | |
| `original_amount_minor` | int | as submitted |
| `original_currency` | string | 3-letter code, uppercased |
| `base_amount_minor` | int | currently a 1:1 stub of `original_amount_minor` |
| `base_currency` | string | currently always `"INR"` |
| `fx_rate_scaled` | int | currently always `1000000` (stub) |
| `fx_rate_scale` | int | currently always `1000000` |
| `fx_source` | string | currently always `"stub"` |
| `fx_fetched_at` | datetime | when the (stub) rate was recorded |
| `status` | `"pending" \| "approved" \| "rejected"` | |
| `expense_date` | date | submitter-supplied |
| `created_at` | datetime | |
| `updated_at` | datetime | set on approve/reject |

---

### `POST /expenses`

Create a new expense. Always created with `status="pending"`.

Request body (`ExpenseCreate`):

```json
{
  "description": "Travel to Italy",
  "category": "Travel",
  "original_amount_minor": 7000,
  "original_currency": "EUR",
  "expense_date": "2026-09-12"
}
```

- `original_amount_minor` must be a positive integer (`422` otherwise).
- `original_currency` must be a 3-letter code in `SUPPORTED_CURRENCIES` (case-insensitive on input, normalized to uppercase; `422` if unsupported or malformed).

Responses:
- `201` — the created `ExpenseRead`.
- `422` — validation error (bad amount or currency).

### `GET /expenses`

List expenses.

Query parameters (both optional, combinable):
- `status` — one of `pending`, `approved`, `rejected`.
- `category` — exact match on category string.

Response: `200` — `list[ExpenseRead]` (empty list if none match).

### `GET /expenses/{expense_id}`

Fetch one expense.

Responses:
- `200` — `ExpenseRead`.
- `404` — no expense with that id (`{"detail": "Expense not found"}`).

### `POST /expenses/{expense_id}/approve`

Approve a pending expense (empty request body).

Responses:
- `200` — `ExpenseRead` with `status="approved"`.
- `404` — expense not found.
- `409` — expense is not currently `pending` (`{"detail": "Expense is already <status>"}`).

### `POST /expenses/{expense_id}/reject`

Reject a pending expense (empty request body). Same response shape/errors as `approve`, but sets `status="rejected"`.

### `GET /reports/insights`

Generate a spending insight over every stored expense (any status).

Response: `200`

```json
{
  "insight": {
    "summary": "Spending is concentrated in Travel this month.",
    "bullets": ["...", "...", "..."]
  }
}
```

- If there are no expenses: `{"summary": "No expenses to analyze yet.", "bullets": []}` (no Claude call made).
- If the Claude call fails, or its response isn't valid `{"summary": str, "bullets": [str, str, str]}` JSON after one retry: falls back to `{"summary": "Insights are unavailable right now. Please try again later.", "bullets": []}`.
- Uses model `claude-sonnet-4-6`, reads `ANTHROPIC_API_KEY` from the environment.
