"""
Tool for simulating chemical additions to a solution.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager

try:
    from utils.mass_balance import add_mass_balance_to_output
except ImportError:
    add_mass_balance_to_output = None
from utils.amorphous_phases import get_amorphous_phases_for_system
from utils.helpers import (
    build_equilibrium_phases_block,
    build_reaction_block,
    build_selected_output_block,
    build_solution_block,
)

from .phreeqc_wrapper import (
    PhreeqcError,
    run_phreeqc_simulation,
    run_phreeqc_simulation_with_precipitation,
    run_phreeqc_with_phreeqpython,
)
from .schemas import SimulateChemicalAdditionInput, SimulateChemicalAdditionOutput

logger = logging.getLogger(__name__)


async def simulate_chemical_addition(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulates adding chemicals to a solution and calculates the resulting equilibrium.

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting solution composition
            - reactants: List of chemicals to add with formulas and amounts
            - allow_precipitation: Whether to allow mineral precipitation (optional, default true)
            - equilibrium_minerals: List of minerals to allow to precipitate (optional)
            - database: Path to PHREEQC database file (optional)
            - temperature_celsius: Override temperature for reaction (optional)
            - pressure_atm: Override pressure for reaction (optional)

    Returns:
        Dictionary containing detailed solution properties after the chemical addition
    """
    logger.info("Running simulate_chemical_addition tool...")

    # Create pydantic model from input data for validation
    try:
        input_model = SimulateChemicalAdditionInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}

    # Centralized database resolution with validation and fallback
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="general")

    try:
        # Extract initial solution parameters
        initial_sol_dict = input_model.initial_solution.model_dump(exclude_defaults=True)

        # Override temp/pressure if provided at reaction level
        if input_model.temperature_celsius is not None:
            initial_sol_dict["temperature_celsius"] = input_model.temperature_celsius
        if input_model.pressure_atm is not None:
            initial_sol_dict["pressure_atm"] = input_model.pressure_atm

        # Prepare reactants list
        reactants_list = [r.model_dump() for r in input_model.reactants]

        # Get precipitation settings
        allow_precipitation = getattr(input_model, "allow_precipitation", True)
        equilibrium_minerals = None

        if allow_precipitation:
            # First, check for explicit mineral list
            if hasattr(input_model, "equilibrium_minerals") and input_model.equilibrium_minerals:
                # User specified minerals - use those with compatibility checking
                requested_minerals = input_model.equilibrium_minerals
                logger.info(f"Using user-specified minerals: {', '.join(requested_minerals)}")

                # Get compatible minerals for the selected database
                mineral_mapping = database_manager.get_compatible_minerals(database_path, requested_minerals)

                # Filter out incompatible minerals and use alternatives where possible
                equilibrium_minerals = []
                for requested_mineral, compatible_mineral in mineral_mapping.items():
                    if compatible_mineral:
                        equilibrium_minerals.append(compatible_mineral)
                    else:
                        logger.warning(
                            f"Mineral '{requested_mineral}' is not compatible with database "
                            f"'{os.path.basename(database_path)}' and no alternative was found."
                        )
            else:
                # No minerals specified - use full database mineral list for comprehensive precipitation modeling
                # This addresses expert review concern about missing precipitate when using limited mineral lists
                equilibrium_minerals = database_manager.get_compatible_minerals(database_path)
                logger.info(
                    f"Using full database mineral list ({len(equilibrium_minerals)} minerals) for comprehensive precipitation modeling"
                )

        # Check if kinetic modeling is requested
        if input_model.kinetic_parameters and input_model.kinetic_parameters.enable_kinetics:
            logger.info("Kinetic modeling requested")

            # Check if we should use PHREEQC's native rates (default) or custom Python functions
            use_phreeqc_rates = getattr(input_model.kinetic_parameters, "use_phreeqc_rates", True)

            if use_phreeqc_rates:
                # Use PHREEQC's native KINETICS blocks with phreeqc_rates.dat
                logger.info("Using PHREEQC's native kinetic rates database")

                from .phreeqc_wrapper import calculate_kinetic_precipitation_phreeqc_native

                # Run kinetic calculation with PHREEQC native approach
                results = await calculate_kinetic_precipitation_phreeqc_native(
                    solution_data=initial_sol_dict,
                    reactants=reactants_list,
                    kinetic_params=input_model.kinetic_parameters.model_dump(),
                    database_path=database_path,
                    temperature=initial_sol_dict.get("temperature_celsius", 25.0),
                )

            else:
                # Use custom Python rate functions (legacy approach)
                logger.info("Using custom Python rate functions for kinetics")

                # First run equilibrium simulation to get initial state after chemical addition
                equilibrium_results = await run_phreeqc_with_phreeqpython(
                    solution_data=initial_sol_dict,
                    reactants=reactants_list,
                    equilibrium_minerals=None,  # No equilibrium precipitation initially
                    database_path=database_path,
                    allow_precipitation=False,  # Prevent equilibrium precipitation
                )

                if "error" in equilibrium_results:
                    return equilibrium_results

                # Now run kinetic precipitation calculation
                from phreeqpython import PhreeqPython

                from .phreeqc_wrapper import calculate_kinetic_precipitation

                # Create PhreeqPython instance
                pp = PhreeqPython(database=database_path if database_path else "phreeqc.dat")

                # Create solution from equilibrium results
                # This is simplified - in practice would need to reconstruct full solution
                solution_dict = {}
                if "solution_summary" in equilibrium_results:
                    summary = equilibrium_results["solution_summary"]
                    solution_dict["pH"] = summary.get("pH", 7.0)
                    solution_dict["pe"] = summary.get("pe", 4.0)
                    solution_dict["temp"] = initial_sol_dict.get("temperature_celsius", 25.0)

                # Add elements from equilibrium results
                if "element_totals_molality" in equilibrium_results:
                    for element, molality in equilibrium_results["element_totals_molality"].items():
                        solution_dict[element] = molality * 1000  # Convert to mmol/L

                # Create phreeqpython solution
                solution = pp.add_solution(solution_dict)

                # Run kinetic calculation
                kinetic_results = await calculate_kinetic_precipitation(
                    pp_instance=pp,
                    solution=solution,
                    minerals=equilibrium_minerals or [],
                    kinetic_params=input_model.kinetic_parameters.model_dump(),
                    temperature=initial_sol_dict.get("temperature_celsius", 25.0),
                )

                # Merge equilibrium and kinetic results
                results = equilibrium_results.copy()
                results.update(kinetic_results)

        else:
            # Standard equilibrium simulation
            results = await run_phreeqc_with_phreeqpython(
                solution_data=initial_sol_dict,
                reactants=reactants_list,
                equilibrium_minerals=equilibrium_minerals,
                database_path=database_path,
                allow_precipitation=allow_precipitation,
            )

        # If we got a list, extract the single result
        if isinstance(results, list) and results:
            results = results[0]

        # Convert to output model
        output_model = SimulateChemicalAdditionOutput(**results)

        logger.info("simulate_chemical_addition tool finished successfully.")
        return output_model.model_dump(exclude_defaults=True)

    except PhreeqcError as e:
        logger.error(f"Chemical addition tool failed: {e}")
        return {"error": str(e)}

    except Exception as e:
        logger.exception("Unexpected error in simulate_chemical_addition")
        return {"error": f"Unexpected server error: {e}"}
