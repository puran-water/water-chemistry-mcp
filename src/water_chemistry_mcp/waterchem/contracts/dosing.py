"""Contracts for dosing and lime-softening tools."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class TargetCondition(StrictFiniteBaseModel):
    """Single target condition for dose finding."""

    model_config = ConfigDict(extra="allow")

    parameter: str
    value: float
    units: Optional[str] = None
    mineral: Optional[str] = None
    element_or_species: Optional[str] = None
    constraint_type: Optional[str] = None
    weight: float = 1.0
    tolerance: Optional[float] = Field(None, gt=0)

    @field_validator("value", "weight", "tolerance")
    @classmethod
    def finite_numeric(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not math.isfinite(float(value)):
            raise ValueError("target numeric values must be finite")
        return value


class DosingReagent(StrictFiniteBaseModel):
    """Reagent definition for dose searches and grid optimizers."""

    model_config = ConfigDict(extra="allow")

    formula: str
    min_dose: float = 0.0
    max_dose: float = 10.0

    @field_validator("min_dose", "max_dose")
    @classmethod
    def finite_nonnegative_dose(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not math.isfinite(float(value)):
            raise ValueError("reagent dose bounds must be finite")
        if value < 0:
            raise ValueError("reagent dose bounds must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "DosingReagent":
        if self.max_dose is not None and self.max_dose < self.min_dose:
            raise ValueError("max_dose must be greater than or equal to min_dose")
        return self


class DoseSearchReagent(DosingReagent):
    """calculate_dosing_requirement's reagent: an absent ``max_dose`` is bracketed from the water
    (`tools.dosing_requirement.DOSE_BRACKET_RULE`)."""

    max_dose: Optional[float] = None


class DosingRequirementRequest(StrictFiniteBaseModel):
    """Public input contract for calculate_dosing_requirement."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    target_condition: TargetCondition
    reagent: DoseSearchReagent
    max_iterations: int = Field(30, ge=1, le=200)
    tolerance: float = Field(0.05, gt=0)
    initial_guess_mmol: float = Field(1.0, gt=0)
    # No implicit database. The MCP schema exposes this as optional and the
    # server default (phreeqc.dat) applies when unset; hardcoding minteq.dat
    # here silently overrode that and changed the caller's input basis --
    # minteq.dat reads Si as H4SiO4 (96.12) where phreeqc.dat reads it as
    # SiO2 (60.08), a 37.5% difference in silica. Name a database only where
    # the chemistry needs one (lime softening needs Brucite from minteq.dat).
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_precipitation: bool = True
    equilibrium_minerals: Optional[List[str]] = None
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_initial_solution(self) -> "DosingRequirementRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = True
        canonicalize_water_analysis(payload)
        return self


class DosingRequirementResult(StrictFiniteBaseModel):
    """Public output contract for calculate_dosing_requirement."""

    model_config = ConfigDict(extra="allow")

    required_dose_mmol_per_L: float
    final_state: Dict[str, Any]
    iterations_taken: int
    convergence_status: str
    solver_converged: bool
    target_met: bool
    solution_bracketed: bool
    recommendation_eligible: bool = False
    recommendation_blockers: List[str] = Field(default_factory=list)
    # Optional because this optimizer delegates diagnostics to final_state.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "DosingRequirementResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "dosing_requirement_result")
        return self


class EnhancedDosingRequest(StrictFiniteBaseModel):
    """Public input contract for calculate_dosing_requirement_enhanced."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    reagents: List[DosingReagent] = Field(..., min_length=1)
    objectives: List[TargetCondition] = Field(..., min_length=1)
    optimization_method: Literal["grid_search"] = "grid_search"
    grid_points: Optional[int] = Field(None, ge=2, le=50)
    max_iterations: int = Field(100, ge=1, le=10000)
    tolerance: float = Field(0.01, gt=0)
    # No implicit database. The MCP schema exposes this as optional and the
    # server default (phreeqc.dat) applies when unset; hardcoding minteq.dat
    # here silently overrode that and changed the caller's input basis --
    # minteq.dat reads Si as H4SiO4 (96.12) where phreeqc.dat reads it as
    # SiO2 (60.08), a 37.5% difference in silica. Name a database only where
    # the chemistry needs one (lime softening needs Brucite from minteq.dat).
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_precipitation: bool = True
    equilibrium_minerals: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_initial_solution(self) -> "EnhancedDosingRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.initial_solution)
        return self


class EnhancedDosingResult(StrictFiniteBaseModel):
    """Public output contract for calculate_dosing_requirement_enhanced."""

    model_config = ConfigDict(extra="allow")

    optimization_summary: Dict[str, Any] = Field(...)
    solver_converged: bool
    target_met: bool
    solution_bracketed: bool
    recommendation_eligible: bool = False
    recommendation_blockers: List[str] = Field(default_factory=list)
    # Optional because enhanced dosing returns nested final-state diagnostics.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "EnhancedDosingResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "enhanced_dosing_result")
        return self


class LimeSofteningCurveRequest(StrictFiniteBaseModel):
    """Public input contract for generate_lime_softening_curve."""

    model_config = ConfigDict(extra="forbid")

    initial_water: Dict[str, Any]
    lime_doses: List[float] = Field(..., min_length=1)
    target_hardness_mg_caco3: float = Field(85.0, ge=0)
    database: str = "minteq.dat"

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_water',))

    @field_validator("lime_doses")
    @classmethod
    def validate_lime_doses(cls, values: List[float]) -> List[float]:
        for value in values:
            if not math.isfinite(float(value)):
                raise ValueError("lime_doses must be finite")
            if value < 0:
                raise ValueError("lime_doses must be non-negative")
        return values

    @model_validator(mode="after")
    def validate_initial_water(self) -> "LimeSofteningCurveRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.initial_water)
        return self


class LimeSofteningCurveResult(StrictFiniteBaseModel):
    """Public output contract for generate_lime_softening_curve."""

    model_config = ConfigDict(extra="allow")

    curve_data: List[Dict[str, Any]]
    # Optional because a sampled curve may not bracket the requested hardness.
    optimal_dose: Optional[Dict[str, Any]] = None
    # Optional because curve points carry their own nested diagnostics.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "LimeSofteningCurveResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "lime_softening_curve_result")
        return self


class LimeSofteningDoseRequest(StrictFiniteBaseModel):
    """Public input contract for calculate_lime_softening_dose."""

    model_config = ConfigDict(extra="forbid")

    initial_water: Dict[str, Any]
    target_hardness_mg_caco3: float = Field(..., ge=0)
    database: str = "minteq.dat"

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_water',))
    reactor_sizing: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Optional SCREENING reactor-sizing add-on. When provided as {'enabled': true, 'flow_m3_d': "
            "..., 'temperature_c': 15, ...}, the equilibrium dose result is augmented with kinetic "
            "contact time / retention time: which mineral limits (Calcite for Ca removal vs Brucite "
            "Mg(OH)2 at high pH), the seeded contact time to approach the softened hardness, an optional "
            "reactor volume (V=Q*HRT), the cold-water penalty, and a Brucite seeding flag. Screening only "
            "-- inlet supersaturation is a representative input (calcite_inlet_si default 1.5, "
            "brucite_inlet_si 1.0); confirm with a jar/pilot test. Omit for the equilibrium dose only."
        ),
    )

    @model_validator(mode="after")
    def validate_initial_water(self) -> "LimeSofteningDoseRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.initial_water)
        return self


class LimeSofteningDoseResult(StrictFiniteBaseModel):
    """Public output contract for calculate_lime_softening_dose."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    element_totals_molality: Dict[str, float] = Field(...)
    optimization_summary: Dict[str, Any]
    # Optional because lime dose returns nested final-state diagnostics when available.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "LimeSofteningDoseResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "lime_softening_dose_result")
        return self
