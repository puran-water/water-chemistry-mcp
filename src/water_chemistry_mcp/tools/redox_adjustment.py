"""Tool for simulating redox adjustments."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from water_chemistry_mcp.waterchem.phreeqc_input import build_gas_phase_block, build_selected_output_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.redox import RedoxAdjustmentRequest, RedoxAdjustmentResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


def eh_to_pe(eh_mv: float, temp_c: float = 25.0) -> float:
    """Convert Eh in mV to pe."""

    gas_constant = 8.314
    faraday = 96485
    temp_k = temp_c + 273.15
    return (eh_mv / 1000.0) * faraday / (2.303 * gas_constant * temp_k)


def pe_to_eh(pe: float, temp_c: float = 25.0) -> float:
    """Convert pe to Eh in mV."""

    gas_constant = 8.314
    faraday = 96485
    temp_k = temp_c + 273.15
    return pe * 2.303 * gas_constant * temp_k / faraday * 1000.0


@holds_batch_redox
async def simulate_redox_adjustment(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate redox adjustment through canonical PHREEQC execution."""

    logger.info("Running simulate_redox_adjustment tool...")

    try:
        input_model = RedoxAdjustmentRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_redox_adjustment", "stage": "validate_input"},
        )

    try:
        database_path = resolve_database(input_model.database, category="general")
        target = input_model.target_redox
        parameter = target.parameter.lower()
        canonical = input_model.initial_canonical()
        temp_c = canonical.temperature_celsius

        if parameter == "pe":
            target_pe = target.value
            phreeqc_input = _pe_adjustment_input(canonical, target_pe)
            redox_info = {
                "parameter": "pe",
                "target_pe": target_pe,
                "target_Eh_mV": pe_to_eh(target_pe, temp_c),
            }
        elif parameter == "eh_mv":
            target_pe = eh_to_pe(target.value, temp_c)
            phreeqc_input = _pe_adjustment_input(canonical, target_pe)
            redox_info = {
                "parameter": "Eh_mV",
                "target_Eh_mV": target.value,
                "target_pe": target_pe,
            }
        else:
            phreeqc_input = _couple_adjustment_input(
                canonical,
                target.couple_name,
                target.couple_logK_or_pressure,
            )
            redox_info = {
                "parameter": "equilibrate_with_couple",
                "couple_name": target.couple_name,
                "couple_value": target.couple_logK_or_pressure,
            }

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="simulate_redox_adjustment",
                job_name="redox_adjustment",
                database_path=database_path,
                database_category="general",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict):
            achieved_pe = (results.get("solution_summary") or {}).get("pe")
            if achieved_pe is not None:
                redox_info["achieved_pe"] = achieved_pe
                redox_info["achieved_Eh_mV"] = pe_to_eh(achieved_pe, temp_c)
            results.setdefault("redox_adjustment", redox_info)
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }

        output_model = RedoxAdjustmentResult.model_validate(results)
        logger.info("simulate_redox_adjustment tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as exc:
        public_payload = exc.to_public_dict()
        logger.error(f"Redox adjustment tool failed: {public_payload.get('message')}")
        return public_payload
    except Exception as exc:
        logger.exception("Unexpected error in simulate_redox_adjustment")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "simulate_redox_adjustment", "stage": "execute"},
        )


def _pe_adjustment_input(canonical, target_pe: float) -> str:
    canonical.pe = target_pe
    phreeqc_input = build_solution_block_from_canonical(canonical)
    phreeqc_input += build_selected_output_block(
        block_num=1,
        saturation_indices=True,
        phases=True,
        molalities=True,
        totals=True,
    )
    phreeqc_input += "END\n"
    return phreeqc_input


def _couple_adjustment_input(canonical, couple_name: str, couple_value: Optional[float]) -> str:
    if "(g)" in couple_name.lower():
        gas_def = {
            "type": "fixed_pressure",
            "fixed_pressure_atm": 1.0,
            "initial_volume_liters": 1.0,
            "temperature_celsius": canonical.temperature_celsius,
            "initial_components": {couple_name: couple_value if couple_value is not None else 0.21},
        }
        phreeqc_input = build_solution_block_from_canonical(canonical)
        phreeqc_input += "\n" + build_gas_phase_block(gas_def, block_num=1)
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
        return phreeqc_input

    canonical.redox = couple_name
    phreeqc_input = build_solution_block_from_canonical(canonical)
    phreeqc_input += build_selected_output_block(
        block_num=1,
        saturation_indices=True,
        phases=True,
        molalities=True,
        totals=True,
    )
    phreeqc_input += "END\n"
    return phreeqc_input
