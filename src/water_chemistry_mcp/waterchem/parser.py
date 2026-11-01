"""Structured PHREEQC result parsing helpers with finite-value checks."""

from __future__ import annotations

from typing import Any, Dict, List, Union

from .contracts.common import assert_finite_payload
from .errors import NonFiniteResultError, PhreeqcParseError

PhreeqcPayload = Union[Dict[str, Any], List[Dict[str, Any]]]


def validate_phreeqc_payload(
    payload: PhreeqcPayload,
    *,
    call_stage: str = "parse",
    tool_name: str | None = None,
    job_name: str | None = None,
) -> PhreeqcPayload:
    """Validate parser output before it crosses the runner boundary."""

    error_payload = _find_error_payload(payload)
    if error_payload:
        raise PhreeqcParseError(
            str(error_payload.get("error", "PHREEQC result parser returned an error")),
            call_stage=call_stage,
            tool_name=tool_name,
            job_name=job_name,
            details=error_payload,
        )

    try:
        assert_finite_payload(payload, "phreeqc_result")
    except ValueError as exc:
        raise NonFiniteResultError(
            str(exc),
            call_stage=call_stage,
            tool_name=tool_name,
            job_name=job_name,
            details={"result_path": "phreeqc_result"},
            cause=exc,
        ) from exc

    return payload


def parse_phreeqpython_result(
    pp_instance: Any,
    *,
    num_steps: int = 1,
    tool_name: str | None = None,
    job_name: str | None = None,
) -> PhreeqcPayload:
    """Parse a completed PhreeqPython instance, then validate."""

    try:
        from water_chemistry_mcp.tools.phreeqc.parser import parse_phreeqc_results

        payload = parse_phreeqc_results(pp_instance, num_steps=num_steps)
        for item in payload if isinstance(payload, list) else [payload]:
            if isinstance(item, dict):
                item.pop("_selected_output_row", None)   # the batch-redox check's raw step row, never an answer
    except Exception as exc:
        raise PhreeqcParseError(
            f"PHREEQC result parser failed: {type(exc).__name__}: {exc}",
            call_stage="parse",
            tool_name=tool_name,
            job_name=job_name,
            cause=exc,
        ) from exc

    return validate_phreeqc_payload(payload, tool_name=tool_name, job_name=job_name)


def _find_error_payload(payload: Any) -> Dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("error"):
            return payload
        for value in payload.values():
            found = _find_error_payload(value)
            if found:
                return found
        return None
    if isinstance(payload, list):
        for item in payload:
            found = _find_error_payload(item)
            if found:
                return found
    return None
