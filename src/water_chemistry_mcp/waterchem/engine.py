"""PHREEQC engine discovery, database resolution, and loadability probes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from pydantic import Field, field_validator

from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .contracts.common import StrictFiniteBaseModel, redact_paths
from .errors import DatabaseLoadabilityError, DatabaseResolutionError, PhreeqcExecutionError

COMMON_DATABASES = ("minteq.v4.dat", "minteq.dat", "phreeqc.dat", "wateq4f.dat")
SMOKE_TEST_INPUT = """SOLUTION 1
    temp 25
    pH 7 charge
END
"""


class DatabaseProbeResult(StrictFiniteBaseModel):
    """Safe-to-expose result from an engine/database smoke test."""

    database_name: str
    database_path: Optional[str] = None
    engine: str = "phreeqpython"
    exists: bool = False
    loadable: bool = False
    call_stage: str = "load_database"
    error: Optional[str] = None

    @field_validator("database_path", "error", mode="before")
    @classmethod
    def redact_public_text(cls, value: Any) -> Any:
        return redact_paths(value) if isinstance(value, str) else value


class EngineSelection(StrictFiniteBaseModel):
    """Selected engine for one PHREEQC job."""

    engine: str
    database_path: Optional[str] = None
    database_name: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("database_path", mode="before")
    @classmethod
    def redact_database_path(cls, value: Any) -> Any:
        return redact_paths(value) if isinstance(value, str) else value


def subprocess_enabled() -> bool:
    """Return whether subprocess mode is enabled by configuration."""

    return os.environ.get("USE_PHREEQC_SUBPROCESS", "1").lower() in ("1", "true", "yes")


def phreeqc_executable_path() -> Optional[str]:
    """Discover the standalone PHREEQC executable."""

    try:
        from water_chemistry_mcp.tools.phreeqc.backend import _get_phreeqc_executable

        return _get_phreeqc_executable()
    except Exception:
        return None


def resolve_database(database: Optional[str], *, category: str = "general") -> str:
    """Resolve and validate a PHREEQC database path for a job."""

    try:
        resolved = database_manager.resolve_and_validate_database(database, category=category)
    except Exception as exc:
        raise DatabaseResolutionError(
            f"Failed to resolve PHREEQC database: {type(exc).__name__}: {exc}",
            call_stage="resolve_database",
            details={"database": database, "category": category},
            cause=exc,
        ) from exc

    if not resolved or not os.path.exists(resolved):
        raise DatabaseResolutionError(
            "No valid PHREEQC database could be resolved",
            call_stage="resolve_database",
            details={"database": database, "category": category, "resolved": resolved},
        )

    return resolved


def get_probe_database_paths(database_names: Iterable[str] = COMMON_DATABASES) -> Dict[str, Optional[str]]:
    """Return paths for databases that should appear in engine status."""

    paths: Dict[str, Optional[str]] = {}
    for db_name in database_names:
        try:
            paths[db_name] = database_manager.get_database_path(db_name)
        except Exception:
            paths[db_name] = None
    return paths


def create_phreeqpython_instance(database_path: Optional[str] = None) -> Any:
    """Create a PhreeqPython instance for a resolved database path."""

    if not PHREEQPYTHON_AVAILABLE:
        raise DatabaseLoadabilityError(
            "phreeqpython package is not installed",
            call_stage="import_phreeqpython",
        )

    from phreeqpython import PhreeqPython

    if not database_path:
        # phreeqpython's own default is a dependency's bundled phreeqc.dat: never loaded unnamed
        raise DatabaseLoadabilityError(
            "No database named: phreeqpython's bundled default is never loaded in its place (PUBLIC-RULE-179c5d968ea4)",
            call_stage="load_database",
        )

    db_path = Path(database_path)
    if not db_path.exists():
        raise DatabaseLoadabilityError(
            "PHREEQC database file does not exist",
            call_stage="load_database",
            details={"database_path": str(db_path)},
        )

    # The named file, loaded once. A failure refuses with its reason: no unnamed instance (phreeqpython's default,
    # vitens.dat) is made to load it afterwards, a load VIPhreeqc reports by an error count rather than an exception
    
    try:
        return PhreeqPython(database=db_path.name, database_directory=db_path.parent)
    except Exception as exc:
        raise DatabaseLoadabilityError(
            f'PHREEQC database {db_path.name} could not be loaded: {type(exc).__name__}: {exc}; nothing loads in its place (PUBLIC-RULE-762de2e5f9c5)',
            call_stage="load_database",
            details={"database_path": str(db_path)},
        ) from exc


def probe_phreeqpython_database(database_path: Optional[str], database_name: Optional[str] = None) -> DatabaseProbeResult:
    """Actually load a database and run a tiny PHREEQC smoke test."""

    db_name = database_name or (os.path.basename(database_path) if database_path else "default")
    exists = bool(database_path and os.path.exists(database_path))

    if not PHREEQPYTHON_AVAILABLE:
        return DatabaseProbeResult(
            database_name=db_name,
            database_path=database_path,
            exists=exists,
            loadable=False,
            call_stage="import_phreeqpython",
            error="phreeqpython package is not installed",
        )

    if not database_path or not exists:
        return DatabaseProbeResult(
            database_name=db_name,
            database_path=database_path,
            exists=False,
            loadable=False,
            call_stage="load_database",
            error="PHREEQC database file does not exist",
        )

    call_stage = "load_database"
    try:
        pp = create_phreeqpython_instance(database_path)
        call_stage = "run_smoke_test"
        pp.ip.run_string(SMOKE_TEST_INPUT)
        error_text = pp.ip.get_error_string() if hasattr(pp.ip, "get_error_string") else ""
        if error_text and "ERROR" in error_text.upper():
            return DatabaseProbeResult(
                database_name=db_name,
                database_path=database_path,
                exists=exists,
                loadable=False,
                call_stage=call_stage,
                error=f"PHREEQC smoke test reported errors: {error_text}",
            )
        return DatabaseProbeResult(
            database_name=db_name,
            database_path=database_path,
            exists=exists,
            loadable=True,
            call_stage=call_stage,
        )
    except OSError as exc:
        return DatabaseProbeResult(
            database_name=db_name,
            database_path=database_path,
            exists=exists,
            loadable=False,
            call_stage=call_stage,
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        return DatabaseProbeResult(
            database_name=db_name,
            database_path=database_path,
            exists=exists,
            loadable=False,
            call_stage=call_stage,
            error=f"{type(exc).__name__}: {exc}",
        )


def engine_declaration() -> Dict[str, Any]:
    'Public engineering calculation interface.'
    from water_chemistry_mcp.waterchem.input_key_rulings import ENGINE_DECLARATION_RULING_ID

    use_subprocess = subprocess_enabled()
    exe_path = phreeqc_executable_path() if use_subprocess else None
    return {
        "engine": "subprocess" if use_subprocess else "phreeqpython",
        "declared_by": f"USE_PHREEQC_SUBPROCESS={1 if use_subprocess else 0}",
        "declared_in_environment": os.environ.get("USE_PHREEQC_SUBPROCESS") is not None,
        "executable": redact_paths(exe_path) if exe_path else None,
        "ruling_id": ENGINE_DECLARATION_RULING_ID,
    }


def select_engine(database_path: Optional[str]) -> EngineSelection:
    "Under ``USE_PHREEQC_SUBPROCESS=1`` (the default) the standalone PHREEQC, and with no executable declared the job\n    refuses: phreeqpython had been selected whenever the machine had no binary. Under 0, phreeqpython's engine whatever\n    is installed, refused when it cannot load the database."

    if subprocess_enabled():
        exe_path = phreeqc_executable_path()
        if not exe_path:
            from water_chemistry_mcp.tools.phreeqc.backend import undeclared_engine_message

            raise PhreeqcExecutionError(
                undeclared_engine_message(os.path.basename(database_path) if database_path else None),
                call_stage="select_engine",
                details={"declared_by": "USE_PHREEQC_SUBPROCESS=1", "database": database_path},
            )
        return EngineSelection(
            engine="subprocess",
            database_path=database_path,
            database_name=os.path.basename(database_path) if database_path else None,
            diagnostics={"phreeqc_executable_path": redact_paths(exe_path), "declared_by": "USE_PHREEQC_SUBPROCESS=1"},
        )

    probe = probe_phreeqpython_database(database_path)
    if probe.loadable:
        return EngineSelection(
            engine="phreeqpython",
            database_path=database_path,
            database_name=probe.database_name,
            diagnostics={"database_probe": probe.model_dump(exclude_none=True), "declared_by": "USE_PHREEQC_SUBPROCESS=0"},
        )

    raise DatabaseLoadabilityError(
        "The declared engine, phreeqpython's (USE_PHREEQC_SUBPROCESS=0), cannot load the database",
        call_stage=probe.call_stage,
        details={
            "database": probe.database_name,
            "phreeqpython_available": PHREEQPYTHON_AVAILABLE,
            "declared_by": "USE_PHREEQC_SUBPROCESS=0",
            "probe_error": probe.error,
        },
    )


def get_engine_status() -> Dict[str, Any]:
    """Return PHREEQC engine status using real database loadability probes."""

    exe_path = phreeqc_executable_path()
    subprocess_available = bool(exe_path)
    probe_paths = get_probe_database_paths()
    probes = {name: probe_phreeqpython_database(path, database_name=name) for name, path in probe_paths.items()}
    phreeqpython_loadable = any(probe.loadable for probe in probes.values())

    # the DECLARED engine, or none: phreeqpython is never the active engine of a deployment that declares the standalone
    
    if subprocess_enabled():
        active_engine = "subprocess" if subprocess_available else "none"
    elif phreeqpython_loadable:
        active_engine = "phreeqpython"
    else:
        active_engine = "none"

    status: Dict[str, Any] = {
        "phreeqpython_available": PHREEQPYTHON_AVAILABLE,
        "phreeqpython_loadable": phreeqpython_loadable,
        "phreeqpython_load_error": _first_probe_error(probes) if not phreeqpython_loadable else None,
        "subprocess_mode_available": subprocess_available,
        "active_engine": active_engine,
        "phreeqc_version": _phreeqpython_version(),
        "database_loadability": {name: probe.loadable for name, probe in probes.items()},
        "database_probe_details": {name: probe.model_dump(exclude_none=True) for name, probe in probes.items()},
        "known_limitations": _known_limitations(active_engine, probes),
        "ready": active_engine != "none",
        "engine_declaration": engine_declaration(),
    }
    if exe_path:
        status["phreeqc_executable_path"] = redact_paths(exe_path)
    return status


def _phreeqpython_version() -> Optional[str]:
    if not PHREEQPYTHON_AVAILABLE:
        return None
    try:
        import phreeqpython

        return getattr(phreeqpython, "__version__", "unknown")
    except Exception:
        return None


def _first_probe_error(probes: Dict[str, DatabaseProbeResult]) -> Optional[str]:
    for probe in probes.values():
        if probe.error:
            return probe.error
    return None


def _known_limitations(active_engine: str, probes: Dict[str, DatabaseProbeResult]) -> list[str]:
    limitations = []
    if active_engine == "phreeqpython":
        limitations.append(
            "Using PhreeqPython (VIPhreeqc), declared by USE_PHREEQC_SUBPROCESS=0: some database features may not work "
            "on it; the standalone PHREEQC answers only where a deployment declares USE_PHREEQC_SUBPROCESS=1 and an "
            "executable (PHREEQC_EXECUTABLE or PATH)."
        )
    if active_engine == "none":
        limitations.append(
            "The declared PHREEQC engine cannot run: under USE_PHREEQC_SUBPROCESS=1 (the default) set PHREEQC_EXECUTABLE "
            "or put phreeqc on PATH, or declare USE_PHREEQC_SUBPROCESS=0 (phreeqpython's engine, which must load)."
        )
    if probes.get("minteq.v4.dat") and probes["minteq.v4.dat"].exists:
        limitations.append(
            "Al-P modeling: Standard databases lack AlPO4/Variscite phases and HAO surface data. "
            "Al coagulants require custom database extensions for accurate P removal modeling."
        )
        limitations.append(
            "Struvite (MgNH4PO4·6H2O): Not available in standard PHREEQC databases. "
            "Requires custom PHASES block for Mg-based P recovery modeling."
        )
    return limitations
