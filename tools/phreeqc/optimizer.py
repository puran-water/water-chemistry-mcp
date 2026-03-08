"""
Dosing optimization module.

Handles iterative binary/secant search for optimal reagent doses
to achieve target water quality parameters.
"""

import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.exceptions import ErrorType, PhreeqcError
from utils.helpers import (
    build_equilibrium_phases_block,
    build_reaction_block,
    build_selected_output_block,
)

from .simulation import extract_minerals_from_input, get_mineral_alternatives, get_truncated_input, run_phreeqc_simulation

logger = logging.getLogger(__name__)


def evaluate_target_parameter(results: Dict[str, Any], target_config: Dict[str, Any]) -> Optional[float]:
    """
    Evaluate complex target parameters from PHREEQC results.

    Args:
        results: PHREEQC simulation results
        target_config: Configuration for target evaluation including:
            - parameter: The target parameter type
            - value: The target value
            - units: Optional units for conversion
            - components: For composite parameters (e.g., metals list)
            - conditions: Additional conditions/constraints

    Returns:
        Current value of the target parameter, or None if not found
    """
    target_parameter = target_config.get("parameter")
    target_units = target_config.get("units", "")

    if "solution_summary" not in results:
        return None
    summary = results["solution_summary"]

    if target_parameter == "pH":
        return summary.get("pH")

    elif target_parameter == "pe":
        return summary.get("pe")

    elif target_parameter == "ionic_strength":
        return summary.get("ionic_strength")

    elif target_parameter == "TDS":
        return summary.get("tds_calculated")

    elif target_parameter == "total_hardness":
        selected_output = results.get("selected_output_data", {})
        if "Total_Hardness_CaCO3" in selected_output:
            phreeqc_hardness = selected_output["Total_Hardness_CaCO3"]
            if "mg/L" in target_units and "CaCO3" in target_units:
                return phreeqc_hardness
            elif "mmol/L" in target_units:
                return phreeqc_hardness / 100.09
            else:
                return phreeqc_hardness / 100090

        element_totals = results.get("element_totals_molality", {})
        ca_molal = element_totals.get("Ca", 0)
        mg_molal = element_totals.get("Mg", 0)

        if "mg/L" in target_units and "CaCO3" in target_units:
            hardness_mg_caco3 = (ca_molal + mg_molal) * 100000
            return hardness_mg_caco3
        elif "mmol/L" in target_units:
            return (ca_molal + mg_molal) * 1000
        else:
            return ca_molal + mg_molal

    elif target_parameter == "residual_phosphorus":
        element_totals = results.get("element_totals_molality", {})
        p_molal = element_totals.get("P", 0)

        if "mg/L" in target_units:
            return p_molal * 30974
        else:
            return p_molal

    elif target_parameter == "total_metals":
        components = target_config.get("components", ["Fe", "Cu", "Zn", "Pb", "Ni", "Cd"])
        element_totals = results.get("element_totals_molality", {})

        total = sum(element_totals.get(metal, 0) for metal in components)

        if "mg/L" in target_units:
            return total * 60000
        else:
            return total

    elif target_parameter == "carbonate_alkalinity":
        selected_output = results.get("selected_output_data", {})
        if "Carbonate_Alkalinity_CaCO3" in selected_output:
            phreeqc_alkalinity = selected_output["Carbonate_Alkalinity_CaCO3"]
            if "mg/L" in target_units and "CaCO3" in target_units:
                return phreeqc_alkalinity
            else:
                return phreeqc_alkalinity / 50000

        species = results.get("species_molality", {})
        hco3 = species.get("HCO3-", 0)
        co3 = species.get("CO3-2", 0)

        alk_molal = hco3 + 2 * co3

        if "mg/L" in target_units and "CaCO3" in target_units:
            return alk_molal * 50000
        else:
            return alk_molal

    elif target_parameter == "langelier_index":
        ph = summary.get("pH")
        temp_c = summary.get("temperature_celsius", 25)
        tds = summary.get("tds_calculated", 0)

        element_totals = results.get("element_totals_molality", {})
        ca_molal = element_totals.get("Ca", 0)
        ca_hardness_caco3 = ca_molal * 100000

        species = results.get("species_molality", {})
        alk_molal = species.get("HCO3-", 0) + 2 * species.get("CO3-2", 0)
        alk_caco3 = alk_molal * 50000

        A = (math.log10(tds) - 1) / 10 if tds > 0 else 0
        B = -13.12 * math.log10(temp_c + 273) + 34.55
        C = math.log10(ca_hardness_caco3) - 0.4 if ca_hardness_caco3 > 0 else 0
        D = math.log10(alk_caco3) if alk_caco3 > 0 else 0

        phs = (9.3 + A + B) - (C + D)
        return ph - phs

    elif target_parameter == "minimum_si":
        minerals = target_config.get("components", ["Calcite", "Gypsum"])
        si_values = results.get("saturation_indices", {})

        valid_si = [si_values.get(m, 999) for m in minerals if si_values.get(m, 999) != -999]
        return min(valid_si) if valid_si else None

    elif target_parameter == "precipitation_potential":
        total_ppt = results.get("total_precipitate_g_L", 0)

        if "kg/m3" in target_units:
            return total_ppt / 1000
        else:
            return total_ppt

    elif target_parameter == "molar_ratio":
        numerator = target_config.get("numerator")
        denominator = target_config.get("denominator")

        element_totals = results.get("element_totals_molality", {})
        species = results.get("species_molality", {})

        num_val = element_totals.get(numerator, species.get(numerator, 0))
        den_val = element_totals.get(denominator, species.get(denominator, 0))

        if den_val > 0:
            return num_val / den_val
        else:
            return float("inf") if num_val > 0 else 0

    elif target_parameter == "Alkalinity":
        current_value = summary.get("alkalinity_mol_kgw")
        if target_units and "mg" in target_units.lower() and "caco3" in target_units.lower():
            from utils.constants import ALKALINITY_MOL_TO_MG_CACO3

            current_value *= ALKALINITY_MOL_TO_MG_CACO3
        return current_value

    elif target_parameter == "SI" and target_config.get("mineral"):
        mineral_name = target_config.get("mineral")
        return results.get("saturation_indices", {}).get(mineral_name)

    elif target_parameter == "Concentration":
        element_or_species = target_config.get("element_or_species")
        element_totals = results.get("element_totals_molality", {})
        species = results.get("species_molality", {})

        current_value = element_totals.get(element_or_species, species.get(element_or_species))

        if current_value is not None and target_units and "mg" in target_units.lower():
            logger.warning(f"Unit conversion from molality to {target_units} requires molecular weight")

        return current_value

    else:
        logger.warning(f"Unknown target parameter: {target_parameter}")
        return None


# Multi-objective optimization support
class OptimizationObjective:
    """Define optimization objectives with constraints."""

    def __init__(
        self,
        parameter: str,
        target_value: float,
        tolerance: float,
        weight: float = 1.0,
        constraint_type: str = "equality",  # 'equality', 'min', 'max'
        units: Optional[str] = None,
        **kwargs,
    ):
        self.parameter = parameter
        self.target_value = target_value
        self.tolerance = tolerance
        self.weight = weight
        self.constraint_type = constraint_type
        self.units = units
        self.config = kwargs

    def evaluate(self, results: Dict[str, Any]) -> Tuple[float, float]:
        """
        Evaluate objective function.
        Returns: (current_value, error)
        """
        config = {"parameter": self.parameter, "units": self.units, **self.config}
        current_value = evaluate_target_parameter(results, config)

        if current_value is None:
            return None, float("inf")

        if self.constraint_type == "equality":
            error = abs(current_value - self.target_value)
        elif self.constraint_type == "min":
            error = max(0, self.target_value - current_value)
        elif self.constraint_type == "max":
            error = max(0, current_value - self.target_value)
        else:
            error = abs(current_value - self.target_value)

        return current_value, error * self.weight


async def find_reactant_dose_for_target(
    initial_solution_str: str,
    target_parameter: str,
    target_value: float,
    reagent_formula: str,
    mineral_name: Optional[str] = None,
    element_or_species: Optional[str] = None,
    target_units: Optional[str] = None,
    initial_guess_mmol: float = 1.0,
    max_iterations: int = 30,
    tolerance: float = 0.01,
    database_path: Optional[str] = None,
    allow_precipitation: bool = True,
    equilibrium_minerals: Optional[List[str]] = None,
) -> Tuple[Optional[float], Dict[str, Any], int]:
    """
    Iteratively finds the dose of a reactant to meet a target.

    Args:
        initial_solution_str: PHREEQC SOLUTION block for starting solution
        target_parameter: Parameter to target (e.g., 'pH', 'SI', 'Alkalinity')
        target_value: Target value for the parameter
        reagent_formula: Chemical formula of the reagent (e.g., 'NaOH')
        mineral_name: Mineral name (required if target_parameter is 'SI')
        element_or_species: Element or species (required if target_parameter is 'Concentration')
        target_units: Units for target value (required for some target parameters)
        initial_guess_mmol: Initial guess for dose in mmol/L
        max_iterations: Maximum number of iterations for the search
        tolerance: Acceptable tolerance for reaching the target
        database_path: Path to PHREEQC database
        allow_precipitation: Whether to allow mineral precipitation
        equilibrium_minerals: List of minerals to consider for precipitation

    Returns:
        Tuple of (optimal_dose or None, final_results_dict, iterations_performed)
    """
    lower_bound_mmol = 0.0
    upper_bound_mmol = None
    current_dose_mmol = initial_guess_mmol
    final_results = {}
    iterations_done = 0

    prev_direction = None
    prev_error = None
    prev_dose_mmol = None

    logger.info(
        f"Starting iterative search for {reagent_formula} dose to reach {target_parameter}={target_value}"
        f"{' (' + target_units + ')' if target_units else ''}"
        f"{' for ' + mineral_name if mineral_name else ''}"
        f"{' for ' + element_or_species if element_or_species else ''}"
    )

    # Use chemical heuristics to set smart optimization bounds
    if target_parameter == "pH":
        try:
            initial_ph_match = re.search(r"pH\s+(\d+\.?\d*)", initial_solution_str, re.IGNORECASE)
            if initial_ph_match:
                initial_ph = float(initial_ph_match.group(1))
                ph_change = abs(target_value - initial_ph)

                if ph_change > 0:
                    if reagent_formula in ["NaOH", "KOH", "Ca(OH)2"]:
                        rough_dose = ph_change * 2 if ph_change > 0.05 else 0.1
                    elif reagent_formula in ["HCl", "H2SO4", "HNO3"]:
                        rough_dose = ph_change * 2 if ph_change > 0.05 else 0.1
                    else:
                        rough_dose = ph_change * 5

                    if upper_bound_mmol is None:
                        upper_bound_mmol = rough_dose * 5.0

                        if current_dose_mmol >= upper_bound_mmol:
                            current_dose_mmol = upper_bound_mmol / 10
        except Exception as e:
            logger.debug(f"Could not estimate bounds from pH change: {e}")

    if upper_bound_mmol is None:
        upper_bound_mmol = initial_guess_mmol * 50

    if current_dose_mmol <= lower_bound_mmol:
        current_dose_mmol = (lower_bound_mmol + upper_bound_mmol) / 100

    if current_dose_mmol >= upper_bound_mmol:
        current_dose_mmol = upper_bound_mmol / 2

    equilibrium_phases_str = ""
    if allow_precipitation:
        if not equilibrium_minerals:
            from utils.constants import MG_HYDROXIDE_NAMES, UNIVERSAL_MINERALS

            equilibrium_minerals = list(UNIVERSAL_MINERALS)

            if database_path:
                db_name = os.path.basename(database_path)
                mg_oh_name = MG_HYDROXIDE_NAMES.get(db_name, "Brucite")
                if mg_oh_name:
                    equilibrium_minerals.append(mg_oh_name)

            logger.info(f"No minerals specified, using universal minerals: {', '.join(equilibrium_minerals)}")

        if equilibrium_minerals:
            phases_to_consider = [{"name": name} for name in equilibrium_minerals]
            equilibrium_phases_str = build_equilibrium_phases_block(phases_to_consider, block_num=1, allow_empty=True)
            if equilibrium_phases_str:
                logger.info(f"Enabled precipitation with minerals: {', '.join(equilibrium_minerals)}")

    for i in range(max_iterations):
        iterations_done = i + 1
        current_dose_mmol = max(1e-9, current_dose_mmol)

        reaction_def = [{"formula": reagent_formula, "amount": current_dose_mmol, "units": "mmol"}]
        reaction_block = build_reaction_block(reaction_def, reaction_num=1)

        convergence_options = """
KNOBS
    -iterations 800       # Default is 100, increase to help convergence (increased from 500)
    -tolerance 1e-10      # Default is 1e-6, tighter tolerance helps (relaxed from 1e-12)
    -convergence_tolerance 1e-8  # Add explicit convergence tolerance
    -step_size 10         # Default is 100, smaller steps help convergence
    -pe_step_size 5       # Default is 10, smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
"""

        phreeqc_input = (
            initial_solution_str + convergence_options + reaction_block + "USE solution 1\n" + "USE reaction 1\n"
        )
        if equilibrium_phases_str:
            phreeqc_input += equilibrium_phases_str
            phreeqc_input += "USE equilibrium_phases 1\n"

        phreeqc_input += "SAVE solution 2\n"

        composite_parameters = [
            "total_hardness", "carbonate_alkalinity", "TDS",
            "residual_phosphorus", "total_metals", "langelier_index",
            "precipitation_potential",
        ]
        needs_composite = target_parameter in composite_parameters

        phreeqc_input += (
            build_selected_output_block(
                block_num=1,
                phases=allow_precipitation,
                saturation_indices=True,
                totals=True,
                molalities=True,
                composite_parameters=needs_composite,
            )
            + "END\n"
        )

        try:
            if target_parameter == "pH" and target_value is not None:
                phreeqc_input_with_buffer = phreeqc_input
                buffer_amount = 0.0001

                if target_value >= 11:
                    if target_value >= 13:
                        buffer_amount = 0.01
                    elif target_value >= 12:
                        buffer_amount = 0.005
                    elif target_value >= 11:
                        buffer_amount = 0.001

                    logger.info(f"Adding pH buffer ({buffer_amount} mol) for high pH target: {target_value}")
                    naoh_buffer = build_reaction_block(
                        [{"formula": "NaOH", "amount": buffer_amount, "units": "mol"}], reaction_num=99
                    )
                    phreeqc_input_with_buffer = phreeqc_input.replace("REACTION 1", naoh_buffer + "\nREACTION 1")
                    phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace(
                        "USE solution 1\n", "USE solution 1\nUSE reaction 99\n"
                    )

                    if target_value >= 12:
                        relaxed_knobs = """
KNOBS
    -iterations 500       # Increase max iterations
    -convergence_tolerance 1e-7  # More relaxed convergence criteria
    -tolerance 1e-10      # Relaxed tolerance
    -step_size 10         # Smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
    -pe_step_size 5       # Smaller pe steps
"""
                        if "KNOBS" in phreeqc_input_with_buffer:
                            phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("KNOBS", relaxed_knobs)
                        else:
                            phreeqc_input_with_buffer = relaxed_knobs + phreeqc_input_with_buffer

                elif target_value <= 3:
                    if target_value <= 1:
                        buffer_amount = 0.01
                    elif target_value <= 2:
                        buffer_amount = 0.005
                    elif target_value <= 3:
                        buffer_amount = 0.001

                    logger.info(f"Adding pH buffer ({buffer_amount} mol) for low pH target: {target_value}")
                    hcl_buffer = build_reaction_block(
                        [{"formula": "HCl", "amount": buffer_amount, "units": "mol"}], reaction_num=99
                    )
                    phreeqc_input_with_buffer = phreeqc_input.replace("REACTION 1", hcl_buffer + "\nREACTION 1")
                    phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace(
                        "USE solution 1\n", "USE solution 1\nUSE reaction 99\n"
                    )

                    if target_value <= 2:
                        relaxed_knobs = """
KNOBS
    -iterations 500       # Increase max iterations
    -convergence_tolerance 1e-7  # More relaxed convergence criteria
    -tolerance 1e-6       # Relaxed tolerance
    -step_size 5          # Smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
"""
                        if "KNOBS" in phreeqc_input_with_buffer:
                            phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("KNOBS", relaxed_knobs)
                        else:
                            phreeqc_input_with_buffer = relaxed_knobs + phreeqc_input_with_buffer
                else:
                    phreeqc_input_with_buffer = phreeqc_input
            else:
                phreeqc_input_with_buffer = phreeqc_input

            # First attempt
            try:
                results = await run_phreeqc_simulation(phreeqc_input_with_buffer, database_path)
            except PhreeqcError as pe:
                if hasattr(pe, "is_water_activity_error") and pe.is_water_activity_error:
                    logger.warning("Water activity convergence error detected, retrying with simplified model")

                    simplified_input = phreeqc_input_with_buffer.replace(
                        "-tolerance 1e-10",
                        "-tolerance 1e-8",
                    )

                    more_robust_knobs = """
KNOBS
    -iterations 1000      # Increase max iterations
    -convergence_tolerance 1e-8  # Relax convergence criteria
    -tolerance 1e-6       # Relaxed tolerance
    -step_size 5          # Smaller steps help convergence
    -pe_step_size 2       # Smaller pe steps
    -diag_tol 1e-5        # More relaxed diagonal tolerance
    -diagonal_scale true  # Help numerical stability
"""
                    simplified_input = simplified_input.replace("KNOBS", more_robust_knobs)

                    if allow_precipitation and equilibrium_minerals:
                        essential_minerals = ["Calcite", "Gypsum", "Halite", "Dolomite", "Aragonite"]
                        simple_minerals = [m for m in equilibrium_minerals if m in essential_minerals]

                        if simple_minerals:
                            from utils.database_management import database_manager

                            mineral_mapping = database_manager.get_compatible_minerals(database_path, simple_minerals)

                            compatible_minerals = [m for m, v in mineral_mapping.items() if v]

                            if compatible_minerals:
                                phases_to_consider = [{"name": name} for name in compatible_minerals]
                                simple_equilibrium_phases_str = build_equilibrium_phases_block(
                                    phases_to_consider, block_num=1, allow_empty=True
                                )

                                if simple_equilibrium_phases_str and "EQUILIBRIUM_PHASES" in simplified_input:
                                    simplified_input = re.sub(
                                        r"EQUILIBRIUM_PHASES\s+\d+(?:\s*#[^\n]*)?\n(.*?)(?=^[A-Z]|\Z)",
                                        simple_equilibrium_phases_str,
                                        simplified_input,
                                        flags=re.MULTILINE | re.DOTALL,
                                    )

                    high_ionic_strength = False
                    high_conc_elements = ["Na", "Cl", "K", "Ca", "Mg", "SO4"]

                    for elem in high_conc_elements:
                        if f"{elem} " in phreeqc_input_with_buffer:
                            for line in phreeqc_input_with_buffer.split("\n"):
                                if f"{elem} " in line and "mol" in line.lower():
                                    try:
                                        parts = line.split()
                                        for k, part in enumerate(parts):
                                            if elem in part and k + 1 < len(parts):
                                                try:
                                                    conc = float(parts[k + 1])
                                                    if conc > 0.5:
                                                        high_ionic_strength = True
                                                        logger.warning(f"High concentration of {elem} detected: {conc}")
                                                        break
                                                except ValueError:
                                                    pass
                                    except Exception:
                                        pass

                    final_error = None
                    success = False

                    try:
                        logger.info("Attempt 1: Retrying with simplified model parameters")
                        results = await run_phreeqc_simulation(simplified_input, database_path)
                        success = True
                    except Exception as e:
                        final_error = e
                        logger.warning(f"Simplified model attempt failed: {e}")

                    if not success and high_ionic_strength and not database_path.lower().endswith("pitzer.dat"):
                        try:
                            from utils.database_management import DatabaseManager

                            logger.info("Attempt 2: High ionic strength detected, attempting to use PITZER database")
                            db_manager = DatabaseManager()
                            pitzer_db = db_manager.get_database_path("pitzer.dat")

                            if pitzer_db:
                                logger.info(
                                    f"Trying with PITZER database for better handling of high ionic strength: {pitzer_db}"
                                )
                                results = await run_phreeqc_simulation(phreeqc_input_with_buffer, pitzer_db)
                                database_path = pitzer_db
                                success = True
                            else:
                                logger.warning("PITZER database not found")
                        except Exception as db_e:
                            final_error = db_e
                            logger.warning(f"PITZER database attempt failed: {db_e}")

                    if not success and target_parameter == "pH":
                        try:
                            logger.info("Attempt 3: Trying with further relaxed convergence parameters")
                            very_relaxed = simplified_input.replace(
                                "KNOBS",
                                """
KNOBS
    -iterations 1500      # Many more iterations
    -convergence_tolerance 1e-5  # Much more relaxed tolerance
    -tolerance 1e-5       # Relaxed tolerance
    -step_size 2          # Tiny step size
    -diagonal_scale true  # Force numerical stability
""",
                            )
                            if "EQUILIBRIUM_PHASES" in very_relaxed:
                                logger.info("Temporarily disabling precipitation to simplify calculation")
                                very_relaxed = re.sub(
                                    r"EQUILIBRIUM_PHASES.*?(?=^[A-Z]|\Z)",
                                    "",
                                    very_relaxed,
                                    flags=re.MULTILINE | re.DOTALL,
                                )

                            results = await run_phreeqc_simulation(very_relaxed, database_path)
                            success = True
                        except Exception as e:
                            final_error = e
                            logger.warning(f"Relaxed parameters attempt failed: {e}")

                    if not success:
                        try:
                            logger.info("Attempt 4: Last resort with drastically simplified model")
                            minimal_input = (
                                initial_solution_str
                                + """
KNOBS
    -iterations 2000
    -convergence_tolerance 1e-5
    -tolerance 1e-4
    -step_size 1
    -diagonal_scale true
"""
                                + reaction_block
                                + "USE solution 1\n"
                                + "USE reaction 1\n"
                                + "SAVE solution 2\n"
                                + build_selected_output_block(
                                    block_num=1,
                                    phases=False,
                                    saturation_indices=False,
                                    totals=True,
                                    molalities=False,
                                )
                                + "END\n"
                            )

                            results = await run_phreeqc_simulation(minimal_input, database_path)
                            success = True
                            logger.info("Succeeded with minimal model")
                        except Exception as e:
                            final_error = e
                            logger.warning(f"Minimal model attempt failed: {e}")

                    if not success:
                        logger.error("All convergence recovery attempts failed")
                        raise final_error
                else:
                    raise pe

            if isinstance(results, list):
                results = results[0]

            final_results = results
            if "error" in results and results["error"]:
                raise PhreeqcError(results["error"])

            current_value = None
            if "solution_summary" not in results:
                logger.warning(f"Iteration {iterations_done}: No solution summary found in results.")
                break

            summary = results["solution_summary"]

            target_config = {
                "parameter": target_parameter,
                "units": target_units,
                "mineral": mineral_name,
                "element_or_species": element_or_species,
            }
            current_value = evaluate_target_parameter(results, target_config)

            if current_value is None:
                logger.warning(
                    f"Could not retrieve current value for {target_parameter} at iteration {iterations_done}"
                )
                status = "Error retrieving target value"

                final_results["error"] = status
                final_results["error_type"] = ErrorType.MISSING_TARGET_PARAMETER
                final_results["context"] = {
                    "target_parameter": target_parameter,
                    "iteration": iterations_done,
                    "dose_mmol": current_dose_mmol,
                    "suggestion": f"Check that '{target_parameter}' is a valid parameter for the simulation.",
                }

                return None, final_results, iterations_done

            error = current_value - target_value
            logger.info(
                f"Iter {iterations_done}: Dose={current_dose_mmol:.6f} mmol, "
                f"Current {target_parameter}={current_value:.6f}, "
                f"Target={target_value:.6f}, Error={error:.6f}"
            )

            if abs(error) < tolerance:
                logger.info(
                    f"Target achieved within tolerance at iteration {iterations_done}. "
                    f"Final Dose: {current_dose_mmol:.6f} mmol"
                )
                return current_dose_mmol, final_results, iterations_done

            current_direction = "increase" if error < 0 else "decrease"

            old_lower = lower_bound_mmol
            old_upper = upper_bound_mmol

            if error < 0:
                lower_bound_mmol = current_dose_mmol
            else:
                upper_bound_mmol = current_dose_mmol

            if prev_direction is not None and prev_direction != current_direction:
                logger.info("Direction changed, possibly approaching target")

                if prev_error is not None and abs(error) < tolerance * 10 and abs(error) < abs(prev_error) * 2:
                    logger.info("Close to target and making progress, narrowing bounds")
                    if current_direction == "increase":
                        upper_bound_mmol = current_dose_mmol + (old_upper - current_dose_mmol) * 0.5
                    else:
                        lower_bound_mmol = current_dose_mmol - (current_dose_mmol - old_lower) * 0.5

            if upper_bound_mmol <= lower_bound_mmol or abs(upper_bound_mmol - lower_bound_mmol) < 1e-10:
                logger.warning(f"Search bounds converged without reaching target tolerance.")
                status = "Bounds converged without reaching tolerance"

                if abs(error) < tolerance * 5:
                    logger.info(
                        f"We're within 5x tolerance ({abs(error):.6f} vs {tolerance:.6f}), returning best result"
                    )
                    return current_dose_mmol, final_results, iterations_done

                final_results["error"] = status
                final_results["error_type"] = ErrorType.BOUNDS_CONVERGENCE
                final_results["context"] = {
                    "target_parameter": target_parameter,
                    "target_value": target_value,
                    "current_value": current_value,
                    "current_dose_mmol": current_dose_mmol,
                    "error": error,
                    "tolerance": tolerance,
                    "iteration": iterations_done,
                    "suggestion": "Try a different initial dose or adjust tolerance.",
                }
                return None, final_results, iterations_done

            next_dose_mmol_bisection = (lower_bound_mmol + upper_bound_mmol) / 2.0

            next_dose_mmol = next_dose_mmol_bisection

            if prev_error is not None and abs(prev_error - error) > 1e-10:
                try:
                    m = (prev_error - error) / (prev_dose_mmol - current_dose_mmol)
                    if abs(m) > 1e-10:
                        b = error - m * current_dose_mmol
                        next_dose_mmol_secant = -b / m

                        if lower_bound_mmol < next_dose_mmol_secant < upper_bound_mmol and abs(
                            next_dose_mmol_secant - current_dose_mmol
                        ) < (upper_bound_mmol - lower_bound_mmol):
                            next_dose_mmol = 0.7 * next_dose_mmol_secant + 0.3 * next_dose_mmol_bisection
                            logger.info(
                                f"Using secant method estimate: {next_dose_mmol_secant:.6f}, "
                                f"combined with bisection: {next_dose_mmol:.6f}"
                            )
                except Exception as e:
                    logger.warning(f"Error calculating secant method: {e}, falling back to bisection")
                    next_dose_mmol = next_dose_mmol_bisection

            next_dose_mmol = max(lower_bound_mmol, min(upper_bound_mmol, next_dose_mmol))

            if abs(next_dose_mmol - current_dose_mmol) < 1e-10:
                if abs(error) < tolerance * 2:
                    logger.info(
                        f"Dose changes very small and error ({abs(error):.6f}) is close to tolerance ({tolerance:.6f}), accepting result"
                    )
                    return current_dose_mmol, final_results, iterations_done

                logger.warning("Iteration dose change too small, stopping.")
                status = "Iteration stalled"
                final_results["error"] = status
                final_results["error_type"] = ErrorType.ITERATION_STALLED
                final_results["context"] = {
                    "target_parameter": target_parameter,
                    "target_value": target_value,
                    "current_value": current_value,
                    "current_dose_mmol": current_dose_mmol,
                    "error": error,
                    "iteration": iterations_done,
                    "suggestion": "Try a different initial dose or increase tolerance.",
                }
                return None, final_results, iterations_done

            prev_direction = current_direction
            prev_error = error
            prev_dose_mmol = current_dose_mmol
            current_dose_mmol = next_dose_mmol

        except PhreeqcError as e:
            logger.error(f"PHREEQC error during iteration {iterations_done} with dose {current_dose_mmol:.4f}: {e}")

            error_dict = e.to_dict() if hasattr(e, "to_dict") else {"error": str(e)}
            error_dict.update(
                {
                    "iteration": iterations_done,
                    "dose_mmol": current_dose_mmol,
                    "target_parameter": target_parameter,
                    "target_value": target_value,
                    "error_type": ErrorType.PHREEQC_SIMULATION_ERROR,
                    "last_successful_results": final_results,
                }
            )

            return None, error_dict, iterations_done

    logger.warning(f"Maximum iterations ({max_iterations}) reached without converging to target.")
    error_dict = {
        "error": "Maximum iterations reached without converging to target value",
        "error_type": ErrorType.MAX_ITERATIONS,
        "context": {
            "target_parameter": target_parameter,
            "target_value": target_value,
            "current_dose_mmol": current_dose_mmol,
            "iterations_performed": iterations_done,
            "lower_bound": lower_bound_mmol,
            "upper_bound": upper_bound_mmol,
            "suggestion": "Try a different initial guess, increase max iterations, or check if the target is achievable.",
        },
        "last_successful_results": final_results,
    }
    return None, error_dict, iterations_done
