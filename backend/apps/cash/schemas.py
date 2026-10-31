"""Validated request contracts for cash-office operations."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class CashDepositBody(BaseModel):
    """A bank deposit entered against one store business day."""

    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    deposit_reference: str = Field(min_length=1, max_length=100)
    deposited_amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    bank_account: str | None = Field(default=None, max_length=100)

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("business_date must be a valid YYYY-MM-DD date")
        return value

    @field_validator("deposit_reference")
    @classmethod
    def normalize_deposit_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("deposit_reference cannot be blank")
        return value.strip()

    @field_validator("store_id")
    @classmethod
    def normalize_store_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("store_id cannot be blank")
        return value.strip()

    @field_validator("bank_account")
    @classmethod
    def normalize_bank_account(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CashControlBody(BaseModel):
    """A cash declaration scoped to a store, register, cashier, and tender."""

    store_id: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    register_id: str | None = Field(default=None, max_length=50)
    cashier_id: str | None = Field(default=None, max_length=100)
    tender_type: str = Field(default="Cash", min_length=1, max_length=50)
    declared_cash: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    paid_out: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)
    safe_drop: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("business_date must be a valid YYYY-MM-DD date")
        return value

    @field_validator("store_id")
    @classmethod
    def normalize_store_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("store_id cannot be blank")
        return value.strip()

    @field_validator("register_id", "cashier_id")
    @classmethod
    def normalize_optional_identifier(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("tender_type")
    @classmethod
    def normalize_tender_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tender_type cannot be blank")
        return value.strip()
