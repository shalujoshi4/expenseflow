"""Streamlit front end for ExpenseFlow: submit expenses, list them, and view spending insights."""

import os
from datetime import date

import httpx
import pandas as pd
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
CURRENCY_DECIMALS = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
STATUS_COLORS = {"pending": "#fff3cd", "approved": "#d4edda", "rejected": "#f8d7da"}


def _friendly_error(error: httpx.HTTPError) -> str:
    """Turn an httpx error into a short, user-facing message instead of a stack trace."""
    if isinstance(error, httpx.ConnectError):
        return f"Could not reach the ExpenseFlow API at {API_BASE}. Is it running?"
    if isinstance(error, httpx.TimeoutException):
        return "The request to the ExpenseFlow API timed out."
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json().get("detail", "")
        except ValueError:
            detail = error.response.text
        return f"API returned {error.response.status_code}: {detail}"
    return f"Could not reach the ExpenseFlow API: {error}"


def submit_expense(payload: dict) -> dict | None:
    """POST a new expense to the API. Returns the created expense, or None on failure."""
    try:
        response = httpx.post(f"{API_BASE}/expenses", json=payload, timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        st.error(_friendly_error(error))
        return None


def fetch_expenses() -> list[dict] | None:
    """GET all expenses from the API. Returns None on failure."""
    try:
        response = httpx.get(f"{API_BASE}/expenses", timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        st.error(_friendly_error(error))
        return None


def decide_expense(expense_id: int, action: str) -> dict | None:
    """POST an approve/reject decision for an expense. Returns the updated expense, or None on failure."""
    try:
        response = httpx.post(f"{API_BASE}/expenses/{expense_id}/{action}", timeout=10.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        st.error(_friendly_error(error))
        return None


def fetch_insights() -> dict | None:
    """GET spending insights from the API. Returns None on failure."""
    try:
        response = httpx.get(f"{API_BASE}/reports/insights", timeout=30.0)
        response.raise_for_status()
        return response.json().get("insight")
    except httpx.HTTPError as error:
        st.error(_friendly_error(error))
        return None


st.set_page_config(page_title="ExpenseFlow")
st.title("ExpenseFlow")

st.header("Submit an expense")
with st.form("submit_expense", clear_on_submit=True):
    amount = st.number_input("Amount", min_value=0.01, step=0.01, format="%.2f")
    currency = st.selectbox("Currency", list(CURRENCY_DECIMALS))
    category = st.text_input("Category")
    description = st.text_input("Description")
    expense_date = st.date_input("Expense date", value=date.today())
    submitted = st.form_submit_button("Submit expense")

if submitted:
    if not category or not description:
        st.warning("Category and description are required.")
    else:
        decimals = CURRENCY_DECIMALS[currency]
        payload = {
            "description": description,
            "category": category,
            "original_amount_minor": round(amount * (10**decimals)),
            "original_currency": currency,
            "expense_date": expense_date.isoformat(),
        }
        created = submit_expense(payload)
        if created is not None:
            st.success(f"Expense #{created['id']} submitted.")

st.header("Expenses")
expenses = fetch_expenses()
if expenses:
    expenses_df = pd.DataFrame(expenses)
    styled_expenses = expenses_df.style.map(
        lambda status: f"background-color: {STATUS_COLORS.get(status, '')}", subset=["status"]
    )
    st.dataframe(styled_expenses, use_container_width=True)
elif expenses is not None:
    st.info("No expenses yet.")

st.header("Approve or reject expenses")
pending_expenses = [expense for expense in (expenses or []) if expense["status"] == "pending"]
if pending_expenses:
    for expense in pending_expenses:
        description_col, amount_col, approve_col, reject_col = st.columns([3, 2, 1, 1])
        description_col.write(f"#{expense['id']} — {expense['description']} ({expense['category']})")
        amount_col.write(f"{expense['base_amount_minor'] / 100:.2f} {expense['base_currency']}")
        if approve_col.button("Approve", key=f"approve_{expense['id']}"):
            if decide_expense(expense["id"], "approve") is not None:
                st.rerun()
        if reject_col.button("Reject", key=f"reject_{expense['id']}"):
            if decide_expense(expense["id"], "reject") is not None:
                st.rerun()
else:
    st.info("No pending expenses to review.")

st.header("Insights")
if st.button("Generate insights"):
    insight = fetch_insights()
    if insight is not None:
        st.subheader(insight.get("summary", ""))
        for bullet in insight.get("bullets", []):
            st.markdown(f"- {bullet}")
