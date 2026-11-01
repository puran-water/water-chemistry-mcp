"""
Batch processing tool for efficient water chemistry calculations.

Supports:
- Parameter sweeps (e.g., pH from 6-11)
- Dose optimization curves
- Sensitivity analysis
- Treatment train optimization
"""

import asyncio
import copy
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from water_chemistry_mcp.waterchem.contracts.batch import BatchScenariosRequest, BatchScenariosResult
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.errors import InputContractError, NestedToolFailureError, error_payload_code, error_payload_message, is_error_payload, public_error

from .chemical_addition import simulate_chemical_addition
from .phosphorus_removal import screen_equilibrium_phosphorus_removal
from .solution_speciation import calculate_solution_speciation
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


def _input_contract_error(exc: Exception, *, tool_name: str) -> Dict[str, Any]:
    return InputContractError(
        f"Input validation error: {redact_paths(str(exc))}",
        call_stage="validate_input",
        tool_name=tool_name,
    ).to_public_dict()


def _canonical_sweep_parameter(parameter: str) -> str:
    key = str(parameter).strip()
    lowered = key.lower()
    if lowered == "ph":
        return "ph"
    if lowered in {"temperature", "temp"}:
        return "temperature_celsius"
    if lowered == "pe":
        return "pe"
    return key


def _display_sweep_parameter(parameter: str) -> str:
    return "pH" if parameter == "ph" else parameter


def _apply_sweep_value(solution: Dict[str, Any], parameter: str, value: Any) -> str:
    """Apply a sweep value without leaving stale pH aliases in the solution."""

    canonical_parameter = _canonical_sweep_parameter(parameter)
    if canonical_parameter == "ph":
        solution.pop("ph", None)
        solution.pop("pH", None)
        analysis = solution.get("analysis")
        if isinstance(analysis, dict):
            analysis.pop("ph", None)
            analysis.pop("pH", None)
        solution["ph"] = value
        return "pH"

    if canonical_parameter in {"pe", "temperature_celsius"}:
        solution[canonical_parameter] = value
        return _display_sweep_parameter(canonical_parameter)

    if "analysis" not in solution or not isinstance(solution["analysis"], dict):
        solution["analysis"] = {}
    analysis = solution["analysis"]
    swept = _same_inventory_keys(analysis, canonical_parameter)
    previous = analysis.get(swept[0]) if swept else None
    for key in swept:
        analysis.pop(key)
    if isinstance(previous, dict) and not isinstance(value, dict):
        # the base solution stated this analyte's basis or units: the swept value keeps them
        value = {**previous, "value": value}
    analysis[canonical_parameter] = value
    return _display_sweep_parameter(canonical_parameter)


def _same_inventory_keys(analysis: Dict[str, Any], parameter: str) -> List[str]:
    'Public engineering calculation interface.'
    from water_chemistry_mcp.waterchem.analysis import normalize_analyte_name, phreeqc_species_name

    def identity(key: str) -> Optional[str]:
        try:
            return phreeqc_species_name(normalize_analyte_name(str(key)))
        except ValueError:
            return None

    target = identity(parameter)
    if target is None:
        return [key for key in analysis if key == parameter]
    return [key for key in analysis if key == parameter or identity(key) == target]


def _scenario_failure(scenario: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    error = NestedToolFailureError(
        f"Scenario '{scenario.get('name', 'unnamed')}' failed in nested tool: {error_payload_message(result)}",
        call_stage="nested_tool",
        tool_name="batch_process_scenarios",
        details={
            "scenario_type": scenario.get("type", "chemical_addition"),
            "nested_error_type": error_payload_code(result),
        },
    ).to_public_dict()
    return {"scenario": scenario, "status": "failed", **error}


def _contains_error_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        if is_error_payload(payload):
            return True
        return any(_contains_error_payload(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_error_payload(item) for item in payload)
    return False


def _nested_result_statuses(payload: Any) -> List[str]:
    statuses: List[str] = []
    if isinstance(payload, dict):
        nested_result = payload.get("result")
        if isinstance(nested_result, dict):
            statuses.append("failed" if _contains_error_payload(nested_result) else "success")
        for value in payload.values():
            statuses.extend(_nested_result_statuses(value))
    elif isinstance(payload, list):
        for item in payload:
            statuses.extend(_nested_result_statuses(item))
    return statuses


def _payload_status(payload: Dict[str, Any]) -> str:
    if is_error_payload(payload):
        return "failed"
    nested_statuses = _nested_result_statuses(payload)
    if nested_statuses:
        failed = sum(1 for status in nested_statuses if status == "failed")
        if failed == 0:
            return "success"
        if failed == len(nested_statuses):
            return "failed"
        return "partially_failed"
    return "failed" if _contains_error_payload(payload) else "success"


def reconstruct_solution_from_result(result: Dict[str, Any], original_solution: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reconstruct a full solution object from PHREEQC calculation results.

    This is critical for treatment trains where the output of one step
    becomes the input for the next step. The solution_summary alone
    does not contain the analysis field required for subsequent calculations.

    Args:
        result: PHREEQC calculation result containing element_totals_molality
        original_solution: Original solution object to preserve database and units

    Returns:
        Complete solution object suitable for use as input to next calculation
    """
    # Extract element concentrations from result (in mol/kg)
    element_totals = result.get("element_totals_molality", {})

    # Build analysis dict from element totals
    # PHREEQC returns mol/kg (molality), convert to mmol/L for consistency
    # Assuming density ~1 kg/L for dilute solutions. A molar total needs no stated basis (S, Si and C included): it has
    
    analysis = {}
    for element, molality in element_totals.items():
        if molality and molality > 0:
            # Convert mol/kg to mmol/L (multiply by 1000)
            analysis[element] = molality * 1000

    # Get pH and pe from solution_summary
    solution_summary = result.get("solution_summary", {})

    # Reconstruct complete solution
    new_solution = {
        "units": "mmol/L",  # Standardized output units
        "analysis": analysis,
        # the train's database, or none: the next step then runs where the step's own tool runs (it had been minteq.dat)
        **_named(original_solution.get("database")),
        "temperature_celsius": solution_summary.get(
            "temperature_celsius", original_solution.get("temperature_celsius", 25.0)
        ),
    }

    # Include pH if available (important for next step's equilibrium)
    if "pH" in solution_summary:
        new_solution["pH"] = solution_summary["pH"]

    # Include pe if available
    if "pe" in solution_summary:
        new_solution["pe"] = solution_summary["pe"]

    return new_solution


@holds_batch_redox
async def batch_process_scenarios(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process multiple water chemistry scenarios in batch for efficiency.

    Supports:
    - Parameter sweeps (e.g., pH from 6-11)
    - Dose optimization curves
    - Sensitivity analysis
    - Treatment train optimization

    Args:
        input_data: {
            'base_solution': Initial water chemistry
            'scenarios': List of scenario configurations
            'parallel_limit': Max concurrent simulations (default 10)
            'output_format': 'full' or 'summary'
        }
    """

    try:
        input_model = BatchScenariosRequest.model_validate(input_data)
    except Exception as e:
        return _input_contract_error(e, tool_name="batch_process_scenarios")

    base_solution = input_model.base_solution
    scenarios = [scenario.model_dump(exclude_none=True) for scenario in input_model.scenarios]
    parallel_limit = input_model.parallel_limit
    output_format = input_model.output_format

    # Process scenarios in batches
    results = []

    for i in range(0, len(scenarios), parallel_limit):
        batch = scenarios[i : i + parallel_limit]

        # Create tasks for this batch
        # CRITICAL FIX: Deep copy base_solution for each task to prevent race conditions
        tasks = []
        for scenario in batch:
            task = process_single_scenario(copy.deepcopy(base_solution), scenario)
            tasks.append(task)

        # Run batch in parallel
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for scenario, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                message = redact_paths(str(result))
                logger.error(f"Scenario failed: {scenario.get('name', 'unnamed')} - {message}")
                results.append(
                    {
                        "scenario": scenario,
                        "status": "failed",
                        **public_error(
                            "UNEXPECTED_ERROR",
                            message,
                            {"tool_name": "batch_process_scenarios", "stage": "process_scenario"},
                        ),
                    }
                )
            elif is_error_payload(result):
                results.append(_scenario_failure(scenario, result))
            else:
                status = _payload_status(result) if isinstance(result, dict) else "success"
                entry = {"scenario": scenario, "result": result, "status": status}
                if status == "failed":
                    entry["failure_reason"] = "All nested scenario evaluations failed"
                elif status == "partially_failed":
                    entry["warning"] = "One or more nested scenario evaluations failed"
                results.append(entry)

    # Format output based on request
    if output_format == "summary":
        summary_data = summarize_batch_results(results)
        return BatchScenariosResult(summary=summary_data, details=results).model_dump(exclude_none=True)
    return BatchScenariosResult(results=results).model_dump(exclude_none=True)


def scenario_database(scenario: Dict[str, Any], solution: Dict[str, Any], scenario_type: str) -> Optional[str]:
    'Public engineering calculation interface.'
    return scenario.get("database") or solution.get("database") or None


def _named(database: Optional[str]) -> Dict[str, Any]:
    """A request's database key, only when one is named, so the scenario's tool applies its own default. Each tool
    treats an explicit None as unnamed today (the phosphorus screen drops it before its contract default applies:
    mutation M17 of enc-e/REPORT.md survived), so this is hygiene, not a guard; the reconstructed train solution uses it
    so no step inherits a database the train never named."""
    return {"database": database} if database else {}


def _screen_fields(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """The phosphorus screen's optional fields a scenario carries (oxide_formation_ph, target_ph, redox, ...): every
    field of its request except the ones the scenario sets itself. The scenario had passed none, so every iron or
    aluminium scenario was refused for its oxide-formation pH."""
    from water_chemistry_mcp.waterchem.contracts.phosphorus import PhosphorusRemovalRequest

    own = {"initial_solution", "target_residual_p_mg_l", "strategy", "database"}
    return {key: value for key, value in scenario.items() if key in PhosphorusRemovalRequest.model_fields and key not in own}


async def process_single_scenario(base_solution: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single scenario configuration."""

    scenario_type = scenario.get("type", "chemical_addition")
    # base_solution is this task's own deep copy: every run of the scenario is on one database
    database = scenario_database(scenario, base_solution, scenario_type)
    if database:
        base_solution["database"] = database

    if scenario_type == "chemical_addition":
        # Standard chemical addition
        return await simulate_chemical_addition(
            {
                "initial_solution": base_solution,
                "reactants": scenario.get("reactants", []),
                "allow_precipitation": scenario.get("allow_precipitation", True),
                "equilibrium_minerals": scenario.get("equilibrium_minerals"),
                **_named(database),
            }
        )

    elif scenario_type == "dose_optimization":
        # Use parameter sweep for dose optimization instead of broken tool
        logger.warning("dose_optimization type deprecated - use parameter_sweep instead")

        # Convert to parameter sweep if possible
        config = scenario.get("optimization_config", {})
        if "reagent" in config and "target_condition" in config:
            # Create dose sweep around estimated range
            doses = np.linspace(0.5, 5.0, 10)  # Default dose range

            sweep_results = []
            for dose in doses:
                try:
                    result = await simulate_chemical_addition(
                        {
                            "initial_solution": base_solution,
                            "reactants": [{"formula": config["reagent"]["formula"], "amount": dose, "units": "mmol"}],
                            "allow_precipitation": True,
                            **_named(database),
                        }
                    )
                    sweep_results.append({"dose": dose, "result": result})
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")

            return {"dose_sweep_results": sweep_results}
        else:
            raise ValueError("dose_optimization requires 'reagent' and 'target_condition' in optimization_config")

    elif scenario_type == "parameter_sweep":
        # Sweep a parameter range
        parameter = _canonical_sweep_parameter(scenario["parameter"])
        values = scenario["values"]

        sweep_results = []
        for value in values:
            # CRITICAL FIX: Deep copy to avoid mutating base_solution across iterations
            modified_solution = copy.deepcopy(base_solution)
            output_parameter = _apply_sweep_value(modified_solution, parameter, value)

            # Run analysis
            result = await calculate_solution_speciation(modified_solution)
            sweep_results.append({output_parameter: value, "result": result})

        return {"sweep_results": sweep_results}

    elif scenario_type == "treatment_train":
        # Sequential treatment steps
        current_solution = copy.deepcopy(base_solution)  # Deep copy to avoid mutation
        train_results = []

        for step in scenario.get("steps", []):
            step_result = await process_treatment_step(current_solution, step)
            train_results.append(step_result)

            # CRITICAL FIX: Reconstruct full solution from result, not just summary
            # The solution_summary lacks the 'analysis' field required for subsequent steps
            if not is_error_payload(step_result):
                current_solution = reconstruct_solution_from_result(step_result, current_solution)
            else:
                # On error, keep current solution but log warning
                logger.warning(
                    f"Treatment step failed, using previous solution state: {error_payload_message(step_result)}"
                )

        # Return final reconstructed solution, not just summary
        return {"train_results": train_results, "final_solution": current_solution}

    elif scenario_type in ("ph_sweep", "temperature_sweep", "dose_response"):
        # Map common scenario types to supported behavior
        if scenario_type == "ph_sweep":
            parameter = "ph"
            values = scenario.get("values")
            if values is None and all(k in scenario for k in ("start", "stop", "step")):
                try:
                    values = list(np.arange(scenario["start"], scenario["stop"] + 1e-9, scenario["step"]))
                except Exception:
                    values = [scenario["start"], scenario["stop"]]
            if values is None:
                raise ValueError("ph_sweep requires 'values' or 'start'/'stop'/'step'")
            sweep_results = []
            for value in values:
                # CRITICAL FIX: Deep copy to avoid mutation
                modified_solution = copy.deepcopy(base_solution)
                output_parameter = _apply_sweep_value(modified_solution, parameter, value)
                result = await calculate_solution_speciation(modified_solution)
                sweep_results.append({output_parameter: value, "result": result})
            return {"sweep_results": sweep_results}
        elif scenario_type == "temperature_sweep":
            parameter = "temperature_celsius"
            values = scenario.get("values")
            if values is None and all(k in scenario for k in ("start", "stop", "step")):
                try:
                    values = list(np.arange(scenario["start"], scenario["stop"] + 1e-9, scenario["step"]))
                except Exception:
                    values = [scenario["start"], scenario["stop"]]
            if values is None:
                raise ValueError("temperature_sweep requires 'values' or 'start'/'stop'/'step'")
            sweep_results = []
            for value in values:
                # CRITICAL FIX: Deep copy to avoid mutation
                modified_solution = copy.deepcopy(base_solution)
                modified_solution[parameter] = value
                result = await calculate_solution_speciation(modified_solution)
                sweep_results.append({parameter: value, "result": result})
            return {"sweep_results": sweep_results}
        else:
            # dose_response
            reagent = scenario.get("reagent") or scenario.get("reactant")
            if not reagent or "formula" not in reagent:
                raise ValueError("dose_response requires 'reagent': {'formula': ...}")
            doses = scenario.get("doses") or scenario.get("values") or []
            units = scenario.get("units", "mmol")
            sweep_results = []
            for dose in doses:
                try:
                    result = await simulate_chemical_addition(
                        {
                            "initial_solution": base_solution,
                            "reactants": [{"formula": reagent["formula"], "amount": dose, "units": units}],
                            "allow_precipitation": scenario.get("allow_precipitation", True),
                            "equilibrium_minerals": scenario.get("equilibrium_minerals"),
                            **_named(database),
                        }
                    )
                    sweep_results.append({"dose": dose, "result": result})
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")
            return {"dose_response_results": sweep_results}

    elif scenario_type == "phosphorus_optimization":
        # Advanced phosphorus removal optimization using unified tool
        coagulant = scenario.get("coagulant", "FeCl3")

        # Determine strategy from coagulant type
        if coagulant in ("FeCl3", "FeSO4", "FeCl2", "Fe2(SO4)3"):
            strategy = "iron"
        elif coagulant in ("AlCl3", "Al2(SO4)3"):
            strategy = "aluminum"
        elif coagulant in ("MgCl2", "MgO", "Mg(OH)2"):
            strategy = "struvite"
        elif coagulant in ("Ca(OH)2", "CaCl2", "CaO"):
            strategy = "calcium_phosphate"
        else:
            strategy = "iron"  # Default to iron

        return await screen_equilibrium_phosphorus_removal(
            {
                "initial_solution": base_solution,
                "target_residual_p_mg_l": scenario["target_p_mg_l"],
                "strategy": {
                    "strategy": strategy,
                    "reagent": coagulant,
                },
                **_named(database),
                **_screen_fields(scenario),
            }
        )

    elif scenario_type == "multi_reagent_optimization":
        # Simplified multi-reagent optimization using parameter sweeps
        logger.warning("multi_reagent_optimization simplified - use parameter_sweeps instead")

        reagents = scenario.get("reagents", [])
        objectives = scenario.get("objectives", [])

        if len(reagents) <= 2:
            # Simple grid search for 1-2 reagents
            sweep_results = []

            # Create dose ranges for each reagent
            dose_ranges = []
            for reagent in reagents:
                min_dose = reagent.get("min_dose", 0.1)
                max_dose = reagent.get("max_dose", 5.0)
                dose_ranges.append(np.linspace(min_dose, max_dose, 5))

            # Grid search
            if len(reagents) == 1:
                for dose1 in dose_ranges[0]:
                    try:
                        result = await simulate_chemical_addition(
                            {
                                "initial_solution": base_solution,
                                "reactants": [{"formula": reagents[0]["formula"], "amount": dose1, "units": "mmol"}],
                                "allow_precipitation": True,
                                **_named(database),
                            }
                        )
                        sweep_results.append({"doses": [dose1], "result": result})
                    except Exception as e:
                        logger.error(f"Multi-reagent dose failed: {e}")
            elif len(reagents) == 2:
                for dose1 in dose_ranges[0]:
                    for dose2 in dose_ranges[1]:
                        try:
                            result = await simulate_chemical_addition(
                                {
                                    "initial_solution": base_solution,
                                    "reactants": [
                                        {"formula": reagents[0]["formula"], "amount": dose1, "units": "mmol"},
                                        {"formula": reagents[1]["formula"], "amount": dose2, "units": "mmol"},
                                    ],
                                    "allow_precipitation": True,
                                    **_named(database),
                                }
                            )
                            sweep_results.append({"doses": [dose1, dose2], "result": result})
                        except Exception as e:
                            logger.error(f"Multi-reagent dose failed: {e}")

            return {"multi_reagent_sweep_results": sweep_results}
        else:
            raise ValueError("Simplified multi-reagent optimization supports max 2 reagents")

    elif scenario_type == "lime_softening_optimization":
        # Use existing lime softening function
        return await calculate_lime_softening_dose(
            initial_water=base_solution,
            target_hardness_mg_caco3=scenario["target_hardness_mg_l"],
            database=database,
        )

    elif scenario_type == "alternative_comparison":
        # Simplified alternative comparison
        logger.warning("alternative_comparison simplified - use multiple scenarios instead")

        alternatives = scenario.get("alternatives", [])
        comparison_results = []

        for alt in alternatives:
            try:
                if alt.get("type") == "chemical_addition":
                    result = await simulate_chemical_addition(
                        {
                            "initial_solution": base_solution,
                            "reactants": alt.get("reactants", []),
                            "allow_precipitation": True,
                            **_named(database),
                        }
                    )
                    comparison_results.append({"alternative": alt.get("name", "unnamed"), "result": result})
            except Exception as e:
                logger.error(f"Alternative {alt.get('name', 'unnamed')} failed: {e}")

        return {"alternative_comparison_results": comparison_results}

    else:
        raise ValueError(f"Unknown scenario type: {scenario_type}")


async def process_treatment_step(current_solution: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single treatment step in a treatment train."""

    step_type = step.get("type", "chemical_addition")
    # a step runs on its own database, else the train's (the solution it is handed); never a hard-coded one
    database = scenario_database(step, current_solution, step_type)
    current_solution = {**current_solution, **_named(database)}

    if step_type == "chemical_addition":
        return await simulate_chemical_addition(
            {
                "initial_solution": current_solution,
                "reactants": step.get("reactants", []),
                "allow_precipitation": step.get("allow_precipitation", True),
                "equilibrium_minerals": step.get("equilibrium_minerals"),
                **_named(database),
            }
        )

    elif step_type == "dose_optimization":
        # Use parameter sweep for dose optimization instead of broken tool
        logger.warning("dose_optimization step deprecated - use parameter_sweep instead")

        # Create a simple dose sweep
        config = step.get("optimization_config", {})
        if "reagent" in config:
            doses = np.linspace(0.5, 3.0, 6)  # Simple dose range
            best_result = None

            for dose in doses:
                try:
                    result = await simulate_chemical_addition(
                        {
                            "initial_solution": current_solution,
                            "reactants": [{"formula": config["reagent"]["formula"], "amount": dose, "units": "mmol"}],
                            "allow_precipitation": True,
                            **_named(database),
                        }
                    )
                    if best_result is None:
                        best_result = result
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")

            return best_result if best_result else await calculate_solution_speciation(current_solution)
        else:
            return await calculate_solution_speciation(current_solution)

    else:
        # Just return speciation for unknown types
        return await calculate_solution_speciation(current_solution)


def summarize_batch_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create summary statistics from batch results."""

    summary = {
        "total_scenarios": len(results),
        "successful": sum(1 for r in results if r.get("status", "success") == "success"),
        "partially_failed": sum(1 for r in results if r.get("status") == "partially_failed"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
    }

    # Extract key parameters for successful runs
    successful_results = [r for r in results if r.get("status") != "failed"]

    if successful_results:
        # Collect common parameters
        ph_values = []
        tds_values = []
        precipitate_masses = []

        for r in successful_results:
            result = r["result"]
            if "solution_summary" in result:
                # a result that carries no value is left out of the statistic, never summed as None (the runner's
                # summaries carry no 'tds_calculated': every batch run raised TypeError here)
                for values, key in ((ph_values, "pH"), (tds_values, "tds_calculated")):
                    if result["solution_summary"].get(key) is not None:
                        values.append(result["solution_summary"][key])
            if result.get("total_precipitate_g_L") is not None:
                precipitate_masses.append(result["total_precipitate_g_L"])

        # Calculate statistics
        if ph_values:
            summary["pH_range"] = (min(ph_values), max(ph_values))
            summary["pH_mean"] = sum(ph_values) / len(ph_values)

        if tds_values:
            summary["TDS_range"] = (min(tds_values), max(tds_values))
            summary["TDS_mean"] = sum(tds_values) / len(tds_values)

        if precipitate_masses:
            summary["precipitate_range"] = (min(precipitate_masses), max(precipitate_masses))
            summary["precipitate_total"] = sum(precipitate_masses)

    return summary


# Specialized functions from the prototype


async def generate_lime_softening_curve(
    initial_water: Dict[str, Any], lime_doses: List[float], database: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate a complete lime softening curve showing hardness vs lime dose.
    Much more efficient than individual API calls.
    """

    from .optimization_tools import generate_lime_softening_curve as _generate_lime_softening_curve

    # the database named here, else the water's, else the tool's declared default (minteq.dat)
    request = {"initial_water": initial_water, "lime_doses": lime_doses}
    if database:
        request["database"] = database
    return await _generate_lime_softening_curve(request)


def find_optimal_dose(curve_data: List[Dict[str, Any]], target_hardness: float) -> Optional[Dict[str, Any]]:
    """Find the dose that achieves target hardness."""

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
        # Interpolate
        fraction = (target_hardness - above_target["hardness_mg_caco3"]) / (
            below_target["hardness_mg_caco3"] - above_target["hardness_mg_caco3"]
        )

        optimal_dose = above_target["lime_dose_mmol"] + fraction * (
            below_target["lime_dose_mmol"] - above_target["lime_dose_mmol"]
        )

        return {
            "dose_mmol": optimal_dose,
            "estimated_pH": above_target["pH"] + fraction * (below_target["pH"] - above_target["pH"]),
            "target_hardness": target_hardness,
        }

    return None


async def calculate_lime_softening_dose(
    initial_water: Dict[str, Any], target_hardness_mg_caco3: float, database: Optional[str] = None
) -> Dict[str, Any]:
    """
    Specialized function for lime softening calculations.
    Handles the complexity of Mg(OH)2 precipitation at high pH.
    """

    from .optimization_tools import calculate_lime_softening_dose as _calculate_lime_softening_dose

    # the database named here, else the water's, else the tool's declared default (minteq.dat)
    request = {"initial_water": initial_water, "target_hardness_mg_caco3": target_hardness_mg_caco3}
    if database:
        request["database"] = database
    return await _calculate_lime_softening_dose(request)
