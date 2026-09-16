"""Pydantic v2 request and response models for ExpenseFlow."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import SUPPORTED_CURRENCIES

ExpenseStatus = Literal["pending", "approved", "rejected"]


class ExpenseCreate(BaseModel):
    """Request body for POST /expenses."""

    description: str
    category: str
    original_amount_minor: int
    original_currency: str
    expense_date: date

    @field_validator("original_amount_minor")
    @classmethod
    def amount_must_be_positive(cls, value: int) -> int:
        """Reject zero/negative amounts; money is always a positive claim."""
        if value <= 0:
            raise ValueError("original_amount_minor must be a positive integer")
        return value

    @field_validator("original_currency")
    @classmethod
    def currency_must_be_supported(cls, value: str) -> str:
        """Require a 3-letter code from the supported allow-list, normalized to uppercase."""
        code = value.upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("original_currency must be a 3-letter code")
        if code not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency code: {code}")
        return code


class ExpenseRead(BaseModel):
    """Response body representing a stored expense, built directly from the ORM row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    category: str
    original_amount_minor: int
    original_currency: str
    base_amount_minor: int
    base_currency: str
    fx_rate_scaled: int
    fx_rate_scale: int
    fx_source: str
    fx_fetched_at: datetime
    status: ExpenseStatus
    expense_date: date
    created_at: datetime
    updated_at: datetime
