"""Contracts for gas-phase interaction simulations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class GasPhaseDefinition(StrictFiniteBaseModel):
    """PHREEQC GAS_PHASE definition."""

    type: str = "fixed_pressure"
    initial_components: Dict[str, float]
    fixed_pressure_atm: float = 1.0
    initial_volume_liters: float = 1.0
    temperature_celsius: float = 25.0

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        key = value.lower().strip().replace("-", "_").replace(" ", "_")
        mapping = {
            "pressure": "fixed_pressure",
            "constant_pressure": "fixed_pressure",
            "fixed_pressure": "fixed_pressure",
            "volume": "fixed_volume",
            "constant_volume": "fixed_volume",
            "fixed_volume": "fixed_volume",
        }
        if key not in mapping:
            raise ValueError("Gas phase type must be fixed_pressure or fixed_volume")
        return mapping[key]

    @field_validator("initial_components")
    @classmethod
    def normalize_components(cls, value: Dict[str, float]) -> Dict[str, float]:
        if not value:
            raise ValueError("gas_phase.initial_components must contain at least one component")
        mapping = {
            "co2": "CO2(g)",
            "carbon dioxide": "CO2(g)",
            "o2": "O2(g)",
            "oxygen": "O2(g)",
            "n2": "N2(g)",
            "nitrogen": "N2(g)",
            "ch4": "CH4(g)",
            "methane": "CH4(g)",
            "h2": "H2(g)",
            "hydrogen": "H2(g)",
            "h2s": "H2S(g)",
            "hydrogen sulfide": "H2S(g)",
            "nh3": "NH3(g)",
            "ammonia": "NH3(g)",
        }
        normalized = {}
        for gas, amount in value.items():
            if amount < 0:
                raise ValueError(f"Gas component {gas} must be non-negative")
            key = gas.lower().strip().replace("_g", "")
            formula = mapping.get(key, gas.replace("_g", "(g)"))
            if "(g)" not in formula:
                formula = f"{formula}(g)"
            normalized[formula] = amount
        return normalized

    @model_validator(mode="after")
    def validate_physical_values(self) -> "GasPhaseDefinition":
        if self.fixed_pressure_atm <= 0:
            raise ValueError("fixed_pressure_atm must be positive")
        if self.initial_volume_liters <= 0:
            raise ValueError("initial_volume_liters must be positive")
        if self.temperature_celsius <= -273.15:
            raise ValueError("temperature_celsius must be greater than -273.15 C")
        return self


class GasPhaseRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_gas_phase_interaction."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    gas_phase: GasPhaseDefinition
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_solution(self) -> "GasPhaseRequest":
        self.initial_canonical()
        return self

    def initial_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = True
        return canonicalize_water_analysis(payload)


class GasPhaseResult(StrictFiniteBaseModel):
    """Public output contract for simulate_gas_phase_interaction."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    # Optional because some databases expose gas output only in selected-output extras.
    gas_phase_composition: Optional[Dict[str, Any]] = None
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "GasPhaseResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "gas_phase_result")
        return self
