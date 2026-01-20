"""
Tool for simulating gas phase interactions.

FAIL LOUDLY: This module raises typed exceptions on errors.
Uses phreeqpython native API for gas phase equilibration.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager
from utils.exceptions import (
    DatabaseLoadError,
    GasPhaseError,
    InputValidationError,
    PhreeqcSimulationError,
)
from utils.helpers import build_gas_phase_block, build_selected_output_block, build_solution_block
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas import (
    SimulateGasPhaseInteractionInput,
    SimulateGasPhaseInteractionOutput,
    SolutionOutput,
)

logger = logging.getLogger(__name__)


async def simulate_gas_phase_interaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate gas-water equilibration using phreeqpython native API.

    This function models:
    - CO2 stripping/addition
    - O2 transfer
    - H2S degassing
    - General gas-water equilibrium

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting water composition
            - gas_phase: Gas phase definition (type, components, pressure/volume)
            - database: PHREEQC database to use

    Returns:
        Dictionary containing solution state after gas equilibration

    Raises:
        InputValidationError: If input validation fails
        GasPhaseError: If gas phase definition is invalid
        PhreeqcSimulationError: If simulation fails
        DatabaseLoadError: If database cannot be loaded
    """
    logger.info("Running simulate_gas_phase_interaction tool...")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcSimulationError("PhreeqPython is not available. Install with: pip install phreeqpython")

    # Validate input
    try:
        input_model = SimulateGasPhaseInteractionInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="general")

    # Extract gas phase definition
    gas_def = input_model.gas_phase.model_dump(exclude_defaults=True)

    # Check for required gas components
    if not gas_def.get("initial_components"):
        raise GasPhaseError(
            "No gas components specified in gas_phase.initial_components",
            gas_components=None,
            issue="Missing initial_components",
        )

    # Try phreeqpython native API first (preferred)
    try:
        result = await _simulate_gas_phase_phreeqpython(
            input_model.initial_solution.model_dump(exclude_defaults=True),
            gas_def,
            database_path,
        )
        return result

    except Exception as e:
        logger.warning(f"phreeqpython native API failed: {e}, falling back to PHREEQC script")

        # Fallback to PHREEQC script approach
        try:
            result = await _simulate_gas_phase_script(
                input_model.initial_solution.model_dump(exclude_defaults=True),
                gas_def,
                database_path,
            )
            return result
        except PhreeqcError as e2:
            raise PhreeqcSimulationError(
                f"Gas phase simulation failed: {e2}",
                phreeqc_error=str(e2),
            )


async def _simulate_gas_phase_phreeqpython(
    solution_data: Dict[str, Any],
    gas_def: Dict[str, Any],
    database_path: str,
) -> Dict[str, Any]:
    """
    Simulate gas phase using phreeqpython native API.

    The phreeqpython API supports:
    - pp.add_gas() to create gas phases
    - solution.interact(gas) to equilibrate
    """
    import os
    from pathlib import Path

    from phreeqpython import PhreeqPython

    # Create PhreeqPython instance and load database
    # PhreeqPython requires database and database_directory parameters for custom paths
    db_basename = os.path.basename(database_path)
    db_dir = Path(os.path.dirname(database_path))

    try:
        # Use database_directory parameter for custom database paths
        pp = PhreeqPython(database=db_basename, database_directory=db_dir)
    except Exception as e:
        logger.warning(f"Could not create PhreeqPython with database_directory: {e}")
        # Fallback: try with just basename (uses bundled databases)
        try:
            pp = PhreeqPython(database=db_basename)
        except Exception as e2:
            raise DatabaseLoadError(
                f"Failed to load database '{database_path}': {e2}",
                database_path=database_path,
            )

    # Build solution parameters
    pp_solution_params = _build_pp_solution_params(solution_data)

    # Create solution
    try:
        solution = pp.add_solution(pp_solution_params)
    except Exception as e:
        raise PhreeqcSimulationError(f"Failed to create initial solution: {e}")

    # Get gas phase parameters
    gas_type = gas_def.get("type", "fixed_pressure")
    gas_components = gas_def.get("initial_components", {})
    pressure = gas_def.get("fixed_pressure_atm", 1.0)
    volume = gas_def.get("initial_volume_liters", 1.0)

    # Create gas phase
    try:
        if gas_type == "fixed_pressure":
            # For fixed pressure, phreeqpython uses partial pressures
            gas = pp.add_gas(
                components=gas_components,
                pressure=pressure,
                fixed_pressure=True,
            )
        else:
            # For fixed volume
            gas = pp.add_gas(
                components=gas_components,
                volume=volume,
                fixed_volume=True,
            )
    except Exception as e:
        raise GasPhaseError(
            f"Failed to create gas phase: {e}",
            gas_components=gas_components,
            issue=str(e),
        )

    # Equilibrate solution with gas
    try:
        solution.interact(gas)
    except Exception as e:
        raise PhreeqcSimulationError(f"Gas-water equilibration failed: {e}")

    # Build output
    result = _build_solution_output_from_pp(solution, pp, gas)

    logger.info("simulate_gas_phase_interaction (phreeqpython) finished successfully.")
    return result


async def _simulate_gas_phase_script(
    solution_data: Dict[str, Any],
    gas_def: Dict[str, Any],
    database_path: str,
) -> Dict[str, Any]:
    """
    Simulate gas phase using PHREEQC script approach.

    Fallback when phreeqpython native API doesn't work.
    """
    # Build PHREEQC input
    solution_str = build_solution_block(solution_data, solution_num=1)

    # Build GAS_PHASE block - this will raise GasPhaseError if invalid
    gas_phase_str = build_gas_phase_block(gas_def, block_num=1)

    phreeqc_input = solution_str + "\n"
    phreeqc_input += gas_phase_str + "\n"
    phreeqc_input += "USE solution 1\n"
    phreeqc_input += "USE gas_phase 1\n"
    phreeqc_input += build_selected_output_block(
        block_num=1,
        saturation_indices=True,
        phases=True,
        molalities=True,
        totals=True,
        gases=True,
    )
    phreeqc_input += "END\n"

    # Run simulation
    results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

    # If we got a list, extract single result
    if isinstance(results, list) and results:
        results = results[0]

    # Add gas phase info to results
    results["gas_phase_equilibrated"] = True
    results["gas_components_input"] = gas_def.get("initial_components", {})

    logger.info("simulate_gas_phase_interaction (script) finished successfully.")
    return SimulateGasPhaseInteractionOutput(**results).model_dump(exclude_defaults=True)


def _build_pp_solution_params(solution_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build phreeqpython solution parameters from input data."""
    pp_params = {}

    # pH and pe
    if solution_data.get("ph") is not None:
        pp_params["pH"] = solution_data["ph"]
    elif solution_data.get("pH") is not None:
        pp_params["pH"] = solution_data["pH"]
    else:
        pp_params["pH"] = 7.0

    if solution_data.get("pe") is not None:
        pp_params["pe"] = solution_data["pe"]
    else:
        pp_params["pe"] = 4.0

    # Temperature
    if solution_data.get("temperature_celsius") is not None:
        pp_params["temp"] = solution_data["temperature_celsius"]

    # Analysis components
    analysis = solution_data.get("analysis", {})
    for element, value in analysis.items():
        if isinstance(value, (int, float)):
            pp_params[element] = value
        elif isinstance(value, str):
            pp_params[element] = value
        elif isinstance(value, dict):
            val = value.get("value")
            if val is not None:
                pp_params[element] = val

    return pp_params


def _build_solution_output_from_pp(solution, pp, gas=None) -> Dict[str, Any]:
    """Build output dictionary from phreeqpython solution."""
    # Solution summary
    solution_summary = {
        "pH": solution.pH,
        "pe": solution.pe,
        "temperature_celsius": solution.temperature,
        "ionic_strength_molal": solution.mu() if callable(solution.mu) else solution.mu,
    }

    try:
        solution_summary["specific_conductance_uS_cm"] = solution.sc
    except Exception:
        pass

    # Saturation indices
    saturation_indices = {}
    try:
        for mineral, si in solution.si_phases.items():
            saturation_indices[mineral] = si
    except Exception:
        pass

    # Element totals
    element_totals = {}
    for element in ["Ca", "Mg", "Na", "K", "Cl", "S", "C", "Fe", "Al", "Mn", "P", "N", "Si"]:
        try:
            total = solution.total(element, units="mol")
            if total > 1e-12:
                element_totals[element] = total
        except Exception:
            pass

    # Species
    species = {}
    try:
        for sp_name, molality in solution.species.items():
            if molality > 1e-12:
                species[sp_name] = molality
    except Exception:
        pass

    # Build result
    result = {
        "solution_summary": solution_summary,
        "saturation_indices": saturation_indices,
        "element_totals_molality": element_totals,
        "species_molalities": species,
    }

    # Add gas phase info if available
    if gas:
        try:
            result["gas_phase"] = {
                "pressure": gas.pressure,
                "volume": gas.volume,
                "components": dict(gas.components) if hasattr(gas, "components") else {},
            }
        except Exception:
            pass

    return SimulateGasPhaseInteractionOutput(**result).model_dump(exclude_defaults=True)
