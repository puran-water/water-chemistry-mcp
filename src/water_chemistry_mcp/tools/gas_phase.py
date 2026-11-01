"""Tool for simulating gas phase interactions."""

from __future__ import annotations

import logging
from typing import Any, Dict

from water_chemistry_mcp.waterchem.phreeqc_input import build_gas_phase_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.gas import GasPhaseRequest, GasPhaseResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


@holds_batch_redox
async def simulate_gas_phase_interaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate gas-water equilibration through canonical PHREEQC execution."""

    logger.info("Running simulate_gas_phase_interaction tool...")

    try:
        input_model = GasPhaseRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_gas_phase_interaction", "stage": "validate_input"},
        )

    try:
        database_path = resolve_database(input_model.database, category="general")
        phreeqc_input = build_solution_block_from_canonical(input_model.initial_canonical())
        phreeqc_input += "\n" + build_gas_phase_block(input_model.gas_phase.model_dump(), block_num=1)
        phreeqc_input += "USE solution 1\n"
        phreeqc_input += "USE gas_phase 1\n"
        phreeqc_input += "SAVE solution 2\n"
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            gases=True,
        )
        phreeqc_input += "END\n"

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="simulate_gas_phase_interaction",
                job_name="gas_phase_interaction",
                database_path=database_path,
                database_category="general",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict):
            results.setdefault("gas_phase_equilibrated", True)
            results.setdefault("gas_components_input", input_model.gas_phase.initial_components)
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }

        output_model = GasPhaseResult.model_validate(results)
        logger.info("simulate_gas_phase_interaction tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as exc:
        public_payload = exc.to_public_dict()
        logger.error(f"Gas phase tool failed: {public_payload.get('message')}")
        return public_payload
    except Exception as exc:
        logger.exception("Unexpected error in simulate_gas_phase_interaction")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "simulate_gas_phase_interaction", "stage": "execute"},
        )
