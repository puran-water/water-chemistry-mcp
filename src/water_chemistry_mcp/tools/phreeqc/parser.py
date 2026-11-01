"""
PHREEQC output parsing module.

Leaf module with no intra-package dependencies.
Handles parsing of SELECTED_OUTPUT files, solution results from PhreeqPython,
and time-series data from kinetics simulations.
"""

import logging
import math
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _required_finite_solution_attribute(solution: Any, attribute: str) -> float:
    """Return a required scalar from a PHREEQC solution or fail closed."""

    if not hasattr(solution, attribute):
        raise ValueError(f"PHREEQC solution omitted required {attribute} output")
    value = getattr(solution, attribute)
    value = value() if callable(value) else value
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"PHREEQC solution returned a nonnumeric {attribute} output"
        ) from exc
    if not math.isfinite(numeric):
        raise ValueError(f"PHREEQC solution returned a nonfinite {attribute} output")
    return numeric


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


_MOL_KGW_SUFFIX = "(mol/kgw)"


def _punched_total_element_name(header: str) -> Optional[str]:
    """
    Return the element(-valence) name for a punched ``-totals`` column, else None.

    Standalone PHREEQC punch files write bare headers (``Mg``, ``S(-2)``);
    VIPhreeqc's get_selected_output_array appends the unit, giving
    ``Mg(mol/kgw)`` / ``S(-2)(mol/kgw)``. Both forms resolve here.
    """
    name = header.strip()
    if name.lower().endswith(_MOL_KGW_SUFFIX):
        name = name[: -len(_MOL_KGW_SUFFIX)].strip()
    if _is_element_total_column(name):
        return name
    return None


def _selected_output_header(header: Any) -> str:
    "A SELECTED_OUTPUT column's name as the standalone engine's punch file writes it, on either engine."
    name = str(header).strip()
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    if name.lower().endswith(_MOL_KGW_SUFFIX):
        name = name[: -len(_MOL_KGW_SUFFIX)].strip()
    return _VIPHREEQC_SCALAR_HEADERS.get(name, name)


#: The scalar columns VIPhreeqc's array names with their unit (``-temperature``, ``-alkalinity``, ``-charge_balance``),
#: by the punch file's name for them (measured on both engines 2026-10-17, closure c16b-chem scripts/probe_so_headers2.py)
_VIPHREEQC_SCALAR_HEADERS = {"temp(C)": "temp", "Alk(eq/kgw)": "Alk", "charge(eq)": "charge"}


def _store_element_total(results: Dict[str, Any], element_name: str, value: float) -> str:
    """Store analytical total without losing an explicitly selected redox state."""

    name = element_name.strip()
    base = _normalize_element_name(name)
    if name != base:
        results["element_redox_totals_molality"][name] = value
        # Preserve backward compatibility for a lone valence-specific column,
        # but never let it overwrite an explicitly selected all-valence total.
        results["element_totals_molality"].setdefault(base, value)
    else:
        results["element_totals_molality"][base] = value
    return base


def _parse_selected_output(selected_output_file: str) -> Dict[str, Any]:
    """
    Parse the SELECTED_OUTPUT file (tab-separated values).

    Args:
        selected_output_file: Path to selected output file

    Returns:
        Dictionary with parsed data
    """
    results = _empty_selected_results()

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

        pairs = []
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
            pairs.append((header, float_val))
        _categorize_selected_columns(pairs, results)

    except Exception as e:
        logger.warning(f"Error parsing SELECTED_OUTPUT file: {e}")

    return results


def _empty_selected_results() -> Dict[str, Any]:
    return {
        "saturation_indices": {},
        "element_totals_molality": {},
        "element_redox_totals_molality": {},
        "species_molalities": {},
        "species_activities": {},
        "equilibrium_phase_moles": {},  # From USER_PUNCH equi_* headers
        "surface_adsorbed_moles": {},  # From USER_PUNCH surf_* headers
        "system_element_moles": {},  # From USER_PUNCH sys_* headers
        "selected_output_data": {},  # Preserve native numeric USER_PUNCH evidence.
    }


def _add_alkalinity_basis(summary: Dict[str, Any]) -> None:
    """Keep legacy numeric compatibility while making the PHREEQC basis explicit."""
    if "alkalinity_eq_kgw" not in summary:
        return
    alkalinity = summary["alkalinity_eq_kgw"]
    summary["alkalinity_eq_L"] = alkalinity  # Deprecated historical mislabel.
    summary.setdefault("deprecated_fields", {})["alkalinity_eq_L"] = {
        "actual_unit": "eq/kgw", "replacement": "alkalinity_eq_kgw",
        "message": "Historical field name is incorrect; do not interpret this value as eq/L.",
    }
    mass, volume = summary.get("mass_kg_water"), summary.get("volume_L")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
           for v in (alkalinity, mass, volume)) and mass > 0 and volume > 0:
        summary["alkalinity_volumetric_eq_L"] = alkalinity * mass / volume

def _categorize_selected_columns(pairs, results: Dict[str, Any]) -> Dict[str, Any]:
    'Public engineering calculation interface.'
    for header, float_val in pairs:
        header_clean = _selected_output_header(header)
        header_lower = header_clean.lower()
        results["selected_output_data"][header_clean] = float_val

        # Categorize based on header prefix
        # Handle both underscore format (si_Calcite) and parentheses format (si(Calcite))
        if header_lower.startswith("si_"):
            # Saturation index: si_Calcite -> Calcite
            mineral_name = header_clean[3:]
            if float_val > -999.0:
                results["saturation_indices"][mineral_name] = float_val
        elif header_lower.startswith("si(") and header_clean.endswith(")"):
            # Saturation index: si(Calcite) -> Calcite
            mineral_name = header_clean[3:-1]
            if float_val > -999.0:
                results["saturation_indices"][mineral_name] = float_val
        elif header_lower.startswith("m_"):
            # Molality: m_Ca+2 -> Ca+2
            species_name = header_clean[2:]
            results["species_molalities"][species_name] = float_val
        elif header_lower.startswith("m(") and header_clean.endswith(")"):
            # Molality: m(Ca+2) -> Ca+2
            species_name = header_clean[2:-1]
            results["species_molalities"][species_name] = float_val
        elif header_lower.startswith("a_"):
            # USER_PUNCH activity: a_Fe2 -> Fe2.  Keep activities separate
            # from molalities so thermodynamic parity audits do not compare
            # unlike quantities.
            species_name = header_clean[2:]
            results["species_activities"][species_name] = float_val
        elif header_lower.startswith("a(") and header_clean.endswith(")"):
            species_name = header_clean[2:-1]
            results["species_activities"][species_name] = float_val
        elif header_lower.startswith("equi_"):
            # Equilibrium phase moles from USER_PUNCH: equi_Strengite -> Strengite
            phase_name = header_clean[5:]
            results["equilibrium_phase_moles"][phase_name] = float_val
        elif header_lower.startswith("surf_"):
            # Surface-adsorbed moles from USER_PUNCH: surf_P_Hfo -> P_Hfo
            surface_key = header_clean[5:]
            results["surface_adsorbed_moles"][surface_key] = float_val
        elif header_lower.startswith("sys_"):
            element_name = header_clean[4:]
            results["system_element_moles"][element_name] = float_val
        elif header_lower.startswith("tot_"):
            # Total: tot_Ca -> Ca, tot_P(5) -> P (USER_PUNCH format)
            element_name = header_clean[4:]
            base_element = _store_element_total(results, element_name, float_val)
            logger.debug(f"Parsed tot_: {header_clean} -> {base_element} = {float_val}")
        elif header_lower.startswith("tot(") and header_clean.endswith(")"):
            # Total: tot(Ca) -> Ca, tot(P(5)) -> P (SELECTED_OUTPUT -tot format)
            element_name = header_clean[4:-1]
            base_element = _store_element_total(results, element_name, float_val)
            logger.debug(f"Parsed tot(): {header_clean} -> {base_element} = {float_val}")
        elif _is_element_total_column(header_clean):
            # Bare element names like P, Fe, Ca, P(5), Fe(2) from -tot true output
            base_element = _store_element_total(results, header_clean, float_val)
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
            results.setdefault("solution_summary", {})["alkalinity_eq_kgw"] = float_val
        elif header_lower in {"mass_h2o", "water"}:
            results.setdefault("solution_summary", {})["mass_kg_water"] = float_val
        elif header_lower in {"density_kg_l", "volume_l", "mass_kg_water", "pressure_atm"}:
            canonical_name = {
                "density_kg_l": "density_kg_L", "volume_l": "volume_L",
                "mass_kg_water": "mass_kg_water",
                "pressure_atm": "pressure_atm",
            }[header_lower]
            results.setdefault("solution_summary", {})[canonical_name] = float_val
    _add_alkalinity_basis(results.get("solution_summary", {}))
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

        headers = [_selected_output_header(h) for h in arr[0]]
        col = {h: i for i, h in enumerate(headers)}

        # Determine if standard columns are present
        has_state = "state" in col

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
                "element_redox_totals_molality": {},
                "species_molality": {},
                # every numeric column of the step's row, read by the batch-redox check and dropped before the answer
                
                "_selected_output_row": {},
            }
            summary = step_result["solution_summary"]

            for h, idx in col.items():
                if idx >= len(row):
                    continue
                val = row[idx]
                if val is None:
                    continue

                h_lower = h.lower().strip()
                if isinstance(val, (int, float)):
                    step_result["_selected_output_row"][h.strip()] = float(val)

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
                # Element totals: a valence state's column is kept apart, as the single-step parser keeps it; it had
                # overwritten its element's total (an S, S(-2), S(6) block reported S as the S(6) column)
                elif _punched_total_element_name(h) is not None:
                    try:
                        _store_element_total(step_result, _punched_total_element_name(h), float(val))
                    except (ValueError, TypeError):
                        pass
                elif h_lower.startswith("tot_") or (h_lower.startswith("tot(") and h.endswith(")")):
                    elem = h[4:] if h_lower.startswith("tot_") else h[4:-1]
                    try:
                        _store_element_total(step_result, elem, float(val))
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


_TDS_SPECIES_WEIGHTS = {
    "Ca+2": 40.08, "Mg+2": 24.31, "Na+": 22.99, "K+": 39.10,
    "Cl-": 35.45, "SO4-2": 96.06, "HCO3-": 61.02, "CO3-2": 60.01,
    "NO3-": 62.00, "PO4-3": 94.97, "F-": 19.00,
    "CaSO4": 136.14, "CaHCO3+": 101.10, "MgSO4": 120.37,
    "NaSO4-": 119.05, "CaCO3": 100.09, "MgCO3": 84.31,
    "SiO2": 60.08, "H4SiO4": 96.11,
}
_TDS_ELEMENT_WEIGHTS = {
    "Ca": 40.08, "Mg": 24.31, "Na": 22.99, "K": 39.10,
    "Cl": 35.45, "S": 32.06, "C": 12.01, "N": 14.01,
    "P": 30.97, "F": 19.00, "Si": 28.09, "Fe": 55.85,
    "Al": 26.98,
}


def tds_from_species(species_molalities: Dict[str, Any], elements: Dict[str, Any]) -> float:
    """The answer's screening TDS, mg/L: the listed species' molalities by their weights, and below 10 mg/L the
    element totals instead (a held batch's answer is recomputed on its mapped-back species, `waterchem.batch_redox`)."""
    tds_mg_L = 0.0
    for species, molality in (species_molalities or {}).items():
        if isinstance(molality, (int, float)) and molality > 1e-12:
            mw = _TDS_SPECIES_WEIGHTS.get(species)
            if mw:
                tds_mg_L += molality * mw * 1000
    if tds_mg_L < 10:
        logger.debug("Species-based TDS calculation yielded low result, using element totals as backup")
        for element, molality in (elements or {}).items():
            if element not in ["H", "O", "O(0)", "H(0)"] and isinstance(molality, (int, float)) and molality > 1e-6:
                mw = _TDS_ELEMENT_WEIGHTS.get(element.split("(")[0], 50.0)
                tds_mg_L += molality * mw * 1000
    return tds_mg_L


def _per_kg_water(moles: Dict[str, Any], solution) -> Dict[str, Any]:
    """A phreeqpython solution's element MOLES (``Solution.elements``, VIPhreeqc's get_elements_totals) per kg of its
    water: the molality the standalone engine punches. Refused when the solution reports no positive mass of water."""
    mass = float(solution.mass)
    if not math.isfinite(mass) or mass <= 0.0:
        raise ValueError(f"the solution reports {mass!r} kg of water: its element totals cannot be put per kg of water")
    return {name: (value / mass if isinstance(value, (int, float)) else value) for name, value in (moles or {}).items()}


def parse_phreeqc_results(pp_instance, num_steps: int = 1, *, database_path: Optional[str] = None,
                          extra_text: str = "") -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    'Parses results from a PhreeqPython instance after a run.\n\n    Returns:\n        Dictionary for single step, or list of dictionaries for multi-step results'
    from water_chemistry_mcp.utils.exceptions import ErrorType

    all_step_results = []

    # The SELECTED_OUTPUT array is read FIRST, before anything below reads the instance: any run replaces VIPhreeqc's
    # selected-output array. The saturation indices' water activity had been a DUMP run on this instance (BK5 of
    
    
    
    try:
        selected_array = (
            pp_instance.ip.get_selected_output_array()
            if hasattr(pp_instance, "ip") and hasattr(pp_instance.ip, "get_selected_output_array")
            else None
        )
    except Exception as so_e:
        logger.debug(f"Could not read the selected output array: {so_e}")
        selected_array = None

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

            # pH and pe are required core outputs. Never replace a parser/API
            # failure with plausible neutral-water defaults.
            summary["pH"] = _required_finite_solution_attribute(solution, "pH")
            summary["pe"] = _required_finite_solution_attribute(solution, "pe")

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
                    ionic_strength = float(solution.mu())
                elif hasattr(solution, "I"):
                    ionic_strength = float(solution.I)
                else:
                    ionic_strength = None
                if ionic_strength is not None:
                    if not math.isfinite(ionic_strength) or ionic_strength < 0:
                        raise ValueError("nonfinite or negative ionic strength")
                    summary["ionic_strength"] = ionic_strength
            except Exception as e:
                logger.warning(f"Error getting ionic strength: {e}")

            # Calculate TDS (Total Dissolved Solids) using proper PHREEQC method
            try:
                if hasattr(solution, "species_molalities"):
                    summary["tds_calculated"] = tds_from_species(
                        solution.species_molalities,
                        _per_kg_water(solution.elements, solution) if hasattr(solution, "elements") else {})
                else:
                    logger.warning("Species molalities not available for TDS calculation")
            except Exception as e:
                logger.warning(f"Error calculating TDS from PHREEQC solution: {e}")

            
            # the database (and the run's own PHASES) define whose elements the solution carries
            try:
                if hasattr(solution, "phases"):
                    if database_path is None:
                        current_step_results["saturation_indices"] = {}
                        current_step_results["saturation_indices_withheld"] = (
                            "no database path reached the parser: phreeqpython's Solution.phases drops the water activity, so the indices are withheld rather than reported uncorrected (BK5 of PUBLIC-RULE-e5ab57ace3c5)")
                    else:
                        from puran_mcp_support.engineering.phreeqc_saturation import saturation_indices

                        current_step_results["saturation_indices"] = saturation_indices(
                            solution, database_path=str(database_path), extra_text=extra_text or "")
                else:
                    current_step_results["saturation_indices"] = {}

            except Exception as si_e:
                logger.warning(f"Could not get saturation indices: {si_e}")
                current_step_results["saturation_indices"] = {}
                # named, never an unexplained empty map (a Pitzer/SIT solution the helper cannot reproduce at the
                
                current_step_results["saturation_indices_withheld"] = f"{type(si_e).__name__}: {si_e}"[:600]

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

            # Element totals using solution.elements, per kg of water: VIPhreeqc's get_elements_totals reports the
            # solution's MOLES (10 mmol/kgw in 0.5 kg of water reads 0.005), so it is divided by the solution's mass of
            # water; it had been stored as molality, low by the water a batch step removed (0.055 % after struvite took
            # its hydrate water, which put the screen's P balance 0.5 mg/L out on the production engine; OQ-C13-2 of
            
            try:
                if hasattr(solution, "elements"):
                    current_step_results["element_totals_molality"] = _per_kg_water(solution.elements, solution)
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

            # Extract ALL SELECTED_OUTPUT columns by their real header — composite parameters
            # (Total_Hardness_CaCO3, Carbonate_Alkalinity_CaCO3, TDS_Species) AND any additional
            # columns such as the `-equilibrium_phases` phase-moles used for per-mineral P
            # attribution. Previously this hardcoded the first three columns to the composite names
            # and read them positionally, so every other selected-output column was silently dropped.
            try:
                selected_output_data = {}
                # Skip PHREEQC's standard state/index columns; keep composites + phase-moles + the rest.
                _skip = {"sim", "state", "soln", "dist_x", "time", "step", "pct_err"}
                arr = selected_array
                if arr and len(arr) >= 2:
                    headers, last_row = [_selected_output_header(h) for h in arr[0]], arr[-1]
                    pairs = [(header, float(value)) for header, value in zip(headers, last_row)
                             if header not in _skip and isinstance(value, (int, float)) and not isinstance(value, bool)]
                    # the same categories as the standalone engine's punch file (`_categorize_selected_columns`): the
                    # punched scalars (Alk, mu ...) into the summary, USER_PUNCH equi_/surf_/sys_/a_ columns into
                    # their own maps; what the solution object already reports keeps precedence (OQ-C13-2 of
                    
                    punched = _categorize_selected_columns(pairs, _empty_selected_results())
                    selected_output_data = punched["selected_output_data"]
                    summary = current_step_results.setdefault("solution_summary", {})
                    for key, value in (punched.get("solution_summary") or {}).items():
                        summary.setdefault(key, value)
                    for category in ("equilibrium_phase_moles", "surface_adsorbed_moles", "system_element_moles",
                                     "species_activities"):
                        if punched[category]:
                            target = current_step_results.setdefault(category, {})
                            for key, value in punched[category].items():
                                target.setdefault(key, value)

                    # Engine parity with subprocess mode: VIPhreeqc's
                    # get_elements_totals() (solution.elements) silently omits
                    # any element whose total is zero, so e.g. a zero-Mg water
                    # loses its "Mg" key entirely while the standalone
                    # executable's punched -totals column reports Mg = 0.0.
                    # Backfill the punched totals so both engines return the
                    # same element coverage; solution.elements values keep
                    # precedence where present.
                    # A valence state's punched column is that state's total, kept apart under
                    # element_redox_totals_molality as the standalone engine keeps it (`_store_element_total`); it
                    # had been written under its ELEMENT whenever the element was absent, so a punched C(-4) of 0
                    # stood for total carbon (0 against 0.041 mol/kgw on a digestate; closure c16b-chem, OQ-C13-2
                    
                    # VIPhreeqc reports none of the element in any state, the standalone engine's lone-column rule.
                    element_totals = current_step_results.get("element_totals_molality")
                    if isinstance(element_totals, dict):
                        for header, value in pairs:
                            punched_name = _punched_total_element_name(str(header))
                            if punched_name is None:
                                continue
                            base = _normalize_element_name(punched_name)
                            if punched_name != base:
                                redox_totals = current_step_results.setdefault("element_redox_totals_molality", {})
                                redox_totals.setdefault(punched_name, float(value))
                                if not any(k == base or k.startswith(base + "(") for k in element_totals):
                                    element_totals[base] = float(value)
                            elif base not in element_totals:
                                element_totals[base] = float(value)

                if selected_output_data:
                    current_step_results["selected_output_data"] = selected_output_data
                    logger.debug(f"Extracted {len(selected_output_data)} selected-output columns")

            except Exception as so_e:
                logger.debug(f"Could not extract selected output data: {so_e}")

            _add_alkalinity_basis(current_step_results["solution_summary"])
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
