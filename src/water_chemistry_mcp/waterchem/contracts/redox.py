"""Contracts for redox adjustment simulations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class TargetRedoxCondition(StrictFiniteBaseModel):
    """Requested redox target."""

    parameter: str = Field(..., description="pe, Eh_mV, or equilibrate_with_couple")
    value: Optional[float] = None
    couple_name: Optional[str] = None
    couple_logK_or_pressure: Optional[float] = None

    @model_validator(mode="after")
    def validate_target(self) -> "TargetRedoxCondition":
        parameter = self.parameter.lower()
        if parameter not in {"pe", "eh_mv", "equilibrate_with_couple"}:
            raise ValueError("target_redox.parameter must be pe, Eh_mV, or equilibrate_with_couple")
        if parameter in {"pe", "eh_mv"} and self.value is None:
            raise ValueError(f"target_redox.value is required for {self.parameter}")
        if parameter == "equilibrate_with_couple" and not self.couple_name:
            raise ValueError("target_redox.couple_name is required for equilibrate_with_couple")
        return self


class RedoxAdjustmentRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_redox_adjustment."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    target_redox: TargetRedoxCondition
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_solution(self) -> "RedoxAdjustmentRequest":
        self.initial_canonical()
        return self

    def initial_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = True
        return canonicalize_water_analysis(payload)


class RedoxAdjustmentResult(StrictFiniteBaseModel):
    """Public output contract for simulate_redox_adjustment."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    redox_adjustment: Dict[str, Any] = Field(...)
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "RedoxAdjustmentResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "redox_adjustment_result")
        return self
