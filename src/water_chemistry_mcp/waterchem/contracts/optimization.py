"""Contracts for multi-reagent optimization tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload
from .dosing import DosingReagent, TargetCondition


class MultiReagentOptimizationRequest(StrictFiniteBaseModel):
    """Public input contract for optimize_multi_reagent_treatment."""

    model_config = ConfigDict(extra="forbid")

    initial_water: Dict[str, Any]
    reagents: List[DosingReagent] = Field(..., min_length=1)
    objectives: List[TargetCondition] = Field(..., min_length=1)
    optimization_strategy: str = "weighted_sum"
    grid_points: int = Field(10, ge=2, le=50)
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

        return unify_request_database(data, ('initial_water',))
    allow_precipitation: bool = True

    @model_validator(mode="after")
    def validate_initial_water(self) -> "MultiReagentOptimizationRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.initial_water)
        return self


class MultiReagentOptimizationResult(StrictFiniteBaseModel):
    """Public output contract for optimize_multi_reagent_treatment."""

    model_config = ConfigDict(extra="allow")

    strategy: str
    solver_converged: bool
    target_met: bool
    recommendation_eligible: bool = False
    recommendation_blockers: List[str] = Field(default_factory=list)
    # Optional because pareto-front strategy returns a solution set instead of one optimum.
    optimal_doses: Optional[Dict[str, float]] = None
    # Optional because some strategies expose objective details in extra strategy fields.
    objective_results: Optional[Dict[str, Any]] = None
    # Optional because pareto-front strategy does not select a single final state.
    final_state: Optional[Dict[str, Any]] = None
    # Optional because nested simulations carry their own diagnostics.
    diagnostics: Optional[RunDiagnostics] = None

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "MultiReagentOptimizationResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "multi_reagent_optimization_result")
        return self
