"""
Tool for simulating kinetic reactions.

FAIL LOUDLY: This module raises typed exceptions on errors.
Uses PHREEQC KINETICS and RATES blocks for time-dependent reactions.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager
from utils.exceptions import (
    DatabaseLoadError,
    FeatureNotSupportedError,
    InputValidationError,
    KineticsDefinitionError,
    PhreeqcSimulationError,
)
from utils.helpers import (
    build_equilibrium_phases_block,
    build_kinetics_block,
    build_selected_output_block,
    build_solution_block,
)
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas import SimulateKineticReactionInput, SimulateKineticReactionOutput, SolutionOutput

logger = logging.getLogger(__name__)


async def simulate_kinetic_reaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate kinetic reactions over time using PHREEQC KINETICS blocks.

    This function models time-dependent reactions where equilibrium is not
    instantaneous. Common uses include:
    - Mineral dissolution/precipitation kinetics
    - Microbial reaction rates
    - Oxidation/reduction kinetics

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting water composition
            - kinetic_reactions: Definition of kinetic reactions and rates
            - time_steps: Time step definition for simulation
            - allow_precipitation: Whether to allow equilibrium precipitation (default True)
            - equilibrium_minerals: List of minerals for equilibrium (optional)
            - database: PHREEQC database to use

    Returns:
        Dictionary containing final state after kinetic simulation

    Raises:
        InputValidationError: If input validation fails
        KineticsDefinitionError: If kinetics definition is invalid
        PhreeqcSimulationError: If PHREEQC simulation fails
        DatabaseLoadError: If database cannot be loaded
    """
    logger.info("Running simulate_kinetic_reaction tool...")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcSimulationError("PhreeqPython is not available. Install with: pip install phreeqpython")

    # Validate input
    try:
        input_model = SimulateKineticReactionInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="general")

    # Extract kinetic reaction definition
    kinetic_def = input_model.kinetic_reactions.model_dump(exclude_defaults=True)
    time_def = input_model.time_steps.model_dump(exclude_defaults=True)

    # Check if we have valid kinetic definitions
    has_rates = bool(kinetic_def.get("rates")) or bool(kinetic_def.get("rates_block_string"))
    has_reactions = bool(kinetic_def.get("reactions")) or bool(kinetic_def.get("kinetics_block_string"))

    if not has_rates and not has_reactions:
        raise KineticsDefinitionError(
            "No kinetic reactions or rate definitions provided. "
            "Provide 'rates' with rate laws, or 'reactions' with reaction definitions.",
            missing_fields=["rates", "reactions"],
        )

    # Build PHREEQC input
    try:
        # Build solution block
        solution_dict = input_model.initial_solution.model_dump(exclude_defaults=True)
        solution_str = build_solution_block(solution_dict, solution_num=1)

        # Build kinetics and rates blocks
        # This will raise KineticsDefinitionError if invalid
        rates_str, kinetics_str = build_kinetics_block(kinetic_def, time_def, block_num=1)

        # Start building PHREEQC input
        phreeqc_input = solution_str

        # Add RATES block if we have custom rates
        if rates_str:
            phreeqc_input += "\n" + rates_str

        # Add KINETICS block
        phreeqc_input += "\n" + kinetics_str

        # Add equilibrium phases if requested
        allow_precipitation = input_model.allow_precipitation if input_model.allow_precipitation is not None else True

        if allow_precipitation:
            equilibrium_minerals = None

            if input_model.equilibrium_minerals:
                # User-specified minerals
                mineral_mapping = database_manager.get_compatible_minerals(
                    database_path, input_model.equilibrium_minerals
                )
                equilibrium_minerals = [m for m in mineral_mapping.values() if m]
            else:
                # Use database defaults - but limit for kinetic simulations
                # to avoid excessive computation
                from utils.constants import UNIVERSAL_MINERALS

                equilibrium_minerals = UNIVERSAL_MINERALS

            if equilibrium_minerals:
                phases_to_consider = [{"name": name} for name in equilibrium_minerals]
                # Use allow_empty=True for optional phases
                equilibrium_phases_str = build_equilibrium_phases_block(
                    phases_to_consider, block_num=1, allow_empty=True
                )
                if equilibrium_phases_str:
                    phreeqc_input += "\n" + equilibrium_phases_str
                    phreeqc_input += "USE equilibrium_phases 1\n"

        # Add SELECTED_OUTPUT
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            kinetics=True,
        )
        phreeqc_input += "END\n"

        logger.debug(f"PHREEQC input:\n{phreeqc_input[:500]}...")

        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        # If we got a list (multiple time steps), get the last result
        if isinstance(results, list):
            if results:
                # Last time step is the final state
                results = results[-1]
            else:
                raise PhreeqcSimulationError("PHREEQC returned empty results")

        # Build output
        final_state = SolutionOutput(**results)

        logger.info("simulate_kinetic_reaction tool finished successfully.")
        return SimulateKineticReactionOutput(
            final_state=final_state,
        ).model_dump(exclude_defaults=True)

    except KineticsDefinitionError:
        raise
    except PhreeqcError as e:
        raise PhreeqcSimulationError(
            f"Kinetic simulation failed: {e}",
            phreeqc_error=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error in simulate_kinetic_reaction")
        raise PhreeqcSimulationError(f"Unexpected error: {e}")


async def simulate_kinetic_reaction_time_series(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate kinetic reactions and return results for all time steps.

    This is an extended version that returns the full time series,
    not just the final state.

    Args:
        input_data: Same as simulate_kinetic_reaction

    Returns:
        Dictionary containing:
            - time_series: List of results at each time step
            - final_state: The final solution state
            - time_values: List of time values

    Raises:
        Same as simulate_kinetic_reaction
    """
    logger.info("Running simulate_kinetic_reaction_time_series tool...")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcSimulationError("PhreeqPython is not available. Install with: pip install phreeqpython")

    # Validate input
    try:
        input_model = SimulateKineticReactionInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="general")

    # Extract definitions
    kinetic_def = input_model.kinetic_reactions.model_dump(exclude_defaults=True)
    time_def = input_model.time_steps.model_dump(exclude_defaults=True)

    # Build PHREEQC input (similar to above but with multiple time steps)
    try:
        solution_dict = input_model.initial_solution.model_dump(exclude_defaults=True)
        solution_str = build_solution_block(solution_dict, solution_num=1)

        rates_str, kinetics_str = build_kinetics_block(kinetic_def, time_def, block_num=1)

        phreeqc_input = solution_str

        if rates_str:
            phreeqc_input += "\n" + rates_str

        phreeqc_input += "\n" + kinetics_str

        # Equilibrium phases
        allow_precipitation = input_model.allow_precipitation if input_model.allow_precipitation is not None else True

        if allow_precipitation and input_model.equilibrium_minerals:
            mineral_mapping = database_manager.get_compatible_minerals(database_path, input_model.equilibrium_minerals)
            equilibrium_minerals = [m for m in mineral_mapping.values() if m]

            if equilibrium_minerals:
                phases_to_consider = [{"name": name} for name in equilibrium_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(
                    phases_to_consider, block_num=1, allow_empty=True
                )
                if equilibrium_phases_str:
                    phreeqc_input += "\n" + equilibrium_phases_str
                    phreeqc_input += "USE equilibrium_phases 1\n"

        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            kinetics=True,
        )
        phreeqc_input += "END\n"

        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        # Process results
        if isinstance(results, list):
            time_series = results
            final_result = results[-1] if results else {}
        else:
            time_series = [results]
            final_result = results

        # Extract time values if available
        time_values = time_def.get("time_values", [])
        if not time_values and "count" in time_def and "duration" in time_def:
            count = time_def["count"]
            duration = time_def["duration"]
            time_values = [i * duration / count for i in range(count + 1)]

        # Build output
        final_state = SolutionOutput(**final_result)

        return {
            "time_series": time_series,
            "final_state": final_state.model_dump(exclude_defaults=True),
            "time_values": time_values,
            "time_units": time_def.get("units", time_def.get("duration_units", "seconds")),
        }

    except KineticsDefinitionError:
        raise
    except PhreeqcError as e:
        raise PhreeqcSimulationError(
            f"Kinetic simulation failed: {e}",
            phreeqc_error=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error in simulate_kinetic_reaction_time_series")
        raise PhreeqcSimulationError(f"Unexpected error: {e}")
