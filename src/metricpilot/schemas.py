"""Strict boundaries for imported data, persisted runs, and published claims."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ImportDataset(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    csv_text: str = Field(min_length=1, max_length=1_048_576)


class CreateRun(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=3, max_length=2000)


class Claim(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    query_id: str = Field(min_length=1, max_length=40)
    row: int = Field(ge=0, le=199, strict=True)
    column: str = Field(min_length=1, max_length=128)
    value: int | float | str | None
    unit: str = Field(default="", max_length=20)

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value):
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError("Claim string exceeds cell limit")
        return value


class Chart(StrictModel):
    query_id: str = Field(min_length=1, max_length=40)
    label_column: str = Field(min_length=1, max_length=128)
    value_column: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=120)


class Report(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1800)
    claims: list[Claim] = Field(default_factory=list, max_length=12)
    chart: Chart | None = None
    limitations: list[str] = Field(default_factory=list, max_length=8)
    verified: bool = False

    @field_validator("limitations")
    @classmethod
    def short_limitations(cls, values):
        if any(len(value) > 500 for value in values):
            raise ValueError("Limitations must be concise")
        return values
