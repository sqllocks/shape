"""Pydantic models for the importer tests."""

from __future__ import annotations

import datetime
import enum
import uuid

from pydantic import BaseModel, Field


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Address(BaseModel):
    id: int
    city: str = Field(max_length=30)


class Item(BaseModel):
    sku: str = Field(max_length=8)
    qty: int = Field(ge=1, le=9)


class Order(BaseModel):
    id: int
    color: Color
    note: str | None = Field(default=None, max_length=25)
    placed: datetime.datetime
    token: uuid.UUID
    price: float = Field(ge=1.0, le=50.0)
    address: Address
    items: list[Item]
    tags: list[str]
