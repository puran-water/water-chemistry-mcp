"""Public contract for the shared-kernel PHREEQC endpoint audit."""

from __future__ import annotations

import math
import re
from typing import Dict, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import StrictFiniteBaseModel

_DEFAULT_PHASES = (
    "FeS_amorphous",
    "Mackinawite",
    "Vivianite",
    "Siderite",
    "HFO",
)
_REQUIRED_VERTICAL_SLICE_TOTALS = frozenset(
    {"P", "S(6)", "S(-2)", "Fe(3)", "Fe(2)", "C(4)", "Cl"}
)


class EndpointThermodynamicAuditRequest(StrictFiniteBaseModel):
    """One nonprecipitating audit of a QSDsan or WaterTAP endpoint."""

    model_config = ConfigDict(extra="forbid")

    totals_mol_l: Dict[str, float]
    temperature_c: float
    ph: float
    gas_boundary_mode: Literal[
        "closed", "fixed_partial_pressure", "finite_headspace"
    ]
    gas_boundary: Dict[str, float] = Field(default_factory=dict)
    gas_volume_l: Optional[float] = None
    gas_total_pressure_atm: Optional[float] = None
    phase_ids: tuple[str, ...] = _DEFAULT_PHASES
    kinetic_phase_ids: tuple[str, ...] = _DEFAULT_PHASES
    reduced_model_saturation: Dict[str, float]
    reduced_model_saturation_convention: Literal[
        "log10_si", "ratio_iap_over_ksp"
    ] = "log10_si"
    reduced_model_activities: Dict[str, float] = Field(default_factory=dict)
    source_mass_balance_passed: bool
    species_coverage_fraction: float = 0.0
    reduced_model_database_hash: Optional[str] = None
    reduced_model_kernel_hash: Optional[str] = None
    database: str = "minteq.v4.dat"
    surface_dataset_id: Optional[str] = None
    surface_parameter_interpretation: str = "source_not_supplied"
    oxide_history_class: str = "unresolved"
    oxide_history_id: str = "not_supplied"
    formation_ph: Optional[float] = None
    formation_ph_basis: str = "endpoint_pH_not_oxide_formation_pH"
    active_metal_fraction_basis: str = "not_identified"
    effective_active_fe_fraction: Optional[float] = None
    parameter_contact: Optional[bool] = None
    parameter_contact_evidence_id: Optional[str] = None
    mechanism_identified: bool = False
    mechanism_evidence_id: Optional[str] = None
    extrapolation_status: Literal[
        "not_assessed",
        "within_declared_domain",
        "outside_declared_domain",
        "not_applicable",
    ] = "not_assessed"
    extrapolation_reasons: tuple[str, ...] = ()

    @field_validator("totals_mol_l", "gas_boundary", "reduced_model_saturation")
    @classmethod
    def finite_nonnegative_maps(
        cls, value: Dict[str, float], info
    ) -> Dict[str, float]:
        normalized = {str(key): float(item) for key, item in value.items()}
        invalid = sorted(
            key
            for key, item in normalized.items()
            if not math.isfinite(item) or item < 0.0
        )
        # log10 saturation indices may legitimately be negative.
        if info.field_name == "reduced_model_saturation":
            invalid = sorted(
                key for key, item in normalized.items() if not math.isfinite(item)
            )
        if invalid:
            raise ValueError(
                f"{info.field_name} contains invalid values for {invalid}"
            )
        return normalized

    @field_validator("reduced_model_activities")
    @classmethod
    def positive_activities(cls, value: Dict[str, float]) -> Dict[str, float]:
        normalized = {str(key): float(item) for key, item in value.items()}
        invalid = sorted(
            key
            for key, item in normalized.items()
            if not math.isfinite(item) or item <= 0.0
        )
        if invalid:
            raise ValueError(
                "reduced_model_activities must be finite and positive; "
                f"invalid={invalid}"
            )
        return normalized

    @field_validator("reduced_model_database_hash", "reduced_model_kernel_hash")
    @classmethod
    def validate_optional_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("hashes must be 64-character hexadecimal SHA-256 values")
        return normalized

    @model_validator(mode="after")
    def validate_audit_scope(self) -> "EndpointThermodynamicAuditRequest":
        missing_totals = sorted(_REQUIRED_VERTICAL_SLICE_TOTALS - set(self.totals_mol_l))
        if missing_totals:
            raise ValueError(
                f"Fe-S-P endpoint audit is missing analytical totals {missing_totals}"
            )
        if not math.isfinite(self.temperature_c) or self.temperature_c <= -273.15:
            raise ValueError("temperature_c must be finite and above absolute zero")
        if not math.isfinite(self.ph) or not 0.0 <= self.ph <= 14.0:
            raise ValueError("ph must be finite and in [0, 14]")
        if len(set(self.phase_ids)) != len(self.phase_ids) or not self.phase_ids:
            raise ValueError("phase_ids must be nonempty and unique")
        if set(self.kinetic_phase_ids) - set(self.phase_ids):
            raise ValueError("kinetic_phase_ids must be a subset of phase_ids")
        if set(self.reduced_model_saturation) != set(self.phase_ids):
            raise ValueError(
                "reduced_model_saturation must contain exactly the audited phase_ids"
            )
        if self.reduced_model_saturation_convention == "ratio_iap_over_ksp":
            invalid_ratios = sorted(
                phase_id
                for phase_id, value in self.reduced_model_saturation.items()
                if value <= 0.0
            )
            if invalid_ratios:
                raise ValueError(
                    "IAP/Ksp saturation ratios must be positive; "
                    f"invalid={invalid_ratios}"
                )
        if not 0.0 <= self.species_coverage_fraction <= 1.0:
            raise ValueError("species_coverage_fraction must be in [0, 1]")
        if self.effective_active_fe_fraction is not None and not (
            0.0 <= self.effective_active_fe_fraction <= 1.0
        ):
            raise ValueError("effective_active_fe_fraction must be in [0, 1]")
        if self.formation_ph is not None and not 0.0 <= self.formation_ph <= 14.0:
            raise ValueError("formation_ph must be in [0, 14]")
        if self.parameter_contact is True and not self.parameter_contact_evidence_id:
            raise ValueError(
                "parameter_contact_evidence_id is required when parameter_contact=true"
            )
        if self.mechanism_identified and not self.mechanism_evidence_id:
            raise ValueError(
                "mechanism_evidence_id is required when mechanism_identified=true"
            )
        if (
            self.extrapolation_status == "outside_declared_domain"
            and not self.extrapolation_reasons
        ):
            raise ValueError(
                "outside_declared_domain requires extrapolation_reasons"
            )

        if self.gas_boundary_mode == "closed":
            if self.gas_boundary:
                raise ValueError("closed gas boundary must not specify gas components")
            if self.gas_volume_l is not None or self.gas_total_pressure_atm is not None:
                raise ValueError("closed gas boundary must not specify gas volume or pressure")
        elif not self.gas_boundary:
            raise ValueError(
                f"{self.gas_boundary_mode} gas boundary requires gas components"
            )

        if self.gas_boundary_mode == "fixed_partial_pressure" and any(
            value <= 0.0 for value in self.gas_boundary.values()
        ):
            raise ValueError("fixed gas partial pressures must be positive")
        if self.gas_boundary_mode == "finite_headspace":
            if self.gas_volume_l is None or self.gas_volume_l <= 0.0:
                raise ValueError("finite headspace requires positive gas_volume_l")
            if self.gas_total_pressure_atm is not None and (
                self.gas_total_pressure_atm <= 0.0
            ):
                raise ValueError("gas_total_pressure_atm must be positive")
            if not any(value > 0.0 for value in self.gas_boundary.values()):
                raise ValueError(
                    "finite headspace requires a positive component partial pressure"
                )
        return self


__all__ = ["EndpointThermodynamicAuditRequest"]
