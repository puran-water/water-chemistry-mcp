"""
Tool for calculating dosing requirements using binary search.

FAIL LOUDLY: This module raises typed exceptions on errors.
No silent fallbacks or returning {"error": ...} patterns.

Uses raw PHREEQC strings via phreeqc_wrapper for full database compatibility
with USGS PHREEQC databases (minteq.dat, phreeqc.dat, etc.).
"""

import logging
from typing import Dict, Any, List, Optional

from utils.database_management import database_manager
from utils.helpers import build_solution_block
from utils.exceptions import (
    InputValidationError,
    DosingConvergenceError,
    PhreeqcSimulationError,
)
from .schemas import (
    CalculateDosingRequirementInput,
    CalculateDosingRequirementOutput,
    SolutionOutput,
)
from .phreeqc_wrapper import find_reactant_dose_for_target

logger = logging.getLogger(__name__)


async def calculate_dosing_requirement(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the required dose of a reagent to achieve a target condition.

    Uses raw PHREEQC strings via find_reactant_dose_for_target for full
    database compatibility with USGS PHREEQC databases.

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting water composition
            - target_condition: The desired final state (parameter and value)
            - reagent: The chemical to dose (formula)
            - max_iterations: Maximum search iterations (default 30)
            - tolerance: Acceptable tolerance for convergence (default 0.05)
            - initial_guess_mmol: Initial dose guess in mmol/L (default 1.0)
            - database: PHREEQC database to use
            - allow_precipitation: Whether to allow mineral precipitation
            - equilibrium_minerals: List of minerals to consider for equilibrium

    Returns:
        Dictionary containing required dose, final state, and convergence info

    Raises:
        InputValidationError: If input validation fails
        DosingConvergenceError: If binary search fails to converge
        PhreeqcSimulationError: If PHREEQC simulation fails
    """
    logger.info("Running calculate_dosing_requirement tool (raw PHREEQC mode)...")

    # Validate input
    try:
        input_model = CalculateDosingRequirementInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(
        input_model.database, category="general"
    )

    # Extract parameters
    target_parameter = input_model.target_condition.parameter
    target_value = input_model.target_condition.value
    reagent_formula = input_model.reagent.formula
    max_iterations = input_model.max_iterations or 30
    tolerance = input_model.tolerance or 0.05
    initial_guess = input_model.initial_guess_mmol or 1.0
    allow_precipitation = (
        input_model.allow_precipitation
        if input_model.allow_precipitation is not None
        else True
    )

    # Get equilibrium minerals from database
    equilibrium_minerals = None
    if allow_precipitation:
        if input_model.equilibrium_minerals:
            # User specified minerals - validate against database
            mineral_mapping = database_manager.get_compatible_minerals(
                database_path, input_model.equilibrium_minerals
            )
            equilibrium_minerals = [m for m in mineral_mapping.values() if m]
        else:
            # Use UNIVERSAL_MINERALS - these exist in all PHREEQC databases
            # Add database-specific Mg hydroxide name
            from utils.constants import UNIVERSAL_MINERALS, MG_HYDROXIDE_NAMES
            import os

            equilibrium_minerals = list(UNIVERSAL_MINERALS)

            # Add database-specific Mg hydroxide (critical for softening/ZLD)
            # NOTE: Some databases (like phreeqc.dat) don't have Mg hydroxide phase
            if database_path:
                db_name = os.path.basename(database_path)
                mg_oh_name = MG_HYDROXIDE_NAMES.get(db_name, "Brucite")
                if mg_oh_name:  # Only add if the database has an Mg hydroxide phase
                    equilibrium_minerals.append(mg_oh_name)

    # Build PHREEQC SOLUTION block from input
    solution_dict = input_model.initial_solution.model_dump(exclude_defaults=True)
    solution_str = build_solution_block(solution_dict, solution_num=1)

    logger.debug(f"Initial solution block:\n{solution_str}")
    logger.info(
        f"Searching for {reagent_formula} dose to reach {target_parameter}={target_value}"
    )

    # Determine mineral name for SI targeting
    mineral_name = None
    if target_parameter.lower().startswith("si_"):
        mineral_name = target_parameter[3:]  # Remove 'SI_' prefix

    # Determine element/species for concentration targeting
    element_or_species = None
    if target_parameter.lower().startswith("total_"):
        element_or_species = target_parameter[6:]  # Remove 'total_' prefix
    elif target_parameter.lower() in [
        "residual_phosphorus",
        "phosphorus",
    ]:
        element_or_species = "P"
    elif target_parameter.lower() in ["residual_iron", "iron"]:
        element_or_species = "Fe"
    elif target_parameter.lower() in ["residual_aluminum", "aluminum"]:
        element_or_species = "Al"

    # Determine units for target
    target_units = None
    if target_parameter.lower() in [
        "total_hardness",
        "calcium_hardness",
        "magnesium_hardness",
        "alkalinity",
        "carbonate_alkalinity",
    ]:
        target_units = "mg/L as CaCO3"
    elif target_parameter.lower() in [
        "residual_phosphorus",
        "phosphorus",
        "residual_iron",
        "iron",
        "residual_aluminum",
        "aluminum",
    ]:
        target_units = "mg/L"

    # Call the raw PHREEQC binary search function
    try:
        optimal_dose, final_results, iterations = await find_reactant_dose_for_target(
            initial_solution_str=solution_str,
            target_parameter=target_parameter,
            target_value=target_value,
            reagent_formula=reagent_formula,
            mineral_name=mineral_name,
            element_or_species=element_or_species,
            target_units=target_units,
            initial_guess_mmol=initial_guess,
            max_iterations=max_iterations,
            tolerance=tolerance,
            database_path=database_path,
            allow_precipitation=allow_precipitation,
            equilibrium_minerals=equilibrium_minerals,
        )
    except Exception as e:
        error_str = str(e)
        if "did not converge" in error_str.lower():
            raise DosingConvergenceError(
                f"Binary search did not converge: {e}",
                last_dose=initial_guess,
                target_param=target_parameter,
                target_value=target_value,
                achieved_value=None,
                iterations=max_iterations,
                tolerance=tolerance,
            )
        raise PhreeqcSimulationError(f"Dosing calculation failed: {e}")

    # Check for convergence
    if optimal_dose is None:
        raise DosingConvergenceError(
            f"Binary search did not converge after {iterations} iterations",
            last_dose=initial_guess,
            target_param=target_parameter,
            target_value=target_value,
            achieved_value=None,
            iterations=iterations,
            tolerance=tolerance,
        )

    logger.info(
        f"Converged at iteration {iterations}: dose = {optimal_dose:.6f} mmol/L"
    )

    # Build output from final results
    final_state = _build_solution_output_from_results(final_results)

    return CalculateDosingRequirementOutput(
        required_dose_mmol_per_L=optimal_dose,
        final_state=final_state,
        iterations_taken=iterations,
        convergence_status="Converged",
    ).model_dump(exclude_defaults=True)


def _build_solution_output_from_results(results: Dict[str, Any]) -> SolutionOutput:
    """
    Build a SolutionOutput from raw PHREEQC simulation results.

    Args:
        results: Dictionary from run_phreeqc_simulation

    Returns:
        SolutionOutput model instance
    """
    # Extract solution summary
    solution_summary = results.get("solution_summary", {})

    # Map field names if needed
    mapped_summary = {
        "pH": solution_summary.get("pH", solution_summary.get("ph")),
        "pe": solution_summary.get("pe"),
        "temperature_celsius": solution_summary.get(
            "temperature_celsius", solution_summary.get("temperature", 25.0)
        ),
    }

    # Add ionic strength if available
    if "ionic_strength" in solution_summary:
        mapped_summary["ionic_strength_molal"] = solution_summary["ionic_strength"]
    elif "ionic_strength_molal" in solution_summary:
        mapped_summary["ionic_strength_molal"] = solution_summary["ionic_strength_molal"]

    # Add specific conductance if available
    if "specific_conductance_uS_cm" in solution_summary:
        mapped_summary["specific_conductance_uS_cm"] = solution_summary[
            "specific_conductance_uS_cm"
        ]
    elif "sc" in solution_summary:
        mapped_summary["specific_conductance_uS_cm"] = solution_summary["sc"]

    # Get saturation indices
    saturation_indices = results.get("saturation_indices", {})

    # Get element totals
    element_totals = results.get(
        "element_totals_molality",
        results.get("element_totals", results.get("totals", {})),
    )

    # Get species molalities
    species = results.get("species_molalities", results.get("species", {}))

    # Get precipitated phases if available
    precipitated_phases = results.get("precipitated_phases", {})

    return SolutionOutput(
        solution_summary=mapped_summary,
        saturation_indices=saturation_indices,
        element_totals_molality=element_totals,
        species_molalities=species,
        precipitated_phases=precipitated_phases if precipitated_phases else None,
    )
