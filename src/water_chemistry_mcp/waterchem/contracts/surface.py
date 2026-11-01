"""Contracts for surface complexation simulations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class SurfaceDefinition(StrictFiniteBaseModel):
    """PHREEQC SURFACE definition."""

    surface_block_string: Optional[str] = None
    sites_block_string: Optional[str] = None
    sites_info: Optional[List[Dict[str, Any]]] = None
    sites: Optional[List[Any]] = None
    site_density: Optional[float] = None
    specific_area: Optional[float] = None
    mass: Optional[float] = None
    equilibrate_with_solution_number: Optional[int] = None
    surface_initialization_mode: str = "mass_balanced_batch"
    surface_dataset_id: Optional[str] = None
    surface_basis: Optional[str] = None
    parameter_interpretation: Optional[str] = None
    oxide_history_class: Optional[str] = None
    site_ratio_policy: Optional[str] = None
    source: Optional[str] = None
    legacy_default_values_invalidated: Optional[bool] = None

    @model_validator(mode="after")
    def validate_surface(self) -> "SurfaceDefinition":
        if not (self.surface_block_string or self.sites_info or self.sites):
            raise ValueError("surface_definition requires surface_block_string, sites_info, or sites")
        if self.equilibrate_with_solution_number is not None:
            if self.equilibrate_with_solution_number <= 0:
                raise ValueError("equilibrate_with_solution_number must be positive")
            if self.surface_initialization_mode != "pre_equilibrated_boundary":
                raise ValueError(
                    "-equilibrate represents a pre-equilibrated infinite-solution boundary; "
                    "set surface_initialization_mode='pre_equilibrated_boundary' explicitly"
                )
        elif self.surface_initialization_mode != "mass_balanced_batch":
            raise ValueError(
                "surface_initialization_mode must be 'mass_balanced_batch' when no "
                "equilibrate_with_solution_number is supplied"
            )
        return self


class SurfaceInteractionRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_surface_interaction."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    surface_definition: SurfaceDefinition
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_solution(self) -> "SurfaceInteractionRequest":
        self.initial_canonical()
        return self

    def initial_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = True
        return canonicalize_water_analysis(payload)


class SurfaceInteractionResult(StrictFiniteBaseModel):
    """Public output contract for simulate_surface_interaction."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    surface_adsorbed_moles: Dict[str, float] = Field(default_factory=dict)
    system_element_moles: Dict[str, float] = Field(default_factory=dict)
    # Optional because PHREEQC selected output may omit detailed surface composition.
    surface_composition: Optional[List[Dict[str, Any]]] = None
    surface_sites: Dict[str, Any] = Field(...)
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "SurfaceInteractionResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "surface_interaction_result")
        return self
