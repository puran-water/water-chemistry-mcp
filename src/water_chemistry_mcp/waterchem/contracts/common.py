"""Common Pydantic contracts for water chemistry tools."""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Optional

from puran_mcp_support.engineering.pydantic_base import StrictFiniteBaseModel
from puran_mcp_support.engineering.safe_paths import redact_paths
from pydantic import ConfigDict, Field, field_validator, model_validator


class ConcentrationUnit(str, Enum):
    """Supported concentration units at the public contract boundary."""

    MG_L = "mg/L"
    UG_L = "ug/L"
    MMOL_L = "mmol/L"
    MOL_L = "mol/L"
    MG_L_AS_CACO3 = "mg/L as CaCO3"

    @classmethod
    def normalize(cls, value: Any) -> "ConcentrationUnit":
        if isinstance(value, ConcentrationUnit):
            return value
        text = str(value or "mg/L").strip()
        key = text.lower().replace("\u00b5", "u")
        mapping = {
            "mg/l": cls.MG_L,
            "ppm": cls.MG_L,
            "ug/l": cls.UG_L,
            "microgram/l": cls.UG_L,
            "ppb": cls.UG_L,
            "mmol/l": cls.MMOL_L,
            "millimol/l": cls.MMOL_L,
            "mol/l": cls.MOL_L,
            "mole/l": cls.MOL_L,
            "mg/l as caco3": cls.MG_L_AS_CACO3,
            "ppm as caco3": cls.MG_L_AS_CACO3,
        }
        if key not in mapping:
            raise ValueError(f"Unsupported concentration unit: {text}")
        return mapping[key]


class ModelWarning(StrictFiniteBaseModel):
    """Non-fatal model warning returned to callers."""

    code: str
    message: str
    severity: str = "warning"


class RunDiagnostics(StrictFiniteBaseModel):
    """Execution diagnostics that are safe to expose to tool callers."""

    database: Optional[str] = None
    engine: Optional[str] = None
    warnings: List[ModelWarning] = Field(default_factory=list)
    # The gram formula weight each supplied analyte was read against. Databases
    # disagree (Si as SiO2 in phreeqc.dat, as H4SiO4 in minteq.dat), so a result
    # is not interpretable without it.
    element_input_basis: Optional[Dict[str, Any]] = None
    element_input_basis_error: Optional[str] = None
    # Each carbonate species amount resolved to its C(4) total before the run: the database, pH, temperature, the
    # species' share and the log K1/K2 it used (waterchem.carbonate_species).
    carbonate_species_conversion: Optional[List[Dict[str, Any]]] = None
    # Engine-selection provenance, previously computed and then discarded.
    phreeqc_executable_path: Optional[str] = None
    database_probe: Optional[Dict[str, Any]] = None
    
    declared_by: Optional[str] = None

    @field_validator("database", "engine", mode="before")
    @classmethod
    def redact_public_text(cls, value: Any) -> Any:
        return redact_paths(value) if isinstance(value, str) else value


class AnalyteAmount(StrictFiniteBaseModel):
    """Canonical analyte concentration after alias and unit normalization."""

    name: str
    value: float
    unit: ConcentrationUnit = ConcentrationUnit.MG_L
    raw_name: Optional[str] = None
    raw_value: Any = None
    as_formula: Optional[str] = None
    phreeqc_name: Optional[str] = None
    # A carbonate species amount ('HCO3-' / 'CO3-2'): its value is the free ion, resolved to the C(4) total on the run's
    
    species_basis: Optional[str] = None

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Analyte concentration must be finite")
        return value

    @model_validator(mode="after")
    def validate_concentration_sign(self) -> "AnalyteAmount":
        # Alkalinity is an acid-neutralizing quantity and may be negative.
        # Ordinary analytical concentrations cannot be negative. Reject them
        # before PHREEQC can silently treat them as absent/zero.
        if self.name != "Alkalinity" and self.value < 0:
            raise ValueError(
                f"Analytical concentration for {self.name!r} must be non-negative; "
                f"received {self.value:g}"
            )
        return self

    def phreeqc_value(self) -> str | float:
        if self.name == "Alkalinity" and self.as_formula:
            return f"as {self.as_formula} {self.value:g}"
        return self.value


class CanonicalWaterAnalysis(StrictFiniteBaseModel):
    """Canonical water analysis used to build PHREEQC inputs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    analytes: Dict[str, AnalyteAmount] = Field(default_factory=dict)
    ph: Optional[float] = None
    pe: Optional[float] = 4.0
    temperature_celsius: float = 25.0
    pressure_atm: float = 1.0
    units: ConcentrationUnit = ConcentrationUnit.MG_L
    charge_balance: Optional[str] = None
    density: Optional[float] = None
    redox: Optional[str] = None
    allow_extreme_ph: bool = False

    @model_validator(mode="after")
    def validate_physical_bounds(self) -> "CanonicalWaterAnalysis":
        if self.ph is not None:
            if self.allow_extreme_ph:
                if self.ph < 0 or self.ph > 14:
                    raise ValueError("pH must be between 0 and 14 when allow_extreme_ph=True")
            elif self.ph <= 0 or self.ph >= 14:
                raise ValueError("pH must be greater than 0 and less than 14")
        if self.temperature_celsius <= -273.15:
            raise ValueError("temperature_celsius must be greater than -273.15 C")
        if self.pressure_atm <= 0:
            raise ValueError("pressure_atm must be positive")
        return self


def assert_finite_payload(payload: Any, path: str = "result") -> None:
    """Raise ValueError when a nested payload contains NaN or infinity."""

    if isinstance(payload, bool) or payload is None:
        return
    if isinstance(payload, (int, float)):
        if not math.isfinite(float(payload)):
            raise ValueError(f"Non-finite numeric value at {path}")
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert_finite_payload(key, f"{path}.<key>")
            assert_finite_payload(value, f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for idx, value in enumerate(payload):
            assert_finite_payload(value, f"{path}[{idx}]")


class SolutionState(StrictFiniteBaseModel):
    """Common solution-state output shape."""

    # Optional because this shared shape is embedded in partial parser outputs.
    solution_summary: Optional[Dict[str, Any]] = None
    # Optional because some PHREEQC calls request summaries without totals.
    element_totals_molality: Optional[Dict[str, float]] = None
    # Optional because selected-output settings may omit species.
    species_molality: Optional[Dict[str, float]] = None

    @model_validator(mode="after")
    def reject_non_finite(self) -> "SolutionState":
        assert_finite_payload(self.model_dump(exclude_none=True), "solution_state")
        return self


class SaturationState(StrictFiniteBaseModel):
    """Saturation-index output shape."""

    saturation_indices: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_non_finite(self) -> "SaturationState":
        assert_finite_payload(self.saturation_indices, "saturation_indices")
        return self
