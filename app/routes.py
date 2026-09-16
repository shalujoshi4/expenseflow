"""API routes for ExpenseFlow: submit, list, view, approve, and reject expenses."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.db import get_db
from app.insights import generate_insight
from app.models import Expense
from app.schemas import ExpenseCreate, ExpenseRead, ExpenseStatus

router = APIRouter()


@router.post("/expenses", response_model=ExpenseRead, status_code=http_status.HTTP_201_CREATED)
def create_expense(expense: ExpenseCreate, db: Session = Depends(get_db)) -> Expense:
    """Create a new expense with status "pending"."""
    # TODO: replace this stub with a real FX conversion (fetch_fx_rate + convert_to_base
    # per ARCHITECTURE.md). For now base_amount_minor is set equal to original_amount_minor
    # at a stub 1:1 rate.
    db_expense = Expense(
        description=expense.description,
        category=expense.category,
        original_amount_minor=expense.original_amount_minor,
        original_currency=expense.original_currency,
        base_amount_minor=expense.original_amount_minor,
        base_currency="INR",
        fx_rate_scaled=1_000_000,
        fx_rate_scale=1_000_000,
        fx_source="stub",
        fx_fetched_at=datetime.now(timezone.utc),
        status="pending",
        expense_date=expense.expense_date,
    )
    db.add(db_expense)
    db.commit()
    db.refresh(db_expense)
    return db_expense


@router.get("/expenses", response_model=list[ExpenseRead])
def list_expenses(
    status: ExpenseStatus | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
) -> list[Expense]:
    """List expenses, optionally filtered by status and/or category."""
    query = db.query(Expense)
    if status is not None:
        query = query.filter(Expense.status == status)
    if category is not None:
        query = query.filter(Expense.category == category)
    return query.all()


@router.get("/expenses/{expense_id}", response_model=ExpenseRead)
def get_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Get a single expense by id."""
    db_expense = db.get(Expense, expense_id)
    if db_expense is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return db_expense


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseRead)
def approve_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Approve a pending expense."""
    db_expense = db.get(Expense, expense_id)
    if db_expense is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if db_expense.status != "pending":
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Expense is already {db_expense.status}",
        )
    db_expense.status = "approved"
    db.commit()
    db.refresh(db_expense)
    return db_expense


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseRead)
def reject_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Reject a pending expense."""
    db_expense = db.get(Expense, expense_id)
    if db_expense is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if db_expense.status != "pending":
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Expense is already {db_expense.status}",
        )
    db_expense.status = "rejected"
    db.commit()
    db.refresh(db_expense)
    return db_expense


@router.get("/reports/insights")
def get_insights(db: Session = Depends(get_db)) -> dict[str, object]:
    """Return a structured spending insight (summary + bullets) across all expenses."""
    expenses = db.query(Expense).all()
    expense_dicts = [
        {
            "amount_base_minor": expense.base_amount_minor,
            "category": expense.category,
            "status": expense.status,
        }
        for expense in expenses
    ]
    return {"insight": generate_insight(expense_dicts)}
