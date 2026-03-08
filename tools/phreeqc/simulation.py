"""
PHREEQC simulation orchestration.

Handles running simulations via PhreeqPython or subprocess,
kinetic precipitation calculations, and helper functions for
mineral alternatives and input truncation.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

import numpy as np

from utils.exceptions import ErrorType, PhreeqcError
from utils.helpers import (
    build_equilibrium_phases_block,
    build_reaction_block,
    build_selected_output_block,
    build_solution_block,
)
from utils.import_helpers import DEFAULT_DATABASE, PHREEQPYTHON_AVAILABLE, get_default_database

from .backend import USE_SUBPROCESS, _get_phreeqc_executable, _get_phreeqc_rates_path, run_phreeqc_subprocess
from .parser import _parse_selected_output_time_series, parse_phreeqc_results

logger = logging.getLogger(__name__)


def get_mineral_alternatives(mineral_name, database_path=None):
    """
    Returns a list of alternative mineral names that could be used in place of the given mineral.

    Args:
        mineral_name (str): Name of the mineral to find alternatives for
        database_path (str, optional): Path to the PHREEQC database

    Returns:
        dict: Dictionary with alternative minerals and their formulas
    """
    try:
        db_name = None
        if database_path:
            db_name = os.path.basename(database_path)

        try:
            from utils.mineral_registry import get_alternative_mineral_names, get_mineral_formula

            formula = get_mineral_formula(mineral_name, db_name)

            if not formula:
                logger.warning(f"Could not find formula for mineral '{mineral_name}' in registry")
                return {}

            alternatives = get_alternative_mineral_names(mineral_name, db_name)

            valid_alternatives = {}

            if database_path:
                from utils.constants import database_validator_available, mineral_exists_in_database

                if database_validator_available():
                    for alt in alternatives:
                        if mineral_exists_in_database(database_path, alt):
                            alt_formula = get_mineral_formula(alt, db_name) or formula
                            valid_alternatives[alt] = alt_formula
                            logger.info(
                                f"Found valid alternative '{alt}' ({alt_formula}) for '{mineral_name}' in {db_name}"
                            )
                else:
                    for alt in alternatives:
                        alt_formula = get_mineral_formula(alt, db_name) or formula
                        valid_alternatives[alt] = alt_formula

                # Special case for common problematic minerals
                if mineral_name == "Ferrihydrite" and db_name == "phreeqc.dat":
                    valid_alternatives["Fe(OH)3(a)"] = "Fe(OH)3"
                elif mineral_name == "Fe(OH)3(a)" and db_name in ["wateq4f.dat", "llnl.dat", "minteq.dat"]:
                    valid_alternatives["Ferrihydrite"] = "Fe(OH)3"
                elif mineral_name == "Gibbsite" and db_name == "phreeqc.dat":
                    valid_alternatives["Al(OH)3(a)"] = "Al(OH)3"
                elif mineral_name == "Al(OH)3(a)" and db_name in ["wateq4f.dat", "llnl.dat", "minteq.dat"]:
                    valid_alternatives["Gibbsite"] = "Al(OH)3"
            else:
                for alt in alternatives:
                    alt_formula = get_mineral_formula(alt) or formula
                    valid_alternatives[alt] = alt_formula

            return valid_alternatives

        except ImportError:
            logger.warning("Mineral registry not available")
            return {}

    except Exception as e:
        logger.error(f"Error finding mineral alternatives: {e}")
        return {}


def extract_minerals_from_input(input_string):
    """
    Extract mineral names from a PHREEQC input string.

    Args:
        input_string (str): PHREEQC input string

    Returns:
        list: List of mineral names found in the input
    """
    minerals = []

    eq_phases_pattern = r"EQUILIBRIUM_PHASES\s+\d+(?:\s*#[^\n]*)?\n(.*?)(?=^\S|\Z)"
    eq_phases_matches = re.finditer(eq_phases_pattern, input_string, re.MULTILINE | re.DOTALL)

    for match in eq_phases_matches:
        block_content = match.group(1)
        for line in block_content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(";"):
                parts = line.split()
                if parts:
                    mineral_name = parts[0].strip()
                    if mineral_name and not mineral_name.startswith("-"):
                        minerals.append(mineral_name)

    si_pattern = r"-si\s+([A-Za-z0-9_\(\)\-\.]+)"
    si_matches = re.finditer(si_pattern, input_string, re.IGNORECASE)
    for match in si_matches:
        minerals.append(match.group(1))

    unique_minerals = []
    for mineral in minerals:
        if mineral not in unique_minerals:
            unique_minerals.append(mineral)

    return unique_minerals


def get_truncated_input(input_string, max_length=500):
    """
    Create a truncated version of the input string for error context.

    Args:
        input_string (str): Original input string
        max_length (int): Maximum length to return

    Returns:
        str: Truncated input string with focusing on relevant parts
    """
    if len(input_string) <= max_length:
        return input_string

    important_blocks = ["EQUILIBRIUM_PHASES", "SOLUTION", "REACTION", "GAS_PHASE", "KINETICS", "SURFACE"]

    snippets = []
    for block in important_blocks:
        block_pattern = f"{block}\\s+\\d+(?:\\s*#[^\\n]*)?\\n(.*?)(?=^\\S|\\Z)"
        block_matches = re.finditer(block_pattern, input_string, re.MULTILINE | re.DOTALL)

        for match in block_matches:
            block_content = match.group(0)
            if len(block_content) > 200:
                block_lines = block_content.splitlines()
                if len(block_lines) > 8:
                    truncated_block = "\n".join(block_lines[:4]) + "\n...\n" + "\n".join(block_lines[-2:])
                else:
                    truncated_block = block_content[:200] + "..."
            else:
                truncated_block = block_content

            snippets.append(truncated_block)

    if not snippets or sum(len(s) for s in snippets) > max_length:
        return input_string[: max_length // 2] + "\n...\n" + input_string[-max_length // 2 :]

    return "\n\n...\n\n".join(snippets)


async def run_phreeqc_simulation(
    input_string: str, database_path: Optional[str] = None, num_steps: int = 1
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Runs a PHREEQC simulation string and parses the results.
    Handles single or multiple step results.

    Execution modes:
    1. Subprocess mode (USE_SUBPROCESS=True): Uses standalone PHREEQC executable for full
       database compatibility.
    2. PhreeqPython mode: Uses bundled VIPhreeqc library.

    Args:
        input_string: PHREEQC input script as a string
        database_path: Path to the PHREEQC database file, or None for default
        num_steps: Number of time steps for multi-step simulations

    Returns:
        Dictionary or list of dictionaries with simulation results
    """
    # Try subprocess mode first if enabled
    if USE_SUBPROCESS and database_path and database_path != "INLINE":
        phreeqc_exe = _get_phreeqc_executable()
        if phreeqc_exe:
            logger.info("Using PHREEQC subprocess for full database compatibility")
            try:
                result = await run_phreeqc_subprocess(input_string, database_path)
                # For multi-step, return as list
                if num_steps > 1:
                    logger.warning("Subprocess mode does not support multi-step; returning single result")
                    return [result]
                return result
            except PhreeqcError as e:
                logger.warning(f"Subprocess mode failed: {e}, falling back to phreeqpython")
                # Fall through to phreeqpython mode

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")

    # Import here to avoid import errors if the library is not available
    from phreeqpython import PhreeqPython

    # Handle special case where database is included in the input
    if database_path == "INLINE":
        db_to_use = None
        pp = None
        logger.info("Using database specified in PHREEQC input (INLINE mode)")
    else:
        db_to_use = database_path or DEFAULT_DATABASE
        pp = None

        if not database_path:
            from utils.database_management import database_manager

            minteq_path = database_manager.get_database_path("minteq.dat")
            if minteq_path and os.path.exists(minteq_path):
                db_to_use = minteq_path
                logger.info(f"Using minteq.dat for Brucite/Mg(OH)2 support: {minteq_path}")

    try:
        if database_path == "INLINE":
            pp = PhreeqPython()
            logger.info("Created PhreeqPython without database (will use INLINE specification)")
        elif db_to_use:
            logger.info(f"Using PHREEQC database: {db_to_use}")
            if not os.path.exists(db_to_use):
                logger.error(f"Database file does not exist: {db_to_use}")
                raise FileNotFoundError(f"Database file does not exist: {db_to_use}")

            from pathlib import Path

            db_basename = os.path.basename(db_to_use)
            db_directory = Path(os.path.dirname(db_to_use))

            try:
                if db_directory and db_directory.exists():
                    pp = PhreeqPython(database=db_basename, database_directory=db_directory)
                    logger.info(f"Successfully created PhreeqPython with database: {db_basename} from {db_directory}")
                else:
                    pp = PhreeqPython(database=db_basename)
                    logger.info(f"Successfully created PhreeqPython with bundled database: {db_basename}")
            except Exception as e:
                logger.warning(f"Could not create PhreeqPython with database_directory: {e}")
                try:
                    pp = PhreeqPython(database=db_to_use)
                    logger.info(f"Successfully created PhreeqPython with full path: {db_to_use}")
                except Exception as e2:
                    logger.warning(f"Could not create PhreeqPython with full path, trying load_database: {e2}")
                    pp = PhreeqPython()
                    try:
                        pp.ip.load_database(db_to_use)
                        err = pp.ip.get_error_string() if hasattr(pp.ip, "get_error_string") else ""
                        if err and "ERROR" in err.upper():
                            raise PhreeqcError(f"Database loaded with errors: {err[:200]}")
                        logger.info(f"Successfully loaded database: {db_to_use}")
                    except PhreeqcError:
                        raise
                    except Exception as load_error:
                        logger.error(f"Error loading database {db_to_use}: {load_error}")
                        raise PhreeqcError(f"Error loading database {db_to_use}: {load_error}")
        else:
            logger.info("No database specified, using default PHREEQC database.")
            pp = PhreeqPython()
            default_db = get_default_database()
            if default_db and os.path.exists(default_db):
                try:
                    pp.ip.load_database(default_db)
                    err = pp.ip.get_error_string() if hasattr(pp.ip, "get_error_string") else ""
                    if err and "ERROR" in err.upper():
                        raise PhreeqcError(f"Default database loaded with errors: {err[:200]}")
                    logger.info(f"Loaded default database: {default_db}")
                except PhreeqcError:
                    raise
                except Exception as e:
                    raise PhreeqcError(f"Could not load default database: {e}")

        logger.debug(f"Running PHREEQC input:\n------\n{input_string}\n------")
        output = pp.ip.run_string(input_string)

        # Log any PHREEQC errors or warnings for debugging
        error_messages = pp.ip.get_error_string() if hasattr(pp.ip, "get_error_string") else ""
        warning_messages = pp.ip.get_warning_string() if hasattr(pp.ip, "get_warning_string") else ""
        logger.debug(f"PHREEQC Errors: {error_messages}")
        logger.debug(f"PHREEQC Warnings: {warning_messages}")

        # Check for errors and warnings
        error_messages = ""
        warning_messages = ""

        if hasattr(pp.ip, "get_error_string"):
            error_messages = pp.ip.get_error_string()

        if hasattr(pp.ip, "get_warning_string"):
            warning_messages = pp.ip.get_warning_string()
            if warning_messages == error_messages:
                warning_messages = ""

        phreeqc_messages = error_messages
        if warning_messages and warning_messages not in error_messages:
            if phreeqc_messages:
                phreeqc_messages += "\nWarnings: " + warning_messages
            else:
                phreeqc_messages = "Warnings: " + warning_messages

        minerals_involved = extract_minerals_from_input(input_string)

        if error_messages and "ERROR" in error_messages.upper():
            logger.error(f"PHREEQC simulation reported errors:\n{error_messages}")

            input_excerpt = get_truncated_input(input_string)

            context = {}

            if "Phase not found" in error_messages:
                missing_phase_match = re.search(r"Phase not found.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if missing_phase_match:
                    missing_phase = missing_phase_match.group(1)
                    context["missing_phase"] = missing_phase
                    context["suggestion"] = (
                        f"The mineral '{missing_phase}' is not available in the selected database. Check database compatibility or try an alternative mineral."
                    )

                    alternatives = get_mineral_alternatives(missing_phase, db_to_use)
                    if alternatives:
                        alt_list = ", ".join([f"{alt} ({formula})" for alt, formula in alternatives.items()])
                        context["alternatives"] = alternatives
                        context["suggestion"] += f" Possible alternatives: {alt_list}"

            elif "activity of water" in error_messages.lower() and "not converged" in error_messages.lower():
                context["error_type"] = ErrorType.WATER_ACTIVITY_CONVERGENCE
                context["suggestion"] = (
                    "Water activity did not converge. This often happens in high-ionic-strength "
                    "solutions or with extreme pH values. Try using the PITZER database for high "
                    "ionic strength solutions, or add a small amount of background electrolyte for stability."
                )

                high_conc_elements = ["Na", "Cl", "K", "Ca", "Mg", "SO4"]
                high_conc_detected = []
                for elem in high_conc_elements:
                    elem_match = re.search(rf"\b{elem}\s+([0-9.]+)\s+[mM]ol", input_string)
                    if elem_match:
                        try:
                            conc = float(elem_match.group(1))
                            if conc > 0.5:
                                high_conc_detected.append(f"{elem}: {conc} mol")
                        except (ValueError, IndexError):
                            pass

                if high_conc_detected:
                    context["high_concentrations"] = high_conc_detected
                    context["suggestion"] += " High concentrations detected: " + ", ".join(high_conc_detected)

                if db_to_use and "pitzer" not in db_to_use.lower():
                    context["alternative_database"] = "pitzer.dat"
                    context[
                        "suggestion"
                    ] += " Try switching to the PITZER database which handles high ionic strength better."

            elif "Element not in database" in error_messages:
                element_match = re.search(r"Element not in database.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if element_match:
                    missing_element = element_match.group(1)
                    context["missing_element"] = missing_element
                    context["suggestion"] = (
                        f"The element '{missing_element}' is not available in the selected database. Try using a more comprehensive database like 'llnl.dat'."
                    )

            elif "Unknown input" in error_messages:
                unknown_match = re.search(r"Unknown input.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if unknown_match:
                    unknown_input = unknown_match.group(1)
                    context["unknown_input"] = unknown_input
                    context["suggestion"] = (
                        f"Check the syntax for '{unknown_input}'. This may be a typo or a keyword not supported in the selected database."
                    )

            raise PhreeqcError(
                message="Simulation failed",
                phreeqc_errors=error_messages,
                database=db_to_use,
                minerals=minerals_involved,
                input_excerpt=input_excerpt,
                context=context,
            )
        elif error_messages or warning_messages:
            logger.warning(f"PHREEQC simulation reported issues:\n{phreeqc_messages}")

        logger.info("PHREEQC simulation finished successfully.")
        results = parse_phreeqc_results(pp, num_steps=num_steps)
        return results

    except ImportError:
        logger.critical("PhreeqPython is not installed.")
        raise PhreeqcError(
            message="Critical error: PhreeqPython library not found.",
            context={"suggestion": "Please install PhreeqPython or check your Python environment."},
        )
    except FileNotFoundError as e:
        logger.error(f"Database file not found: {db_to_use}. Error: {e}")
        minerals_involved = extract_minerals_from_input(input_string)
        raise PhreeqcError(
            message=f"Database file not found: {db_to_use}",
            minerals=minerals_involved,
            input_excerpt=get_truncated_input(input_string),
            context={
                "suggestion": "Check that the database path is correct and the file exists.",
                "error_type": ErrorType.DATABASE_NOT_FOUND,
            },
        )
    except PhreeqcError as e:
        raise e
    except Exception as e:
        logger.error(f"Exception during PHREEQC simulation or setup: {e}", exc_info=True)
        phreeqc_errors = "Unknown PHREEQC error."
        if pp and hasattr(pp.ip, "get_error_string"):
            try:
                phreeqc_errors = pp.ip.get_error_string()
            except Exception:
                pass

        minerals_involved = extract_minerals_from_input(input_string)
        input_excerpt = get_truncated_input(input_string)

        raise PhreeqcError(
            message=f"Simulation failed: {e}",
            phreeqc_errors=phreeqc_errors,
            database=db_to_use,
            minerals=minerals_involved,
            input_excerpt=input_excerpt,
            context={"error_type": ErrorType.UNKNOWN_SIMULATION_ERROR},
        )


async def run_phreeqc_with_phreeqpython(
    solution_data: Dict[str, Any],
    reactants: List[Dict[str, Any]] = None,
    equilibrium_minerals: List[str] = None,
    database_path: Optional[str] = None,
    allow_precipitation: bool = True,
) -> Dict[str, Any]:
    """
    Run chemical addition simulation using phreeqpython with proper precipitation handling.

    Args:
        solution_data: Initial solution composition
        reactants: List of chemicals to add
        equilibrium_minerals: Minerals to allow for precipitation
        database_path: Path to PHREEQC database
        allow_precipitation: Whether to allow precipitation

    Returns:
        Dictionary with simulation results including proper mass balance
    """
    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")

    from pathlib import Path

    from phreeqpython import PhreeqPython

    db_to_use = database_path or DEFAULT_DATABASE

    try:
        if db_to_use and os.path.exists(db_to_use):
            db_basename = os.path.basename(db_to_use)
            db_directory = Path(os.path.dirname(db_to_use))
            try:
                if db_directory and db_directory.exists():
                    pp = PhreeqPython(database=db_basename, database_directory=db_directory)
                    logger.info(f"Created PhreeqPython with database: {db_basename} from {db_directory}")
                else:
                    pp = PhreeqPython(database=db_basename)
                    logger.info(f"Created PhreeqPython with bundled database: {db_basename}")
            except Exception as e:
                logger.warning(f"Could not use database_directory, trying full path: {e}")
                pp = PhreeqPython(database=db_to_use)
        else:
            pp = PhreeqPython()

        ELEMENT_MAPPING = {
            "P": "P", "N": "N", "Fe": "Fe", "S": "S",
        }

        pp_solution_data = {}
        analysis = solution_data.get("analysis", {})

        for element, value in analysis.items():
            if isinstance(value, (int, float)):
                pp_element = ELEMENT_MAPPING.get(element, element)
                pp_solution_data[pp_element] = value

        if "ph" in solution_data:
            pp_solution_data["pH"] = solution_data["ph"]
        elif "pH" in solution_data:
            pp_solution_data["pH"] = solution_data["pH"]
        if "pe" in solution_data:
            pp_solution_data["pe"] = solution_data["pe"]
        if "temperature_celsius" in solution_data:
            pp_solution_data["temp"] = solution_data["temperature_celsius"]

        pp_solution_data["units"] = solution_data.get("units", "mg/L")

        solution = pp.add_solution(pp_solution_data)

        logger.info(f"Created initial solution with pH {solution.pH:.2f}")

        if reactants:
            for reactant in reactants:
                formula = reactant.get("formula")
                amount = reactant.get("amount")
                units = reactant.get("units", "mmol")

                if formula and amount is not None:
                    logger.info(f"Adding {amount} {units} of {formula}")
                    solution.add(formula, amount, units)

        precipitated_phases = {}

        logger.info(
            f"Precipitation handling: allow_precipitation={allow_precipitation}, equilibrium_minerals={equilibrium_minerals}"
        )
        if allow_precipitation and equilibrium_minerals:
            valid_minerals = []
            for mineral in equilibrium_minerals:
                try:
                    si = solution.si(mineral)
                    if si is not None and si > -900:
                        valid_minerals.append(mineral)
                except Exception:
                    pass

            if valid_minerals:
                logger.info(f"Using {len(valid_minerals)} valid minerals for simultaneous equilibration")

                supersaturated_minerals = []
                for mineral in valid_minerals:
                    try:
                        si = solution.si(mineral)
                        if si is not None and si > 0:
                            supersaturated_minerals.append(mineral)
                            logger.debug(f"  {mineral}: SI = {si:.3f} (supersaturated)")
                    except Exception:
                        pass

                if supersaturated_minerals:
                    try:
                        target_si_list = [0.0] * len(supersaturated_minerals)
                        initial_moles_list = [0.0] * len(supersaturated_minerals)

                        eq_phase = pp.add_equilibrium_phase(supersaturated_minerals, target_si_list, initial_moles_list)

                        initial_elements = {
                            elem: solution.total_element(elem, "mol")
                            for elem in solution.elements.keys()
                            if elem not in ["H", "O", "H(0)", "O(0)"]
                        }

                        solution.interact(eq_phase)

                        if hasattr(eq_phase, "components") and eq_phase.components:
                            for mineral, moles in eq_phase.components.items():
                                if moles > 1e-10:
                                    precipitated_phases[mineral] = moles
                                    logger.info(f"Precipitated {moles:.6f} mol of {mineral}")

                        logger.info("Used phreeqpython simultaneous equilibration (order-independent)")

                    except Exception as e:
                        logger.warning(f"Simultaneous equilibration failed: {e}, falling back to sequential")
                        for mineral in supersaturated_minerals:
                            try:
                                initial_si = solution.si(mineral)
                                if initial_si > 0:
                                    affected_elements = {
                                        "Calcite": "Ca", "Aragonite": "Ca", "Gypsum": "Ca",
                                        "Brucite": "Mg", "Dolomite": "Ca", "Strengite": "P",
                                    }
                                    affected_elem = affected_elements.get(mineral)
                                    initial_total = solution.total_element(affected_elem, "mol") if affected_elem else 0

                                    solution.desaturate(mineral, to_si=0)

                                    final_total = solution.total_element(affected_elem, "mol") if affected_elem else 0
                                    precip_amount = initial_total - final_total
                                    if precip_amount > 1e-10:
                                        precipitated_phases[mineral] = precip_amount
                            except Exception as e2:
                                logger.debug(f"Could not desaturate {mineral}: {e2}")
                else:
                    logger.debug("No supersaturated minerals found - no precipitation needed")
            else:
                logger.warning("No valid minerals found in database for precipitation")

        # Calculate TDS
        tds_mg_L = 0.0

        try:
            if hasattr(solution, "species_molalities"):
                species_molecular_weights = {
                    "Ca+2": 40.08, "Mg+2": 24.31, "Na+": 22.99, "K+": 39.10,
                    "Cl-": 35.45, "SO4-2": 96.06, "HCO3-": 61.02, "CO3-2": 60.01,
                    "NO3-": 62.00, "PO4-3": 94.97, "F-": 19.00,
                    "CaSO4": 136.14, "CaHCO3+": 101.10, "MgSO4": 120.37,
                    "NaSO4-": 119.05, "CaCO3": 100.09, "MgCO3": 84.31,
                    "SiO2": 60.08, "H4SiO4": 96.11,
                    "HSO4-": 97.07, "OH-": 17.01, "H+": 1.01,
                }

                for species, molality in solution.species_molalities.items():
                    if molality > 1e-12:
                        mw = species_molecular_weights.get(species)
                        if mw:
                            mg_L = molality * mw * 1000
                            tds_mg_L += mg_L
                        else:
                            if molality > 1e-6:
                                logger.debug(
                                    f"Unknown species molecular weight for TDS calculation: {species} ({molality:.6f} mol)"
                                )

                if tds_mg_L < 50:
                    logger.debug("Low species-based TDS, supplementing with element totals")
                    for element, molality in solution.elements.items():
                        if element not in ["H", "O", "O(0)", "H(0)"] and molality > 1e-6:
                            atomic_weights = {
                                "Ca": 40.08, "Mg": 24.31, "Na": 22.99, "K": 39.10,
                                "Cl": 35.45, "S": 32.06, "C": 12.01, "N": 14.01,
                                "P": 30.97, "F": 19.00, "Si": 28.09, "Fe": 55.85,
                                "Al": 26.98,
                            }
                            base_element = element.split("(")[0]
                            mw = atomic_weights.get(base_element, 50.0)
                            mg_L = molality * mw * 1000
                            tds_mg_L += mg_L
            else:
                logger.warning("Species molalities not available for TDS calculation")
                tds_mg_L = 0.0

        except Exception as e:
            logger.warning(f"Error in PHREEQC-based TDS calculation: {e}")
            tds_mg_L = 0.0

        results = {
            "solution_summary": {
                "pH": solution.pH,
                "pe": solution.pe,
                "ionic_strength": solution.mu(),
                "temperature_celsius": solution.temperature,
                "volume_L": solution.volume,
                "mass_kg_water": solution.mass,
                "density_kg_L": solution.density,
                "tds_calculated": tds_mg_L,
            },
            "saturation_indices": solution.phases.copy() if hasattr(solution, "phases") else {},
            "element_totals_molality": solution.elements,
            "species_molality": solution.species_molalities,
        }

        try:
            if hasattr(solution, "sc"):
                sc_attr = solution.sc
                sc_val = sc_attr() if callable(sc_attr) else sc_attr
                if isinstance(sc_val, (int, float)):
                    results["solution_summary"]["specific_conductance_uS_cm"] = sc_val
        except Exception:
            pass

        if solution.pH > 10.0 or solution.total_element("Mg", "mol") > 1e-6:
            important_minerals = ["Mg(OH)2", "Brucite", "Sepiolite", "Chrysotile", "Talc"]
            for mineral in important_minerals:
                try:
                    si_value = solution.si(mineral)
                    if si_value is not None and mineral not in results["saturation_indices"]:
                        results["saturation_indices"][mineral] = si_value
                        logger.info(f"Added SI for {mineral}: {si_value:.3f}")
                except Exception as e:
                    logger.debug(f"Could not get SI for {mineral}: {e}")

        if precipitated_phases:
            results["precipitated_phases"] = precipitated_phases
            results["precipitation_occurred"] = True
        else:
            logger.debug(
                "No precipitated phases found - solution remains undersaturated or minerals not in equilibrium list"
            )
            results["precipitation_occurred"] = False

        if "precipitated_phases" in results:
            total_precipitate_g_L = 0.0
            precipitate_details = {}

            mineral_mw = {
                "Calcite": 100.09, "Aragonite": 100.09, "Gypsum": 172.17, "Anhydrite": 136.14,
                "Brucite": 58.32, "Dolomite": 184.40, "Magnesite": 84.31, "Siderite": 115.86,
                "Fe(OH)3(a)": 106.87, "Ferrihydrite": 106.87, "Al(OH)3(a)": 78.00, "Gibbsite": 78.00,
                "SiO2(a)": 60.08, "Chalcedony": 60.08, "Quartz": 60.08, "Fluorite": 78.07,
                "CaF2": 78.07, "Hydroxyapatite": 502.31, "Ca5(PO4)3(OH)": 502.31,
                "Strengite": 150.82, "FePO4": 150.82, "Struvite": 245.41, "MgNH4PO4:6H2O": 245.41,
            }

            for mineral, moles in results["precipitated_phases"].items():
                mw = mineral_mw.get(mineral, 100.0)
                mass_g = moles * mw
                total_precipitate_g_L += mass_g
                precipitate_details[mineral] = {"moles": moles, "mass_g": mass_g, "mw_g_mol": mw}

            results["total_precipitate_g_L"] = total_precipitate_g_L
            results["precipitate_details"] = precipitate_details
            logger.info(f"Total precipitate: {total_precipitate_g_L:.3f} g/L")

        logger.info(f"Final solution pH: {solution.pH:.2f}, ionic strength: {solution.mu():.4f}")

        return results

    except Exception as e:
        logger.error(f"PhreeqPython simulation failed: {e}", exc_info=True)
        return {"error": f"PhreeqPython simulation failed: {e}"}


async def calculate_kinetic_precipitation(
    pp_instance, solution, minerals: List[str], kinetic_params: Dict[str, Any], temperature: float = 25.0
) -> Dict[str, Any]:
    """
    Calculate time-dependent precipitation using phreeqpython kinetics.

    Args:
        pp_instance: PhreeqPython instance
        solution: Initial phreeqpython solution object
        minerals: List of minerals to model kinetically
        kinetic_params: Dictionary with kinetic parameters
        temperature: Temperature in Celsius

    Returns:
        Dictionary with kinetic precipitation results
    """
    from tools.schemas import KineticPrecipitationProfile

    logger.info(f"Starting kinetic precipitation calculation for {len(minerals)} minerals")

    results = {"kinetic_profiles": [], "kinetic_modeling_used": True, "final_solution": None}

    time_steps = kinetic_params.get("time_steps", [0, 60, 300, 600, 1800, 3600])

    for mineral in minerals:
        if mineral not in kinetic_params.get("minerals_kinetic", {}):
            logger.warning(f"No kinetic parameters provided for {mineral}, skipping")
            continue

        mineral_params = kinetic_params["minerals_kinetic"][mineral]

        def create_rate_function(mineral_name, params, temp_c):
            def rate_function(sol, amount_precipitated, *args):
                try:
                    si = sol.si(mineral_name)
                    nucleation_threshold = params.get("nucleation_si_threshold", 0.0)
                    if si < nucleation_threshold:
                        return 0.0

                    k_25 = params["rate_constant"]
                    Ea = params.get("activation_energy", 48000)
                    R = 8.314
                    T = temp_c + 273.15
                    T_ref = 298.15

                    k_T = k_25 * np.exp(-Ea / R * (1 / T - 1 / T_ref))

                    A_V = params.get("surface_area", 1.0)

                    if amount_precipitated > 0:
                        exponent = params.get("surface_area_exponent", 0.67)
                        m0 = 1e-6
                        m = m0 + amount_precipitated
                        A_V = A_V * (m / m0) ** exponent

                    omega = 10**si
                    rate = k_T * A_V * (omega - 1)

                    if rate < 0:
                        rate = 0

                    return rate

                except Exception as e:
                    logger.error(f"Error in rate function for {mineral_name}: {e}")
                    return 0.0

            return rate_function

        rate_func = create_rate_function(mineral, mineral_params, temperature)

        time_points = []
        amounts_precipitated = []
        saturation_indices = []
        precipitation_rates = []

        try:
            prev_amount = 0
            prev_time = 0

            for time, sol in solution.kinetics(
                formula=mineral, rate_function=rate_func, time=time_steps, m0=0
            ):
                time_points.append(time)

                current_amount = 0
                if hasattr(sol, "phases") and mineral in sol.phases:
                    current_amount = sol.phases[mineral]
                amounts_precipitated.append(current_amount)

                current_si = sol.si(mineral)
                saturation_indices.append(current_si)

                if time > prev_time:
                    rate = (current_amount - prev_amount) / (time - prev_time)
                else:
                    rate = 0
                precipitation_rates.append(rate)

                prev_amount = current_amount
                prev_time = time

                solution = sol

            profile = KineticPrecipitationProfile(
                mineral=mineral,
                time_seconds=time_points,
                amount_precipitated_mol=amounts_precipitated,
                saturation_index=saturation_indices,
                precipitation_rate_mol_s=precipitation_rates,
            )

            results["kinetic_profiles"].append(profile.model_dump())

            logger.info(
                f"Completed kinetic simulation for {mineral}: " f"final amount = {amounts_precipitated[-1]:.6f} mol"
            )

        except Exception as e:
            logger.error(f"Error in kinetic simulation for {mineral}: {e}")
            continue

    if solution:
        final_state = {
            "pH": solution.pH,
            "pe": solution.pe,
            "temperature": solution.temperature,
            "ionic_strength": solution.mu(),
            "saturation_indices": {},
        }

        for mineral in minerals:
            try:
                final_state["saturation_indices"][mineral] = solution.si(mineral)
            except (ValueError, KeyError, AttributeError) as e:
                logger.debug(f"Could not get SI for mineral {mineral}: {e}")

        results["final_solution"] = final_state

    return results


async def calculate_kinetic_precipitation_phreeqc_native(
    solution_data: Dict[str, Any],
    reactants: List[Dict[str, Any]],
    kinetic_params: Dict[str, Any],
    database_path: Optional[str] = None,
    temperature: float = 25.0,
) -> Dict[str, Any]:
    """
    Calculate time-dependent precipitation using PHREEQC's native KINETICS blocks.

    Args:
        solution_data: Initial solution composition
        reactants: List of chemicals added
        kinetic_params: Dictionary with kinetic parameters
        database_path: Path to PHREEQC database
        temperature: Temperature in Celsius

    Returns:
        Dictionary with kinetic precipitation results
    """
    from tools.schemas import KineticPrecipitationProfile

    logger.info("Starting PHREEQC-native kinetic precipitation calculation")

    rates_db_path = _get_phreeqc_rates_path()
    if not rates_db_path:
        if kinetic_params.get("minerals_kinetic"):
            logger.warning("phreeqc_rates.dat not found, kinetic calculations may fail")

    input_lines = []

    if rates_db_path and os.path.exists(rates_db_path):
        input_lines.append(f"INCLUDE {rates_db_path}")
        logger.debug(f"Including PHREEQC rates database from: {rates_db_path}")
    else:
        logger.error("phreeqc_rates.dat not found - kinetic calculations will fail")
        raise FileNotFoundError(
            "phreeqc_rates.dat not found. Set PHREEQC_DATABASE environment variable to your PHREEQC database directory, "
            "or place phreeqc_rates.dat in databases/official/"
        )

    if database_path:
        input_lines.append(f"DATABASE {database_path}")

    solution_block = build_solution_block(solution_data, solution_number=1)
    input_lines.append(solution_block)

    if reactants:
        reaction_block = build_reaction_block(reactants)
        input_lines.append(reaction_block)

    time_steps = kinetic_params.get("time_steps", [0, 60, 300, 600, 1800, 3600])
    minerals_kinetic = kinetic_params.get("minerals_kinetic", {})

    if not isinstance(minerals_kinetic, dict):
        logger.warning(f"minerals_kinetic is not a dict: {type(minerals_kinetic)}, defaulting to empty dict")
        minerals_kinetic = {}

    if minerals_kinetic:
        kinetics_lines = ["KINETICS 1"]

        for mineral, params in minerals_kinetic.items():
            kinetics_lines.append(f"\n{mineral}")

            if "m0" in params:
                kinetics_lines.append(f"    -m0 {params['m0']}")
            else:
                kinetics_lines.append(f"    -m0 0.0")
            if params.get("m0", 0) == 0:
                if "m" not in params or params["m"] is None:
                    kinetics_lines.append(f"    -m 1e-6")

            if "m" in params and params["m"] is not None:
                kinetics_lines.append(f"    -m {params['m']}")
            elif "m0" in params and params["m0"] > 0:
                kinetics_lines.append(f"    -m {params['m0']}")
            else:
                kinetics_lines.append(f"    -m 1e-6")

            if "parms" in params and params["parms"]:
                parms_str = " ".join(str(p) for p in params["parms"])
                kinetics_lines.append(f"    -parms {parms_str}")

            if "tol" in params:
                kinetics_lines.append(f"    -tol {params['tol']}")

        if len(time_steps) > 1:
            time_intervals = []
            for i in range(1, len(time_steps)):
                interval = time_steps[i] - time_steps[i - 1]
                time_intervals.append(interval)

            steps_str = " ".join(str(t) for t in time_intervals)
            kinetics_lines.append(f"    -steps {steps_str}")

        kinetics_lines.append(f"    -bad_step_max 1000")
        kinetics_lines.append(f"    -runge_kutta 3")
        kinetics_lines.append(f"    -step_divide 10")
        kinetics_lines.append(f"    -cvode false")

        input_lines.append("\n".join(kinetics_lines))

    selected_output = build_selected_output_block(block_num=1, saturation_indices=True, totals=True, phases=True)
    input_lines.append(selected_output)
    user_punch_lines = ["USER_PUNCH 1"]
    user_punch_lines.append("    -headings Time")
    if minerals_kinetic and isinstance(minerals_kinetic, dict):
        for mineral in minerals_kinetic.keys():
            user_punch_lines.append(f"    -headings {mineral}_mol")

    user_punch_lines.append("    -start")
    user_punch_lines.append("10 PUNCH TOTAL_TIME/3600  # Convert seconds to hours")

    punch_num = 20
    if minerals_kinetic and isinstance(minerals_kinetic, dict):
        for mineral in minerals_kinetic.keys():
            user_punch_lines.append(f"{punch_num} PUNCH KIN('{mineral}')")
            punch_num += 10

    user_punch_lines.append("    -end")
    input_lines.append("\n".join(user_punch_lines))

    input_lines.append("END")

    phreeqc_input = "\n".join(input_lines)

    logger.info(f"PHREEQC kinetic input length: {len(phreeqc_input)} chars")
    logger.debug(f"PHREEQC input for kinetic simulation:\n{phreeqc_input}")

    debug_file = "debug_kinetic_input.pqi"
    with open(debug_file, "w") as f:
        f.write(phreeqc_input)
    logger.info(f"Saved debug input to {debug_file}")

    try:
        raw_results = await run_phreeqc_simulation(
            phreeqc_input,
            database_path="INLINE",
            num_steps=len(time_steps),
        )

        results = {
            "kinetic_profiles": [],
            "kinetic_modeling_used": True,
            "phreeqc_rates_used": True,
            "final_solution": None,
            "time_series_solutions": [],
            "errors": [],
        }

        for mineral in minerals_kinetic.keys():
            time_points = []
            amounts_precipitated = []
            saturation_indices = []
            precipitation_rates = []

            if isinstance(raw_results, list):
                expected_steps = len(time_steps)
                actual_steps = len(raw_results) if isinstance(raw_results, list) else 1

                if actual_steps < expected_steps:
                    logger.warning(f"Only {actual_steps} of {expected_steps} time steps completed")
                    if isinstance(raw_results, list):
                        for j in range(actual_steps, expected_steps):
                            raw_results.append(
                                {
                                    "error": f"Simulation terminated early - step {j+1} not reached",
                                    "terminated_early": True,
                                }
                            )

                for i, step_result in enumerate(raw_results):
                    if "error" in step_result:
                        error_msg = f"Error at time step {i} ({time_steps[i] if i < len(time_steps) else '?'}s): {step_result.get('error')}"
                        logger.warning(error_msg)
                        results["errors"].append(error_msg)

                        if "RK" in str(step_result.get("error", "")) or "integration" in str(
                            step_result.get("error", "")
                        ):
                            logger.info("RK integration error detected - attempting to recover partial results")
                            if i > 0 and raw_results:
                                prev_result = raw_results[i - 1]
                                time_points.append(time_steps[i] if i < len(time_steps) else 0)
                                amounts_precipitated.append(amounts_precipitated[-1] if amounts_precipitated else 0.0)
                                saturation_indices.append(saturation_indices[-1] if saturation_indices else -999.0)
                                precipitation_rates.append(0.0)

                                if prev_result.get("solution_summary"):
                                    results["time_series_solutions"].append(
                                        {
                                            "time_seconds": time_steps[i] if i < len(time_steps) else 0,
                                            "pH": prev_result["solution_summary"].get("pH"),
                                            "temperature": prev_result["solution_summary"].get(
                                                "temperature_celsius", 25.0
                                            ),
                                            "ionic_strength": prev_result["solution_summary"].get("ionic_strength"),
                                            "elements": prev_result.get("element_totals_molality", {}),
                                            "note": "Approximated from previous step due to RK error",
                                        }
                                    )
                            else:
                                time_points.append(time_steps[i] if i < len(time_steps) else 0)
                                amounts_precipitated.append(0.0)
                                saturation_indices.append(-999.0)
                                precipitation_rates.append(0.0)
                        else:
                            time_points.append(time_steps[i] if i < len(time_steps) else 0)
                            amounts_precipitated.append(0.0)
                            saturation_indices.append(-999.0)
                            precipitation_rates.append(0.0)
                        continue

                    time_points.append(time_steps[i] if i < len(time_steps) else 0)

                    mineral_amount = 0
                    if "phases" in step_result:
                        for phase in step_result["phases"]:
                            if phase.get("name") == mineral:
                                mineral_amount = phase.get("moles", 0)
                                break

                    amounts_precipitated.append(mineral_amount)
                    if mineral_amount <= 1e-12 and i > 0:
                        logger.warning(f"{mineral} appears exhausted at step {i} (amount={mineral_amount})")
                        if i > 0 and saturation_indices:
                            si = saturation_indices[-1]
                        else:
                            si = -999.0
                    else:
                        si = step_result.get("saturation_indices", {}).get(mineral, -999)
                    saturation_indices.append(si)

                    if i > 0 and time_points[i] > time_points[i - 1]:
                        rate = (amounts_precipitated[i] - amounts_precipitated[i - 1]) / (
                            time_points[i] - time_points[i - 1]
                        )
                    else:
                        rate = 0
                    precipitation_rates.append(rate)

                if raw_results:
                    last_result = raw_results[-1]
                    results["final_solution"] = last_result.get("solution_summary", {})
            if time_points:
                profile = KineticPrecipitationProfile(
                    mineral=mineral,
                    time_seconds=time_points,
                    amount_precipitated_mol=amounts_precipitated,
                    saturation_index=saturation_indices,
                    precipitation_rate_mol_s=precipitation_rates,
                )

                results["kinetic_profiles"].append(profile.model_dump())

                logger.info(
                    f"Completed PHREEQC kinetic simulation for {mineral}: "
                    f"final amount = {amounts_precipitated[-1] if amounts_precipitated else 0:.6f} mol"
                )

        if isinstance(raw_results, list) and len(results["time_series_solutions"]) == 0:
            logger.info(f"Collecting time series solutions for {len(raw_results)} time steps")

            for i, step_result in enumerate(raw_results):
                time_point = time_steps[i] if i < len(time_steps) else 0

                if "error" not in step_result:
                    sol_data = {
                        "time_seconds": time_point,
                        "pH": step_result.get("solution_summary", {}).get("pH", None),
                        "temperature": step_result.get("solution_summary", {}).get("temperature_celsius", 25.0),
                        "ionic_strength": step_result.get("solution_summary", {}).get("ionic_strength", None),
                        "elements": step_result.get("element_totals_molality", {}),
                    }

                    results["time_series_solutions"].append(sol_data)
                else:
                    results["time_series_solutions"].append(
                        {"time_seconds": time_point, "error": step_result.get("error", "Unknown error")}
                    )

        return results

    except Exception as e:
        logger.error(f"PHREEQC kinetic simulation failed: {e}")
        return {"error": f"PHREEQC kinetic simulation failed: {e}"}
