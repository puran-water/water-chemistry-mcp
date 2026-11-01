"""Contracts for batch scenario processing."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class BatchScenario(StrictFiniteBaseModel):
    """One batch-processing scenario."""

    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    type: str = "chemical_addition"


class BatchScenariosRequest(StrictFiniteBaseModel):
    """Public input contract for batch_process_scenarios."""

    model_config = ConfigDict(extra="forbid")

    base_solution: Dict[str, Any] = Field(..., description="Initial water analysis shared by all scenarios.")
    scenarios: List[BatchScenario] = Field(..., min_length=1)
    parallel_limit: int = Field(10, ge=1, le=100)
    output_format: str = "summary"

    @field_validator("output_format")
    @classmethod
    def validate_output_format(cls, value: str) -> str:
        if value not in {"summary", "full"}:
            raise ValueError("output_format must be 'summary' or 'full'")
        return value

    @model_validator(mode="after")
    def validate_base_solution(self) -> "BatchScenariosRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.base_solution)
        return self


class BatchScenariosResult(StrictFiniteBaseModel):
    """Public output contract for batch_process_scenarios."""

    model_config = ConfigDict(extra="allow")

    # Optional because summary mode and full mode expose different top-level shapes.
    summary: Optional[Dict[str, Any]] = None
    # Optional because full mode returns results instead of summary details.
    details: Optional[List[Dict[str, Any]]] = None
    # Optional because summary mode returns summary/details instead of results.
    results: Optional[List[Dict[str, Any]]] = None
    # Optional because batch orchestration currently has no single PHREEQC run diagnostic.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "BatchScenariosResult":
        if self.summary is None and self.results is None:
            raise ValueError("Batch output requires either summary/details or results")
        assert_finite_payload(self.model_dump(exclude_none=True), "batch_result")
        return self
