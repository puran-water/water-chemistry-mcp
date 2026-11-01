"""Dosing-requirement tool using canonical contracts and bounded bisection."""

from __future__ import annotations

import copy
import logging
import math
from typing import Any, Dict, Tuple

from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.dosing import DosingRequirementRequest, DosingRequirementResult
from water_chemistry_mcp.waterchem.errors import InfeasibleTargetError, NestedToolFailureError, WaterChemistryError, error_payload_code, error_payload_message, is_error_payload, public_error, stable_error_code

from .chemical_addition import simulate_chemical_addition
from .solution_speciation import calculate_solution_speciation
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)

#: Doublings of an absent ``max_dose``'s first top before the search refuses, one PHREEQC run each (2**10 times the
#: water's own alkalinity scale).
BRACKET_DOUBLINGS = 10
#: Lane c187 (c179 G8): the 10 mmol/L default top refused a ZLR bleed needing about 71 meq/L of HCl.
DOSE_BRACKET_RULE = (
    "reagent.max_dose, where given, is the search's top. Absent, the first top is the water's alkalinity at the "
    "search's bottom (PHREEQC Alk, eq/kgw, times the solution's water mass) plus, for a pH target, the free strong acid "
    "or base the target holds at unit activity and pKw 14, read as mmol of a reagent carrying one equivalent per mol: "
    "an acid of at least one equivalent per mol never needs more to reach a pH at or above its alkalinity end point. "
    "While the target lies beyond it and each top moves toward the target, the top doubles, at most BRACKET_DOUBLINGS "
    "times; the bisection runs between the last two tops")


@holds_batch_redox
async def calculate_dosing_requirement(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate the dose required to reach a target condition.

    Uses a conservative bracketing
    search over the same runner-backed tool path used by chemical addition.

    reagent: {formula, min_dose (mmol/L, default 0), max_dose (mmol/L)}. An absent max_dose is bracketed from the
    water's alkalinity, doubled while the target lies beyond it (DOSE_BRACKET_RULE); the answer and any refusal carry
    dose_bracket (basis, first top, top, doublings).
    """

    logger.info("Running calculate_dosing_requirement tool...")

    try:
        request = DosingRequirementRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "calculate_dosing_requirement", "stage": "validate_input"},
        )

    try:
        from water_chemistry_mcp.waterchem.answered_by import collecting_runs

        with collecting_runs() as runs:
            answer = await _solve_by_bisection(request)
        return _one_chemical_model(answer, runs, request)
    except InfeasibleTargetError as exc:
        return exc.to_public_dict()
    except WaterChemistryError as exc:
        return exc.to_public_dict()
    except Exception as exc:
        logger.exception("Unexpected dosing-requirement failure")
        return NestedToolFailureError(
            f"calculate_dosing_requirement failed unexpectedly: {type(exc).__name__}: {redact_paths(str(exc))}",
            call_stage="dose_search",
            tool_name="calculate_dosing_requirement",
            cause=exc,
        ).to_public_dict()


async def _solve_by_bisection(request: DosingRequirementRequest) -> Dict[str, Any]:
    target_value = request.target_condition.value
    tolerance = request.tolerance

    low_dose = float(request.reagent.min_dose)
    low_value, low_result = await _evaluate_dose(request, low_dose)
    low_residual = low_value - target_value
    best_dose = low_dose
    best_value = low_value
    best_result = low_result
    best_abs_residual = abs(low_residual)

    bracket = _dose_bracket(request, low_dose, low_result)
    if best_abs_residual <= tolerance:
        return _success_response(
            request,
            best_dose,
            best_result,
            iterations=0,
            status="Converged",
            solution_bracketed=False,
            bracket=bracket,
        )

    high_dose = bracket["first_top_mmol_per_L"]
    while True:
        try:
            high_value, high_result = await _evaluate_dose(request, high_dose)
            high_residual = high_value - target_value
        except NestedToolFailureError as exc:
            raise NestedToolFailureError(
                "The upper dose-bound evaluation failed; the physical target bracket "
                "is unresolved and was not changed.",
                call_stage="dose_bracketing",
                tool_name="calculate_dosing_requirement",
                details={
                    "failed_dose_mmol_per_L": high_dose,
                    "search_lower_bound_mmol_per_L": low_dose,
                    "search_upper_bound_mmol_per_L": high_dose,
                    "physical_bracket_preserved": True,
                    "dose_bracket": {**bracket, "top_mmol_per_L": high_dose},
                    **_nested_error_summary(exc),
                },
                cause=exc,
            ) from exc

        moved_toward = abs(high_residual) < abs(low_residual)
        if abs(high_residual) < best_abs_residual:
            best_dose = high_dose
            best_value = high_value
            best_result = high_result
            best_abs_residual = abs(high_residual)
        if (best_abs_residual <= tolerance or low_residual * high_residual <= 0 or bracket["basis"] != WATER_BASIS
                or not moved_toward or bracket["doublings"] == BRACKET_DOUBLINGS):
            break
        # the target lies beyond this top and the reagent moves toward it: the top becomes the bottom (DOSE_BRACKET_RULE)
        low_dose, low_residual = high_dose, high_residual
        high_dose *= 2.0
        bracket["doublings"] += 1
    bracket["top_mmol_per_L"] = high_dose

    if best_abs_residual <= tolerance:
        return _success_response(
            request,
            best_dose,
            best_result,
            iterations=0,
            status="Converged",
            solution_bracketed=True,
            bracket=bracket,
        )

    if low_residual * high_residual > 0:
        details = {
            "target_parameter": request.target_condition.parameter,
            "target_value": target_value,
            "best_dose_mmol_per_L": best_dose,
            "best_value": best_value,
            "search_lower_bound_mmol_per_L": low_dose,
            "search_upper_bound_mmol_per_L": high_dose,
            "reason": "target_unreachable",
            "dose_bracket": bracket,
        }
        if bracket["basis"] == WATER_BASIS:
            why = ("the reagent moved the target away at the top" if not moved_toward else
                   f"not bracketed in {BRACKET_DOUBLINGS} doublings of the water's alkalinity scale")
            message = f"Target unreachable: {why} ({high_dose:.6g} mmol/L); state reagent.max_dose to search further"
        else:
            message = "Target unreachable within reagent dose bounds"
        raise InfeasibleTargetError(
            message,
            call_stage="dose_bracketing",
            tool_name="calculate_dosing_requirement",
            details=details,
        )

    for iteration in range(1, request.max_iterations + 1):
        mid_dose = 0.5 * (low_dose + high_dose)
        try:
            mid_value, mid_result = await _evaluate_dose(request, mid_dose)
        except NestedToolFailureError as exc:
            raise NestedToolFailureError(
                "A dose evaluation failed inside a valid physical bracket; the "
                "bracket is unresolved and was not narrowed.",
                call_stage="dose_bisection",
                tool_name="calculate_dosing_requirement",
                details={
                    "failed_dose_mmol_per_L": mid_dose,
                    "search_lower_bound_mmol_per_L": low_dose,
                    "search_upper_bound_mmol_per_L": high_dose,
                    "physical_bracket_preserved": True,
                    **_nested_error_summary(exc),
                },
                cause=exc,
            ) from exc

        mid_residual = mid_value - target_value
        if abs(mid_residual) < best_abs_residual:
            best_dose = mid_dose
            best_value = mid_value
            best_result = mid_result
            best_abs_residual = abs(mid_residual)

        if abs(mid_residual) <= tolerance:
            return _success_response(
                request,
                mid_dose,
                mid_result,
                iteration,
                "Converged",
                solution_bracketed=True,
                bracket=bracket,
            )

        if low_residual * mid_residual <= 0:
            high_dose = mid_dose
            high_residual = mid_residual
        else:
            low_dose = mid_dose
            low_residual = mid_residual

    details = {
        "target_parameter": request.target_condition.parameter,
        "target_value": target_value,
        "best_dose_mmol_per_L": best_dose,
        "best_value": best_value,
        "residual": best_value - target_value,
        "iterations": request.max_iterations,
        "target_met": False,
        "solution_bracketed": True,
        "dose_bracket": bracket,
    }
    raise InfeasibleTargetError(
        "Dose search did not converge within the requested tolerance",
        call_stage="dose_bisection",
        tool_name="calculate_dosing_requirement",
        details=details,
    )


WATER_BASIS = "water_alkalinity"


def _dose_bracket(request: DosingRequirementRequest, low_dose: float, low_result: Dict[str, Any]) -> Dict[str, Any]:
    """The search's first top and its basis (`DOSE_BRACKET_RULE`), read off the bottom's own PHREEQC state. Refuses,
    naming max_dose, where that state reports no alkalinity or water mass."""
    if request.reagent.max_dose is not None:
        return {"basis": "reagent.max_dose", "first_top_mmol_per_L": float(request.reagent.max_dose), "doublings": 0}
    summary = low_result.get("solution_summary") or {}
    # PHREEQC's Alk is eq per kg WATER, whatever the parser's key says (tools.phreeqc.parser)
    alkalinity, water = summary.get("alkalinity_eq_L"), summary.get("mass_kg_water")
    if alkalinity is None or not water:
        raise InfeasibleTargetError(
            "No dose bracket: the water's PHREEQC state reports no alkalinity or water mass; state reagent.max_dose",
            call_stage="dose_bracketing",
            tool_name="calculate_dosing_requirement",
            details={"reason": "no_water_bracket", "rule": DOSE_BRACKET_RULE},
        )
    free = 0.0
    condition = request.target_condition
    if condition.parameter.lower() == "ph" and summary.get("pH") is not None:
        free = 10.0 ** -condition.value if condition.value < summary["pH"] else 10.0 ** (condition.value - 14.0)
    scale = (abs(float(alkalinity)) + free) * float(water) * 1000.0
    return {"basis": WATER_BASIS, "alkalinity_eq_per_kgw": float(alkalinity), "mass_kg_water": float(water),
            "free_acid_or_base_mmol": free * float(water) * 1000.0,
            "first_top_mmol_per_L": low_dose + (scale if scale > 0 else request.initial_guess_mmol),
            "doublings": 0, "rule": DOSE_BRACKET_RULE}


async def _evaluate_dose(request: DosingRequirementRequest, dose_mmol_per_l: float) -> Tuple[float, Dict[str, Any]]:
    solution = copy.deepcopy(request.initial_solution)
    if request.allow_extreme_ph:
        solution["allow_extreme_ph"] = True

    if dose_mmol_per_l <= 0:
        solution["database"] = request.database
        result = await calculate_solution_speciation(solution)
    else:
        result = await simulate_chemical_addition(
            {
                "initial_solution": solution,
                "reactants": [
                    {
                        "formula": request.reagent.formula,
                        "amount": float(dose_mmol_per_l),
                        "units": "mmol",
                    }
                ],
                "allow_precipitation": request.allow_precipitation,
                "equilibrium_minerals": request.equilibrium_minerals,
                "database": request.database,
            }
        )

    if is_error_payload(result):
        raise NestedToolFailureError(
            f"Nested PHREEQC evaluation failed at dose {dose_mmol_per_l:g} mmol/L: {error_payload_message(result)}",
            call_stage="dose_evaluation",
            tool_name="calculate_dosing_requirement",
            details={
                "dose_mmol_per_L": dose_mmol_per_l,
                "nested_error_type": error_payload_code(result),
            },
        )

    value = _extract_target_value(result, request)
    if value is None or not math.isfinite(float(value)):
        raise NestedToolFailureError(
            f"Target parameter '{request.target_condition.parameter}' was missing or nonfinite in PHREEQC output",
            call_stage="target_extraction",
            tool_name="calculate_dosing_requirement",
            details={"target_condition": request.target_condition.model_dump(exclude_none=True)},
        )
    return float(value), result


def _extract_target_value(result: Dict[str, Any], request: DosingRequirementRequest) -> float | None:
    condition = request.target_condition
    parameter = condition.parameter
    key = parameter.lower()

    if key == "ph":
        return result.get("solution_summary", {}).get("pH")
    if key in {"pe", "redox"}:
        return result.get("solution_summary", {}).get("pe")
    if key in {"total_hardness", "hardness", "calcium_magnesium_hardness"}:
        elements = result.get("element_totals_molality", {})
        if not isinstance(elements, dict) or "Ca" not in elements or "Mg" not in elements:
            return None
        ca = elements["Ca"]
        mg = elements["Mg"]
        if ca is None or mg is None:
            return None
        return (ca + mg) * 100000.0
    if key.startswith("si_"):
        mineral = condition.mineral or parameter[3:]
        return result.get("saturation_indices", {}).get(mineral)
    if key.startswith("total_"):
        element = condition.element_or_species or parameter[6:]
        return result.get("element_totals_molality", {}).get(element)
    if key in {"residual_phosphorus", "phosphorus", "p"}:
        p_molal = result.get("element_totals_molality", {}).get("P")
        return None if p_molal is None else p_molal * 30974.0

    summary = result.get("solution_summary", {})
    for candidate in (parameter, parameter.upper(), parameter.capitalize()):
        if candidate in summary:
            return summary[candidate]

    elements = result.get("element_totals_molality", {})
    for candidate in (parameter, parameter.capitalize(), parameter.upper()):
        if candidate in elements:
            return elements[candidate]
    return None


def _nested_error_summary(error: WaterChemistryError) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "last_nested_error_code": stable_error_code(error.error_type),
        "last_nested_error_message": error.message,
    }
    if error.call_stage:
        summary["last_nested_error_stage"] = error.call_stage
    if error.tool_name:
        summary["last_nested_error_tool_name"] = error.tool_name
    if error.details:
        summary["last_nested_error_details"] = dict(error.details)
    return summary


def _one_chemical_model(answer: Dict[str, Any], runs, request: DosingRequirementRequest) -> Dict[str, Any]:
    """The dose runs of one search solve one chemical model (review M20, gap G08: a search whose recovery changed the
    model answered a different question); more than one refuses. The model's fingerprint goes on the answer."""
    from water_chemistry_mcp.waterchem.chemical_model import CHEMICAL_MODEL_RULING_ID

    reagent = request.reagent.formula
    dosed = {run["model"]["fingerprint"] for run in runs if run.get("model") and reagent in run["model"]["reactions"]}
    if len(dosed) > 1:
        raise NestedToolFailureError(
            f"the dose search solved {len(dosed)} chemical models for one question ({sorted(dosed)}): a dose run's "
            f"phases, gas, surfaces, held states or reactions differed from another's ({CHEMICAL_MODEL_RULING_ID}, "
            "review M20)",
            call_stage="chemical_model",
            tool_name="calculate_dosing_requirement",
        )
    if isinstance(answer, dict) and dosed:
        answer["chemical_model_fingerprint"] = next(iter(dosed))
    return answer


def _success_response(
    request: DosingRequirementRequest,
    dose_mmol_per_l: float,
    final_result: Dict[str, Any],
    iterations: int,
    status: str,
    *,
    solution_bracketed: bool,
    bracket: Dict[str, Any],
) -> Dict[str, Any]:
    output = DosingRequirementResult(
        dose_bracket=bracket,
        required_dose_mmol_per_L=float(dose_mmol_per_l),
        final_state=final_result,
        iterations_taken=iterations,
        convergence_status=status,
        solver_converged=True,
        target_met=True,
        solution_bracketed=solution_bracketed,
        recommendation_eligible=False,
        recommendation_blockers=[
            "generic_bisection_does_not_establish_global_monotonicity_or_branch_uniqueness",
            "not_plant_calibrated_or_holdout_validated",
        ],
    )
    logger.info("Dosing search %s at %.6g mmol/L", status.lower(), dose_mmol_per_l)
    return output.model_dump(exclude_none=True)
