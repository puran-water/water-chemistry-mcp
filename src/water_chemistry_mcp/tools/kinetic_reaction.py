"""Tool for simulating kinetic reactions."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_block, build_kinetics_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.kinetics import KineticReactionRequest, KineticReactionResult, KineticTimeSeriesResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


@holds_batch_redox
async def simulate_kinetic_reaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate kinetic reactions and return the final state."""

    logger.info("Running simulate_kinetic_reaction tool...")
    request = _validate_request(input_data)
    if isinstance(request, dict):
        return request

    try:
        run_result, time_values, time_units = await _run_kinetic_request(request)
        data = run_result.data
        final_state = data[-1] if isinstance(data, list) and data else data
        payload = {
            "final_state": final_state,
            "diagnostics": {"engine": run_result.engine, "database": run_result.database},
            "time_values": time_values,
            "time_units": time_units,
        }
        output_model = KineticReactionResult.model_validate(payload)
        logger.info("simulate_kinetic_reaction tool finished successfully.")
        return output_model.model_dump(exclude_none=True)
    except WaterChemistryError as exc:
        public_payload = exc.to_public_dict()
        logger.error(f"Kinetic reaction tool failed: {public_payload.get('message')}")
        return public_payload
    except Exception as exc:
        logger.exception("Unexpected error in simulate_kinetic_reaction")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "simulate_kinetic_reaction", "stage": "execute"},
        )


@holds_batch_redox
async def simulate_kinetic_reaction_time_series(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate kinetic reactions and return all parsed time steps."""

    logger.info("Running simulate_kinetic_reaction_time_series tool...")
    request = _validate_request(input_data)
    if isinstance(request, dict):
        return request

    try:
        run_result, time_values, time_units = await _run_kinetic_request(request)
        data = run_result.data if isinstance(run_result.data, list) else [run_result.data]
        final_state = data[-1] if data else {}
        payload = {
            "time_series": data,
            "final_state": final_state,
            "time_values": time_values,
            "time_units": time_units,
            "diagnostics": {"engine": run_result.engine, "database": run_result.database},
        }
        output_model = KineticTimeSeriesResult.model_validate(payload)
        logger.info("simulate_kinetic_reaction_time_series tool finished successfully.")
        return output_model.model_dump(exclude_none=True)
    except WaterChemistryError as exc:
        public_payload = exc.to_public_dict()
        logger.error(f"Kinetic time-series tool failed: {public_payload.get('message')}")
        return public_payload
    except Exception as exc:
        logger.exception("Unexpected error in simulate_kinetic_reaction_time_series")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "simulate_kinetic_reaction_time_series", "stage": "execute"},
        )


def _validate_request(input_data: Dict[str, Any]) -> KineticReactionRequest | Dict[str, Any]:
    try:
        return KineticReactionRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_kinetic_reaction", "stage": "validate_input"},
        )


async def _run_kinetic_request(request: KineticReactionRequest):
    database_path = resolve_database(request.database, category="general")
    kinetic_def = request.kinetic_reactions.model_dump(exclude_none=True)
    time_def = request.time_steps.model_dump(exclude_none=True, by_alias=False)
    rates_str, kinetics_str = build_kinetics_block(kinetic_def, time_def, block_num=1)

    phreeqc_input = build_solution_block_from_canonical(request.initial_canonical())
    if rates_str:
        phreeqc_input += "\n" + rates_str
    phreeqc_input += "\n" + kinetics_str

    if request.allow_precipitation and request.equilibrium_minerals:
        mineral_mapping = database_manager.get_compatible_minerals(database_path, request.equilibrium_minerals)
        equilibrium_minerals = [mineral for mineral in mineral_mapping.values() if mineral]
        if equilibrium_minerals:
            phases = [{"name": name} for name in equilibrium_minerals]
            phreeqc_input += "\n" + build_equilibrium_phases_block(phases, block_num=1, allow_empty=True)
            phreeqc_input += "USE equilibrium_phases 1\n"

    num_steps = _num_time_steps(time_def)
    if num_steps <= 1:
        # one step is read from the stored solutions, as any one-step run is: the final state is SAVEd above the
        
        # water). A multi-step run is read from its selected output, one row per step, and saves nothing.
        phreeqc_input += "SAVE solution 2\n"
    phreeqc_input += build_selected_output_block(
        block_num=1,
        saturation_indices=True,
        phases=True,
        molalities=True,
        totals=True,
        kinetics=True,
    )
    phreeqc_input += "END\n"

    run_result = await run_phreeqc_job(
        PhreeqcJob(
            input_string=phreeqc_input,
            tool_name="simulate_kinetic_reaction",
            job_name="kinetic_reaction",
            database_path=database_path,
            database_category="general",
            num_steps=num_steps,
        )
    )
    return run_result, _time_values(time_def), time_def.get("units", time_def.get("duration_units", "seconds"))


def _num_time_steps(time_def: Dict[str, Any]) -> int:
    values = time_def.get("time_values")
    if isinstance(values, list) and len(values) > 1:
        return len(values)
    count = time_def.get("count")
    if count:
        return int(count)
    return 1


def _time_values(time_def: Dict[str, Any]) -> List[float]:
    values = time_def.get("time_values")
    if isinstance(values, list):
        return values
    count = time_def.get("count")
    duration = time_def.get("duration")
    if count and duration:
        return [idx * duration / count for idx in range(count + 1)]
    return []
