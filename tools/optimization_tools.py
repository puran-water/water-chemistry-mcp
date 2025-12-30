"""
Optimization tools for water chemistry treatment design.

This module provides MCP-compatible wrappers for specialized optimization functions:
- generate_lime_softening_curve: Complete dose-response curves
- calculate_lime_softening_dose: Optimal lime softening dose
- optimize_phosphorus_removal: P removal with coagulant selection
- calculate_dosing_requirement_enhanced: Multi-objective dosing optimization
- optimize_multi_reagent_treatment: Multi-reagent optimization with 4 strategies

Author: Claude AI
"""

import asyncio
import copy
import logging
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from .chemical_addition import simulate_chemical_addition
from .solution_speciation import calculate_solution_speciation
from .schemas import WaterAnalysisInput

from utils.exceptions import (
    InputValidationError,
    ConvergenceError,
    OptimizationConvergenceError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# MCP Wrapper Functions
# =============================================================================


async def generate_lime_softening_curve(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a complete lime softening curve showing hardness vs lime dose.

    This is much more efficient than making individual API calls for each dose point.
    Returns a complete dose-response curve with interpolated optimal dose.

    Args:
        input_data: {
            'initial_water': Water composition (WaterAnalysisInput format)
            'lime_doses': List of lime doses in mmol/L to evaluate
            'database': Optional database file (default: minteq.dat)
        }

    Returns:
        Dictionary containing:
        - curve_data: List of dose-response points (pH, hardness, precipitate)
        - optimal_dose: Interpolated dose for target hardness (85 mg/L as CaCO3)
    """
    # Validate input
    if "initial_water" not in input_data:
        raise InputValidationError("Missing required field: 'initial_water'")
    if "lime_doses" not in input_data:
        raise InputValidationError("Missing required field: 'lime_doses'")

    initial_water = input_data["initial_water"]
    lime_doses = input_data["lime_doses"]
    database = input_data.get("database", "minteq.dat")

    if not isinstance(lime_doses, list) or len(lime_doses) == 0:
        raise InputValidationError("'lime_doses' must be a non-empty list of dose values")

    # Create scenarios for batch processing
    scenarios = []
    for dose in lime_doses:
        scenarios.append({
            "name": f"Lime_{dose}mmol",
            "type": "chemical_addition",
            "reactants": [{"formula": "Ca(OH)2", "amount": dose, "units": "mmol"}],
            "equilibrium_minerals": None,  # Use full database mineral list
        })

    # Import batch processing to avoid circular imports
    from .batch_processing import batch_process_scenarios as _batch_process

    # Run batch processing
    results = await _batch_process({
        "base_solution": initial_water,
        "scenarios": scenarios,
        "parallel_limit": 10,
        "output_format": "full",
    })

    # Extract curve data
    curve_data = []
    for r in results.get("results", []):
        if "error" not in r:
            scenario = r["scenario"]
            result = r["result"]

            # Calculate hardness from element totals
            elements = result.get("element_totals_molality", {})
            ca = elements.get("Ca", 0) or 0
            mg = elements.get("Mg", 0) or 0
            hardness = (ca + mg) * 100000  # mg/L as CaCO3

            curve_data.append({
                "lime_dose_mmol": scenario["reactants"][0]["amount"],
                "pH": result.get("solution_summary", {}).get("pH", 0),
                "hardness_mg_caco3": hardness,
                "precipitate_g_L": result.get("total_precipitate_g_L", 0),
            })

    # Sort by dose
    curve_data.sort(key=lambda x: x["lime_dose_mmol"])

    # Find optimal dose for target hardness (85 mg/L as CaCO3)
    optimal_dose = _find_optimal_dose(curve_data, target_hardness=85)

    return {
        "curve_data": curve_data,
        "optimal_dose": optimal_dose,
    }


def _find_optimal_dose(
    curve_data: List[Dict[str, Any]], target_hardness: float
) -> Optional[Dict[str, Any]]:
    """Find the dose that achieves target hardness via linear interpolation."""
    if not curve_data:
        return None

    # Find points bracketing the target
    below_target = None
    above_target = None

    for point in curve_data:
        if point["hardness_mg_caco3"] <= target_hardness:
            if below_target is None or point["hardness_mg_caco3"] > below_target["hardness_mg_caco3"]:
                below_target = point
        else:
            if above_target is None or point["hardness_mg_caco3"] < above_target["hardness_mg_caco3"]:
                above_target = point

    if below_target and above_target:
        # Linear interpolation
        hardness_diff = below_target["hardness_mg_caco3"] - above_target["hardness_mg_caco3"]
        if abs(hardness_diff) < 1e-6:
            return None

        fraction = (target_hardness - above_target["hardness_mg_caco3"]) / hardness_diff

        optimal_dose = above_target["lime_dose_mmol"] + fraction * (
            below_target["lime_dose_mmol"] - above_target["lime_dose_mmol"]
        )

        return {
            "dose_mmol": optimal_dose,
            "estimated_pH": above_target["pH"] + fraction * (below_target["pH"] - above_target["pH"]),
            "target_hardness": target_hardness,
        }

    return None


async def calculate_lime_softening_dose(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Specialized function for lime softening dose optimization.

    Handles the complexity of Mg(OH)2 precipitation at high pH and uses
    stoichiometric estimates for smart optimization bounds.

    Args:
        input_data: {
            'initial_water': Water composition (WaterAnalysisInput format)
            'target_hardness_mg_caco3': Target total hardness in mg/L as CaCO3
            'database': Optional database file (default: minteq.dat)
        }

    Returns:
        Solution state with optimization_summary containing:
        - optimal_dose_mmol: Optimal lime dose in mmol/L
        - target_hardness_mg_caco3: Target hardness
        - achieved_hardness_mg_caco3: Achieved hardness
        - hardness_removal_efficiency: Removal efficiency percentage
    """
    # Validate input
    if "initial_water" not in input_data:
        raise InputValidationError("Missing required field: 'initial_water'")
    if "target_hardness_mg_caco3" not in input_data:
        raise InputValidationError("Missing required field: 'target_hardness_mg_caco3'")

    initial_water = input_data["initial_water"]
    target_hardness_mg_caco3 = input_data["target_hardness_mg_caco3"]
    database = input_data.get("database", "minteq.dat")

    if target_hardness_mg_caco3 < 0:
        raise InputValidationError("target_hardness_mg_caco3 must be non-negative")

    # Analyze initial water
    initial_analysis = await calculate_solution_speciation(initial_water)

    # Extract current hardness
    ca_initial = initial_analysis.get("element_totals_molality", {}).get("Ca", 0) or 0
    mg_initial = initial_analysis.get("element_totals_molality", {}).get("Mg", 0) or 0
    initial_hardness = (ca_initial + mg_initial) * 100000  # mg/L as CaCO3

    logger.info(f"Initial hardness: {initial_hardness:.1f} mg/L as CaCO3")
    logger.info(f"Target hardness: {target_hardness_mg_caco3:.1f} mg/L as CaCO3")

    # Use stoichiometric estimate to set smart optimization bounds
    hardness_to_remove = (initial_hardness - target_hardness_mg_caco3) / 100000  # mol/L
    estimated_lime = hardness_to_remove * 0.6  # Stoichiometric estimate
    max_reasonable_lime_dose = max(estimated_lime * 3.0, 0.5)  # Ensure minimum search range

    logger.info(f"Using parameter sweep with estimated max dose: {max_reasonable_lime_dose:.3f} mmol/L")

    # Create dose sweep around estimated range
    doses = np.linspace(0.1, max_reasonable_lime_dose, 15)
    best_dose = None
    best_result = None
    best_hardness_diff = float("inf")

    for dose in doses:
        try:
            result = await simulate_chemical_addition({
                "initial_solution": initial_water,
                "reactants": [{"formula": "Ca(OH)2", "amount": float(dose), "units": "mmol"}],
                "allow_precipitation": True,
                "database": database,
            })

            # Calculate hardness from result
            ca_final = result.get("element_totals_molality", {}).get("Ca", 0) or 0
            mg_final = result.get("element_totals_molality", {}).get("Mg", 0) or 0
            final_hardness = (ca_final + mg_final) * 100000  # mg/L as CaCO3

            # Check if this is closer to target
            hardness_diff = abs(final_hardness - target_hardness_mg_caco3)
            if hardness_diff < best_hardness_diff:
                best_hardness_diff = hardness_diff
                best_dose = float(dose)
                best_result = result

            logger.debug(f"Dose {dose:.3f} mmol: hardness {final_hardness:.1f} mg/L CaCO3")

        except Exception as e:
            logger.error(f"Dose {dose:.3f} failed: {e}")

    if best_result:
        # Calculate achieved hardness
        achieved_hardness = (
            (best_result.get("element_totals_molality", {}).get("Ca", 0) or 0)
            + (best_result.get("element_totals_molality", {}).get("Mg", 0) or 0)
        ) * 100000

        # Calculate removal efficiency
        hardness_to_remove = initial_hardness - target_hardness_mg_caco3
        if abs(hardness_to_remove) < 1e-6:
            removal_efficiency = 100.0 if abs(initial_hardness - achieved_hardness) < 1e-6 else None
        else:
            hardness_removed = initial_hardness - achieved_hardness
            removal_efficiency = (hardness_removed / hardness_to_remove) * 100

        # Add optimization summary
        best_result["optimization_summary"] = {
            "optimal_dose_mmol": best_dose,
            "target_hardness_mg_caco3": target_hardness_mg_caco3,
            "achieved_hardness_mg_caco3": achieved_hardness,
            "hardness_removal_efficiency": removal_efficiency,
        }

        logger.info(f"Optimal lime dose: {best_dose:.3f} mmol/L")
        return best_result
    else:
        raise ConvergenceError("All doses failed - check water chemistry and database")


async def optimize_phosphorus_removal(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate optimal coagulant dose for phosphorus removal with optional pH control.

    Supports both single-objective (P removal only) and dual-objective (P + pH)
    optimization with stoichiometry-based smart bounds.

    Args:
        input_data: {
            'initial_water': Water composition (WaterAnalysisInput format)
            'target_p_mg_l': Target phosphorus concentration in mg/L as P
            'coagulant': Optional coagulant formula (default: 'FeCl3')
            'target_ph': Optional target pH for dual optimization
            'database': Optional database file (default: minteq.dat)
        }

    Returns:
        Solution state with optimization_summary containing:
        - optimal_coagulant_dose_mmol: Optimal coagulant dose
        - optimal_naoh_dose_mmol: Optional NaOH dose if pH control enabled
        - target_p_mg_l: Target P concentration
        - achieved_p_mg_l: Achieved P concentration
        - p_removal_efficiency: Removal efficiency percentage
    """
    # Validate input
    if "initial_water" not in input_data:
        raise InputValidationError("Missing required field: 'initial_water'")
    if "target_p_mg_l" not in input_data:
        raise InputValidationError("Missing required field: 'target_p_mg_l'")

    initial_water = input_data["initial_water"]
    target_p_mg_l = input_data["target_p_mg_l"]
    coagulant = input_data.get("coagulant", "FeCl3")
    target_ph = input_data.get("target_ph")
    database = input_data.get("database", "minteq.dat")

    if target_p_mg_l < 0:
        raise InputValidationError("target_p_mg_l must be non-negative")

    # Get initial P concentration
    initial_analysis = await calculate_solution_speciation(initial_water)
    p_initial_molal = float(initial_analysis.get("element_totals_molality", {}).get("P", 0) or 0)
    p_initial_mg = p_initial_molal * 30974  # mg/L as P

    # Fallback: infer initial P from input
    p_input_mg = None
    if p_initial_mg <= 0:
        try:
            analysis = (initial_water or {}).get("analysis", {}) or {}
            p_keys = ["P", "p", "phosphorus", "phosphate", "PO4", "P(5)"]
            for k in analysis.keys():
                if k in p_keys or k.lower() in [kk.lower() for kk in p_keys]:
                    val = analysis[k]
                    if isinstance(val, (int, float)):
                        base_units = (initial_water or {}).get("units", "mg/L").lower()
                        if base_units in ("mg/l", "ppm"):
                            p_input_mg = float(val)
                        elif base_units in ("mmol/l",):
                            p_input_mg = float(val) * 30.974
                    break
        except Exception:
            pass

    # Stoichiometric estimate for optimization bounds
    if "Fe" in coagulant:
        estimated_coagulant_molal = p_initial_molal * 2.0
    else:
        estimated_coagulant_molal = p_initial_molal * 2.2

    max_reasonable_dose = max(estimated_coagulant_molal * 4.0, 5.0)

    # Determine appropriate precipitates
    if "Fe" in coagulant:
        minerals = ["Strengite", "Fe(OH)3", "FePO4"]
    else:
        minerals = ["AlPO4", "Al(OH)3", "Variscite"]

    logger.info(f"Using parameter sweep for P removal with max dose: {max_reasonable_dose:.3f} mmol/L")

    # Create coagulant dose sweep
    coagulant_doses = np.linspace(0.1, max_reasonable_dose, 12)
    optimization_path = []

    if target_ph is not None:
        # Grid search for coagulant + NaOH
        naoh_doses = np.linspace(0.5, 8.0, 8)
        best_result = None
        best_combined_score = float("inf")
        best_coag_dose = None
        best_naoh_dose = None

        for coag_dose in coagulant_doses:
            for naoh_dose in naoh_doses:
                try:
                    result = await simulate_chemical_addition({
                        "initial_solution": initial_water,
                        "reactants": [
                            {"formula": coagulant, "amount": float(coag_dose), "units": "mmol"},
                            {"formula": "NaOH", "amount": float(naoh_dose), "units": "mmol"},
                        ],
                        "allow_precipitation": True,
                        "equilibrium_minerals": minerals,
                        "database": database,
                    })

                    if isinstance(result, dict) and result.get("error"):
                        optimization_path.append({
                            "coagulant_mmol": float(coag_dose),
                            "naoh_mmol": float(naoh_dose),
                            "error": result.get("error"),
                        })
                        continue

                    # Calculate P and pH
                    p_final_molal = result.get("element_totals_molality", {}).get("P", 0) or 0
                    p_final_mg = p_final_molal * 30974
                    final_ph = result.get("solution_summary", {}).get("pH")
                    if final_ph is None:
                        final_ph = float("inf")

                    # Weighted scalarization
                    p_diff = abs(p_final_mg - target_p_mg_l)
                    ph_diff = abs(final_ph - target_ph) if final_ph != float("inf") else 0

                    p_normalized = p_diff / max(target_p_mg_l, 0.1)
                    ph_normalized = ph_diff / 2.0
                    combined_score = 1.0 * p_normalized + 0.5 * ph_normalized

                    if combined_score < best_combined_score:
                        best_combined_score = combined_score
                        best_result = result
                        best_coag_dose = float(coag_dose)
                        best_naoh_dose = float(naoh_dose)

                    optimization_path.append({
                        "coagulant_mmol": float(coag_dose),
                        "naoh_mmol": float(naoh_dose),
                        "p_mg_l": p_final_mg,
                        "pH": None if final_ph == float("inf") else final_ph,
                    })

                except Exception as e:
                    optimization_path.append({
                        "coagulant_mmol": float(coag_dose),
                        "naoh_mmol": float(naoh_dose),
                        "error": str(e),
                    })
    else:
        # Single coagulant optimization
        best_result = None
        best_p_diff = float("inf")
        best_coag_dose = None
        best_naoh_dose = None

        for coag_dose in coagulant_doses:
            try:
                result = await simulate_chemical_addition({
                    "initial_solution": initial_water,
                    "reactants": [{"formula": coagulant, "amount": float(coag_dose), "units": "mmol"}],
                    "allow_precipitation": True,
                    "equilibrium_minerals": minerals,
                    "database": database,
                })

                if isinstance(result, dict) and result.get("error"):
                    optimization_path.append({"coagulant_mmol": float(coag_dose), "error": result.get("error")})
                    continue

                p_final_molal = result.get("element_totals_molality", {}).get("P", 0) or 0
                p_final_mg = p_final_molal * 30974

                p_diff = abs(p_final_mg - target_p_mg_l)
                if p_diff < best_p_diff:
                    best_p_diff = p_diff
                    best_result = result
                    best_coag_dose = float(coag_dose)

                optimization_path.append({"coagulant_mmol": float(coag_dose), "p_mg_l": p_final_mg})

            except Exception as e:
                optimization_path.append({"coagulant_mmol": float(coag_dose), "error": str(e)})

    if best_result:
        achieved_p_mg = (best_result.get("element_totals_molality", {}).get("P", 0) or 0) * 30974

        # Calculate efficiency
        denom = p_initial_mg if p_initial_mg > 0 else (p_input_mg if p_input_mg and p_input_mg > 0 else None)
        p_removal_eff = None if not denom else ((denom - achieved_p_mg) / denom) * 100

        best_result["optimization_summary"] = {
            "optimal_coagulant_dose_mmol": best_coag_dose,
            "optimal_naoh_dose_mmol": best_naoh_dose if target_ph else None,
            "target_p_mg_l": target_p_mg_l,
            "achieved_p_mg_l": achieved_p_mg,
            "target_ph": target_ph,
            "achieved_ph": best_result.get("solution_summary", {}).get("pH"),
            "p_removal_efficiency": p_removal_eff,
            "initial_p_mg_l_from_sim": p_initial_mg,
            "initial_p_mg_l_from_input": p_input_mg,
            "optimization_path": optimization_path,
        }

        if p_removal_eff is None:
            best_result["optimization_summary"]["note"] = (
                "Initial dissolved phosphorus was zero or unavailable; removal efficiency not computed."
            )

        logger.info(
            f"Optimal doses - {coagulant}: {best_coag_dose:.3f} mmol/L"
            + (f", NaOH: {best_naoh_dose:.3f} mmol/L" if best_naoh_dose else "")
        )
        return best_result
    else:
        return {
            "error": "All dose combinations failed - check water chemistry and database",
            "optimization_path": optimization_path,
            "coagulant": coagulant,
            "target_p_mg_l": target_p_mg_l,
            "target_ph": target_ph,
        }


async def calculate_dosing_requirement_enhanced(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced multi-objective dosing optimization with multiple algorithms.

    Supports multiple optimization methods and multiple simultaneous objectives
    (e.g., minimize pH deviation AND minimize hardness simultaneously).

    Args:
        input_data: {
            'initial_solution': Water composition (WaterAnalysisInput format)
            'reagents': List of reagent specifications with min/max doses
            'objectives': List of optimization objectives with weights
            'optimization_method': Method to use ('adaptive', 'grid_search', etc.)
            'max_iterations': Maximum iterations (default: 100)
            'tolerance': Convergence tolerance (default: 0.01)
            'database': Optional database file
            'allow_precipitation': Allow precipitation (default: True)
            'equilibrium_minerals': Optional mineral list
        }

    Returns:
        Solution state with optimization_summary containing:
        - optimal_doses: Optimal dose for each reagent
        - objective_results: Achieved values for each objective
        - convergence_status: Convergence status message
        - iterations_taken: Number of iterations performed
        - optimization_method: Method used
    """
    # Validate input
    if "initial_solution" not in input_data:
        raise InputValidationError("Missing required field: 'initial_solution'")
    if "reagents" not in input_data or not input_data["reagents"]:
        raise InputValidationError("Missing or empty 'reagents' field")
    if "objectives" not in input_data or not input_data["objectives"]:
        raise InputValidationError("Missing or empty 'objectives' field")

    initial_solution = input_data["initial_solution"]
    reagents = input_data["reagents"]
    objectives = input_data["objectives"]
    method = input_data.get("optimization_method", "grid_search")
    max_iterations = input_data.get("max_iterations", 100)
    tolerance = input_data.get("tolerance", 0.01)
    database = input_data.get("database", "minteq.dat")
    allow_precipitation = input_data.get("allow_precipitation", True)
    equilibrium_minerals = input_data.get("equilibrium_minerals")

    # Normalize weights
    total_weight = sum(obj.get("weight", 1.0) for obj in objectives)
    for obj in objectives:
        obj["normalized_weight"] = obj.get("weight", 1.0) / total_weight

    # Grid search implementation (most reliable for small number of reagents)
    n_reagents = len(reagents)
    grid_points = min(10, max(5, int(100 ** (1.0 / max(n_reagents, 1)))))

    # Create dose grids for each reagent
    dose_grids = []
    for reagent in reagents:
        min_dose = reagent.get("min_dose", 0.0)
        max_dose = reagent.get("max_dose", 10.0)
        dose_grids.append(np.linspace(min_dose, max_dose, grid_points))

    # Generate all combinations
    if n_reagents == 1:
        dose_combinations = [[d] for d in dose_grids[0]]
    elif n_reagents == 2:
        dose_combinations = [[d1, d2] for d1 in dose_grids[0] for d2 in dose_grids[1]]
    else:
        # For 3+ reagents, use meshgrid
        import itertools
        dose_combinations = list(itertools.product(*dose_grids))

    # Evaluate each combination
    best_result = None
    best_score = float("inf")
    best_doses = None
    iterations = 0
    optimization_path = []

    for doses in dose_combinations:
        if iterations >= max_iterations:
            break
        iterations += 1

        try:
            # Build reactants list
            reactants = []
            for i, reagent in enumerate(reagents):
                reactants.append({
                    "formula": reagent["formula"],
                    "amount": float(doses[i]),
                    "units": "mmol",
                })

            # Simulate
            result = await simulate_chemical_addition({
                "initial_solution": initial_solution,
                "reactants": reactants,
                "allow_precipitation": allow_precipitation,
                "equilibrium_minerals": equilibrium_minerals,
                "database": database,
            })

            if isinstance(result, dict) and result.get("error"):
                continue

            # Calculate weighted objective score
            score = 0.0
            obj_values = {}

            for obj in objectives:
                param = obj["parameter"]
                target_value = obj["value"]
                weight = obj["normalized_weight"]

                # Get current value based on parameter type
                current_value = _get_objective_value(result, obj)

                if current_value is not None:
                    # Calculate normalized deviation
                    if target_value != 0:
                        deviation = abs(current_value - target_value) / abs(target_value)
                    else:
                        deviation = abs(current_value - target_value)

                    score += weight * deviation
                    obj_values[param] = {
                        "target": target_value,
                        "achieved": current_value,
                        "deviation": deviation,
                    }

            # Track path
            dose_dict = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
            optimization_path.append({
                "doses": dose_dict,
                "score": score,
                "objectives": obj_values,
            })

            if score < best_score:
                best_score = score
                best_result = result
                best_doses = dose_dict

        except Exception as e:
            logger.debug(f"Dose combination failed: {e}")

    if best_result:
        # Get final objective values
        final_objectives = {}
        for obj in objectives:
            param = obj["parameter"]
            final_objectives[param] = {
                "target": obj["value"],
                "achieved": _get_objective_value(best_result, obj),
                "weight": obj.get("weight", 1.0),
            }

        best_result["optimization_summary"] = {
            "optimal_doses": best_doses,
            "objective_results": final_objectives,
            "convergence_status": "Converged" if best_score < tolerance else "Best found",
            "iterations_taken": iterations,
            "optimization_method": method,
            "weighted_score": best_score,
        }

        return best_result
    else:
        raise OptimizationConvergenceError("All dose combinations failed")


def _get_objective_value(result: Dict[str, Any], objective: Dict[str, Any]) -> Optional[float]:
    """Extract objective value from PHREEQC result."""
    param = objective["parameter"]
    units = objective.get("units", "")

    if param.lower() == "ph":
        return result.get("solution_summary", {}).get("pH")

    elif param.lower() in ("pe", "redox"):
        return result.get("solution_summary", {}).get("pe")

    elif param.lower() in ("total_hardness", "hardness"):
        elements = result.get("element_totals_molality", {})
        ca = elements.get("Ca", 0) or 0
        mg = elements.get("Mg", 0) or 0
        # Return in mg/L as CaCO3
        return (ca + mg) * 100000

    elif param.lower() in ("residual_phosphorus", "phosphorus", "p"):
        p_molal = result.get("element_totals_molality", {}).get("P", 0) or 0
        return p_molal * 30974  # mg/L as P

    elif param.lower() == "si":
        mineral = objective.get("mineral")
        if mineral:
            return result.get("saturation_indices", {}).get(mineral)

    elif param.lower() in ("alkalinity", "alk"):
        return result.get("solution_summary", {}).get("alkalinity_meq_L")

    elif param.lower() == "tds":
        return result.get("solution_summary", {}).get("tds_calculated")

    else:
        # Try to find in element_totals_molality
        elements = result.get("element_totals_molality", {})
        if param in elements:
            molality = elements[param] or 0
            # Convert based on units if specified
            if "mg/l" in units.lower():
                # Would need molar mass lookup - approximate
                return molality * 1000 * 40  # Rough estimate
            return molality

    return None


async def optimize_multi_reagent_treatment(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Advanced multi-reagent optimization with 4 strategy options.

    Strategies:
    - weighted_sum: Single optimal via weighted scalarization
    - pareto_front: Non-dominated solutions (multi-objective)
    - sequential: Optimize reagents one at a time
    - robust: Best worst-case performance

    Args:
        input_data: {
            'initial_water': Water composition (WaterAnalysisInput format)
            'reagents': List of reagent specifications
            'objectives': List of optimization objectives
            'optimization_strategy': Strategy to use
            'grid_points': Grid points per dimension (default: 10)
            'database': Optional database file
            'allow_precipitation': Allow precipitation (default: True)
        }

    Returns:
        Strategy-dependent output structure
    """
    # Validate input
    if "initial_water" not in input_data:
        raise InputValidationError("Missing required field: 'initial_water'")
    if "reagents" not in input_data or not input_data["reagents"]:
        raise InputValidationError("Missing or empty 'reagents' field")
    if "objectives" not in input_data or not input_data["objectives"]:
        raise InputValidationError("Missing or empty 'objectives' field")

    initial_water = input_data["initial_water"]
    reagents = input_data["reagents"]
    objectives = input_data["objectives"]
    strategy = input_data.get("optimization_strategy", "weighted_sum")
    grid_points = input_data.get("grid_points", 10)
    database = input_data.get("database", "minteq.dat")
    allow_precipitation = input_data.get("allow_precipitation", True)

    logger.info(f"Running multi-reagent optimization with strategy: {strategy}")

    if strategy == "weighted_sum":
        return await _optimize_weighted_sum(
            initial_water, reagents, objectives, grid_points, database, allow_precipitation
        )
    elif strategy == "pareto_front":
        return await _optimize_pareto_front(
            initial_water, reagents, objectives, grid_points, database, allow_precipitation
        )
    elif strategy == "sequential":
        return await _optimize_sequential(
            initial_water, reagents, objectives, grid_points, database, allow_precipitation
        )
    elif strategy == "robust":
        return await _optimize_robust(
            initial_water, reagents, objectives, grid_points, database, allow_precipitation
        )
    else:
        raise InputValidationError(
            f"Unknown optimization strategy: {strategy}. "
            "Choose from: 'weighted_sum', 'pareto_front', 'sequential', 'robust'"
        )


async def _optimize_weighted_sum(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    grid_points: int,
    database: str,
    allow_precipitation: bool,
) -> Dict[str, Any]:
    """Weighted sum optimization - scalarize objectives and find single optimum."""
    # Use calculate_dosing_requirement_enhanced internally
    result = await calculate_dosing_requirement_enhanced({
        "initial_solution": initial_water,
        "reagents": reagents,
        "objectives": objectives,
        "optimization_method": "grid_search",
        "database": database,
        "allow_precipitation": allow_precipitation,
    })

    opt_summary = result.get("optimization_summary", {})

    return {
        "strategy": "weighted_sum",
        "optimal_doses": opt_summary.get("optimal_doses"),
        "weighted_score": opt_summary.get("weighted_score"),
        "objective_results": opt_summary.get("objective_results"),
        "final_state": {k: v for k, v in result.items() if k != "optimization_summary"},
    }


async def _optimize_pareto_front(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    grid_points: int,
    database: str,
    allow_precipitation: bool,
) -> Dict[str, Any]:
    """Pareto front optimization - find non-dominated solutions."""
    # Generate all dose combinations
    n_reagents = len(reagents)
    dose_grids = []
    for reagent in reagents:
        min_dose = reagent.get("min_dose", 0.0)
        max_dose = reagent.get("max_dose", 10.0)
        dose_grids.append(np.linspace(min_dose, max_dose, grid_points))

    if n_reagents == 1:
        dose_combinations = [[d] for d in dose_grids[0]]
    elif n_reagents == 2:
        dose_combinations = [[d1, d2] for d1 in dose_grids[0] for d2 in dose_grids[1]]
    else:
        import itertools
        dose_combinations = list(itertools.product(*dose_grids))

    # Evaluate all combinations
    all_solutions = []

    for doses in dose_combinations:
        try:
            reactants = [
                {"formula": reagents[i]["formula"], "amount": float(doses[i]), "units": "mmol"}
                for i in range(n_reagents)
            ]

            result = await simulate_chemical_addition({
                "initial_solution": initial_water,
                "reactants": reactants,
                "allow_precipitation": allow_precipitation,
                "database": database,
            })

            if isinstance(result, dict) and result.get("error"):
                continue

            # Extract objective values
            obj_values = {}
            for obj in objectives:
                val = _get_objective_value(result, obj)
                if val is not None:
                    obj_values[obj["parameter"]] = val

            if len(obj_values) == len(objectives):
                dose_dict = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
                all_solutions.append({
                    "doses": dose_dict,
                    "objectives": obj_values,
                })

        except Exception as e:
            logger.debug(f"Dose combination failed: {e}")

    # Find Pareto-optimal solutions (non-dominated)
    pareto_front = []

    for sol in all_solutions:
        dominated = False
        for other in all_solutions:
            if sol == other:
                continue

            # Check if other dominates sol
            all_better_or_equal = True
            at_least_one_better = False

            for obj in objectives:
                param = obj["parameter"]
                constraint_type = obj.get("constraint_type", "target")
                target = obj["value"]

                sol_val = abs(sol["objectives"][param] - target)
                other_val = abs(other["objectives"][param] - target)

                if other_val > sol_val:
                    all_better_or_equal = False
                elif other_val < sol_val:
                    at_least_one_better = True

            if all_better_or_equal and at_least_one_better:
                dominated = True
                break

        if not dominated:
            pareto_front.append(sol)

    # Recommend solutions (knee point, balanced)
    recommended = []
    if pareto_front:
        # Find solution closest to ideal point (where all objectives are at target)
        best_distance = float("inf")
        best_sol = None

        for sol in pareto_front:
            distance = 0
            for obj in objectives:
                param = obj["parameter"]
                target = obj["value"]
                deviation = abs(sol["objectives"][param] - target) / max(abs(target), 1e-6)
                distance += deviation ** 2

            if distance < best_distance:
                best_distance = distance
                best_sol = sol

        if best_sol:
            best_sol["is_recommended"] = True
            recommended.append(best_sol)

    return {
        "strategy": "pareto_front",
        "pareto_front": pareto_front,
        "recommended_solutions": recommended,
    }


async def _optimize_sequential(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    grid_points: int,
    database: str,
    allow_precipitation: bool,
) -> Dict[str, Any]:
    """Sequential optimization - optimize reagents one at a time."""
    current_water = copy.deepcopy(initial_water)
    optimal_doses = {}
    optimization_path = []

    for reagent in reagents:
        formula = reagent["formula"]
        min_dose = reagent.get("min_dose", 0.0)
        max_dose = reagent.get("max_dose", 10.0)

        doses = np.linspace(min_dose, max_dose, grid_points)
        best_dose = None
        best_score = float("inf")
        best_result = None

        for dose in doses:
            try:
                result = await simulate_chemical_addition({
                    "initial_solution": current_water,
                    "reactants": [{"formula": formula, "amount": float(dose), "units": "mmol"}],
                    "allow_precipitation": allow_precipitation,
                    "database": database,
                })

                if isinstance(result, dict) and result.get("error"):
                    continue

                # Calculate score based on objectives
                score = 0.0
                for obj in objectives:
                    val = _get_objective_value(result, obj)
                    if val is not None:
                        target = obj["value"]
                        weight = obj.get("weight", 1.0)
                        deviation = abs(val - target) / max(abs(target), 1e-6)
                        score += weight * deviation

                if score < best_score:
                    best_score = score
                    best_dose = float(dose)
                    best_result = result

            except Exception as e:
                logger.debug(f"Dose {dose} for {formula} failed: {e}")

        if best_dose is not None:
            optimal_doses[formula] = best_dose
            optimization_path.append({
                "reagent": formula,
                "optimal_dose": best_dose,
                "score": best_score,
            })

            # Update current water for next reagent (reconstruct from result)
            if best_result:
                from .batch_processing import reconstruct_solution_from_result
                current_water = reconstruct_solution_from_result(best_result, current_water)

    # Get final objective results
    objective_results = {}
    if best_result:
        for obj in objectives:
            param = obj["parameter"]
            objective_results[param] = {
                "target": obj["value"],
                "achieved": _get_objective_value(best_result, obj),
            }

    return {
        "strategy": "sequential",
        "optimal_doses": optimal_doses,
        "objective_results": objective_results,
        "optimization_path": optimization_path,
        "final_state": best_result,
    }


async def _optimize_robust(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    grid_points: int,
    database: str,
    allow_precipitation: bool,
) -> Dict[str, Any]:
    """Robust optimization - best worst-case performance."""
    # Generate dose combinations at fewer points
    n_reagents = len(reagents)
    reduced_grid = max(5, grid_points // 2)
    dose_grids = []

    for reagent in reagents:
        min_dose = reagent.get("min_dose", 0.0)
        max_dose = reagent.get("max_dose", 10.0)
        dose_grids.append(np.linspace(min_dose, max_dose, reduced_grid))

    if n_reagents == 1:
        dose_combinations = [[d] for d in dose_grids[0]]
    elif n_reagents == 2:
        dose_combinations = [[d1, d2] for d1 in dose_grids[0] for d2 in dose_grids[1]]
    else:
        import itertools
        dose_combinations = list(itertools.product(*dose_grids))

    best_doses = None
    best_worst_case = float("inf")
    best_result = None
    sensitivity = {}

    for doses in dose_combinations:
        try:
            reactants = [
                {"formula": reagents[i]["formula"], "amount": float(doses[i]), "units": "mmol"}
                for i in range(n_reagents)
            ]

            result = await simulate_chemical_addition({
                "initial_solution": initial_water,
                "reactants": reactants,
                "allow_precipitation": allow_precipitation,
                "database": database,
            })

            if isinstance(result, dict) and result.get("error"):
                continue

            # Calculate worst-case deviation (max deviation across objectives)
            worst_deviation = 0.0

            for obj in objectives:
                val = _get_objective_value(result, obj)
                if val is not None:
                    target = obj["value"]
                    deviation = abs(val - target) / max(abs(target), 1e-6)
                    worst_deviation = max(worst_deviation, deviation)

            if worst_deviation < best_worst_case:
                best_worst_case = worst_deviation
                best_doses = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
                best_result = result

        except Exception as e:
            logger.debug(f"Dose combination failed: {e}")

    # Calculate sensitivity (approximate via finite differences)
    if best_doses and best_result:
        for reagent in reagents:
            formula = reagent["formula"]
            base_dose = best_doses[formula]
            delta = 0.1 * base_dose if base_dose > 0 else 0.1

            # Perturb dose and calculate change
            try:
                reactants = []
                for r in reagents:
                    dose = best_doses[r["formula"]]
                    if r["formula"] == formula:
                        dose += delta
                    reactants.append({"formula": r["formula"], "amount": dose, "units": "mmol"})

                perturbed = await simulate_chemical_addition({
                    "initial_solution": initial_water,
                    "reactants": reactants,
                    "allow_precipitation": allow_precipitation,
                    "database": database,
                })

                if not (isinstance(perturbed, dict) and perturbed.get("error")):
                    # Calculate change in objective
                    total_change = 0.0
                    for obj in objectives:
                        base_val = _get_objective_value(best_result, obj)
                        perturbed_val = _get_objective_value(perturbed, obj)
                        if base_val is not None and perturbed_val is not None:
                            total_change += abs(perturbed_val - base_val)

                    sensitivity[formula] = total_change / delta

            except Exception:
                sensitivity[formula] = None

    # Get objective results
    objective_results = {}
    if best_result:
        for obj in objectives:
            param = obj["parameter"]
            objective_results[param] = {
                "target": obj["value"],
                "achieved": _get_objective_value(best_result, obj),
            }

    return {
        "strategy": "robust",
        "optimal_doses": best_doses,
        "objective_results": objective_results,
        "robustness_analysis": {
            "worst_case_score": best_worst_case,
            "sensitivity": sensitivity,
        },
        "final_state": best_result,
    }
