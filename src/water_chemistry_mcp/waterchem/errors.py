"""Typed public error taxonomy for water chemistry execution."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Dict, Optional

from puran_mcp_support.engineering.safe_paths import redact_mapping, redact_paths


ERROR_CODE_MAP = {
    "input_contract_error": "INVALID_INPUT",
    "physical_validation_error": "INVALID_INPUT",
    "unit_conversion_error": "INVALID_INPUT",
    "database_resolution_error": "DATABASE_RESOLUTION_ERROR",
    "database_loadability_error": "DATABASE_RESOLUTION_ERROR",
    "phreeqc_execution_error": "PHREEQC_EXECUTION_ERROR",
    "phreeqc_convergence_error": "PHREEQC_CONVERGENCE_ERROR",
    "phreeqc_parse_error": "PHREEQC_EXECUTION_ERROR",
    "non_finite_result_error": "PHREEQC_EXECUTION_ERROR",
    "infeasible_target_error": "INFEASIBLE_TARGET",
    "optimization_failure_error": "OPTIMIZATION_FAILURE",
    "nested_tool_failure_error": "NESTED_TOOL_FAILURE",
    "unsupported_chemistry_error": "INVALID_INPUT",
}

EXCEPTION_CODE_MAP = {
    "InputValidationError": "INVALID_INPUT",
    "ValidationError": "INVALID_INPUT",
    "ValueError": "INVALID_INPUT",
    "DatabaseNotFoundError": "DATABASE_RESOLUTION_ERROR",
    "DatabaseLoadError": "DATABASE_RESOLUTION_ERROR",
    "DatabaseQueryError": "DATABASE_QUERY_ERROR",
    "TermNotFoundError": "DATABASE_QUERY_ERROR",
    "AmbiguousQueryError": "DATABASE_QUERY_ERROR",
    "PhreeqcSimulationError": "PHREEQC_EXECUTION_ERROR",
    "PhreeqcError": "PHREEQC_EXECUTION_ERROR",
    "ConvergenceError": "PHREEQC_CONVERGENCE_ERROR",
    "DosingConvergenceError": "PHREEQC_CONVERGENCE_ERROR",
    "OptimizationConvergenceError": "PHREEQC_CONVERGENCE_ERROR",
}


def stable_error_code(error_type: str) -> str:
    """Return the stable public error code for an internal error type."""

    normalized = str(error_type or "unexpected_error").strip()
    return ERROR_CODE_MAP.get(normalized, normalized.upper())


def _details_dict(details: Optional[Mapping[str, Any] | Any]) -> Dict[str, Any]:
    if details is None:
        return {}
    if isinstance(details, Mapping):
        return dict(details)
    return {"value": details}


def public_error(code: str, message: Any, details: Optional[Mapping[str, Any] | Any] = None) -> Dict[str, Any]:
    """Build the canonical public error envelope."""

    payload = {
        "code": str(code),
        "message": str(message),
        "details": _details_dict(details),
        "correlation_id": uuid.uuid4().hex,
    }
    return redact_paths(payload)


def public_error_from_exception(
    exc: BaseException,
    *,
    code: Optional[str] = None,
    message: Optional[str] = None,
    details: Optional[Mapping[str, Any] | Any] = None,
) -> Dict[str, Any]:
    """Build a canonical public error envelope from an arbitrary exception."""

    if isinstance(exc, WaterChemistryError) and code is None and message is None and details is None:
        return exc.to_public_dict()

    exception_details = _details_dict(details)
    exception_details.setdefault("exception_type", type(exc).__name__)
    for attr in ("database_path", "available_databases", "query_type", "query_term", "term", "parameter"):
        if hasattr(exc, attr):
            exception_details[attr] = getattr(exc, attr)

    resolved_code = code or EXCEPTION_CODE_MAP.get(type(exc).__name__, "UNEXPECTED_ERROR")
    resolved_message = message if message is not None else str(exc)
    return public_error(resolved_code, resolved_message, exception_details)


def is_error_payload(payload: Any) -> bool:
    """Return True for canonical public errors and legacy error payloads."""

    return isinstance(payload, Mapping) and bool(payload.get("code") or payload.get("error"))


def error_payload_message(payload: Mapping[str, Any]) -> str:
    """Extract a public message from canonical or legacy error payloads."""

    return str(payload.get("message") or payload.get("error") or "")


def error_payload_code(payload: Mapping[str, Any]) -> Optional[str]:
    """Extract a public code from canonical or legacy error payloads."""

    value = payload.get("code") or payload.get("error_type")
    return str(value) if value is not None else None


class WaterChemistryError(Exception):
    """Base class for typed errors exposed by the refactored tool surface."""

    error_type = "water_chemistry_error"

    def __init__(
        self,
        message: str,
        *,
        call_stage: Optional[str] = None,
        tool_name: Optional[str] = None,
        job_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.message = redact_paths(str(message))
        self.call_stage = call_stage
        self.tool_name = tool_name
        self.job_name = job_name
        self.details = redact_mapping(details or {})
        self.cause = cause
        super().__init__(self.message)

    def to_public_dict(self) -> Dict[str, Any]:
        details = dict(self.details)
        if self.call_stage:
            details["call_stage"] = self.call_stage
        if self.tool_name:
            details["tool_name"] = self.tool_name
        if self.job_name:
            details["job_name"] = self.job_name
        if self.cause is not None:
            details.setdefault("cause", self.cause)
        return public_error(stable_error_code(self.error_type), self.message, details)


class InputContractError(WaterChemistryError):
    error_type = "input_contract_error"


class PhysicalValidationError(WaterChemistryError):
    error_type = "physical_validation_error"


class UnitConversionError(WaterChemistryError):
    error_type = "unit_conversion_error"


class DatabaseResolutionError(WaterChemistryError):
    error_type = "database_resolution_error"


class DatabaseLoadabilityError(WaterChemistryError):
    error_type = "database_loadability_error"


class PhreeqcExecutionError(WaterChemistryError):
    error_type = "phreeqc_execution_error"


class PhreeqcConvergenceError(WaterChemistryError):
    error_type = "phreeqc_convergence_error"


class PurePhaseNotConvergedError(PhreeqcConvergenceError):
    'Public engineering calculation interface.'


class PhreeqcParseError(WaterChemistryError):
    error_type = "phreeqc_parse_error"


class NonFiniteResultError(WaterChemistryError):
    error_type = "non_finite_result_error"


class InfeasibleTargetError(WaterChemistryError):
    error_type = "infeasible_target_error"


class OptimizationFailureError(WaterChemistryError):
    error_type = "optimization_failure_error"


class NestedToolFailureError(WaterChemistryError):
    error_type = "nested_tool_failure_error"


class UnsupportedChemistryError(WaterChemistryError):
    error_type = "unsupported_chemistry_error"
