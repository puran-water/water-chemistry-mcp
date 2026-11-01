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

from water_chemistry_mcp.utils.exceptions import ErrorType, PhreeqcError
from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_block, build_reaction_block, build_selected_output_block, build_solution_block
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.errors import WaterChemistryError
from water_chemistry_mcp.utils.import_helpers import DEFAULT_DATABASE, PHREEQPYTHON_AVAILABLE
from water_chemistry_mcp.waterchem.answered_by import record_run
from water_chemistry_mcp.waterchem.input_key_rulings import DATABASE_CHOICE_RULING_ID, FALLBACKS_D1_5_RULING_ID

from .backend import (
    USE_SUBPROCESS,
    _get_phreeqc_executable,
    _get_phreeqc_rates_path,
    run_phreeqc_subprocess,
    undeclared_engine_message,
)
from .parser import _parse_selected_output_time_series, parse_phreeqc_results

logger = logging.getLogger(__name__)


def get_mineral_alternatives(mineral_name, database_path=None):
    "The names under which the database defines the SAME phase as the mineral named, for a 'phase not found' error.\n\n    Returns:\n        dict: {name the database defines: what the phase is}"
    if not database_path or not os.path.isfile(str(database_path)):
        return {}
    from water_chemistry_mcp.waterchem.phase_choice import same_phase_alternatives

    return same_phase_alternatives(mineral_name, database_path)


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


_DATABASE_LINE = re.compile(r"^[ \t]*DATABASE[ \t]+(?P<path>\S.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _inline_database(input_string: str) -> tuple:
    'Public engineering calculation interface.'
    lines = _DATABASE_LINE.findall(input_string)
    if len(lines) != 1:
        raise PhreeqcError(
            f"An INLINE run names its database on exactly one DATABASE line; this input has {len(lines)}. The "
            f"dependency's bundled phreeqc.dat would otherwise answer ({DATABASE_CHOICE_RULING_ID}).",
            context={"error_type": ErrorType.DATABASE_NOT_FOUND},
        )
    path = lines[0].strip()
    if not os.path.isfile(path):
        raise PhreeqcError(
            f"The INLINE run's DATABASE line names a file that is not there: {redact_paths(path)} "
            f"({DATABASE_CHOICE_RULING_ID}).",
            database=path,
            context={"error_type": ErrorType.DATABASE_NOT_FOUND},
        )
    return _DATABASE_LINE.sub("", input_string, count=1), path


def _run_database(database_path: Optional[str]) -> str:
    'Public engineering calculation interface.'
    chosen = database_path or DEFAULT_DATABASE
    if not chosen:
        raise PhreeqcError(
            "No database named and the house default, the repository's databases/official/phreeqc.dat, is not "
            f"installed: name a database ({DATABASE_CHOICE_RULING_ID}).",
            context={"error_type": ErrorType.DATABASE_NOT_FOUND},
        )
    return chosen


def _with_state_column(input_string: str) -> str:
    'Public engineering calculation interface.'
    lines = input_string.split("\n")
    out = []
    index = 0
    while index < len(lines):
        line = lines[index]
        out.append(line)
        index += 1
        if not line.strip().upper().startswith("SELECTED_OUTPUT"):
            continue
        block = []
        while index < len(lines) and not lines[index][:1].strip() and lines[index].strip():
            block.append(lines[index])
            index += 1
        out.extend(block)
        # after the block's own options: a "-reset false" earlier in it would switch these off again
        options = [entry.strip().lower() for entry in block]
        if not any(entry.startswith("-state") for entry in options):
            out.append("    -state true")
        if not any(entry.startswith("-time") for entry in options):
            out.append("    -time true")
    return "\n".join(out)


def _drop_step_rows(result) -> None:
    """The raw step rows the time-series parser keeps for the batch-redox check never reach an answer."""
    for item in result if isinstance(result, list) else [result]:
        if isinstance(item, dict):
            item.pop("_selected_output_row", None)


def _take_private(result, key: str):
    """Take ``key`` off every item of an engine's answer (the standalone engine puts what the run's checks read on its
    first item: its warnings and its SELECTED_OUTPUT rows); returns the first value found, else None."""
    found = None
    for item in result if isinstance(result, list) else [result]:
        if isinstance(item, dict) and key in item:
            value = item.pop(key)
            found = value if found is None else found
    return found


def _refuse_unconverged(warnings: str, *, engine: str, database_path: str) -> None:
    'Public engineering calculation interface.'
    from puran_mcp_support.engineering.phreeqc_convergence import AddFormulaNotConvergedError, refuse_unconverged_add_formula
    from water_chemistry_mcp.waterchem.errors import PurePhaseNotConvergedError

    try:
        refuse_unconverged_add_formula(
            warnings, step=f"the PHREEQC run on {os.path.basename(database_path)} ({engine} engine)")
    except AddFormulaNotConvergedError as exc:
        raise PurePhaseNotConvergedError(
            str(exc), call_stage="phreeqc_convergence",
            details={"phases": list(exc.phases), "engine": engine,
                     "database": os.path.basename(database_path)}) from exc


def _every_step(result, num_steps: int, engine: str):
    'Public engineering calculation interface.'
    if num_steps <= 1:
        return result
    steps = result if isinstance(result, list) else [result]
    if len(steps) != num_steps:
        raise PhreeqcError(
            f'The {engine} run answered {len(steps)} step(s) where {num_steps} were asked: a multi-step answer carries every step or none (review M20, PUBLIC-RULE-8226e460e1d8)',
            context={"engine": engine, "steps_asked": num_steps, "steps_answered": len(steps)},
        )
    return steps


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
    from water_chemistry_mcp.waterchem.analysis import SPECIES_MARKER
    from water_chemistry_mcp.waterchem.defined_analytes import refuse_undefined_solution_analytes

    # One database per run, the one named (an INLINE input names it on its DATABASE line), else the house default;
    # nothing else picks it, and the run records which it loaded (`waterchem.answered_by`).
    if database_path == "INLINE":
        input_string, database_path = _inline_database(input_string)
    else:
        database_path = _run_database(database_path)

    if num_steps > 1:
        input_string = _with_state_column(input_string)

    # PHREEQC takes a SOLUTION name its database does not define as 0, without an error: refused here, on every run
    # (the phosphorus screen and the optimiser call this function directly, not through the runner)
    refuse_undefined_solution_analytes(input_string, database_path)

    if SPECIES_MARKER in input_string:
        # A carbonate SPECIES amount is resolved to its C(4) total on THIS run's database, at the input's pH and
        
        # so the same engine. The record goes back on every result as `input_basis_conversions`.
        from water_chemistry_mcp.waterchem.carbonate_species import resolve_species_basis

        input_string, conversions = await resolve_species_basis(input_string, database_path, run_phreeqc_simulation)
        result = await run_phreeqc_simulation(input_string, database_path=database_path, num_steps=num_steps)
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, dict):
                item["input_basis_conversions"] = conversions
        return result

    # A batch step holds ammonium, nitrate, nitrite, sulfide and sulfate as fed unless the tool call asked
    
    # input, and its answer is checked (a coupled state no feed carried refuses) and mapped back to the coupled names.
    from water_chemistry_mcp.waterchem.batch_redox import POLICY_EQUILIBRATED, equilibrate_requested, hold_batch_redox, is_batch_input, record_held_run
    from water_chemistry_mcp.waterchem.chemical_model import chemical_model

    requested_input = input_string
    held = None if equilibrate_requested() else hold_batch_redox(input_string, database_path)
    if held is not None:
        input_string = held.input_string
    elif equilibrate_requested() and is_batch_input(input_string):
        record_held_run({"policy": POLICY_EQUILIBRATED})
    # the chemical model this run solves, recorded with its database on the answer (review M20, gap G08)
    model = chemical_model(
        input_string, equilibrate_requested=equilibrate_requested(),
        reactions_as_requested=held.reactions_as_requested if held is not None else None,
        requested_input=requested_input)

    
    # (the default) is the standalone PHREEQC, and with no executable declared the run refuses; it had answered on
    # phreeqpython's build whenever the machine had no binary (OCI, and the laptop service, whose PATH has none), while an
    # agent shell with one answered on the subprocess. A failed standalone run refuses with its reason and no other
    
    declared_by = f"USE_PHREEQC_SUBPROCESS={1 if USE_SUBPROCESS else 0}"
    if USE_SUBPROCESS:
        phreeqc_exe = _get_phreeqc_executable()
        if not phreeqc_exe:
            raise PhreeqcError(
                message=undeclared_engine_message(os.path.basename(database_path)),
                database=database_path,
                input_excerpt=get_truncated_input(input_string),
                context={"engine": "subprocess", "declared_by": declared_by},
            )
        if phreeqc_exe:
            logger.info("Using PHREEQC subprocess for full database compatibility")
            try:
                # the step count only for a multi-step run: a single-step call keeps the runner's two-argument form
                steps = {"num_steps": num_steps} if num_steps > 1 else {}
                result = await run_phreeqc_subprocess(input_string, database_path, **steps)
            except PhreeqcError as e:
                raise PhreeqcError(
                    message=(
                        f"The standalone PHREEQC run on {os.path.basename(database_path)} failed: "
                        f"{redact_paths(str(e))[:600]}. It is refused, not answered by phreeqpython's engine "
                        f"(D1 of {FALLBACKS_D1_5_RULING_ID})."
                    ),
                    phreeqc_errors=getattr(e, "phreeqc_errors", None),
                    database=database_path,
                    minerals=extract_minerals_from_input(input_string),
                    input_excerpt=get_truncated_input(input_string),
                    context={**(getattr(e, "context", None) or {}), "engine": "subprocess"},
                ) from e
            warnings = _take_private(result, "_phreeqc_warnings") or ""
            rows = _take_private(result, "_selected_output_rows")
            if warnings:
                logger.warning("PHREEQC (standalone) warnings:\n%s", redact_paths(warnings))
            _refuse_unconverged(warnings, engine="subprocess", database_path=database_path)
            if held is not None:
                result = held.check_and_fold(result, engine="subprocess", rows=rows)
            else:
                _drop_step_rows(result)
            record_run(database_path, "subprocess", model, declared_by=declared_by)
            return _every_step(result, num_steps, "subprocess")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")

    # Import here to avoid import errors if the library is not available
    from phreeqpython import PhreeqPython

    # A one-step answer is read from the highest-numbered solution the run STORED: a batch step that SAVEs nothing, or
    # saves below a stored input, would be answered with an input water while the punched columns are the batch's (the
    # mixing tool returned its last input water as the mixture, a one-step kinetic run its initial water). Refused by
    
    # from its selected output and must answer every step (`_every_step`).
    if num_steps <= 1:
        from water_chemistry_mcp.waterchem.batch_save import refuse_unsaved_batch

        refuse_unsaved_batch(input_string)

    # One deterministic default (`_run_database`). Databases disagree on the INPUT formula a
    # mg/L value is read against -- phreeqc.dat reads Si as SiO2 (60.08) and
    # Alkalinity as CaCO3, minteq.dat reads Si as H4SiO4 (96.12) and
    # Alkalinity as HCO3 -- so silently substituting one for another changes
    # the caller's stated basis by tens of percent with no error. A tool that
    # needs a mineral the default lacks (Brucite/Mg(OH)2 for lime softening
    # and struvite work) names its database in its own schema.
    db_to_use = database_path
    pp = None

    try:
        if db_to_use:
            logger.info(f"Using PHREEQC database: {db_to_use}")
            if not os.path.exists(db_to_use):
                logger.error(f"Database file does not exist: {db_to_use}")
                raise FileNotFoundError(f"Database file does not exist: {db_to_use}")

            from pathlib import Path

            db_basename = os.path.basename(db_to_use)
            db_directory = Path(os.path.dirname(db_to_use))

            try:
                # the named file, from its own directory, loaded once: never a same-named file bundled with
                # phreeqpython, and never an unnamed instance (phreeqpython's default, vitens.dat) that loads it
                
                pp = PhreeqPython(database=db_basename, database_directory=db_directory)
                logger.info(f"Successfully created PhreeqPython with database: {db_basename} from {db_directory}")
            except Exception as e:
                raise PhreeqcError(
                    f"PHREEQC database {db_basename} could not be loaded by phreeqpython: {type(e).__name__}: "
                    f"{redact_paths(str(e))[:300]}; nothing loads in its place ({FALLBACKS_D1_5_RULING_ID})",
                    database=db_to_use,
                    context={"error_type": ErrorType.DATABASE_NOT_FOUND},
                ) from e
        else:  # pragma: no cover - _run_database refuses first
            raise PhreeqcError(f"No database to load ({DATABASE_CHOICE_RULING_ID}).")

        logger.debug(f"Running PHREEQC input:\n------\n{input_string}\n------")
        output = pp.ip.run_string(input_string)

        # PHREEQC's errors and warnings. VIPhreeqc wraps no get_warning_string, so the warnings are read from the library
        # itself (`engineering_utils.phreeqc_convergence.phreeqpython_warning_text`): the old hasattr branch never read
        
        from puran_mcp_support.engineering.phreeqc_convergence import phreeqpython_warning_text

        error_messages = pp.ip.get_error_string() if hasattr(pp.ip, "get_error_string") else ""
        warning_messages = phreeqpython_warning_text(pp.ip)
        try:
            selected_rows = pp.ip.get_selected_output_array()  # read before anything else runs on the instance
        except Exception:  # a run with no SELECTED_OUTPUT; a held run always has one, and refuses below without rows
            selected_rows = []
        logger.debug(f"PHREEQC Errors: {error_messages}")
        logger.debug(f"PHREEQC Warnings: {warning_messages}")
        convergence_warnings = warning_messages
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
                        f"The mineral '{missing_phase}' is not a phase of the selected database. Name a database that defines it; only the same phase under another name is offered below (D3 of PUBLIC-RULE-762de2e5f9c5)."
                    )

                    alternatives = get_mineral_alternatives(missing_phase, db_to_use)
                    if alternatives:
                        alt_list = ", ".join([f"{alt} ({formula})" for alt, formula in alternatives.items()])
                        context["alternatives"] = alternatives
                        context["suggestion"] += f" The same phase there: {alt_list}"

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

        _refuse_unconverged(convergence_warnings, engine="phreeqpython", database_path=db_to_use)
        logger.info("PHREEQC simulation finished successfully.")
        results = parse_phreeqc_results(pp, num_steps=num_steps, database_path=db_to_use, extra_text=input_string)
        if held is not None:
            results = held.check_and_fold(results, engine="phreeqpython", rows=selected_rows)
        else:
            _drop_step_rows(results)
        record_run(db_to_use, "phreeqpython", model, declared_by=declared_by)
        return _every_step(results, num_steps, "phreeqpython")

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
    except WaterChemistryError:
        raise  # a typed refusal (the batch-redox check: `waterchem.batch_redox`) reaches its tool as itself
    except Exception as e:
        logger.error("Exception during PHREEQC simulation or setup: %s", redact_paths(str(e)))
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

    db_to_use = _run_database(database_path)
    if not os.path.exists(db_to_use):
        raise PhreeqcError(f"Database file does not exist: {redact_paths(db_to_use)} ({DATABASE_CHOICE_RULING_ID})",
                           database=db_to_use, context={"error_type": ErrorType.DATABASE_NOT_FOUND})

    try:
        db_basename = os.path.basename(db_to_use)
        db_directory = Path(os.path.dirname(db_to_use))
        try:
            # the named file, from its own directory: never a same-named file bundled with phreeqpython
            pp = PhreeqPython(database=db_basename, database_directory=db_directory)
            logger.info(f"Created PhreeqPython with database: {db_basename} from {db_directory}")
        except Exception as e:
            logger.warning(f"Could not use database_directory, trying full path: {e}")
            pp = PhreeqPython(database=db_to_use)

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
        from puran_mcp_support.engineering.phreeqc_saturation import saturation_index, saturation_indices

        if allow_precipitation and equilibrium_minerals:
            valid_minerals = []
            for mineral in equilibrium_minerals:
                try:
                    if solution.si(mineral) > -900:     # VIPhreeqc's sentinel: the water carries the phase's elements
                        valid_minerals.append(mineral)
                except Exception:
                    pass

            if valid_minerals:
                logger.info(f"Using {len(valid_minerals)} valid minerals for simultaneous equilibration")

                supersaturated_minerals = []
                for mineral in valid_minerals:
                    try:
                        
                        si = saturation_index(solution, mineral, database_path=db_to_use)
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
                                initial_si = saturation_index(solution, mineral, database_path=db_to_use)
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
            
            "saturation_indices": saturation_indices(solution, database_path=db_to_use),
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

        # (the high-pH magnesium silicates and hydroxides are among the database phases `saturation_indices` already
        # reads, water activity included: no second, uncorrected read of them)

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
    pp_instance, solution, minerals: List[str], kinetic_params: Dict[str, Any], temperature: float = 25.0, *,
    database_path: str,
) -> Dict[str, Any]:
    'Calculate time-dependent precipitation using phreeqpython kinetics.\n\n    Returns:\n        Dictionary with kinetic precipitation results'
    from water_chemistry_mcp.tools.schemas import KineticPrecipitationProfile

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
                    from puran_mcp_support.engineering.phreeqc_saturation import saturation_index

                    si = saturation_index(sol, mineral_name, database_path=database_path)
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

                from puran_mcp_support.engineering.phreeqc_saturation import saturation_index

                current_si = saturation_index(sol, mineral, database_path=database_path)
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

        from puran_mcp_support.engineering.phreeqc_saturation import saturation_index

        for mineral in minerals:
            try:
                final_state["saturation_indices"][mineral] = saturation_index(solution, mineral,
                                                                              database_path=database_path)
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
    from water_chemistry_mcp.tools.schemas import KineticPrecipitationProfile

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
