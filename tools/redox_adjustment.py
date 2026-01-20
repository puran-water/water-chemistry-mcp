"""
Tool for simulating redox adjustments.

FAIL LOUDLY: This module raises typed exceptions on errors.
Supports pe, Eh, and redox couple equilibration.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager
from utils.exceptions import (
    DatabaseLoadError,
    InputValidationError,
    PhreeqcSimulationError,
    RedoxSpecificationError,
)
from utils.helpers import build_selected_output_block, build_solution_block
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas import (
    SimulateRedoxAdjustmentInput,
    SimulateRedoxAdjustmentOutput,
    SolutionOutput,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Redox conversion utilities
# ============================================================================


def eh_to_pe(eh_mv: float, temp_c: float = 25.0) -> float:
    """
    Convert Eh (mV) to pe.

    pe = Eh * F / (2.303 * R * T)

    where:
        F = 96485 C/mol (Faraday constant)
        R = 8.314 J/(mol·K) (gas constant)
        T = temperature in Kelvin

    Args:
        eh_mv: Eh in millivolts
        temp_c: Temperature in Celsius

    Returns:
        pe value
    """
    R = 8.314  # J/(mol·K)
    F = 96485  # C/mol
    T_K = temp_c + 273.15

    # Convert mV to V
    eh_v = eh_mv / 1000.0

    # pe = Eh * F / (2.303 * R * T)
    pe = eh_v * F / (2.303 * R * T_K)

    return pe


def pe_to_eh(pe: float, temp_c: float = 25.0) -> float:
    """
    Convert pe to Eh (mV).

    Eh = pe * 2.303 * R * T / F

    Args:
        pe: pe value
        temp_c: Temperature in Celsius

    Returns:
        Eh in millivolts
    """
    R = 8.314  # J/(mol·K)
    F = 96485  # C/mol
    T_K = temp_c + 273.15

    # Eh in V = pe * 2.303 * R * T / F
    eh_v = pe * 2.303 * R * T_K / F

    # Convert to mV
    return eh_v * 1000.0


# ============================================================================
# Main function
# ============================================================================


async def simulate_redox_adjustment(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate redox adjustment to a target pe, Eh, or redox couple.

    This function models:
    - Setting specific pe or Eh conditions
    - Equilibrating with a redox couple (e.g., O2(g), Fe(3+)/Fe(2+))
    - Speciation changes due to redox shifts

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting water composition
            - target_redox: Target redox condition (pe, Eh_mV, or couple)
            - database: PHREEQC database to use

    Returns:
        Dictionary containing solution state after redox adjustment

    Raises:
        InputValidationError: If input validation fails
        RedoxSpecificationError: If redox specification is invalid
        PhreeqcSimulationError: If simulation fails
        DatabaseLoadError: If database cannot be loaded
    """
    logger.info("Running simulate_redox_adjustment tool...")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcSimulationError("PhreeqPython is not available. Install with: pip install phreeqpython")

    # Validate input
    try:
        input_model = SimulateRedoxAdjustmentInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="general")

    # Extract redox target
    target_redox = input_model.target_redox
    parameter = target_redox.parameter.lower()

    # Validate redox specification
    if parameter not in ["pe", "eh_mv", "equilibrate_with_couple"]:
        raise RedoxSpecificationError(
            f"Invalid redox parameter: '{parameter}'. " f"Valid options: 'pe', 'Eh_mV', 'equilibrate_with_couple'",
            parameter=parameter,
        )

    if parameter in ["pe", "eh_mv"] and target_redox.value is None:
        raise RedoxSpecificationError(
            f"Target value required for '{parameter}' specification",
            parameter=parameter,
            issue="Missing value",
        )

    if parameter == "equilibrate_with_couple" and not target_redox.couple_name:
        raise RedoxSpecificationError(
            "Couple name required for 'equilibrate_with_couple' specification",
            parameter=parameter,
            issue="Missing couple_name",
        )

    # Get solution data
    solution_data = input_model.initial_solution.model_dump(exclude_defaults=True)
    temp_c = solution_data.get("temperature_celsius", 25.0)

    # Determine target pe
    if parameter == "pe":
        target_pe = target_redox.value
    elif parameter == "eh_mv":
        target_pe = eh_to_pe(target_redox.value, temp_c)
        logger.info(f"Converted Eh {target_redox.value} mV to pe {target_pe:.4f}")
    else:
        # Will use redox couple in PHREEQC
        target_pe = None

    # Try phreeqpython approach first
    try:
        if parameter == "equilibrate_with_couple":
            result = await _simulate_redox_couple_script(
                solution_data,
                target_redox.couple_name,
                target_redox.couple_logK_or_pressure,
                database_path,
            )
        else:
            result = await _simulate_redox_pe(solution_data, target_pe, database_path)
        return result

    except Exception as e:
        logger.exception(f"Redox adjustment failed: {e}")
        raise PhreeqcSimulationError(f"Redox adjustment failed: {e}")


async def _simulate_redox_pe(
    solution_data: Dict[str, Any],
    target_pe: float,
    database_path: str,
) -> Dict[str, Any]:
    """
    Simulate redox adjustment by setting a specific pe value.

    Since phreeqpython's solution.pe is read-only, we need to create
    a new solution with the target pe.
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

    # Build solution parameters with target pe
    pp_params = {}

    # pH (keep original)
    if solution_data.get("ph") is not None:
        pp_params["pH"] = solution_data["ph"]
    elif solution_data.get("pH") is not None:
        pp_params["pH"] = solution_data["pH"]
    else:
        pp_params["pH"] = 7.0

    # Set target pe
    pp_params["pe"] = target_pe

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

    # Create solution with target pe
    try:
        solution = pp.add_solution(pp_params)
    except Exception as e:
        raise PhreeqcSimulationError(f"Failed to create solution with target pe: {e}")

    # Build output
    result = _build_solution_output_from_pp(solution)
    result["redox_adjustment"] = {
        "parameter": "pe",
        "target_pe": target_pe,
        "achieved_pe": solution.pe,
        "achieved_Eh_mV": pe_to_eh(solution.pe, solution.temperature),
    }

    logger.info("simulate_redox_adjustment (pe) finished successfully.")
    return result


async def _simulate_redox_couple_script(
    solution_data: Dict[str, Any],
    couple_name: str,
    couple_value: Optional[float],
    database_path: str,
) -> Dict[str, Any]:
    """
    Simulate redox adjustment by equilibrating with a redox couple.

    Uses PHREEQC script approach since phreeqpython doesn't directly
    support redox couple specification.
    """
    # Modify solution data to include redox specification
    modified_solution = solution_data.copy()

    # Format the redox couple for PHREEQC
    # Common couples: O(-2)/O(0), Fe(2)/Fe(3), S(-2)/S(6)
    # If it's a gas like O2(g), we need to use GAS_PHASE block instead

    if "(g)" in couple_name.lower():
        # Gas-based redox - use GAS_PHASE block
        logger.info(f"Using gas phase equilibration for redox couple: {couple_name}")

        # Build solution block
        solution_str = build_solution_block(solution_data, solution_num=1)

        # Build gas phase block
        gas_pressure = couple_value if couple_value is not None else 0.21  # Default O2 partial pressure
        gas_str = f"""GAS_PHASE 1
    -fixed_pressure
    -pressure 1.0
    -volume 1.0
    {couple_name} {gas_pressure}
"""

        phreeqc_input = solution_str + "\n"
        phreeqc_input += gas_str + "\n"
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

    else:
        # Use redox couple specification in solution block
        modified_solution["redox"] = couple_name

        solution_str = build_solution_block(modified_solution, solution_num=1)

        phreeqc_input = solution_str + "\n"
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
        )
        phreeqc_input += "END\n"

    # Run simulation
    results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

    # If we got a list, extract single result
    if isinstance(results, list) and results:
        results = results[0]

    # Add redox info
    results["redox_adjustment"] = {
        "parameter": "equilibrate_with_couple",
        "couple_name": couple_name,
        "couple_value": couple_value,
    }

    logger.info("simulate_redox_adjustment (couple) finished successfully.")
    return SimulateRedoxAdjustmentOutput(**results).model_dump(exclude_defaults=True)


def _build_solution_output_from_pp(solution) -> Dict[str, Any]:
    """Build output dictionary from phreeqpython solution."""
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

    return SimulateRedoxAdjustmentOutput(
        solution_summary=solution_summary,
        saturation_indices=saturation_indices,
        element_totals_molality=element_totals,
        species_molalities=species,
    ).model_dump(exclude_defaults=True)
