"""
Tool for simulating chemical additions to a solution.
"""

import logging
from typing import Any, Dict

from water_chemistry_mcp.utils.exceptions import InputValidationError
from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_block, build_kinetics_block, build_reaction_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import ConcentrationUnit, redact_paths
from water_chemistry_mcp.waterchem.contracts.kinetics import KineticReactionDefinition, TimeStepDefinition
from water_chemistry_mcp.waterchem.contracts.reaction import ChemicalAdditionRequest, ChemicalAdditionResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.formulas import phreeqc_formula_for_reactant
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.units import convert_concentration
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


@holds_batch_redox
async def simulate_chemical_addition(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulates adding chemicals to a solution and calculates the resulting equilibrium.

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting solution composition
            - reactants: List of chemicals to add with formulas and amounts
            - allow_precipitation: Whether to allow mineral precipitation (optional, default true)
            - equilibrium_minerals: List of minerals to allow to precipitate (optional)
            - database: Path to PHREEQC database file (optional)
            - temperature_celsius: Override temperature for reaction (optional)
            - pressure_atm: Override pressure for reaction (optional)

    Returns:
        Dictionary containing detailed solution properties after the chemical addition
    """
    logger.info("Running simulate_chemical_addition tool...")

    try:
        input_model = ChemicalAdditionRequest.model_validate(input_data)
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_chemical_addition", "stage": "validate_input"},
        )

    try:
        if input_model.kinetic_parameters and input_model.kinetic_parameters.get("enable_kinetics"):
            return await _simulate_chemical_addition_kinetics(input_model)

        database_path = resolve_database(input_model.database, category="general")
        phreeqc_input = build_solution_block_from_canonical(input_model.initial_canonical())

        reactants_list = [_phreeqc_reactant(reactant.model_dump()) for reactant in input_model.reactants]
        use_blocks = []
        if reactants_list:
            phreeqc_input += build_reaction_block(reactants_list, reaction_num=1)
            use_blocks.append("USE reaction 1")

        if input_model.allow_precipitation and input_model.equilibrium_minerals:
            mineral_mapping = database_manager.get_compatible_minerals(database_path, input_model.equilibrium_minerals)
            equilibrium_minerals = [mineral for mineral in mineral_mapping.values() if mineral]
            if equilibrium_minerals:
                phases = [{"name": name} for name in equilibrium_minerals]
                phreeqc_input += build_equilibrium_phases_block(phases, block_num=1, allow_empty=True)
                use_blocks.append("USE equilibrium_phases 1")

        if use_blocks:
            phreeqc_input += "USE solution 1\n"
            phreeqc_input += "\n".join(use_blocks) + "\n"
            phreeqc_input += "SAVE solution 2\n"

        phreeqc_input += (
            build_selected_output_block(block_num=1, saturation_indices=True, phases=True, molalities=True, totals=True)
            + "END\n"
        )

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="simulate_chemical_addition",
                job_name="chemical_addition",
                database_path=database_path,
                database_category="general",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict):
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }
        output_model = ChemicalAdditionResult.model_validate(results)

        logger.info("simulate_chemical_addition tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as e:
        public_payload = e.to_public_dict()
        logger.error(f"Chemical addition tool failed: {public_payload.get('message')}")
        return public_payload

    except InputValidationError as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_chemical_addition", "stage": "validate_reactant"},
        )

    except Exception as e:
        logger.exception("Unexpected error in simulate_chemical_addition")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(e).__name__}: {redact_paths(str(e))}",
            {"tool_name": "simulate_chemical_addition", "stage": "execute"},
        )


def _phreeqc_reactant(reactant: Dict[str, Any]) -> Dict[str, Any]:
    formula = reactant["formula"]
    phreeqc_formula = phreeqc_formula_for_reactant(formula)
    amount = reactant["amount"]
    units = reactant.get("units", "mmol")
    unit_key = units.strip().lower()

    if unit_key in {"mol", "mmol"}:
        return {**reactant, "formula": phreeqc_formula, "units": unit_key}
    if unit_key == "umol":
        return {**reactant, "formula": phreeqc_formula, "amount": amount / 1000.0, "units": "mmol"}
    if unit_key in {"g", "gram", "grams"}:
        amount_mmol = _mass_dose_to_mmol(amount * 1000.0, formula, ConcentrationUnit.MG_L)
        return {**reactant, "formula": phreeqc_formula, "amount": amount_mmol, "units": "mmol"}
    if unit_key in {"mg", "mg/l", "mg/kgw"}:
        amount_mmol = _mass_dose_to_mmol(amount, formula, ConcentrationUnit.MG_L)
        return {**reactant, "formula": phreeqc_formula, "amount": amount_mmol, "units": "mmol"}
    if unit_key in {"ug", "µg", "microgram", "micrograms"}:
        amount_mmol = _mass_dose_to_mmol(amount, formula, ConcentrationUnit.UG_L)
        return {**reactant, "formula": phreeqc_formula, "amount": amount_mmol, "units": "mmol"}
    if unit_key in {"mmol/l", "mmol/kgw"}:
        return {**reactant, "formula": phreeqc_formula, "units": "mmol"}
    if unit_key == "mol/l":
        return {**reactant, "formula": phreeqc_formula, "units": "mol"}
    return {**reactant, "formula": phreeqc_formula}


def _mass_dose_to_mmol(amount: float, formula: str, unit: ConcentrationUnit) -> float:
    try:
        return convert_concentration(amount, formula, unit, ConcentrationUnit.MMOL_L)
    except ValueError as exc:
        raise InputValidationError(f"Unsupported reactant formula for mass-unit dosing: {exc}") from exc


async def _simulate_chemical_addition_kinetics(input_model: ChemicalAdditionRequest) -> Dict[str, Any]:
    database_path = resolve_database(input_model.database, category="general")
    phreeqc_input = build_solution_block_from_canonical(input_model.initial_canonical())

    reactants_list = [_phreeqc_reactant(reactant.model_dump()) for reactant in input_model.reactants]
    use_blocks = []
    if reactants_list:
        phreeqc_input += build_reaction_block(reactants_list, reaction_num=1)
        use_blocks.append("USE reaction 1")

    kinetic_def, time_def = _chemical_kinetics_definitions(input_model.kinetic_parameters)
    rates_str, kinetics_str = build_kinetics_block(kinetic_def, time_def, block_num=1)
    if rates_str:
        phreeqc_input += "\n" + rates_str
    phreeqc_input += "\n" + kinetics_str

    if input_model.allow_precipitation and input_model.equilibrium_minerals:
        mineral_mapping = database_manager.get_compatible_minerals(database_path, input_model.equilibrium_minerals)
        equilibrium_minerals = [mineral for mineral in mineral_mapping.values() if mineral]
        if equilibrium_minerals:
            phases = [{"name": name} for name in equilibrium_minerals]
            phreeqc_input += build_equilibrium_phases_block(phases, block_num=1, allow_empty=True)
            use_blocks.append("USE equilibrium_phases 1")

    if use_blocks:
        phreeqc_input += "USE solution 1\n"
        phreeqc_input += "\n".join(use_blocks) + "\n"

    phreeqc_input += (
        build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            kinetics=True,
        )
        + "END\n"
    )

    num_steps = len(time_def.get("time_values") or []) or int(time_def.get("count") or 1)
    run_result = await run_phreeqc_job(
        PhreeqcJob(
            input_string=phreeqc_input,
            tool_name="simulate_chemical_addition",
            job_name="chemical_addition_kinetics",
            database_path=database_path,
            database_category="general",
            num_steps=num_steps,
        )
    )
    results = run_result.data[-1] if isinstance(run_result.data, list) and run_result.data else run_result.data
    if isinstance(results, dict):
        results["diagnostics"] = {
            **(run_result.diagnostics or {}),
            **(results.get("diagnostics") or {}),
            "engine": run_result.engine,
            "database": run_result.database,
        }
        results["kinetic_modeling_used"] = True
        results.setdefault("kinetic_profiles", [])
    output_model = ChemicalAdditionResult.model_validate(results)
    return output_model.model_dump(exclude_none=True)


def _chemical_kinetics_definitions(kinetic_parameters: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    minerals = kinetic_parameters.get("minerals_kinetic") or {}
    reactions = []
    for mineral, params in minerals.items():
        parameters = {}
        for source, target in {
            "m0": "m0",
            "m": "m",
            "tol": "tol",
            "parms": "parms",
            "parameters": "parms",
            "params": "parms",
        }.items():
            if source in params:
                parameters[target] = params[source]
        reactions.append({"name": mineral, "parameters": parameters or {"m0": 0.0}})

    if not reactions:
        raise ValueError("kinetic_parameters.enable_kinetics requires minerals_kinetic entries")

    time_values = kinetic_parameters.get("time_steps")
    if isinstance(time_values, list) and time_values:
        time_def = {"time_values": time_values, "units": "seconds"}
    else:
        time_def = {"duration": kinetic_parameters.get("duration", 3600.0), "count": kinetic_parameters.get("count", 1)}
    kinetic_contract = KineticReactionDefinition.model_validate({"reactions": reactions})
    time_contract = TimeStepDefinition.model_validate(time_def)
    return kinetic_contract.model_dump(exclude_none=True), time_contract.model_dump(exclude_none=True)
