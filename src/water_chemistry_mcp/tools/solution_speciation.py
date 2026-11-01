"""
Tool for calculating solution speciation and equilibrium.
"""

import logging
from typing import Any, Dict

from water_chemistry_mcp.waterchem.phreeqc_input import build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.speciation import SpeciationRequest, SpeciationResult
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job

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
        input_model = SpeciationRequest.model_validate(input_data)
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "calculate_solution_speciation", "stage": "validate_input"},
        )

    try:
        canonical_water = input_model.to_canonical()

        # Build PHREEQC input
        solution_str = build_solution_block_from_canonical(canonical_water)
        selected_output_str = build_selected_output_block(
            block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True
        )
        phreeqc_input = solution_str + selected_output_str + "END\n"

        # Run simulation through the unified PHREEQC boundary
        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="calculate_solution_speciation",
                job_name="solution_speciation",
                database=input_model.database,
                database_category="general",
                num_steps=1,
            )
        )
        results = run_result.data
        if isinstance(results, dict):
            results.setdefault(
                "diagnostics",
                {
                    "engine": run_result.engine,
                    "database": run_result.database,
                },
            )

        # Convert to output model
        try:
            output_model = SpeciationResult.model_validate(results)
        except Exception as e:
            message = redact_paths(str(e))
            logger.error(f"Output validation error: {message}")
            return public_error(
                "UNEXPECTED_ERROR",
                f"Output validation error: {message}",
                {"tool_name": "calculate_solution_speciation", "stage": "validate_output"},
            )

        logger.info("calculate_solution_speciation tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as e:
        public_payload = e.to_public_dict()
        logger.error(f"Speciation tool failed: {public_payload.get('message')}")
        return public_payload

    except Exception as e:
        logger.exception("Unexpected error in calculate_solution_speciation")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(e).__name__}: {redact_paths(str(e))}",
            {"tool_name": "calculate_solution_speciation", "stage": "execute"},
        )
