"""Independent PHREEQC audit of a shared-kernel process-model endpoint."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict

from puran_mcp_support.schema import MADM1_FECL3_FE_S_P_KERNEL, mechanism_interpretation_from_ledger
from water_chemistry_mcp.waterchem.contracts.audit import EndpointThermodynamicAuditRequest
from water_chemistry_mcp.waterchem.contracts.common import assert_finite_payload, redact_paths
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import PhreeqcParseError, WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox
from water_chemistry_mcp.waterchem.thermodynamic_audit import build_endpoint_audit_input, classify_audit, custom_phase_manifest, normalize_reduced_saturation, saturation_indices_from_activities, translate_endpoint_totals

logger = logging.getLogger(__name__)

_REQUIRED_TOTALS = ("P", "S(6)", "S(-2)", "Fe(3)", "Fe(2)", "C(4)", "Cl")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(manifest: list[dict[str, object]]) -> str:
    canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@holds_batch_redox
async def audit_process_chemistry_endpoint(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Audit one fixed process-model endpoint without equilibrating its solids.

    This tool compares free-ion activities and phase saturation indices under
    explicit analytical totals, gas boundaries, phase reactions, and database
    provenance. It never recommends or optimizes a reagent dose.
    """

    try:
        request = EndpointThermodynamicAuditRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "audit_process_chemistry_endpoint", "stage": "validate_input"},
        )

    kernel = MADM1_FECL3_FE_S_P_KERNEL
    try:
        unknown_phases = sorted(
            set(request.phase_ids) - {phase.phase_id for phase in kernel.phases}
        )
        if unknown_phases:
            raise KeyError(f"shared Fe-S-P kernel omits phases {unknown_phases}")

        translated = translate_endpoint_totals(
            request.totals_mol_l,
            required_totals=_REQUIRED_TOTALS,
            temperature_c=request.temperature_c,
            ph=request.ph,
            gas_boundary=request.gas_boundary,
            gas_boundary_mode=request.gas_boundary_mode,
        )
        phreeqc_input, phase_name_map, required_activities = (
            build_endpoint_audit_input(
                translated,
                kernel=kernel,
                phase_ids=request.phase_ids,
                gas_volume_l=request.gas_volume_l,
                gas_total_pressure_atm=request.gas_total_pressure_atm,
            )
        )
        unknown_activity_inputs = sorted(
            set(request.reduced_model_activities) - set(required_activities)
        )
        if unknown_activity_inputs:
            raise KeyError(
                "reduced_model_activities contains species outside the audited "
                f"phase definitions: {unknown_activity_inputs}"
            )

        database_path = resolve_database(request.database, category="general")
        runtime_database_hash = _sha256_file(database_path)
        manifest = custom_phase_manifest(kernel, request.phase_ids)
        phase_definition_hash = _manifest_hash(manifest)
        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="audit_process_chemistry_endpoint",
                job_name="shared_kernel_endpoint_audit",
                database_path=database_path,
                database_category="general",
                num_steps=1,
            )
        )
        if not isinstance(run_result.data, dict):
            raise PhreeqcParseError(
                "PHREEQC endpoint audit returned a non-dictionary payload",
                call_stage="parse",
                tool_name="audit_process_chemistry_endpoint",
                job_name="shared_kernel_endpoint_audit",
            )
        raw_si = dict(run_result.data.get("saturation_indices") or {})
        raw_activities = dict(run_result.data.get("species_activities") or {})
        missing_si = sorted(
            phase_id
            for phase_id in request.phase_ids
            if phase_name_map[phase_id] not in raw_si
        )
        missing_activities = sorted(set(required_activities) - set(raw_activities))
        if missing_si or missing_activities:
            raise PhreeqcParseError(
                "PHREEQC endpoint audit omitted required selected output",
                call_stage="parse",
                tool_name="audit_process_chemistry_endpoint",
                job_name="shared_kernel_endpoint_audit",
                details={
                    "missing_phase_si": missing_si,
                    "missing_species_activities": missing_activities,
                },
            )

        phreeqc_si = {
            phase_id: float(raw_si[phase_name_map[phase_id]])
            for phase_id in request.phase_ids
        }
        phreeqc_activities = {
            component_id: float(raw_activities[component_id])
            for component_id in required_activities
        }
        shared_si_from_punched_activities = saturation_indices_from_activities(
            kernel,
            request.phase_ids,
            phreeqc_activities,
            temperature_k=float(request.temperature_c) + 273.15,
        )
        internal_si_errors = {
            phase_id: abs(
                shared_si_from_punched_activities[phase_id] - phreeqc_si[phase_id]
            )
            for phase_id in request.phase_ids
        }
        failed_internal_si = sorted(
            phase_id
            for phase_id, difference in internal_si_errors.items()
            if not math.isfinite(difference) or difference > 1e-6
        )
        if failed_internal_si:
            raise PhreeqcParseError(
                "Rendered shared phase SI does not match punched PHREEQC activities",
                call_stage="audit",
                tool_name="audit_process_chemistry_endpoint",
                job_name="shared_kernel_endpoint_audit",
                details={
                    "failed_phases": failed_internal_si,
                    "absolute_si_difference": internal_si_errors,
                },
            )

        reduced_si = normalize_reduced_saturation(
            request.reduced_model_saturation,
            convention=request.reduced_model_saturation_convention,
        )
        activity_differences = {
            component_id: math.log10(request.reduced_model_activities[component_id])
            - math.log10(phreeqc_activities[component_id])
            for component_id in sorted(request.reduced_model_activities)
        }
        classification = classify_audit(
            translation_passed=True,
            database_hash_match=(
                None
                if request.reduced_model_database_hash is None
                else request.reduced_model_database_hash == runtime_database_hash
            ),
            phase_definition_hash_match=(
                None
                if request.reduced_model_kernel_hash is None
                else request.reduced_model_kernel_hash == kernel.sha256
            ),
            source_mass_balance_passed=request.source_mass_balance_passed,
            species_coverage_fraction=request.species_coverage_fraction,
            reduced_model_si=reduced_si,
            phreeqc_si=phreeqc_si,
            kinetic_phase_ids=frozenset(request.kinetic_phase_ids),
            activity_log10_difference=activity_differences,
        )

        mechanism_interpretation = mechanism_interpretation_from_ledger(
            {
                "mechanism_identified": request.mechanism_identified,
                "mechanism_evidence_id": request.mechanism_evidence_id,
                "surface_dataset_id": (
                    request.surface_dataset_id
                    or kernel.surfaces[0].surface_dataset_id
                ),
                "surface_parameter_interpretation": (
                    request.surface_parameter_interpretation
                ),
                "oxide_history_class": request.oxide_history_class,
                "oxide_history_id": request.oxide_history_id,
                "oxide_formation_ph": request.formation_ph,
                "oxide_formation_ph_basis": request.formation_ph_basis,
                "active_metal_fraction_basis": (
                    request.active_metal_fraction_basis
                ),
                "effective_active_fe_fraction": (
                    request.effective_active_fe_fraction
                ),
                "parameter_contact": request.parameter_contact,
                "parameter_contact_evidence_id": (
                    request.parameter_contact_evidence_id
                ),
                "extrapolation_status": request.extrapolation_status,
                "extrapolation_reasons": request.extrapolation_reasons,
            }
        )
        mechanism_interpretation.update(
            {
                "identified_by_phreeqc_audit": False,
                "limitations": [
                    "kinetic solids were not equilibrated or destroyed by the audit",
                    "an endpoint SI comparison cannot identify oxide history",
                    "an endpoint SI comparison cannot identify active delivered-Fe fraction",
                ],
            }
        )

        result: Dict[str, Any] = {
            "status": "completed_diagnostic_audit",
            "data_class": "simulated",
            "not_empirical": True,
            "recommendation_eligible": False,
            "recommendation_blockers": [
                "thermodynamic_audit_is_not_a_dose_recommendation",
                "plant_calibration_and_holdout_validation_not_established_by_this_tool",
            ],
            "audit_classification_passed": not classification["hard_veto"],
            "audit": classification,
            "endpoint_translation": translated,
            "reduced_model_saturation_log10_si": reduced_si,
            "phreeqc_saturation_log10_si": phreeqc_si,
            "phreeqc_free_ion_activities": phreeqc_activities,
            "activity_log10_difference": activity_differences,
            "internal_phase_si_absolute_difference": internal_si_errors,
            "thermodynamic_provenance": {
                "kernel_id": kernel.kernel_id,
                "kernel_hash": kernel.sha256,
                "phase_definition_hash": phase_definition_hash,
                "phase_manifest": manifest,
                "database_name": Path(database_path).name,
                "database_hash": runtime_database_hash,
                "engine": run_result.engine,
                "gas_boundary_semantics": (
                    "none"
                    if request.gas_boundary_mode == "closed"
                    else "fixed_individual_fugacity_infinite_reservoir"
                    if request.gas_boundary_mode == "fixed_partial_pressure"
                    else "fixed_volume_finite_headspace_initial_partial_pressures"
                ),
            },
            "mechanism_interpretation": mechanism_interpretation,
        }
        if classification["recommendation_ineligible"]:
            result["recommendation_blockers"].append(
                "thermodynamic_audit_reported_recommendation_ineligible_findings"
            )
        assert_finite_payload(result, "endpoint_thermodynamic_audit")
        return result
    except WaterChemistryError as exc:
        logger.error("Endpoint thermodynamic audit failed: %s", exc.message)
        return exc.to_public_dict()
    except (KeyError, TypeError, ValueError) as exc:
        message = redact_paths(str(exc))
        return public_error(
            "INVALID_INPUT",
            f"Endpoint audit input is invalid: {message}",
            {"tool_name": "audit_process_chemistry_endpoint", "stage": "build_audit"},
        )
    except Exception as exc:
        logger.exception("Unexpected endpoint thermodynamic audit failure")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected endpoint audit error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "audit_process_chemistry_endpoint", "stage": "execute"},
        )


__all__ = ["audit_process_chemistry_endpoint"]
