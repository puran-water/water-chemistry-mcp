"""Unified PHREEQC runner boundary."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Union

from pydantic import Field, field_validator

from .answered_by import collecting_runs
from .contracts.common import StrictFiniteBaseModel, redact_paths
from .engine import resolve_database, select_engine
from .errors import PhreeqcConvergenceError, PhreeqcExecutionError, WaterChemistryError
from .parser import PhreeqcPayload, validate_phreeqc_payload


class PhreeqcJob(StrictFiniteBaseModel):
    """One PHREEQC call request."""

    input_string: str
    tool_name: str
    job_name: str
    database: Optional[str] = None
    database_path: Optional[str] = None
    database_category: str = "general"
    num_steps: int = 1
    timeout_seconds: int = 60
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PhreeqcRunResult(StrictFiniteBaseModel):
    """Validated PHREEQC call result."""

    data: PhreeqcPayload
    engine: str
    database: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("database", mode="before")
    @classmethod
    def redact_database(cls, value: Any) -> Any:
        return redact_paths(value) if isinstance(value, str) else value


async def run_phreeqc_job(job: PhreeqcJob) -> PhreeqcRunResult:
    """Resolve, execute, parse, and validate one PHREEQC job."""

    database_path = job.database_path or resolve_database(job.database, category=job.database_category)
    engine_selection = select_engine(database_path)

    try:
        from water_chemistry_mcp.tools.phreeqc.simulation import run_phreeqc_simulation

        with collecting_runs() as runs:
            result = await run_phreeqc_simulation(
                job.input_string,
                database_path=database_path,
                num_steps=job.num_steps,
            )
    except WaterChemistryError:
        raise
    except Exception as exc:
        if _is_convergence_failure(exc):
            raise PhreeqcConvergenceError(
                f"PHREEQC convergence failure in run_phreeqc_simulation: {type(exc).__name__}: {exc}",
                call_stage="execute",
                tool_name=job.tool_name,
                job_name=job.job_name,
                details={
                    "database": database_path,
                    "engine": engine_selection.engine,
                    "call_boundary": 'water_chemistry_mcp.tools.phreeqc.simulation.run_phreeqc_simulation',
                },
                cause=exc,
            ) from exc
        raise PhreeqcExecutionError(
            f"PHREEQC execution failed in run_phreeqc_simulation: {type(exc).__name__}: {exc}",
            call_stage="execute",
            tool_name=job.tool_name,
            job_name=job.job_name,
            details={
                "database": database_path,
                "engine": engine_selection.engine,
                "call_boundary": 'water_chemistry_mcp.tools.phreeqc.simulation.run_phreeqc_simulation',
            },
            cause=exc,
        ) from exc

    payload = validate_phreeqc_payload(
        _normalize_backend_aliases(_single_step_result(result, job.num_steps)),
        call_stage="parse",
        tool_name=job.tool_name,
        job_name=job.job_name,
    )
    diagnostics = dict(engine_selection.diagnostics)
    
    engine = runs[-1]["engine"] if runs else engine_selection.engine
    first = payload[0] if isinstance(payload, list) and payload else payload
    if isinstance(first, dict) and first.get("input_basis_conversions"):
        diagnostics["carbonate_species_conversion"] = first["input_basis_conversions"]
    # Databases disagree on the input formula a mg/L value is read against, so a
    # caller cannot know what their numbers meant without being told. Report it.
    try:
        from water_chemistry_mcp.utils.phreeqc_database_introspection import element_input_basis

        analytes = _solution_analytes(job.input_string)
        if analytes:
            diagnostics["element_input_basis"] = element_input_basis(
                database_path, analytes, stated_bases=_solution_stated_bases(job.input_string)
            )
    except Exception as exc:  # never fail a solved run over its own provenance
        diagnostics["element_input_basis_error"] = f"{type(exc).__name__}: {exc}"

    return PhreeqcRunResult(
        data=payload,
        engine=engine,
        database=database_path,
        diagnostics=diagnostics,
    )


_SOLUTION_BLOCK = re.compile(r"^SOLUTION[_A-Z]*\s", re.IGNORECASE)


def _solution_analytes(input_string: str) -> list[str]:
    """Analyte names supplied in the SOLUTION blocks of a PHREEQC input."""
    names: list[str] = []
    in_solution = False
    for raw in input_string.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line[:1].isspace():
            in_solution = bool(_SOLUTION_BLOCK.match(line)) or line.upper().startswith("SOLUTION")
            continue
        if not in_solution:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1].isalpha():
            key = parts[0]
            if key.lower().lstrip("-") in {
                "units", "temp", "temperature", "ph", "pe", "density", "redox",
                "water", "pressure", "isotope", "charge", "unit", "title",
            }:
                continue
            if key not in names:
                names.append(key)
    return names


def _solution_stated_bases(input_string: str) -> dict[str, str]:
    """The formula each SOLUTION line states its mass as (``S(6)  96.06 as SO4`` -> SO4): PHREEQC reads such a line
    on that formula, not on the database's column 4."""
    stated: dict[str, str] = {}
    in_solution = False
    for raw in input_string.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line[:1].isspace():
            in_solution = bool(_SOLUTION_BLOCK.match(line)) or line.upper().startswith("SOLUTION")
            continue
        parts = line.split()
        if in_solution and len(parts) >= 4 and parts[2].lower() == "as" and parts[0][:1].isalpha():
            stated.setdefault(parts[0], parts[3])
    return stated


def _single_step_result(result: Union[PhreeqcPayload, Dict[str, Any]], num_steps: int) -> PhreeqcPayload:
    if num_steps == 1 and isinstance(result, list) and result:
        return result[0]
    return result


def _normalize_backend_aliases(payload: PhreeqcPayload) -> PhreeqcPayload:
    """Normalize backend-specific field names at the canonical runner boundary."""

    if isinstance(payload, list):
        return [_normalize_backend_aliases(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    if "species_molality" not in normalized and "species_molalities" in normalized:
        normalized["species_molality"] = dict(normalized["species_molalities"] or {})
    return normalized


def _is_convergence_failure(exc: BaseException) -> bool:
    context = getattr(exc, "context", {}) or {}
    error_type = str(context.get("error_type", "")).lower()
    text = str(exc).lower()
    return (
        "convergence" in error_type
        or "not converged" in text
        or "failed to converge" in text
        or "model failed to converge" in text
    )
