"""
Optimization tools for water chemistry treatment design.

This module provides MCP-compatible wrappers for specialized optimization functions:
- generate_lime_softening_curve: Complete dose-response curves
- calculate_lime_softening_dose: Optimal lime softening dose
- calculate_dosing_requirement_enhanced: Multi-objective dosing optimization
- optimize_multi_reagent_treatment: Multi-reagent optimization with 4 strategies

Note: For phosphorus removal, use screen_equilibrium_phosphorus_removal.
The former calculate_phosphorus_removal_dose name is a deprecated Python-only
compatibility alias and is intentionally not registered as an MCP tool. See
tools/phosphorus_removal.py for the diagnostic equilibrium screen with:
- 4 strategies: iron, aluminum, struvite, calcium_phosphate
- Dose-response scanning, branch classification, and bracketed refinement
- HFO/HAO surface complexation modeling
- Inline PHREEQC blocks for phases not in standard databases
- Support for multiple coagulants and redox modes
- Detailed convergence, redox, provenance, and recommendation-ineligibility diagnostics

Author: Claude AI
"""

import copy
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
from water_chemistry_mcp.utils.constants import UNIVERSAL_MINERALS
from water_chemistry_mcp.utils.kinetic_database import seeded_precipitation_contact_time
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.dosing import EnhancedDosingRequest, EnhancedDosingResult, LimeSofteningCurveRequest, LimeSofteningCurveResult, LimeSofteningDoseRequest, LimeSofteningDoseResult
from water_chemistry_mcp.waterchem.contracts.optimization import MultiReagentOptimizationRequest, MultiReagentOptimizationResult
from water_chemistry_mcp.waterchem.errors import NestedToolFailureError, OptimizationFailureError, WaterChemistryError, error_payload_code, error_payload_message, is_error_payload, public_error

from .chemical_addition import simulate_chemical_addition
from .solution_speciation import calculate_solution_speciation
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


def _input_validation_error(exc: Exception, *, tool_name: str) -> Dict[str, Any]:
    return public_error(
        "INVALID_INPUT",
        f"Input validation error: {redact_paths(str(exc))}",
        {"tool_name": tool_name, "stage": "validate_input"},
    )


def _hardness_mg_caco3(result: Dict[str, Any]) -> Optional[float]:
    elements = result.get("element_totals_molality", {})
    if not isinstance(elements, dict) or "Ca" not in elements or "Mg" not in elements:
        return None
    ca = elements["Ca"]
    mg = elements["Mg"]
    if ca is None or mg is None:
        return None
    hardness = (float(ca) + float(mg)) * 100000.0
    return hardness if math.isfinite(hardness) else None


def _nested_failure(tool_name: str, message: str, result: Optional[Dict[str, Any]] = None) -> NestedToolFailureError:
    details = {}
    if result:
        details["nested_error_type"] = error_payload_code(result)
    return NestedToolFailureError(
        message,
        call_stage="nested_tool",
        tool_name=tool_name,
        details=details,
    )


def _lime_equilibrium_minerals(database: str) -> List[str]:
    return list(UNIVERSAL_MINERALS)


async def _lime_point(initial_water: Dict[str, Any], dose: float, database: str) -> Dict[str, Any]:
    if dose <= 0:
        untreated = copy.deepcopy(initial_water)
        untreated["database"] = database
        result = await calculate_solution_speciation(untreated)
    else:
        result = await simulate_chemical_addition(
            {
                "initial_solution": initial_water,
                "reactants": [{"formula": "Ca(OH)2", "amount": float(dose), "units": "mmol"}],
                "allow_precipitation": True,
                "equilibrium_minerals": _lime_equilibrium_minerals(database),
                "database": database,
            }
        )

    if is_error_payload(result):
        raise _nested_failure(
            "generate_lime_softening_curve",
            f"Lime-softening point failed at {dose:g} mmol/L: {error_payload_message(result)}",
            result,
        )

    summary = result.get("solution_summary", {})
    ph = summary.get("pH")
    if ph is None:
        raise _nested_failure(
            "generate_lime_softening_curve",
            f"Lime-softening point at {dose:g} mmol/L did not include pH",
        )

    hardness = _hardness_mg_caco3(result)
    if hardness is None:
        raise _nested_failure(
            "generate_lime_softening_curve",
            f"Lime-softening point at {dose:g} mmol/L did not include finite Ca and Mg totals",
        )

    return {
        "lime_dose_mmol": float(dose),
        "pH": float(ph),
        "hardness_mg_caco3": hardness,
        "precipitate_g_L": result.get("total_precipitate_g_L"),
        "baseline": dose <= 0,
        "_raw_result": result,
    }


async def _simulate_reagent_set(
    *,
    initial_solution: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    doses: List[float],
    database: str,
    allow_precipitation: bool,
    equilibrium_minerals: Optional[List[str]] = None,
    tool_name: str = "optimization",
) -> Dict[str, Any]:
    reactants = []
    for index, reagent in enumerate(reagents):
        dose = float(doses[index])
        if dose > 0:
            reactants.append({"formula": reagent["formula"], "amount": dose, "units": "mmol"})

    if not reactants:
        untreated = copy.deepcopy(initial_solution)
        untreated["database"] = database
        result = await calculate_solution_speciation(untreated)
    else:
        result = await simulate_chemical_addition(
            {
                "initial_solution": initial_solution,
                "reactants": reactants,
                "allow_precipitation": allow_precipitation,
                "equilibrium_minerals": equilibrium_minerals,
                "database": database,
            }
        )

    if is_error_payload(result):
        raise _nested_failure(
            tool_name,
            f"Nested reagent simulation failed: {error_payload_message(result)}",
            result,
        )
    return result


# =============================================================================
# MCP Wrapper Functions
# =============================================================================


@holds_batch_redox
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
    try:
        request = LimeSofteningCurveRequest.model_validate(input_data)
    except Exception as exc:
        return _input_validation_error(exc, tool_name="generate_lime_softening_curve")

    curve_data = []
    curve_errors = []
    for dose in request.lime_doses:
        try:
            point = await _lime_point(request.initial_water, float(dose), request.database)
            point.pop("_raw_result", None)
            curve_data.append(point)
        except WaterChemistryError as exc:
            public_error = exc.to_public_dict()
            curve_errors.append({"lime_dose_mmol": float(dose), **public_error, "skipped": True})

    # Sort by dose
    curve_data.sort(key=lambda x: x["lime_dose_mmol"])

    optimal_dose = _find_optimal_dose(curve_data, target_hardness=request.target_hardness_mg_caco3)

    return LimeSofteningCurveResult(
        curve_data=curve_data,
        optimal_dose=optimal_dose,
        curve_errors=curve_errors or None,
    ).model_dump(exclude_none=True)


def _find_optimal_dose(curve_data: List[Dict[str, Any]], target_hardness: float) -> Optional[Dict[str, Any]]:
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


def _lime_reactor_sizing(
    cfg: Dict[str, Any],
    initial_elements: Dict[str, float],
    softened_elements: Dict[str, float],
    achieved_hardness_mg_caco3: Optional[float] = None,
    target_hardness_mg_caco3: Optional[float] = None,
    optimal_dose_mmol: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Screening reactor-sizing add-on for lime softening (roadmap #2).

    Turns the equilibrium dose result into a reactor basis: which mineral kinetically LIMITS the
    reactor (Calcite for Ca removal vs Brucite Mg(OH)2 when Mg is being precipitated at high pH), the
    seeded contact time to approach the softened hardness, an optional reactor volume, the cold-water
    penalty, and a Brucite-seeding flag. Uses the vetted kinetic layer (seeded_precipitation_contact_time)
    with a representative INLET supersaturation (the equilibrium endpoint SI is ~0, so the driving
    supersaturation of the freshly-limed water is taken as an explicit screening input). Screening only.
    """
    if not cfg or not cfg.get("enabled"):
        return None

    T = float(cfg.get("temperature_c", 25.0))
    approach = float(cfg.get("approach_fraction", 0.90))
    sf = float(cfg.get("design_safety_factor", 1.0))
    flow_m3_d = cfg.get("flow_m3_d")

    # molality (mol/kg) ~ mol/L for dilute waters -> mmol/L
    ca_soft = (softened_elements.get("Ca", 0) or 0) * 1000.0
    mg_soft = (softened_elements.get("Mg", 0) or 0) * 1000.0
    mg_init = (initial_elements.get("Mg", 0) or 0) * 1000.0

    minerals = [{
        "mineral": "Calcite", "inlet_si": float(cfg.get("calcite_inlet_si", 1.5)),
        "area": float(cfg.get("calcite_surface_area_m2_L", 1.0)),
        "c_eq_mmol": max(ca_soft, 0.05),  # softened Ca floor
    }]
    # Brucite (Mg removal) is active only when Mg is actually being precipitated (excess-lime, high pH)
    if (mg_init - mg_soft) > 0.1:
        minerals.append({
            "mineral": "Brucite", "inlet_si": float(cfg.get("brucite_inlet_si", 1.0)),
            "area": float(cfg.get("brucite_surface_area_m2_L", 2.0)),
            "c_eq_mmol": max(mg_soft, 0.02),
        })

    per_mineral = {}
    for m in minerals:
        k = seeded_precipitation_contact_time(
            mineral=m["mineral"], initial_si=m["inlet_si"], reactive_surface_area_m2_per_L=m["area"],
            equilibrium_conc_mmol_per_L=m["c_eq_mmol"], approach_fraction=approach, temperature_c=T,
        )
        per_mineral[m["mineral"]] = {
            "inlet_si": m["inlet_si"],
            "screening_contact_time_min": k.get("contact_time_min"),
            "design_contact_time_min": (k["contact_time_min"] * sf) if k.get("contact_time_min") else None,
            "t90_min": k.get("t90_min"),
            "seeding_required": k.get("seeding_required"),
        }

    # The limiting mineral is the slowest one -> it sets the reactor retention time.
    sized = {name: v for name, v in per_mineral.items() if v["design_contact_time_min"] is not None}
    limiting = max(sized, key=lambda n: sized[n]["design_contact_time_min"]) if sized else None
    design_hrt_min = sized[limiting]["design_contact_time_min"] if limiting else None

    # Target-compliance is load-bearing: the reactor basis is only meaningful if the equilibrium dose
    # actually meets the target hardness. If the optimizer fell short (or picked a 0 dose), the block is
    # sized for the ACHIEVED removal, not the target -- surface that prominently so it is not misread.
    target_met = None
    if achieved_hardness_mg_caco3 is not None and target_hardness_mg_caco3 is not None:
        target_met = achieved_hardness_mg_caco3 <= target_hardness_mg_caco3 + 1.0  # ~1 mg/L tolerance

    block: Dict[str, Any] = {
        "enabled": True,
        "temperature_c": T,
        "design_safety_factor": sf,
        "target_hardness_met": target_met,
        "sized_for": "achieved removal at the optimal dose",
        "per_mineral": per_mineral,
        "limiting_mineral": limiting,
        "design_hrt_min": design_hrt_min,
        "brucite_active": any(m["mineral"] == "Brucite" for m in minerals),
        "model": "surface-controlled first-order kinetics (screening); inlet SI is a representative input",
        "caveats": (
            "SCREENING reactor basis, not a final design. Inlet supersaturation is a representative input "
            "(equilibrium endpoint SI ~0); the model is lumped first-order seeded growth with no crystal "
            "population balance, CSTR-vs-batch hydraulics, solids recycle or mass-transfer limit. Two-stage "
            "(Ca then high-pH Mg) split, recarbonation and seed recycle are not modelled. Confirm with a "
            "jar/pilot test."
        ),
    }
    if target_met is False:
        block["target_not_met_warning"] = (
            f"The lime dose optimizer did NOT reach the target hardness "
            f"(achieved {achieved_hardness_mg_caco3:.0f} vs target {target_hardness_mg_caco3:.0f} "
            f"mg/L as CaCO3 at {optimal_dose_mmol:.2f} mmol/L lime). This reactor basis sizes the "
            "ACHIEVED removal only -- it does NOT deliver the target. Raise the lime dose / search cap "
            "(and pH for Mg removal) until the target is met before trusting the retention time."
        )
    if T < 15.0:
        block["cold_water_note"] = (
            f"At {T:.0f} C the precipitation rate is Arrhenius-slowed vs 25 C, lengthening the required "
            "contact time (the cold-lime-softening penalty)."
        )
    if flow_m3_d is not None and design_hrt_min is not None:
        block["flow_m3_d"] = float(flow_m3_d)
        block["screening_reactor_volume_m3"] = float(flow_m3_d) * design_hrt_min / 1440.0
        block["hydraulic_model_assumption"] = (
            "ideal contact volume V = Q * HRT; not mixing-regime-specific. Apply a hydraulic/pilot factor."
        )
    return block


@holds_batch_redox
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
    try:
        request = LimeSofteningDoseRequest.model_validate(input_data)
    except Exception as exc:
        return _input_validation_error(exc, tool_name="calculate_lime_softening_dose")

    try:
        initial_point = await _lime_point(request.initial_water, 0.0, request.database)
    except WaterChemistryError as exc:
        return exc.to_public_dict()

    initial_hardness = initial_point["hardness_mg_caco3"]
    target_hardness_mg_caco3 = request.target_hardness_mg_caco3

    logger.info(f"Initial hardness: {initial_hardness:.1f} mg/L as CaCO3")
    logger.info(f"Target hardness: {target_hardness_mg_caco3:.1f} mg/L as CaCO3")

    # Use stoichiometric estimate to set smart optimization bounds
    hardness_to_remove_mmol = max(initial_hardness - target_hardness_mg_caco3, 0.0) / 100.0869
    estimated_lime = hardness_to_remove_mmol * 0.6  # mmol/L stoichiometric bound estimate
    max_reasonable_lime_dose = max(estimated_lime * 3.0, 5.0)  # Ensure practical softening search range

    logger.info(f"Using parameter sweep with estimated max dose: {max_reasonable_lime_dose:.3f} mmol/L")

    # Include zero as a real untreated baseline; never synthesize pH=0/hardness=0.
    doses = np.linspace(0.0, max_reasonable_lime_dose, 15)
    best_dose = 0.0
    best_result = initial_point["_raw_result"]
    best_hardness_diff = abs(initial_hardness - target_hardness_mg_caco3)
    achieved_hardness = initial_hardness
    failed_points = []

    for dose in doses:
        try:
            point = await _lime_point(request.initial_water, float(dose), request.database)
            result = point["_raw_result"]
            final_hardness = point["hardness_mg_caco3"]

            # Check if this is closer to target
            hardness_diff = abs(final_hardness - target_hardness_mg_caco3)
            if hardness_diff < best_hardness_diff:
                best_hardness_diff = hardness_diff
                best_dose = float(dose)
                best_result = result
                achieved_hardness = final_hardness

            logger.debug(f"Dose {dose:.3f} mmol: hardness {final_hardness:.1f} mg/L CaCO3")

        except WaterChemistryError as e:
            logger.error(f"Dose {dose:.3f} failed: {e}")
            failed_points.append({"lime_dose_mmol": float(dose), **e.to_public_dict(), "skipped": True})

    if best_result:
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
            "initial_hardness_mg_caco3": initial_hardness,
            "failed_points": failed_points or None,
        }

        # Optional screening reactor-sizing add-on (roadmap #2): kinetic contact time / limiting mineral.
        if request.reactor_sizing:
            reactor_block = _lime_reactor_sizing(
                request.reactor_sizing,
                initial_elements=initial_point["_raw_result"].get("element_totals_molality", {}) or {},
                softened_elements=best_result.get("element_totals_molality", {}) or {},
                achieved_hardness_mg_caco3=achieved_hardness,
                target_hardness_mg_caco3=target_hardness_mg_caco3,
                optimal_dose_mmol=best_dose,
            )
            if reactor_block is not None:
                best_result["optimization_summary"]["reactor_sizing"] = reactor_block

        logger.info(f"Optimal lime dose: {best_dose:.3f} mmol/L")
        return LimeSofteningDoseResult.model_validate(best_result).model_dump(exclude_none=True)
    else:
        return OptimizationFailureError(
            "All lime-softening dose points failed",
            call_stage="dose_sweep",
            tool_name="calculate_lime_softening_dose",
            details={"failed_points": failed_points},
        ).to_public_dict()


@holds_batch_redox
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
            'optimization_method': Must be 'grid_search'
            'grid_points': Optional grid points per reagent dimension
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
    try:
        input_model = EnhancedDosingRequest.model_validate(input_data)
    except Exception as exc:
        return _input_validation_error(exc, tool_name="calculate_dosing_requirement_enhanced")

    initial_solution = input_model.initial_solution
    reagents = [reagent.model_dump(exclude_none=True) for reagent in input_model.reagents]
    objectives = [objective.model_dump(exclude_none=True) for objective in input_model.objectives]
    invalid_constraints = [
        obj.get("constraint_type")
        for obj in objectives
        if not _supported_objective_constraint(obj.get("constraint_type"))
    ]
    if invalid_constraints:
        return public_error(
            "INVALID_INPUT",
            f"Unsupported objective constraint type(s): {invalid_constraints}",
            {
                "tool_name": "calculate_dosing_requirement_enhanced",
                "stage": "validate_input",
            },
        )
    method = input_model.optimization_method
    max_iterations = input_model.max_iterations
    tolerance = input_model.tolerance
    database = input_model.database
    allow_precipitation = input_model.allow_precipitation
    equilibrium_minerals = input_model.equilibrium_minerals

    # Normalize weights
    total_weight = sum(obj.get("weight", 1.0) for obj in objectives)
    if total_weight <= 0:
        return public_error(
            "INVALID_INPUT",
            "Objective weights must sum to a positive value",
            {"tool_name": "calculate_dosing_requirement_enhanced", "stage": "validate_input"},
        )
    for obj in objectives:
        obj["normalized_weight"] = obj.get("weight", 1.0) / total_weight

    # Grid search implementation (most reliable for small number of reagents)
    n_reagents = len(reagents)
    grid_points = input_model.grid_points or min(
        10,
        max(2, int(max_iterations ** (1.0 / max(n_reagents, 1)))),
    )
    total_grid_evaluations = grid_points**n_reagents
    if total_grid_evaluations > max_iterations:
        return public_error(
            "INVALID_INPUT",
            "max_iterations is too small to evaluate the requested Cartesian dose grid",
            {
                "tool_name": "calculate_dosing_requirement_enhanced",
                "stage": "validate_input",
                "grid_points_per_reagent": grid_points,
                "reagent_count": n_reagents,
                "required_evaluations": total_grid_evaluations,
                "max_iterations": max_iterations,
            },
        )

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
    failed_evaluations = []

    for doses in dose_combinations:
        if iterations >= max_iterations:
            break
        iterations += 1

        try:
            result = await _simulate_reagent_set(
                initial_solution=initial_solution,
                reagents=reagents,
                doses=[float(dose) for dose in doses],
                database=database,
                allow_precipitation=allow_precipitation,
                equilibrium_minerals=equilibrium_minerals,
                tool_name="calculate_dosing_requirement_enhanced",
            )

            # Calculate weighted objective score
            score = 0.0
            obj_values = {}
            missing_objectives = []

            for obj in objectives:
                param = obj["parameter"]
                target_value = obj["value"]
                weight = obj["normalized_weight"]

                # Get current value based on parameter type
                current_value = _get_objective_value(result, obj)

                if current_value is None or not math.isfinite(float(current_value)):
                    missing_objectives.append(param)
                    continue

                deviation = _normalized_objective_violation(float(current_value), obj)

                score += weight * deviation
                obj_values[param] = {
                    "target": target_value,
                    "achieved": current_value,
                    "deviation": deviation,
                }

            if missing_objectives:
                raise NestedToolFailureError(
                    "One or more requested optimization objectives were absent "
                    "from the chemistry result.",
                    call_stage="objective_extraction",
                    tool_name="calculate_dosing_requirement_enhanced",
                    details={
                        "missing_objectives": missing_objectives,
                        "doses": {
                            reagents[i]["formula"]: float(doses[i])
                            for i in range(n_reagents)
                        },
                    },
                )

            # Track path
            dose_dict = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
            optimization_path.append(
                {
                    "doses": dose_dict,
                    "score": score,
                    "objectives": obj_values,
                }
            )

            if score < best_score:
                best_score = score
                best_result = result
                best_doses = dose_dict

        except Exception as e:
            failed_evaluations.append(
                {
                    "doses": {
                        reagents[i]["formula"]: float(doses[i])
                        for i in range(n_reagents)
                    },
                    "error_type": type(e).__name__,
                }
            )
            logger.debug(f"Dose combination failed: {e}")

    if best_result:
        # Get final objective values and evaluate every requested target
        # independently. The weighted score ranks grid points; it must not let
        # a low-weight target miss masquerade as overall target attainment.
        final_objectives = {}
        objective_target_checks = []
        for obj in objectives:
            param = obj["parameter"]
            target_value = float(obj["value"])
            achieved_value = _get_objective_value(best_result, obj)
            if achieved_value is None or not math.isfinite(float(achieved_value)):
                return OptimizationFailureError(
                    "The selected enhanced-dosing point omitted a requested objective",
                    call_stage="objective_extraction",
                    tool_name="calculate_dosing_requirement_enhanced",
                    details={"missing_objective": param},
                ).to_public_dict()

            achieved_value = float(achieved_value)
            deviation = _normalized_objective_violation(achieved_value, obj)
            objective_tolerance = float(obj.get("tolerance", tolerance))
            within_tolerance = bool(deviation <= objective_tolerance)
            final_objectives[param] = {
                "target": target_value,
                "achieved": achieved_value,
                "weight": obj.get("weight", 1.0),
                "normalized_deviation": deviation,
                "tolerance": objective_tolerance,
                "target_met": within_tolerance,
            }
            objective_target_checks.append(within_tolerance)

        target_met = bool(objective_target_checks and all(objective_target_checks))
        recommendation_blockers = [
            "grid_search_does_not_establish_branch_uniqueness",
            "not_plant_calibrated_or_holdout_validated",
        ]
        if not target_met:
            recommendation_blockers.insert(0, "one_or_more_objective_targets_not_met")

        best_result.update(
            {
                "solver_converged": True,
                "target_met": target_met,
                "solution_bracketed": False,
                "recommendation_eligible": False,
                "recommendation_blockers": recommendation_blockers,
            }
        )
        best_result["optimization_summary"] = {
            "optimal_doses": best_doses if target_met else None,
            "screening_best_doses": best_doses,
            "objective_results": final_objectives,
            "convergence_status": (
                "converged_target_met" if target_met else "converged_target_not_met"
            ),
            "solver_converged": True,
            "target_met": target_met,
            "solution_bracketed": False,
            "iterations_taken": iterations,
            "optimization_method": method,
            "grid_points_per_reagent": grid_points,
            "total_grid_evaluations": total_grid_evaluations,
            "grid_complete": iterations == total_grid_evaluations,
            "weighted_score": best_score,
            "failed_evaluation_count": len(failed_evaluations),
            "recommendation_eligible": False,
            "recommendation_blockers": recommendation_blockers,
        }

        return EnhancedDosingResult.model_validate(best_result).model_dump(exclude_none=True)
    else:
        return OptimizationFailureError(
            "All enhanced dosing combinations failed",
            call_stage="grid_search",
            tool_name="calculate_dosing_requirement_enhanced",
            details={
                "iterations_taken": iterations,
                "failed_evaluation_count": len(failed_evaluations),
                "missing_or_failed_objectives_are_fatal": True,
            },
        ).to_public_dict()


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
        if not isinstance(elements, dict) or "Ca" not in elements or "Mg" not in elements:
            return None
        ca = elements["Ca"]
        mg = elements["Mg"]
        if ca is None or mg is None:
            return None
        # Return in mg/L as CaCO3
        return (ca + mg) * 100000

    elif param.lower() in ("residual_phosphorus", "phosphorus", "p"):
        elements = result.get("element_totals_molality", {})
        if not isinstance(elements, dict) or "P" not in elements:
            return None
        p_molal = elements["P"]
        if p_molal is None:
            return None
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
            molality = elements[param]
            if molality is None:
                return None
            # Convert based on units if specified
            if "mg/l" in units.lower():
                # Would need molar mass lookup - approximate
                return molality * 1000 * 40  # Rough estimate
            return molality

    return None


_SUPPORTED_OBJECTIVE_CONSTRAINTS = {
    "target",
    "equality",
    "min",
    "max",
    "minimize",
    "maximize",
    "less_than",
    "greater_than",
}


def _supported_objective_constraint(constraint_type: Any) -> bool:
    if constraint_type is None:
        return True
    return str(constraint_type).strip().lower() in _SUPPORTED_OBJECTIVE_CONSTRAINTS


def _normalized_objective_violation(
    current_value: float,
    objective: Dict[str, Any],
) -> float:
    """Return a nonnegative normalized target/constraint violation."""

    target = float(objective["value"])
    scale = abs(target) if target != 0 else 1.0
    constraint = str(objective.get("constraint_type") or "target").strip().lower()
    if constraint in {"target", "equality"}:
        absolute_violation = abs(current_value - target)
    elif constraint in {"max", "minimize", "less_than"}:
        absolute_violation = max(current_value - target, 0.0)
    elif constraint in {"min", "maximize", "greater_than"}:
        absolute_violation = max(target - current_value, 0.0)
    else:
        raise ValueError(f"unsupported objective constraint type: {constraint}")
    return absolute_violation / scale


def _objectives_meet_targets(
    objective_values: Dict[str, float],
    objectives: List[Dict[str, Any]],
    *,
    default_tolerance: float = 0.01,
) -> bool:
    for objective in objectives:
        parameter = objective["parameter"]
        if parameter not in objective_values:
            return False
        tolerance = float(objective.get("tolerance", default_tolerance))
        if _normalized_objective_violation(objective_values[parameter], objective) > tolerance:
            return False
    return True


def _diagnostic_optimization_blockers(target_met: bool) -> List[str]:
    blockers = [
        "grid_search_does_not_establish_branch_uniqueness",
        "not_plant_calibrated_or_holdout_validated",
    ]
    if not target_met:
        blockers.insert(0, "one_or_more_objective_targets_not_met")
    return blockers


@holds_batch_redox
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
    try:
        input_model = MultiReagentOptimizationRequest.model_validate(input_data)
    except Exception as exc:
        return _input_validation_error(exc, tool_name="optimize_multi_reagent_treatment")

    initial_water = input_model.initial_water
    reagents = [reagent.model_dump(exclude_none=True) for reagent in input_model.reagents]
    objectives = [objective.model_dump(exclude_none=True) for objective in input_model.objectives]
    invalid_constraints = [
        obj.get("constraint_type")
        for obj in objectives
        if not _supported_objective_constraint(obj.get("constraint_type"))
    ]
    if invalid_constraints:
        return public_error(
            "INVALID_INPUT",
            f"Unsupported objective constraint type(s): {invalid_constraints}",
            {
                "tool_name": "optimize_multi_reagent_treatment",
                "stage": "validate_input",
            },
        )
    strategy = input_model.optimization_strategy
    grid_points = input_model.grid_points
    database = input_model.database
    allow_precipitation = input_model.allow_precipitation

    logger.info(f"Running multi-reagent optimization with strategy: {strategy}")

    if strategy == "weighted_sum":
        result = await _optimize_weighted_sum(initial_water, reagents, objectives, grid_points, database, allow_precipitation)
    elif strategy == "pareto_front":
        result = await _optimize_pareto_front(initial_water, reagents, objectives, grid_points, database, allow_precipitation)
    elif strategy == "sequential":
        result = await _optimize_sequential(initial_water, reagents, objectives, grid_points, database, allow_precipitation)
    elif strategy == "robust":
        result = await _optimize_robust(initial_water, reagents, objectives, grid_points, database, allow_precipitation)
    else:
        return public_error(
            "INVALID_INPUT",
            f"Unknown optimization strategy: {strategy}. "
            "Choose from: 'weighted_sum', 'pareto_front', 'sequential', 'robust'",
            {"tool_name": "optimize_multi_reagent_treatment", "stage": "validate_input"},
        )

    if is_error_payload(result):
        return result
    result.setdefault("solver_converged", True)
    result.setdefault("target_met", False)
    result["recommendation_eligible"] = False
    result.setdefault(
        "recommendation_blockers",
        _diagnostic_optimization_blockers(bool(result["target_met"])),
    )
    return MultiReagentOptimizationResult.model_validate(result).model_dump(exclude_none=True)


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
    required_evaluations = grid_points ** len(reagents)
    if required_evaluations > 10000:
        return OptimizationFailureError(
            "Requested weighted-sum Cartesian grid exceeds the supported evaluation budget",
            call_stage="validate_grid",
            tool_name="optimize_multi_reagent_treatment",
            details={
                "grid_points_per_reagent": grid_points,
                "reagent_count": len(reagents),
                "required_evaluations": required_evaluations,
                "maximum_evaluations": 10000,
            },
        ).to_public_dict()
    result = await calculate_dosing_requirement_enhanced(
        {
            "initial_solution": initial_water,
            "reagents": reagents,
            "objectives": objectives,
            "optimization_method": "grid_search",
            "grid_points": grid_points,
            "max_iterations": required_evaluations,
            "database": database,
            "allow_precipitation": allow_precipitation,
        }
    )
    if is_error_payload(result):
        return NestedToolFailureError(
            f"Weighted-sum optimization failed in nested enhanced dosing tool: {error_payload_message(result)}",
            call_stage="nested_tool",
            tool_name="optimize_multi_reagent_treatment",
            details={"nested_error_type": error_payload_code(result)},
        ).to_public_dict()

    opt_summary = result.get("optimization_summary", {})

    return {
        "strategy": "weighted_sum",
        "optimal_doses": opt_summary.get("optimal_doses"),
        "screening_best_doses": opt_summary.get("screening_best_doses"),
        "weighted_score": opt_summary.get("weighted_score"),
        "objective_results": opt_summary.get("objective_results"),
        "final_state": {k: v for k, v in result.items() if k != "optimization_summary"},
        "solver_converged": bool(result.get("solver_converged", False)),
        "target_met": bool(result.get("target_met", False)),
        "recommendation_eligible": False,
        "recommendation_blockers": list(result.get("recommendation_blockers", [])),
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
            result = await _simulate_reagent_set(
                initial_solution=initial_water,
                reagents=reagents,
                doses=[float(dose) for dose in doses],
                database=database,
                allow_precipitation=allow_precipitation,
                tool_name="optimize_multi_reagent_treatment",
            )

            # Extract objective values
            obj_values = {}
            for obj in objectives:
                val = _get_objective_value(result, obj)
                if val is not None and math.isfinite(float(val)):
                    obj_values[obj["parameter"]] = float(val)

            if len(obj_values) == len(objectives):
                dose_dict = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
                all_solutions.append(
                    {
                        "doses": dose_dict,
                        "objectives": obj_values,
                    }
                )

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

                sol_val = _normalized_objective_violation(sol["objectives"][param], obj)
                other_val = _normalized_objective_violation(other["objectives"][param], obj)

                if other_val > sol_val:
                    all_better_or_equal = False
                elif other_val < sol_val:
                    at_least_one_better = True

            if all_better_or_equal and at_least_one_better:
                dominated = True
                break

        if not dominated:
            pareto_front.append(sol)

    # Retain a diagnostic knee point without labeling it a recommendation.
    screening_candidates = []
    target_met = False
    if pareto_front:
        # Find solution closest to ideal point (where all objectives are at target)
        best_distance = float("inf")
        best_sol = None

        for sol in pareto_front:
            distance = 0
            for obj in objectives:
                param = obj["parameter"]
                deviation = _normalized_objective_violation(sol["objectives"][param], obj)
                distance += deviation**2

            if distance < best_distance:
                best_distance = distance
                best_sol = sol

        if best_sol:
            best_sol["is_recommended"] = False
            best_sol["screening_candidate"] = True
            screening_candidates.append(best_sol)
            target_met = _objectives_meet_targets(best_sol["objectives"], objectives)

    if not all_solutions:
        return OptimizationFailureError(
            "All Pareto dose combinations failed or omitted requested objectives",
            call_stage="pareto_grid_search",
            tool_name="optimize_multi_reagent_treatment",
        ).to_public_dict()

    return {
        "strategy": "pareto_front",
        "pareto_front": pareto_front,
        "recommended_solutions": [],
        "screening_candidates": screening_candidates,
        "solver_converged": True,
        "target_met": target_met,
        "recommendation_eligible": False,
        "recommendation_blockers": _diagnostic_optimization_blockers(target_met),
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
                result = await _simulate_reagent_set(
                    initial_solution=current_water,
                    reagents=[reagent],
                    doses=[float(dose)],
                    database=database,
                    allow_precipitation=allow_precipitation,
                    tool_name="optimize_multi_reagent_treatment",
                )

                # Calculate score based on objectives
                score = 0.0
                objective_values = {}
                for obj in objectives:
                    val = _get_objective_value(result, obj)
                    if val is None or not math.isfinite(float(val)):
                        raise NestedToolFailureError(
                            "Sequential dose point omitted a requested objective",
                            call_stage="objective_extraction",
                            tool_name="optimize_multi_reagent_treatment",
                            details={"missing_objective": obj["parameter"]},
                        )
                    objective_values[obj["parameter"]] = float(val)
                    weight = obj.get("weight", 1.0)
                    score += weight * _normalized_objective_violation(float(val), obj)

                if score < best_score:
                    best_score = score
                    best_dose = float(dose)
                    best_result = result

            except Exception as e:
                logger.debug(f"Dose {dose} for {formula} failed: {e}")

        if best_dose is not None:
            optimal_doses[formula] = best_dose
            optimization_path.append(
                {
                    "reagent": formula,
                    "optimal_dose": best_dose,
                    "score": best_score,
                }
            )

            # Update current water for next reagent (reconstruct from result)
            if best_result:
                from .batch_processing import reconstruct_solution_from_result

                current_water = reconstruct_solution_from_result(best_result, current_water)

    if not optimal_doses or not best_result:
        return OptimizationFailureError(
            "Sequential optimization found no complete objective evaluation",
            call_stage="sequential_grid_search",
            tool_name="optimize_multi_reagent_treatment",
        ).to_public_dict()

    # Get final objective results
    objective_results = {}
    final_values = {}
    for obj in objectives:
        param = obj["parameter"]
        achieved = _get_objective_value(best_result, obj)
        if achieved is None or not math.isfinite(float(achieved)):
            return OptimizationFailureError(
                "Sequential final state omitted a requested objective",
                call_stage="objective_extraction",
                tool_name="optimize_multi_reagent_treatment",
                details={"missing_objective": param},
            ).to_public_dict()
        final_values[param] = float(achieved)
        objective_results[param] = {
            "target": obj["value"],
            "achieved": float(achieved),
        }

    target_met = _objectives_meet_targets(final_values, objectives)

    return {
        "strategy": "sequential",
        "optimal_doses": optimal_doses if target_met else None,
        "screening_best_doses": optimal_doses,
        "objective_results": objective_results,
        "optimization_path": optimization_path,
        "final_state": best_result,
        "solver_converged": True,
        "target_met": target_met,
        "recommendation_eligible": False,
        "recommendation_blockers": _diagnostic_optimization_blockers(target_met),
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
            result = await _simulate_reagent_set(
                initial_solution=initial_water,
                reagents=reagents,
                doses=[float(dose) for dose in doses],
                database=database,
                allow_precipitation=allow_precipitation,
                tool_name="optimize_multi_reagent_treatment",
            )

            # Calculate worst-case deviation (max deviation across objectives)
            worst_deviation = 0.0

            for obj in objectives:
                val = _get_objective_value(result, obj)
                if val is None or not math.isfinite(float(val)):
                    raise NestedToolFailureError(
                        "Robust dose point omitted a requested objective",
                        call_stage="objective_extraction",
                        tool_name="optimize_multi_reagent_treatment",
                        details={"missing_objective": obj["parameter"]},
                    )
                deviation = _normalized_objective_violation(float(val), obj)
                worst_deviation = max(worst_deviation, deviation)

            if worst_deviation < best_worst_case:
                best_worst_case = worst_deviation
                best_doses = {reagents[i]["formula"]: float(doses[i]) for i in range(n_reagents)}
                best_result = result

        except Exception as e:
            logger.debug(f"Dose combination failed: {e}")

    if best_doses is None or best_result is None:
        return OptimizationFailureError(
            "Robust optimization found no complete objective evaluation",
            call_stage="robust_grid_search",
            tool_name="optimize_multi_reagent_treatment",
        ).to_public_dict()

    # Calculate sensitivity (approximate via finite differences)
    if best_doses and best_result:
        for reagent in reagents:
            formula = reagent["formula"]
            base_dose = best_doses[formula]
            delta = 0.1 * base_dose if base_dose > 0 else 0.1

            # Perturb dose and calculate change
            try:
                perturbed_doses = []
                for r in reagents:
                    dose = best_doses[r["formula"]]
                    if r["formula"] == formula:
                        dose += delta
                    perturbed_doses.append(float(dose))

                perturbed = await _simulate_reagent_set(
                    initial_solution=initial_water,
                    reagents=reagents,
                    doses=perturbed_doses,
                    database=database,
                    allow_precipitation=allow_precipitation,
                    tool_name="optimize_multi_reagent_treatment",
                )

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
    final_values = {}
    for obj in objectives:
        param = obj["parameter"]
        achieved = _get_objective_value(best_result, obj)
        if achieved is None or not math.isfinite(float(achieved)):
            return OptimizationFailureError(
                "Robust final state omitted a requested objective",
                call_stage="objective_extraction",
                tool_name="optimize_multi_reagent_treatment",
                details={"missing_objective": param},
            ).to_public_dict()
        final_values[param] = float(achieved)
        objective_results[param] = {
            "target": obj["value"],
            "achieved": float(achieved),
        }

    target_met = _objectives_meet_targets(final_values, objectives)

    return {
        "strategy": "robust",
        "optimal_doses": best_doses if target_met else None,
        "screening_best_doses": best_doses,
        "objective_results": objective_results,
        "robustness_analysis": {
            "worst_case_score": best_worst_case,
            "sensitivity": sensitivity,
        },
        "final_state": best_result,
        "solver_converged": True,
        "target_met": target_met,
        "recommendation_eligible": False,
        "recommendation_blockers": _diagnostic_optimization_blockers(target_met),
    }
