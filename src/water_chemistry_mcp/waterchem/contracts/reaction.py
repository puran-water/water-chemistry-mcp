"""Contracts for chemical-addition simulations."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from water_chemistry_mcp.waterchem.formulas import normalize_formula_name

from .common import RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class Reactant(StrictFiniteBaseModel):
    """Chemical reactant to add to a solution."""

    formula: str
    amount: float
    units: str = "mmol"

    @field_validator("formula")
    @classmethod
    def sanitize_formula(cls, value: str) -> str:
        normalized = normalize_formula_name(value)
        sanitized = re.sub(r"[^A-Za-z0-9\(\)\+\-\.:·]", "", normalized)
        formula_map = {
            "CO2g": "CO2(g)",
            "CO2_g": "CO2(g)",
            "NaOH(s)": "NaOH",
            "CaOH2": "Ca(OH)2",
            "Ca(OH)2(s)": "Ca(OH)2",
            "CaCO3(s)": "CaCO3",
            "NaHCO3(s)": "NaHCO3",
        }
        return normalize_formula_name(formula_map.get(sanitized, sanitized))

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Reactant amount must be positive")
        return value

    @field_validator("units")
    @classmethod
    def normalize_reactant_units(cls, value: str) -> str:
        text = (value or "mmol").strip().lower()
        mapping = {
            "g": "g",
            "gram": "g",
            "grams": "g",
            "mg": "mg",
            "milligram": "mg",
            "milligrams": "mg",
            "ug": "ug",
            "microgram": "ug",
            "micrograms": "ug",
            "mol": "mol",
            "mole": "mol",
            "moles": "mol",
            "mmol": "mmol",
            "millimol": "mmol",
            "millimole": "mmol",
            "millimoles": "mmol",
            "umol": "umol",
            "micromol": "umol",
            "micromole": "umol",
            "micromoles": "umol",
            "mg/l": "mg/L",
            "mmol/l": "mmol/L",
            "mg/kgw": "mg/kgw",
            "mmol/kgw": "mmol/kgw",
        }
        return mapping.get(text, value)


class ChemicalAdditionRequest(StrictFiniteBaseModel):
    """Public input contract for simulate_chemical_addition."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any]
    reactants: List[Reactant] = Field(default_factory=list)
    allow_precipitation: bool = True
    equilibrium_minerals: Optional[List[str]] = None
    kinetic_parameters: Optional[Dict[str, Any]] = None
    database: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    temperature_celsius: Optional[float] = None
    pressure_atm: Optional[float] = None
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_initial_solution(self) -> "ChemicalAdditionRequest":
        self.initial_canonical()
        return self

    def initial_canonical(self):
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = dict(self.initial_solution)
        if self.temperature_celsius is not None:
            payload["temperature_celsius"] = self.temperature_celsius
        if self.pressure_atm is not None:
            payload["pressure_atm"] = self.pressure_atm
        if self.allow_extreme_ph:
            payload["allow_extreme_ph"] = self.allow_extreme_ph
        return canonicalize_water_analysis(payload)


class ChemicalAdditionResult(StrictFiniteBaseModel):
    """Public output contract for simulate_chemical_addition."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    # Optional because no equilibrium phases may appear in no-precipitation runs.
    phases: Optional[List[Dict[str, Any]]] = None
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    # Optional because precipitation is caller-controlled and may not occur.
    precipitated_phases: Optional[Dict[str, float]] = None
    # Optional because profiles exist only for kinetics-enabled runs.
    kinetic_profiles: Optional[List[Dict[str, Any]]] = None
    # Optional for legacy parser payloads; handlers default it to False on standard runs.
    kinetic_modeling_used: Optional[bool] = False
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "ChemicalAdditionResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "chemical_addition_result")
        return self
