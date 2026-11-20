from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DropCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    total_units: int = Field(gt=0, le=1_000_000)
    strategy: str = "naive"
    hold_seconds: int = Field(default=120, gt=0, le=3600)
    price_cents: int = Field(default=1000, ge=0)


class DropOut(BaseModel):
    id: UUID
    name: str
    total_units: int
    strategy: str
    hold_seconds: int
    price_cents: int
    available: int
    created_at: datetime


class HoldOut(BaseModel):
    hold_id: UUID
    drop_id: UUID
    user_id: UUID
    expires_at: datetime


class OrderOut(BaseModel):
    id: UUID
    hold_id: UUID
    drop_id: UUID
    user_id: UUID
    status: str
    amount_cents: int
    created_at: datetime
