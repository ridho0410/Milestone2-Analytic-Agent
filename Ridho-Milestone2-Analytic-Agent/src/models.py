"""Kontrak proposal SQL, bukti hasil kueri, dan percakapan tersimpan."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SQLQuery(StrictModel):
    sql: str = Field(min_length=1, max_length=20000, description="One PostgreSQL SELECT from one gold table. Do not filter cohort dates in WHERE; the application binds start/end independently.")
    params: dict[str, StrictStr | StrictInt | StrictFloat | StrictBool | None] = Field(default_factory=dict)
    start: date = Field(description="Inclusive requested calendar start; use context/dataset only if no period was requested.")
    end: date = Field(description="Inclusive requested calendar end. Preserve September 30 when the user requests September; do not clip it to dataset September 20.")
    date_basis: Literal["order_date", "delivery_date"] = "order_date"

    @model_validator(mode="after")
    def validate_query(self) -> SQLQuery:
        if self.start > self.end:
            raise ValueError("Tanggal awal melebihi tanggal akhir.")
        if len(self.params) > 64:
            raise ValueError("Terlalu banyak parameter.")
        for name, value in self.params.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
                raise ValueError("Nama parameter tidak valid.")
            if isinstance(value, str) and len(value) > 1000:
                raise ValueError("Nilai parameter terlalu panjang.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Parameter numerik harus finite.")
        return self


class QueryReview(StrictModel):
    approved: bool
    feedback: str = Field(max_length=1000)


class NarrativeDraft(StrictModel):
    """Model menyusun kalimat terverifikasi yang utuh tanpa menulis ulang faktanya."""
    paragraphs: list[list[StrictStr]] = Field(min_length=1, max_length=20)


class Evidence(StrictModel):
    query: SQLQuery
    rows: list[dict[str, Any]]
    tables: list[str]
    trusted_metrics: list[str] = Field(default_factory=list)
    bucket_unit: str = ""
    partial: bool = False
    selection_limited: bool = False


class TurnContext(StrictModel):
    query: SQLQuery | None = None
    legacy: dict[str, Any] | None = None

    def to_json(self) -> str:
        return self.model_dump_json() if self.query else ""

    @classmethod
    def from_json(cls, raw: str | None) -> TurnContext:
        try:
            data = json.loads(raw) if raw and len(raw) <= 30000 else {}
            if isinstance(data, dict) and isinstance(data.get("plan"), dict):
                return cls(legacy=data["plan"])
            return cls.model_validate(data)
        except (ValueError, TypeError):
            return cls()


class AgentResult(StrictModel):
    answer: str
    outcome: Literal["answered", "clarify", "refused", "no_data"]
    data_basis: str = ""
    context: TurnContext = Field(default_factory=TurnContext)
    audit: dict[str, Any] = Field(default_factory=dict)
    chart: dict[str, Any] | None = None
    table: list[dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = {"answer": self.answer, "outcome": self.outcome, "data_basis": self.data_basis}
        if self.chart:
            result["chart"] = self.chart
        return result
