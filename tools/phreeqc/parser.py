"""
PHREEQC output parsing module.

Leaf module with no intra-package dependencies.
Handles parsing of SELECTED_OUTPUT files, solution results from PhreeqPython,
and time-series data from kinetics simulations.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _normalize_element_name(element_name: str) -> str:
    """
    Normalize element name by stripping valence state.

    Examples:
        P(5) -> P
        Fe(2) -> Fe
        Fe(3) -> Fe
        S(-2) -> S
        Ca -> Ca
    """
    # Match element symbol followed by optional valence in parentheses
    match = re.match(r"^([A-Z][a-z]?)(\([+-]?\d+\))?$", element_name.strip())
    if match:
        return match.group(1)  # Return just the base element
    return element_name  # Return unchanged if pattern doesn't match


def _is_element_total_column(header: str) -> bool:
    """
    Check if a header represents an element total column.

    Matches: P, Fe, Ca, Mg, P(5), Fe(2), S(-2), etc.
    """
    # Pattern: 1-2 letter element symbol with optional valence
    pattern = r"^[A-Z][a-z]?(\([+-]?\d+\))?$"
    return bool(re.match(pattern, header.strip()))


def _parse_selected_output(selected_output_file: str) -> Dict[str, Any]:
    """
    Parse the SELECTED_OUTPUT file (tab-separated values).

    Args:
        selected_output_file: Path to selected output file

    Returns:
        Dictionary with parsed data
    """
    results = {
        "saturation_indices": {},
        "element_totals_molality": {},
        "species_molalities": {},
        "equilibrium_phase_moles": {},  # From USER_PUNCH equi_* headers
        "surface_adsorbed_moles": {},  # From USER_PUNCH surf_* headers
    }

    try:
        with open(selected_output_file, "r") as f:
            lines = f.readlines()

        if len(lines) < 2:
            logger.warning(f"Selected output file has only {len(lines)} lines, expected >= 2")
            return results

        # First line is headers
        headers = lines[0].strip().split("\t")
        logger.debug(f"Selected output headers ({len(headers)}): {headers[:20]}...")  # Log first 20

        # Get last data row (final state)
        data_line = lines[-1].strip()
        values = data_line.split("\t")

        # Map headers to values
        for i, header in enumerate(headers):
            if i >= len(values):
                break

            value = values[i].strip()
            if not value or value == "-":
                continue

            try:
                float_val = float(value)
            except ValueError:
                continue

            header_clean = header.strip()
            # Remove surrounding quotes (PHREEQC USER_PUNCH headers often have quotes)
            if header_clean.startswith('"') and header_clean.endswith('"'):
                header_clean = header_clean[1:-1]
            header_lower = header_clean.lower()

            # Categorize based on header prefix
            # Handle both underscore format (si_Calcite) and parentheses format (si(Calcite))
            if header_lower.startswith("si_"):
                # Saturation index: si_Calcite -> Calcite
                mineral_name = header_clean[3:]
                results["saturation_indices"][mineral_name] = float_val
            elif header_lower.startswith("si(") and header_clean.endswith(")"):
                # Saturation index: si(Calcite) -> Calcite
                mineral_name = header_clean[3:-1]
                results["saturation_indices"][mineral_name] = float_val
            elif header_lower.startswith("m_"):
                # Molality: m_Ca+2 -> Ca+2
                species_name = header_clean[2:]
                results["species_molalities"][species_name] = float_val
            elif header_lower.startswith("m(") and header_clean.endswith(")"):
                # Molality: m(Ca+2) -> Ca+2
                species_name = header_clean[2:-1]
                results["species_molalities"][species_name] = float_val
            elif header_lower.startswith("equi_"):
                # Equilibrium phase moles from USER_PUNCH: equi_Strengite -> Strengite
                phase_name = header_clean[5:]
                results["equilibrium_phase_moles"][phase_name] = float_val
            elif header_lower.startswith("surf_"):
                # Surface-adsorbed moles from USER_PUNCH: surf_P_Hfo -> P_Hfo
                surface_key = header_clean[5:]
                results["surface_adsorbed_moles"][surface_key] = float_val
            elif header_lower.startswith("tot_"):
                # Total: tot_Ca -> Ca, tot_P(5) -> P (USER_PUNCH format)
                element_name = header_clean[4:]
                base_element = _normalize_element_name(element_name)
                results["element_totals_molality"][base_element] = float_val
                logger.debug(f"Parsed tot_: {header_clean} -> {base_element} = {float_val}")
            elif header_lower.startswith("tot(") and header_clean.endswith(")"):
                # Total: tot(Ca) -> Ca, tot(P(5)) -> P (SELECTED_OUTPUT -tot format)
                element_name = header_clean[4:-1]
                base_element = _normalize_element_name(element_name)
                results["element_totals_molality"][base_element] = float_val
                logger.debug(f"Parsed tot(): {header_clean} -> {base_element} = {float_val}")
            elif _is_element_total_column(header_clean):
                # Bare element names like P, Fe, Ca, P(5), Fe(2) from -tot true output
                base_element = _normalize_element_name(header_clean)
                results["element_totals_molality"][base_element] = float_val
                logger.debug(f"Parsed element name: {header_clean} -> {base_element} = {float_val}")
            elif header_lower == "ph":
                results.setdefault("solution_summary", {})["pH"] = float_val
            elif header_lower == "pe":
                results.setdefault("solution_summary", {})["pe"] = float_val
            elif header_lower in ["temp", "temp(c)", "temperature"]:
                results.setdefault("solution_summary", {})["temperature_celsius"] = float_val
            elif header_lower in ["mu", "ionic_strength"]:
                results.setdefault("solution_summary", {})["ionic_strength_molal"] = float_val
            elif header_lower == "alk":
                results.setdefault("solution_summary", {})["alkalinity_eq_L"] = float_val

    except Exception as e:
        logger.warning(f"Error parsing SELECTED_OUTPUT file: {e}")

    return results


def _parse_selected_output_time_series(pp_instance, num_steps: int) -> Optional[List[Dict[str, Any]]]:
    """
    Parse SELECTED_OUTPUT array from VIPhreeqc for multi-step kinetics.

    VIPhreeqc only keeps the final solution for KINETICS simulations, but
    SELECTED_OUTPUT captures data at every time step. This function builds
    a list of result dicts from those rows.

    Handles two formats:
    - Standard columns present (sim, state, soln, time, step) when -reset true
    - No standard columns when -reset false (rows are in time order)

    Returns None if selected output is unavailable or empty.
    """
    if not hasattr(pp_instance, "ip") or not hasattr(pp_instance.ip, "get_selected_output_array"):
        return None

    try:
        arr = pp_instance.ip.get_selected_output_array()
        if not arr or len(arr) < 2:
            return None

        headers = [str(h) for h in arr[0]]
        col = {h: i for i, h in enumerate(headers)}

        # Determine if standard columns are present
        has_state = "state" in col
        has_time = "time" in col

        data_rows = arr[1:]

        # If we have "state" column, filter to only "react" rows (kinetic steps)
        if has_state:
            state_idx = col["state"]
            data_rows = [r for r in data_rows if str(r[state_idx]).strip().lower() == "react"]

        if not data_rows:
            return None

        results_list = []
        for row in data_rows:
            step_result = {
                "solution_summary": {},
                "saturation_indices": {},
                "element_totals_molality": {},
                "species_molality": {},
            }
            summary = step_result["solution_summary"]

            for h, idx in col.items():
                if idx >= len(row):
                    continue
                val = row[idx]
                if val is None:
                    continue

                h_lower = h.lower().strip()

                # Standard columns
                if h_lower == "ph":
                    summary["pH"] = float(val)
                elif h_lower == "pe":
                    summary["pe"] = float(val)
                elif h_lower in ("temp", "temp(c)", "temperature"):
                    summary["temperature_celsius"] = float(val)
                elif h_lower in ("mu", "ionic_strength"):
                    summary["ionic_strength"] = float(val)
                elif h_lower == "time":
                    summary["time_seconds"] = float(val)
                # SI columns
                elif h_lower.startswith("si_") or (h_lower.startswith("si(") and h.endswith(")")):
                    mineral = h[3:] if h_lower.startswith("si_") else h[3:-1]
                    try:
                        step_result["saturation_indices"][mineral] = float(val)
                    except (ValueError, TypeError):
                        pass
                # Element totals
                elif _is_element_total_column(h.strip()):
                    base = _normalize_element_name(h.strip())
                    try:
                        step_result["element_totals_molality"][base] = float(val)
                    except (ValueError, TypeError):
                        pass
                elif h_lower.startswith("tot_") or (h_lower.startswith("tot(") and h.endswith(")")):
                    elem = h[4:] if h_lower.startswith("tot_") else h[4:-1]
                    base = _normalize_element_name(elem)
                    try:
                        step_result["element_totals_molality"][base] = float(val)
                    except (ValueError, TypeError):
                        pass
                # Molalities
                elif h_lower.startswith("m_") or (h_lower.startswith("m(") and h.endswith(")")):
                    species = h[2:] if h_lower.startswith("m_") else h[2:-1]
                    try:
                        step_result["species_molality"][species] = float(val)
                    except (ValueError, TypeError):
                        pass

            results_list.append(step_result)

        return results_list if results_list else None

    except Exception as e:
        logger.warning(f"Error parsing SELECTED_OUTPUT time series: {e}")
        return None


def parse_phreeqc_results(pp_instance, num_steps: int = 1) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Parses results from a PhreeqPython instance after a run.

    Args:
        pp_instance: PhreeqPython instance with completed simulation
        num_steps: Number of time steps to parse

    Returns:
        Dictionary for single step, or list of dictionaries for multi-step results
    """
    from utils.exceptions import ErrorType

    all_step_results = []

    try:
        raw_solutions = pp_instance.get_solution_list()
        # Filter out negative (built-in) solution IDs like -2, -1
        solutions = [s for s in (raw_solutions or []) if s >= 0]
        num_solutions = len(solutions)
        logger.debug(f"Parsing results. Found {num_solutions} user solutions (raw: {raw_solutions})")

        # For kinetics with multiple steps, VIPhreeqc only keeps the LAST
        # solution in the solution list (each step overwrites the previous).
        # Intermediate results are available via SELECTED_OUTPUT.
        if num_steps > 1 and num_solutions <= 1:
            # Try to build time-series from SELECTED_OUTPUT array
            so_results = _parse_selected_output_time_series(pp_instance, num_steps)
            if so_results:
                logger.debug(f"Built {len(so_results)} time-series entries from SELECTED_OUTPUT")
                return so_results
            logger.debug("SELECTED_OUTPUT parsing returned no results; falling back to solution list")

        if num_steps > 1 and num_solutions > 1:
            # Multi-step with multiple solutions available
            start_index = 0
            end_index = num_solutions
        else:
            # Single step - just get the last solution
            start_index = max(0, num_solutions - 1)
            end_index = num_solutions

        logger.debug(f"Parsing solutions from index {start_index} to {end_index} (total: {num_solutions})")

        for i in range(start_index, end_index):
            current_step_results = {}
            solution_number = solutions[i]  # This is the solution number, not the solution object
            solution = pp_instance.get_solution(solution_number)

            if not solution:
                logger.warning(f"Could not get solution object for number {solution_number}")
                continue

            # Get solution properties directly from the solution object
            # Basic Solution Summary
            current_step_results["solution_summary"] = {}
            summary = current_step_results["solution_summary"]

            summary["step_number"] = i

            # Get standard properties that should be available
            summary["pH"] = solution.pH if hasattr(solution, "pH") else 7.0
            summary["pe"] = solution.pe if hasattr(solution, "pe") else 4.0

            # Density
            if hasattr(solution, "density"):
                summary["density_kg_L"] = solution.density

            # Temperature
            if hasattr(solution, "temperature"):
                summary["temperature_celsius"] = solution.temperature

            # Volume
            if hasattr(solution, "volume"):
                summary["volume_L"] = solution.volume

            # Mass of water
            if hasattr(solution, "mass"):
                summary["mass_kg_water"] = solution.mass

            # Specific conductance
            if hasattr(solution, "sc"):
                try:
                    sc_attr = solution.sc
                    summary["specific_conductance_uS_cm"] = sc_attr() if callable(sc_attr) else sc_attr
                except Exception:
                    # Leave unset if not available
                    pass

            # Ionic strength using solution.mu method if available
            try:
                if hasattr(solution, "mu") and callable(solution.mu):
                    summary["ionic_strength"] = solution.mu()
                elif hasattr(solution, "I"):
                    summary["ionic_strength"] = solution.I
                else:
                    summary["ionic_strength"] = 0.0
            except Exception as e:
                logger.warning(f"Error getting ionic strength: {e}")
                summary["ionic_strength"] = 0.0

            # Calculate TDS (Total Dissolved Solids) using proper PHREEQC method
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
                    }

                    for species, molality in solution.species_molalities.items():
                        if molality > 1e-12:
                            mw = species_molecular_weights.get(species)
                            if mw:
                                mg_L = molality * mw * 1000
                                tds_mg_L += mg_L

                    if tds_mg_L < 10:
                        logger.debug("Species-based TDS calculation yielded low result, using element totals as backup")
                        if hasattr(solution, "elements"):
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
                logger.warning(f"Error calculating TDS from PHREEQC solution: {e}")
                tds_mg_L = 0.0

            summary["tds_calculated"] = tds_mg_L

            # Saturation Indices using solution.phases
            try:
                if hasattr(solution, "phases"):
                    current_step_results["saturation_indices"] = solution.phases
                else:
                    current_step_results["saturation_indices"] = {}

                if summary.get("pH", 0) > 10.0:
                    important_minerals = ["Mg(OH)2", "Brucite", "Sepiolite", "Chrysotile"]
                    for mineral in important_minerals:
                        try:
                            if hasattr(solution, "si") and callable(solution.si):
                                si_value = solution.si(mineral)
                                if si_value is not None and mineral not in current_step_results["saturation_indices"]:
                                    current_step_results["saturation_indices"][mineral] = si_value
                                    logger.info(f"Added SI for {mineral}: {si_value}")
                        except Exception as mineral_e:
                            logger.debug(f"Could not get SI for {mineral}: {mineral_e}")

            except Exception as si_e:
                logger.warning(f"Could not get saturation indices: {si_e}")
                current_step_results["saturation_indices"] = {}

            # Extract precipitated phases if available
            try:
                if hasattr(pp_instance, "phases") and pp_instance.phases:
                    precipitated_phases = {}
                    for phase_name, phase_obj in pp_instance.phases.items():
                        if hasattr(phase_obj, "moles") and phase_obj.moles > 0:
                            precipitated_phases[phase_name] = phase_obj.moles
                    if precipitated_phases:
                        current_step_results["precipitated_phases"] = precipitated_phases
            except Exception as pp_e:
                logger.debug(f"Could not extract precipitated phases: {pp_e}")

            # Element totals using solution.elements
            try:
                if hasattr(solution, "elements"):
                    current_step_results["element_totals_molality"] = solution.elements
                else:
                    current_step_results["element_totals_molality"] = {}
            except Exception as el_e:
                logger.warning(f"Could not get element totals: {el_e}")
                current_step_results["element_totals_molality"] = {}

            # Species molalities using solution.species_molalities
            try:
                if hasattr(solution, "species_molalities"):
                    current_step_results["species_molality"] = solution.species_molalities
                else:
                    current_step_results["species_molality"] = {}
            except Exception as sp_e:
                logger.warning(f"Could not get species molalities: {sp_e}")
                current_step_results["species_molality"] = {}

            # Extract composite parameters from SELECTED_OUTPUT (USER_PUNCH data)
            try:
                selected_output_data = {}
                if hasattr(pp_instance.ip, "get_selected_output_value"):
                    composite_headers = ["Total_Hardness_CaCO3", "Carbonate_Alkalinity_CaCO3", "TDS_Species"]

                    if hasattr(pp_instance.ip, "row_count") and hasattr(pp_instance.ip, "column_count"):
                        rows = pp_instance.ip.row_count
                        cols = pp_instance.ip.column_count

                        if rows > 0 and cols > 0:
                            row_idx = rows - 1

                            for col_idx, header in enumerate(composite_headers):
                                if col_idx < cols:
                                    try:
                                        value = pp_instance.ip.get_selected_output_value(row_idx, col_idx)
                                        if value is not None and isinstance(value, (int, float)):
                                            selected_output_data[header] = value
                                    except Exception:
                                        pass

                if selected_output_data:
                    current_step_results["selected_output_data"] = selected_output_data
                    logger.debug(f"Extracted composite parameters: {selected_output_data}")

            except Exception as so_e:
                logger.debug(f"Could not extract selected output data: {so_e}")

            all_step_results.append(current_step_results)

    except Exception as e:
        logger.error(f"Error parsing PHREEQC results: {e}", exc_info=True)
        error_message = f"Error parsing PHREEQC results: {e}"
        error_result = {"error": error_message, "error_type": ErrorType.RESULT_PARSING_ERROR}
        return [error_result] * num_steps if num_steps > 1 else error_result

    if not all_step_results:
        error_message = "Simulation produced no solution results."
        logger.warning(error_message)
        error_result = {
            "error": error_message,
            "error_type": ErrorType.EMPTY_RESULTS,
            "suggestion": "Check your input for errors or try a different database.",
        }
        return [error_result] * num_steps if num_steps > 1 else error_result

    # Return list if multiple steps, otherwise single dictionary
    return all_step_results if len(all_step_results) > 1 else all_step_results[0]
