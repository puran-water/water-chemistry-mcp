"""
Tool for predicting mineral scaling potential in water.
"""

import logging
from typing import Any, Dict

from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.scaling import ScalingRequest, ScalingResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


@holds_batch_redox
async def predict_scaling_potential(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Predicts mineral scaling potential (saturation indices) and optionally calculates
    precipitation amounts by forcing equilibrium with specified minerals.

    Args:
        input_data: Dictionary containing water analysis parameters and optionally:
            - force_equilibrium_minerals: List of mineral names to force equilibrium with
            - database: Path to PHREEQC database file

    Returns:
        Dictionary containing saturation indices and precipitation amounts if requested
    """
    logger.info("Running predict_scaling_potential tool...")

    try:
        input_model = ScalingRequest.model_validate(input_data)
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "predict_scaling_potential", "stage": "validate_input"},
        )

    try:
        database_path = resolve_database(input_model.database, category="minerals")
        solution_str = build_solution_block_from_canonical(input_model.to_canonical())
        phreeqc_input = solution_str

        if input_model.force_equilibrium_minerals:
            mineral_mapping = database_manager.get_compatible_minerals(
                database_path, input_model.force_equilibrium_minerals
            )
            compatible_minerals = [mineral for mineral in mineral_mapping.values() if mineral]
            if compatible_minerals:
                phases_to_force = [{"name": name} for name in compatible_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(phases_to_force, block_num=1, allow_empty=True)
                if equilibrium_phases_str:
                    phreeqc_input += equilibrium_phases_str
                    phreeqc_input += "USE solution 1\n"
                    phreeqc_input += "USE equilibrium_phases 1\n"
                    phreeqc_input += "SAVE solution 2\n"

        phreeqc_input += (
            build_selected_output_block(block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True)
            + "END\n"
        )

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="predict_scaling_potential",
                job_name="scaling_potential",
                database_path=database_path,
                database_category="minerals",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict):
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }
        output_model = ScalingResult.model_validate(results)

        logger.info("predict_scaling_potential tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as e:
        public_payload = e.to_public_dict()
        logger.error(f"Scaling potential tool failed: {public_payload.get('message')}")
        return public_payload

    except Exception as e:
        logger.exception("Unexpected error in predict_scaling_potential")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(e).__name__}: {redact_paths(str(e))}",
            {"tool_name": "predict_scaling_potential", "stage": "execute"},
        )
