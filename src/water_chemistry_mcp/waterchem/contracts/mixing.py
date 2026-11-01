"""Contracts for solution-mixing simulations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class SolutionMixItem(StrictFiniteBaseModel):
    """One solution and its mixing weight."""

    solution: Dict[str, Any]
    fraction: Optional[float] = None
    volume_L: Optional[float] = None

    @model_validator(mode="after")
    def validate_weight(self) -> "SolutionMixItem":
        if self.fraction is not None and self.volume_L is not None:
            raise ValueError("Provide either fraction or volume_L, not both")
        if self.fraction is not None and self.fraction < 0:
            raise ValueError("fraction must be non-negative")
        if self.volume_L is not None and self.volume_L < 0:
            raise ValueError("volume_L must be non-negative")
        self.to_canonical()
        return self

    def to_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        return canonicalize_water_analysis(self.solution)


class SolutionMixingRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_solution_mixing."""

    model_config = ConfigDict(extra="forbid")

    solutions_to_mix: List[SolutionMixItem] = Field(..., min_length=2)
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('solutions_to_mix[].solution',))
    allow_precipitation: bool = True
    equilibrium_minerals: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def allow_simplified_solutions_schema(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if "solutions_to_mix" not in values and "solutions" in values:
            structured = []
            for item in values.pop("solutions") or []:
                item_copy = dict(item)
                if "temperature" in item_copy and "temperature_celsius" not in item_copy:
                    item_copy["temperature_celsius"] = item_copy.pop("temperature")
                mix_kwargs = {}
                if "fraction" in item_copy:
                    mix_kwargs["fraction"] = item_copy.pop("fraction")
                if "volume_L" in item_copy:
                    mix_kwargs["volume_L"] = item_copy.pop("volume_L")
                structured.append({"solution": item_copy, **mix_kwargs})
            values["solutions_to_mix"] = structured
        return values

    def canonical_solutions(self):
        return [item.to_canonical() for item in self.solutions_to_mix]


class SolutionMixingResult(StrictFiniteBaseModel):
    """Public output contract for simulate_solution_mixing."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    # Optional because no phases may precipitate during conservative mixing.
    phases: Optional[List[Dict[str, Any]]] = None
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    mixed_solution: Dict[str, Any] = Field(...)
    # Optional because precipitation is caller-controlled and may not occur.
    precipitated_minerals: Optional[Dict[str, float]] = None
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "SolutionMixingResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "solution_mixing_result")
        return self
