"""
Tool for calculating solution speciation and equilibrium.
"""

import logging
from typing import Dict, Any, Optional
import asyncio

from utils.database_management import database_manager
from .schemas import CalculateSolutionSpeciationInput, CalculateSolutionSpeciationOutput
from .phreeqc_wrapper import run_phreeqc_simulation, PhreeqcError
from utils.helpers import build_solution_block, build_selected_output_block

logger = logging.getLogger(__name__)


async def calculate_solution_speciation(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates the full speciation of a water sample including pH, pe, saturation indices,
    species distribution, etc.

    Args:
        input_data: Dictionary containing:
            - analysis: Dictionary of element/species concentrations
            - ph: Initial pH (optional)
            - pe: Initial pe (optional, default 4.0)
            - temperature_celsius: Temperature in Celsius (optional, default 25.0)
            - pressure_atm: Pressure in atmospheres (optional, default 1.0)
            - units: Units for concentration values (optional, default 'mg/L')
            - charge_balance: Element to adjust for charge balance (optional)
            - density: Solution density in kg/L (optional)
            - redox: Redox couple to define pe (optional)
            - database: Path to database file (optional)

    Returns:
        Dictionary containing detailed solution properties, saturation indices, and speciation
    """
    logger.info("Running calculate_solution_speciation tool...")

    # Create pydantic model from input data for validation
    try:
        input_model = CalculateSolutionSpeciationInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}

    # Centralized database resolution with validation and fallback
    database_path = database_manager.resolve_and_validate_database(
        input_model.database, category="general"
    )

    try:
        # Build PHREEQC input
        solution_str = build_solution_block(input_model.model_dump(exclude_defaults=True))
        selected_output_str = build_selected_output_block(
            block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True
        )
        phreeqc_input = solution_str + selected_output_str + "END\n"

        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        # If we got a list, extract the single result (shouldn't happen for this tool)
        if isinstance(results, list) and results:
            results = results[0]

        # Convert to output model
        output_model = CalculateSolutionSpeciationOutput(**results)

        logger.info("calculate_solution_speciation tool finished successfully.")
        return output_model.model_dump(exclude_defaults=True)

    except PhreeqcError as e:
        logger.error(f"Speciation tool failed: {e}")
        return {"error": str(e)}

    except Exception as e:
        logger.exception("Unexpected error in calculate_solution_speciation")
        return {"error": f"Unexpected server error: {e}"}
