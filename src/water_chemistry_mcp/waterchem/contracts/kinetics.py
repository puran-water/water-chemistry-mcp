"""Contracts for kinetic reaction simulations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import ConfigDict, Field, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class KineticReaction(StrictFiniteBaseModel):
    """One PHREEQC KINETICS reaction entry."""

    name: str
    formula: Optional[Union[str, Dict[str, Union[float, int]]]] = None
    parameters: Optional[Dict[str, Any]] = None
    custom_kinetics_line: Optional[str] = None


class KineticRateDefinition(StrictFiniteBaseModel):
    """One PHREEQC RATES definition."""

    name: str
    rate_law: str


class KineticReactionDefinition(StrictFiniteBaseModel):
    """PHREEQC RATES/KINETICS definition."""

    rates_block_string: Optional[str] = None
    kinetics_block_string: Optional[str] = None
    reactions: Optional[List[KineticReaction]] = None
    rates: Optional[List[KineticRateDefinition]] = None

    @model_validator(mode="after")
    def validate_kinetics(self) -> "KineticReactionDefinition":
        if not (self.kinetics_block_string or self.reactions):
            raise ValueError("kinetic_reactions requires kinetics_block_string or reactions")
        return self


class TimeStepDefinition(StrictFiniteBaseModel):
    """Kinetic simulation time-step definition."""

    model_config = ConfigDict(populate_by_name=True)

    time_values: Optional[List[float]] = Field(None, alias="values")
    units: str = "seconds"
    count: Optional[int] = None
    duration: Optional[float] = None
    duration_units: str = "seconds"


class KineticReactionRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_kinetic_reaction."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    kinetic_reactions: KineticReactionDefinition
    time_steps: TimeStepDefinition
    allow_precipitation: bool = True
    equilibrium_minerals: Optional[List[str]] = None
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_solution(self) -> "KineticReactionRequest":
        self.initial_canonical()
        return self

    def initial_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = True
        return canonicalize_water_analysis(payload)


class KineticReactionResult(StrictFiniteBaseModel):
    """Public output contract for simulate_kinetic_reaction."""

    model_config = ConfigDict(extra="allow")

    final_state: Dict[str, Any]
    diagnostics: RunDiagnostics = Field(...)
    # Optional because only failed legacy-compatible payloads include an inline error.
    error: Optional[str] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "KineticReactionResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "kinetic_reaction_result")
        return self


class KineticTimeSeriesResult(StrictFiniteBaseModel):
    """Public output contract for kinetic time-series simulations."""

    model_config = ConfigDict(extra="allow")

    time_series: List[Dict[str, Any]]
    final_state: Dict[str, Any]
    time_values: List[float] = Field(default_factory=list)
    time_units: str = "seconds"
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "KineticTimeSeriesResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "kinetic_time_series_result")
        return self
