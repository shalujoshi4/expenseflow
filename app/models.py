"""ORM models and currency reference data for ExpenseFlow."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SUPPORTED_CURRENCIES: dict[str, int] = {
    "INR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
}
"""Supported currency codes mapped to their minor-unit decimal places."""


class Expense(Base):
    """An expense claim: its original submission, its INR conversion, and its approval status."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("original_amount_minor > 0", name="ck_original_amount_positive"),
        CheckConstraint("base_amount_minor >= 0", name="ck_base_amount_non_negative"),
        CheckConstraint(
            "status in ('pending', 'approved', 'rejected')", name="ck_status_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)

    original_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    base_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    fx_rate_scaled: Mapped[int] = mapped_column(Integer, nullable=False)
    fx_rate_scale: Mapped[int] = mapped_column(Integer, nullable=False, default=1_000_000)
    fx_source: Mapped[str] = mapped_column(String(100), nullable=False)
    fx_fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
