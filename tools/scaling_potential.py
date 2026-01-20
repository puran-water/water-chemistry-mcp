"""
Tool for predicting mineral scaling potential in water.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager
from utils.helpers import build_equilibrium_phases_block, build_selected_output_block, build_solution_block
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas import PredictScalingPotentialInput, PredictScalingPotentialOutput

logger = logging.getLogger(__name__)

# Membrane scaling analysis has been removed as per expert review
# (was entirely heuristics-based and not scientifically sound)
MEMBRANE_SCALING_AVAILABLE = False


async def predict_scaling_potential_legacy(input_data: Dict[str, Any]) -> Dict[str, Any]:
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

    # Create pydantic model from input data for validation
    try:
        input_model = PredictScalingPotentialInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}

    # Centralized database resolution with validation and fallback
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="minerals")

    try:
        # Build solution block
        solution_str = build_solution_block(input_model.model_dump(exclude_defaults=True))
        phreeqc_input = solution_str

        # Add equilibrium phases if requested
        equilibrium_phases_str = ""
        use_equilibrium = False

        if input_model.force_equilibrium_minerals:
            # Get compatible minerals for the selected database
            mineral_mapping = database_manager.get_compatible_minerals(
                database_path, input_model.force_equilibrium_minerals
            )

            # Filter out incompatible minerals and use alternatives where possible
            compatible_minerals = []
            for requested_mineral, compatible_mineral in mineral_mapping.items():
                if compatible_mineral:
                    compatible_minerals.append(compatible_mineral)
                else:
                    logger.warning(
                        f"Mineral '{requested_mineral}' is not compatible with database "
                        f"'{os.path.basename(database_path)}' and no alternative was found."
                    )

            # Build equilibrium phases block with compatible minerals
            if compatible_minerals:
                phases_to_force = [{"name": name} for name in compatible_minerals]
                # Use allow_empty=True since having no compatible minerals is acceptable
                equilibrium_phases_str = build_equilibrium_phases_block(phases_to_force, block_num=1, allow_empty=True)
                if equilibrium_phases_str:
                    phreeqc_input += equilibrium_phases_str
                    phreeqc_input += "USE solution 1\n"  # Need to explicitly use initial solution
                    phreeqc_input += "USE equilibrium_phases 1\n"
                    use_equilibrium = True

        # Add selected output
        phreeqc_input += (
            build_selected_output_block(block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True)
            + "END\n"
        )

        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        # If we got a list, extract the single result
        if isinstance(results, list) and results:
            results = results[0]

        # Convert to output model
        output_model = PredictScalingPotentialOutput(**results)

        logger.info("predict_scaling_potential tool finished successfully.")
        return output_model.model_dump(exclude_defaults=True)

    except PhreeqcError as e:
        logger.error(f"Scaling potential tool failed: {e}")
        return {"error": str(e)}

    except Exception as e:
        logger.exception("Unexpected error in predict_scaling_potential")
        return {"error": f"Unexpected server error: {e}"}


async def predict_scaling_potential(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main scaling potential prediction function.
    Performs standard mineral scaling analysis using PHREEQC saturation indices.
    """
    # Check if membrane system parameters are provided
    if any(key in input_data for key in ["target_recovery", "recovery_rate", "concentration_factor"]):
        logger.warning("Membrane scaling analysis has been removed due to scientific integrity concerns")
        # Add warning to standard output
        result = await predict_scaling_potential_legacy(input_data)
        if "warnings" not in result:
            result["warnings"] = []
        result["warnings"].append(
            "Membrane scaling analysis removed. Use standard scaling analysis for feed water only."
        )
        return result
    else:
        # Standard scaling analysis
        return await predict_scaling_potential_legacy(input_data)
