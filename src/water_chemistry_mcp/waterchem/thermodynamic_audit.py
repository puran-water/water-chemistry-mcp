"""Deterministic shared-kernel → PHREEQC translation and audit classification."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal, Mapping, Optional

from puran_mcp_support.schema import ChemistryKernelDefinition

_PHREEQC_SPECIES = {
    "H_plus": "H+",
    "Fe_III_aq": "Fe+3",
    "Fe_II_aq": "Fe+2",
    "H2_aq": "H2",
    "HS_minus": "HS-",
    "SO4": "SO4-2",
    "PO4": "PO4-3",
    "CO3": "CO3-2",
    "Cl_minus": "Cl-",
    "H2O": "H2O",
    # mineral-extension kernel (Ca/Mg/K/Al phase set)
    "Ca_ii": "Ca+2",
    "Mg_ii": "Mg+2",
    "K_i": "K+",
    "NH4": "NH4+",
    "Al_iii": "Al+3",
}

GasBoundaryMode = Literal["closed", "fixed_partial_pressure", "finite_headspace"]

_AUDIT_TOTAL_NAMES = frozenset(
    {
        "Al",
        "C(4)",
        "Ca",
        "Cl",
        "Fe(2)",
        "Fe(3)",
        "K",
        "Mg",
        "N(-3)",
        "N(5)",
        "Na",
        "P",
        "S(-2)",
        "S(6)",
    }
)
_GAS_NAME_ALIASES = {
    "ch4": "CH4(g)",
    "ch4(g)": "CH4(g)",
    "co2": "CO2(g)",
    "co2(g)": "CO2(g)",
    "h2": "H2(g)",
    "h2(g)": "H2(g)",
    "h2s": "H2S(g)",
    "h2s(g)": "H2S(g)",
    "n2": "N2(g)",
    "n2(g)": "N2(g)",
    "nh3": "NH3(g)",
    "nh3(g)": "NH3(g)",
    "o2": "O2(g)",
    "o2(g)": "O2(g)",
}


@dataclass(frozen=True)
class AuditFinding:
    code: str
    classification: str
    message: str
    phase_id: Optional[str] = None

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "classification": self.classification,
            "message": self.message,
        }
        if self.phase_id is not None:
            payload["phase_id"] = self.phase_id
        return payload


def translate_endpoint_totals(
    totals_mol_l: Mapping[str, float],
    *,
    required_totals: tuple[str, ...],
    temperature_c: float,
    ph: float,
    gas_boundary: Mapping[str, float],
    gas_boundary_mode: GasBoundaryMode,
) -> dict[str, object]:
    missing = [name for name in required_totals if name not in totals_mol_l]
    if missing:
        raise KeyError(f"endpoint translation missing analytical totals {missing}")
    totals = {name: float(totals_mol_l[name]) for name in sorted(totals_mol_l)}
    invalid_totals = sorted(
        name for name, value in totals.items() if not math.isfinite(value) or value < 0
    )
    if invalid_totals:
        raise ValueError(
            "endpoint analytical totals must be finite and nonnegative; "
            f"invalid={invalid_totals}"
        )
    temperature = float(temperature_c)
    ph_value = float(ph)
    if not math.isfinite(temperature) or temperature <= -273.15:
        raise ValueError("endpoint temperature must be finite and above absolute zero")
    if not math.isfinite(ph_value) or not 0.0 <= ph_value <= 14.0:
        raise ValueError("endpoint pH must be finite and in [0, 14]")
    if gas_boundary_mode not in {
        "closed",
        "fixed_partial_pressure",
        "finite_headspace",
    }:
        raise ValueError("endpoint gas-boundary mode is invalid")
    gas = {name: float(gas_boundary[name]) for name in sorted(gas_boundary)}
    invalid_gas = sorted(
        name for name, value in gas.items() if not math.isfinite(value) or value < 0
    )
    if invalid_gas:
        raise ValueError(
            f"gas-boundary values must be finite and nonnegative; invalid={invalid_gas}"
        )
    if gas_boundary_mode == "closed" and gas:
        raise ValueError("closed gas boundary must not specify gas values")
    if gas_boundary_mode != "closed" and not gas:
        raise ValueError(
            f"{gas_boundary_mode} gas boundary requires at least one gas value"
        )
    payload = {
        "totals_mol_l": totals,
        "temperature_c": temperature,
        "ph": ph_value,
        "gas_boundary_mode": gas_boundary_mode,
        "gas_boundary": gas,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {**payload, "translation_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def normalize_reduced_saturation(
    values: Mapping[str, float],
    *,
    convention: Literal["log10_si", "ratio_iap_over_ksp"],
) -> dict[str, float]:
    """Normalize an engine's saturation output to PHREEQC's log10-SI basis."""

    normalized: dict[str, float] = {}
    for phase_id, raw_value in sorted(values.items()):
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(
                f"reduced-model saturation for {phase_id!r} must be finite"
            )
        if convention == "ratio_iap_over_ksp":
            if value <= 0.0:
                raise ValueError(
                    "saturation ratios must be positive; "
                    f"{phase_id!r}={value!r}"
                )
            value = math.log10(value)
        elif convention != "log10_si":
            raise ValueError(f"unsupported saturation convention {convention!r}")
        normalized[str(phase_id)] = value
    return normalized


def _normalize_gas_name(name: str) -> str:
    normalized = _GAS_NAME_ALIASES.get(str(name).strip().lower())
    if normalized is None:
        raise KeyError(f"unsupported PHREEQC audit gas component {name!r}")
    return normalized


def build_endpoint_audit_input(
    endpoint: Mapping[str, object],
    *,
    kernel: ChemistryKernelDefinition,
    phase_ids: tuple[str, ...],
    gas_volume_l: float | None = None,
    gas_total_pressure_atm: float | None = None,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """Render a deterministic, nonprecipitating PHREEQC endpoint audit.

    The dynamic/steady model's phase inventories are intentionally not placed
    in ``EQUILIBRIUM_PHASES``.  PHREEQC therefore reports the saturation state
    of the supplied kinetic endpoint without erasing its path history.
    """

    totals = {
        str(name): float(value)
        for name, value in dict(endpoint["totals_mol_l"]).items()
    }
    unsupported_totals = sorted(set(totals) - _AUDIT_TOTAL_NAMES)
    if unsupported_totals:
        raise KeyError(
            "unsupported PHREEQC analytical-total identifiers "
            f"{unsupported_totals}"
        )
    invalid_totals = sorted(
        name for name, value in totals.items() if not math.isfinite(value) or value < 0
    )
    if invalid_totals:
        raise ValueError(
            "PHREEQC analytical totals must be finite and nonnegative; "
            f"invalid={invalid_totals}"
        )

    temperature_c = float(endpoint["temperature_c"])
    ph = float(endpoint["ph"])
    gas_mode = str(endpoint["gas_boundary_mode"])
    gas = {
        _normalize_gas_name(str(name)): float(value)
        for name, value in dict(endpoint.get("gas_boundary", {})).items()
    }
    invalid_gas = sorted(
        name for name, value in gas.items() if not math.isfinite(value) or value < 0
    )
    if invalid_gas:
        raise ValueError(
            "PHREEQC gas partial pressures must be finite and nonnegative; "
            f"invalid={invalid_gas}"
        )

    rendered_phases, phase_name_map = render_custom_phases(
        kernel,
        phase_ids,
    )
    phase_by_id = {phase.phase_id: phase for phase in kernel.phases}
    required_activities = tuple(
        sorted(
            {
                component_id
                for phase_id in phase_ids
                for component_id in phase_by_id[phase_id].dissolution_reaction
                if component_id != phase_id
            }
        )
    )
    unmapped_activities = sorted(set(required_activities) - set(_PHREEQC_SPECIES))
    if unmapped_activities:
        raise KeyError(
            f"no PHREEQC activity mapping for shared components {unmapped_activities}"
        )

    lines = [
        rendered_phases,
        "SOLUTION 1 Shared-kernel endpoint audit",
        "    units mol/L",
        f"    temp {temperature_c:.12g}",
        f"    pH {ph:.12g}",
        "    water 1",
    ]
    lines.extend(f"    {name} {value:.12g}" for name, value in sorted(totals.items()))

    if gas_mode == "fixed_partial_pressure":
        if not gas or any(value <= 0.0 for value in gas.values()):
            raise ValueError(
                "fixed partial-pressure boundaries require positive gas pressures"
            )
        # Official PHREEQC guidance uses EQUILIBRIUM_PHASES for individually
        # fixed gas fugacities. Ten moles is an intentionally explicit large
        # reservoir for the one-kilogram audit solution.
        lines.append("EQUILIBRIUM_PHASES 1 Fixed gas fugacities")
        lines.extend(
            f"    {name} {math.log10(value):.12g} 10"
            for name, value in sorted(gas.items())
        )
        lines.extend(("USE solution 1", "USE equilibrium_phases 1", "SAVE solution 2"))
    elif gas_mode == "finite_headspace":
        volume_l = float(gas_volume_l) if gas_volume_l is not None else float("nan")
        pressure_atm = (
            float(gas_total_pressure_atm)
            if gas_total_pressure_atm is not None
            else sum(gas.values())
        )
        if (
            not gas
            or not math.isfinite(volume_l)
            or volume_l <= 0.0
            or not math.isfinite(pressure_atm)
            or pressure_atm <= 0.0
            or not any(value > 0.0 for value in gas.values())
        ):
            raise ValueError(
                "finite headspace requires a positive volume, pressure, and "
                "at least one positive component partial pressure"
            )
        lines.extend(
            (
                "GAS_PHASE 1 Finite endpoint headspace",
                "    -fixed_volume",
                f"    -volume {volume_l:.12g}",
                f"    -pressure {pressure_atm:.12g}",
                f"    -temperature {temperature_c:.12g}",
            )
        )
        lines.extend(f"    {name} {value:.12g}" for name, value in sorted(gas.items()))
        lines.extend(("USE solution 1", "USE gas_phase 1", "SAVE solution 2"))
    elif gas_mode != "closed":
        raise ValueError(f"unsupported endpoint gas-boundary mode {gas_mode!r}")

    selected_phase_names = " ".join(phase_name_map[phase_id] for phase_id in phase_ids)
    lines.extend(
        (
            "SELECTED_OUTPUT 1",
            "    -reset false",
            "    -high_precision true",
            "    -pH true",
            "    -pe true",
            "    -mu true",
            f"    -si {selected_phase_names}",
            "USER_PUNCH 1",
            "    -headings "
            + " ".join(f"a_{component_id}" for component_id in required_activities),
            "    -start",
            "10 PUNCH "
            + ", ".join(
                f'ACT("{_PHREEQC_SPECIES[component_id]}")'
                for component_id in required_activities
            ),
            "    -end",
            "END",
        )
    )
    return "\n".join(lines) + "\n", phase_name_map, required_activities


def custom_phase_manifest(
    kernel: ChemistryKernelDefinition, phase_ids: tuple[str, ...]
) -> list[dict[str, object]]:
    phases = {phase.phase_id: phase for phase in kernel.phases}
    missing = sorted(set(phase_ids) - set(phases))
    if missing:
        raise KeyError(f"kernel {kernel.kernel_id!r} omits phases {missing}")
    return [
        {
            "phase_id": phases[phase_id].phase_id,
            "formula": phases[phase_id].formula,
            "dissolution_reaction": dict(phases[phase_id].dissolution_reaction),
            "log_k_25c": phases[phase_id].log_k_25c,
            "temperature_correction_id": phases[phase_id].temperature_correction_id,
            "delta_h_j_mol": phases[phase_id].delta_h_j_mol,
            "source": phases[phase_id].source,
            "metastability_class": phases[phase_id].metastability_class,
        }
        for phase_id in phase_ids
    ]


def render_custom_phases(
    kernel: ChemistryKernelDefinition,
    phase_ids: tuple[str, ...],
    *,
    phase_name_prefix: str = "Shared_",
) -> tuple[str, dict[str, str]]:
    """Render shared phase definitions into deterministic PHREEQC syntax."""

    phases = {phase.phase_id: phase for phase in kernel.phases}
    missing = sorted(set(phase_ids) - set(phases))
    if missing:
        raise KeyError(f"kernel {kernel.kernel_id!r} omits phases {missing}")

    def term(name: str, coefficient: float, phase_formula: str, phase_id: str) -> str:
        species = phase_formula if name == phase_id else _PHREEQC_SPECIES.get(name)
        if species is None:
            raise KeyError(f"no PHREEQC species mapping for {name!r}")
        magnitude = abs(float(coefficient))
        prefix = "" if magnitude == 1.0 else f"{magnitude:g}"
        return f"{prefix}{species}"

    lines = ["PHASES"]
    name_map: dict[str, str] = {}
    for phase_id in phase_ids:
        phase = phases[phase_id]
        phreeqc_name = f"{phase_name_prefix}{phase_id}"
        name_map[phase_id] = phreeqc_name
        left = [
            term(name, coefficient, phase.formula, phase_id)
            for name, coefficient in phase.dissolution_reaction.items()
            if coefficient < 0
        ]
        right = [
            term(name, coefficient, phase.formula, phase_id)
            for name, coefficient in phase.dissolution_reaction.items()
            if coefficient > 0
        ]
        lines.extend(
            (
                phreeqc_name,
                f"    {' + '.join(left)} = {' + '.join(right)}",
                f"    log_k {phase.log_k_25c:.12g}",
            )
        )
        if phase.delta_h_j_mol is not None:
            lines.append(f"    delta_h {phase.delta_h_j_mol / 1000.0:.12g} kJ")
        analytical = getattr(phase, "analytical_expression", None)
        if analytical is not None:
            # PHREEQC -analytical_expression supersedes log_k/delta_h inside
            # PHREEQC exactly as PhaseDefinition.log10_k does on our side —
            # one fitted curve on both sides of the identity benchmark.
            coefficients = " ".join(f"{float(value):.12g}" for value in analytical)
            lines.append(f"    -analytical_expression {coefficients}")
    return "\n".join(lines), name_map


def saturation_indices_from_activities(
    kernel: ChemistryKernelDefinition,
    phase_ids: tuple[str, ...],
    activities: Mapping[str, float],
    *,
    temperature_k: float = 298.15,
) -> dict[str, float]:
    """Evaluate shared phase SI definitions from dimensionless free-ion activities.

    The solid activity is unity.  Every aqueous reaction term, including H2O
    for hydrated phases, must be supplied explicitly so an omitted PHREEQC
    punch value cannot be mistaken for a benign zero or unity default.
    """

    phases = {phase.phase_id: phase for phase in kernel.phases}
    missing_phases = sorted(set(phase_ids) - set(phases))
    if missing_phases:
        raise KeyError(f"kernel {kernel.kernel_id!r} omits phases {missing_phases}")

    result: dict[str, float] = {}
    for phase_id in phase_ids:
        phase = phases[phase_id]
        log_iap = 0.0
        for component_id, coefficient in phase.dissolution_reaction.items():
            if component_id == phase_id:
                continue
            if component_id not in activities:
                raise KeyError(
                    f"activity for {component_id!r} is required to evaluate {phase_id!r}"
                )
            activity = float(activities[component_id])
            if not math.isfinite(activity) or activity <= 0.0:
                raise ValueError(
                    f"activity for {component_id!r} must be finite and positive; got {activity!r}"
                )
            log_iap += float(coefficient) * math.log10(activity)
        result[phase_id] = log_iap - math.log10(
            phase.equilibrium_constant(temperature_k)
        )
    return result


def classify_audit(
    *,
    translation_passed: bool,
    database_hash_match: bool | None,
    reduced_model_si: Mapping[str, float],
    phreeqc_si: Mapping[str, float],
    kinetic_phase_ids: frozenset[str] = frozenset(),
    activity_log10_difference: Mapping[str, float] | None = None,
    discrepancy_threshold_log10: float = 0.5,
    phase_definition_hash_match: bool | None = True,
    source_mass_balance_passed: bool | None = True,
    species_coverage_fraction: float | None = None,
) -> dict[str, object]:
    """Classify discrepancies without vetoing legitimate kinetic supersaturation."""

    findings: list[AuditFinding] = []
    if not translation_passed:
        findings.append(
            AuditFinding(
                "TOTAL_TRANSLATION_FAILED",
                "hard_veto",
                "Elemental totals could not be translated consistently.",
            )
        )
    if source_mass_balance_passed is False:
        findings.append(
            AuditFinding(
                "SOURCE_MASS_BALANCE_FAILED",
                "hard_veto",
                "The source process-model endpoint failed its mass-balance gate.",
            )
        )
    elif source_mass_balance_passed is None:
        findings.append(
            AuditFinding(
                "SOURCE_MASS_BALANCE_UNVERIFIED",
                "recommendation_ineligible",
                "The source process model did not report a mass-balance result.",
            )
        )
    if database_hash_match is False:
        findings.append(
            AuditFinding(
                "THERMODYNAMIC_DATASET_MISMATCH",
                "recommendation_ineligible",
                "The reduced model and PHREEQC did not use identical phase reactions/log-K data.",
            )
        )
    elif database_hash_match is None:
        findings.append(
            AuditFinding(
                "THERMODYNAMIC_DATASET_UNVERIFIED",
                "recommendation_ineligible",
                "The reduced-model thermodynamic database hash was not supplied.",
            )
        )
    if phase_definition_hash_match is False:
        findings.append(
            AuditFinding(
                "PHASE_DEFINITION_MISMATCH",
                "recommendation_ineligible",
                "The reduced model did not use the audited shared-kernel definition hash.",
            )
        )
    elif phase_definition_hash_match is None:
        findings.append(
            AuditFinding(
                "PHASE_DEFINITION_UNVERIFIED",
                "recommendation_ineligible",
                "The reduced-model shared-kernel hash was not supplied.",
            )
        )
    if species_coverage_fraction is not None:
        coverage = float(species_coverage_fraction)
        if not math.isfinite(coverage) or not 0.0 <= coverage <= 1.0:
            findings.append(
                AuditFinding(
                    "INVALID_SPECIES_COVERAGE",
                    "hard_veto",
                    f"Species coverage must be in [0, 1]; got {coverage!r}.",
                )
            )
        elif coverage < 1.0:
            findings.append(
                AuditFinding(
                    "INCOMPLETE_AQUEOUS_SPECIES_COVERAGE",
                    "recommendation_ineligible",
                    f"Only {coverage:.1%} of the declared aqueous-species scope is represented.",
                )
            )

    common_phases = sorted(set(reduced_model_si) & set(phreeqc_si))
    missing_reduced = sorted(set(phreeqc_si) - set(reduced_model_si))
    missing_phreeqc = sorted(set(reduced_model_si) - set(phreeqc_si))
    if missing_reduced or missing_phreeqc:
        findings.append(
            AuditFinding(
                "PHASE_TRANSLATION_INCOMPLETE",
                "hard_veto",
                f"Phase SI mapping differs: missing_reduced={missing_reduced}, missing_phreeqc={missing_phreeqc}",
            )
        )

    for phase_id in common_phases:
        reduced = float(reduced_model_si[phase_id])
        audited = float(phreeqc_si[phase_id])
        if not math.isfinite(reduced) or not math.isfinite(audited):
            findings.append(
                AuditFinding(
                    "NONFINITE_SATURATION_INDEX",
                    "hard_veto",
                    f"Non-finite SI comparison: reduced={reduced!r}, PHREEQC={audited!r}.",
                    phase_id,
                )
            )
            continue
        difference = abs(reduced - audited)
        if phase_id in kinetic_phase_ids and audited > 0:
            findings.append(
                AuditFinding(
                    "EXPECTED_KINETIC_SUPERSATURATION",
                    "expected_kinetic_departure",
                    f"PHREEQC SI={audited:.3g}; kinetic model may retain supersaturation over finite contact time.",
                    phase_id,
                )
            )
        sign_contradiction = (
            reduced < 0 and audited > discrepancy_threshold_log10
        ) or (
            audited < 0 and reduced > discrepancy_threshold_log10
        )
        if sign_contradiction and phase_id not in kinetic_phase_ids:
            findings.append(
                AuditFinding(
                    "THERMODYNAMIC_SIGN_CONTRADICTION",
                    "hard_veto",
                    f"Reduced model SI={reduced:.3g} and PHREEQC SI={audited:.3g} "
                    "have contradictory signs under matched definitions.",
                    phase_id,
                )
            )
        elif difference > discrepancy_threshold_log10:
            findings.append(
                AuditFinding(
                    "SATURATION_INDEX_DISCREPANCY",
                    "uncertainty_inflation",
                    f"SI differs by {difference:.3g} log units.",
                    phase_id,
                )
            )

    for species, difference in sorted((activity_log10_difference or {}).items()):
        if abs(float(difference)) > discrepancy_threshold_log10:
            findings.append(
                AuditFinding(
                    "FREE_ION_ACTIVITY_DISCREPANCY",
                    "recommendation_ineligible",
                    f"{species} activity differs by {float(difference):.3g} log units.",
                )
            )

    hard_veto = any(finding.classification == "hard_veto" for finding in findings)
    recommendation_ineligible = hard_veto or any(
        finding.classification == "recommendation_ineligible" for finding in findings
    )
    return {
        "audit_class": "thermodynamic_audit_classification",
        "hard_veto": hard_veto,
        "recommendation_ineligible": recommendation_ineligible,
        "findings": [finding.public_dict() for finding in findings],
    }


__all__ = [
    "AuditFinding",
    "GasBoundaryMode",
    "classify_audit",
    "build_endpoint_audit_input",
    "custom_phase_manifest",
    "normalize_reduced_saturation",
    "render_custom_phases",
    "saturation_indices_from_activities",
    "translate_endpoint_totals",
]
