"""Contracts for the solution speciation tool."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field, model_validator

from .common import CanonicalWaterAnalysis, RunDiagnostics, StrictFiniteBaseModel, assert_finite_payload


class SpeciationRequest(StrictFiniteBaseModel):
    """Public input contract for calculate_solution_speciation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    analysis: Dict[str, Any] = Field(..., description="Water analysis by analyte/species name.")
    ph: Optional[float] = Field(None, description="Initial pH. Alias 'pH' is accepted.")
    pH: Optional[float] = Field(None, exclude=True)
    pe: Optional[float] = Field(4.0, description="Initial pe.")

    @model_validator(mode="after")
    def name_the_house_defaults(self):
        """The pe 4 (and pH 7) the house supplies is named on the answer (review M19, waterchem.basis_defaults)."""
        from water_chemistry_mcp.waterchem.basis_defaults import record_schema_defaults

        return record_schema_defaults(self)

    temperature_celsius: Optional[float] = Field(25.0, description="Temperature in Celsius.")
    temperature: Optional[float] = Field(None, exclude=True)
    temp: Optional[float] = Field(None, exclude=True)
    pressure_atm: Optional[float] = Field(1.0, description="Pressure in atm.")
    units: Optional[str] = Field("mg/L", description="Default concentration unit.")
    charge_balance: Optional[str] = None
    density: Optional[float] = None
    redox: Optional[str] = None
    database: Optional[str] = None
    allow_extreme_ph: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        if "pH" in values and "ph" not in values:
            values["ph"] = values.pop("pH")
        for source in ("temperature", "temp", "Temperature", "Temp"):
            if source in values and "temperature_celsius" not in values:
                values["temperature_celsius"] = values.pop(source)
        return values

    @model_validator(mode="after")
    def validate_with_canonical_analysis(self) -> "SpeciationRequest":
        self.to_canonical()
        return self

    def to_canonical(self) -> CanonicalWaterAnalysis:
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        payload = self.model_dump(exclude_none=True)
        if self.pH is not None and "ph" not in payload:
            payload["ph"] = self.pH
        if self.temperature is not None and "temperature_celsius" not in payload:
            payload["temperature_celsius"] = self.temperature
        if self.temp is not None and "temperature_celsius" not in payload:
            payload["temperature_celsius"] = self.temp
        return canonicalize_water_analysis(payload)


class SpeciationResult(StrictFiniteBaseModel):
    """Public output contract for calculate_solution_speciation."""

    model_config = ConfigDict(extra="allow")

    solution_summary: Dict[str, Any] = Field(...)
    saturation_indices: Dict[str, float] = Field(...)
    # Optional because no equilibrium phases may be present for simple waters.
    phases: Optional[list[Dict[str, Any]]] = None
    element_totals_molality: Dict[str, float] = Field(...)
    species_molality: Dict[str, float] = Field(...)
    # Optional because gas output appears only when a gas phase is modeled.
    gas_phase_composition: Optional[Dict[str, Any]] = None
    # Optional because surface output appears only when surfaces are modeled.
    surface_composition: Optional[list[Dict[str, Any]]] = None
    diagnostics: RunDiagnostics = Field(...)

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "SpeciationResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "speciation_result")
        return self
