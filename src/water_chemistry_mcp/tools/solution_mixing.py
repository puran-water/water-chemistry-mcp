"""
Tool for simulating the mixing of multiple solutions.
"""

import logging
import os
from typing import Any, Dict, List

from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_block, build_mix_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import analyte_value_as, build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import ConcentrationUnit, redact_paths
from water_chemistry_mcp.waterchem.contracts.mixing import SolutionMixingRequest, SolutionMixingResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.units import convert_concentration
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


@holds_batch_redox
async def simulate_solution_mixing(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulates mixing multiple solutions and calculates the resulting equilibrium.

    Args:
        input_data: Dictionary containing:
            - solutions_to_mix: List of solutions and their mixing fractions/volumes
            - database: Path to PHREEQC database file (optional)

    Returns:
        Dictionary containing detailed solution properties after mixing
    """
    logger.info("Running simulate_solution_mixing tool...")

    try:
        input_model = SolutionMixingRequest.model_validate(input_data)
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_solution_mixing", "stage": "validate_input"},
        )

    try:
        database_path = resolve_database(input_model.database, category="general")
        phreeqc_input = ""
        mix_map = {}
        automatic_minerals = None
        solutions_input = input_model.solutions_to_mix
        canonical_solutions = input_model.canonical_solutions()
        weights, any_volume = _mixing_weights(solutions_input)

        for i, water in enumerate(canonical_solutions):
            sol_num = i + 1
            phreeqc_input += build_solution_block_from_canonical(water, solution_num=sol_num)

        for i, w in enumerate(weights, start=1):
            mix_map[i] = w

        phreeqc_input += build_mix_block(mix_num=1, solution_map=mix_map)
        phreeqc_input += "USE mix 1\n"

        if input_model.allow_precipitation:
            if any_volume:
                total = sum(weights)
                frac_weights = [w / total for w in weights]
            else:
                frac_weights = weights

            if input_model.equilibrium_minerals:
                mineral_mapping = database_manager.get_compatible_minerals(database_path, input_model.equilibrium_minerals)
                compatible_minerals = [mineral for mineral in mineral_mapping.values() if mineral]
            else:
                from water_chemistry_mcp.utils.constants import AUTOMATIC_MINERALS_RULING_ID, automatic_mineral_list

                combined_analysis = _combined_analysis_mg_l(canonical_solutions, frac_weights)
                compatible_minerals, left_out = automatic_mineral_list(combined_analysis, database_path)
                automatic_minerals = {
                    "database": os.path.basename(database_path),
                    "used": list(compatible_minerals),
                    "left_out_not_defined": list(left_out),
                    "ruling_id": AUTOMATIC_MINERALS_RULING_ID,
                    "basis": (
                        "no minerals named: the house's automatic list for this water, as phases the database "
                        "defines; the candidates it does not define are left out and named here"
                    ),
                }

            if compatible_minerals:
                phases_to_consider = [{"name": name} for name in compatible_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(
                    phases_to_consider, block_num=1, allow_empty=True
                )

                if equilibrium_phases_str:
                    phreeqc_input += equilibrium_phases_str
                    phreeqc_input += "USE equilibrium_phases 1\n"

        # the mixture is stored above every input water: the answer is read from the highest-numbered stored solution
        
        phreeqc_input += f"SAVE solution {len(canonical_solutions) + 1}\n"
        phreeqc_input += (
            build_selected_output_block(block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True)
            + "END\n"
        )

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="simulate_solution_mixing",
                job_name="solution_mixing",
                database_path=database_path,
                database_category="general",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict) and automatic_minerals is not None:
            results["automatic_minerals"] = automatic_minerals
            if automatic_minerals["left_out_not_defined"]:
                results.setdefault("warnings", []).append(
                    f"Automatic mineral list on {automatic_minerals['database']}: left out "
                    f"{', '.join(automatic_minerals['left_out_not_defined'])} (not defined by the database; "
                    f"{AUTOMATIC_MINERALS_RULING_ID}, E5). Name a database that defines them to screen them."
                )
        if isinstance(results, dict):
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }
            results.setdefault("mixed_solution", _mixed_solution_summary(results))
            precipitated = results.get("precipitated_phases") or results.get("equilibrium_phase_moles")
            if precipitated:
                results.setdefault("precipitated_minerals", precipitated)
        output_model = SolutionMixingResult.model_validate(results)

        logger.info("simulate_solution_mixing tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as e:
        public_payload = e.to_public_dict()
        logger.error(f"Solution mixing tool failed: {public_payload.get('message')}")
        return public_payload

    except Exception as e:
        logger.exception("Unexpected error in simulate_solution_mixing")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(e).__name__}: {redact_paths(str(e))}",
            {"tool_name": "simulate_solution_mixing", "stage": "execute"},
        )


def _mixing_weights(solutions_input) -> tuple[List[float], bool]:
    any_volume = any(item.volume_L is not None for item in solutions_input)
    if any_volume:
        weights = [item.volume_L if item.volume_L is not None else 0.0 for item in solutions_input]
        if sum(weights) <= 0:
            raise ValueError("All provided volumes are missing or zero; cannot perform volume-based mixing")
        return weights, True

    raw_weights = [item.fraction for item in solutions_input]
    if all(weight is None for weight in raw_weights):
        return [1.0 / len(solutions_input)] * len(solutions_input), False

    positive_weights = [weight if weight is not None else 0.0 for weight in raw_weights]
    total = sum(positive_weights)
    if total <= 0:
        return [1.0 / len(solutions_input)] * len(solutions_input), False
    return [weight / total for weight in positive_weights], False


def _combined_analysis_mg_l(canonical_solutions, weights: List[float]) -> Dict[str, float]:
    combined: Dict[str, float] = {}
    for water, weight in zip(canonical_solutions, weights):
        for name, analyte in water.analytes.items():
            target_unit = ConcentrationUnit.MG_L_AS_CACO3 if name == "Alkalinity" else ConcentrationUnit.MG_L
            try:
                value = analyte_value_as(analyte, target_unit)
            except Exception:
                continue
            combined[name] = combined.get(name, 0.0) + value * weight
    return combined


def _mixed_solution_summary(results: Dict[str, Any]) -> Dict[str, Any]:
    mixed: Dict[str, Any] = {}
    for element, molality in (results.get("element_totals_molality") or {}).items():
        try:
            mixed[element] = convert_concentration(
                molality,
                element,
                ConcentrationUnit.MOL_L,
                ConcentrationUnit.MG_L,
            )
        except Exception:
            continue
    summary = results.get("solution_summary") or {}
    if "pH" in summary:
        mixed["pH"] = summary["pH"]
    if "temperature_celsius" in summary:
        mixed["temperature"] = summary["temperature_celsius"]
    return mixed
