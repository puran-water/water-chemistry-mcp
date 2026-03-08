"""
PHREEQC execution infrastructure.

Handles subprocess execution, executable discovery, WSL path conversion,
output file parsing, and engine status reporting.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid
from functools import lru_cache
from typing import Any, Dict, Optional

from utils.exceptions import PhreeqcError
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .parser import _parse_selected_output

logger = logging.getLogger(__name__)


# Cache for database paths
@lru_cache(maxsize=128)
def _get_phreeqc_rates_path() -> Optional[str]:
    """
    Find the phreeqc_rates.dat file with caching.

    Returns:
        Path to phreeqc_rates.dat or None if not found
    """
    # Check environment variable first
    env_db = os.environ.get("PHREEQC_DATABASE")
    if env_db:
        rates_path = os.path.join(env_db, "phreeqc_rates.dat")
        if os.path.exists(rates_path):
            return rates_path

    # Repo-local database
    repo_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "databases", "official", "phreeqc_rates.dat")
    potential_paths = [os.path.abspath(repo_local)]

    # Check each path
    for path in potential_paths:
        if os.path.exists(path):
            logger.debug(f"Found phreeqc_rates.dat at: {path}")
            return path

    return None


# ============================================================================
# PHREEQC Subprocess Execution
# ============================================================================


@lru_cache(maxsize=1)
def _get_phreeqc_executable() -> Optional[str]:
    """
    Find a PHREEQC executable with caching.

    Returns:
        Path to phreeqc executable or None if not found
    """
    # Check environment variable first (highest priority)
    phreeqc_exe = os.environ.get("PHREEQC_EXECUTABLE")
    if phreeqc_exe and os.path.exists(phreeqc_exe):
        logger.info(f"Using PHREEQC executable from environment: {phreeqc_exe}")
        return phreeqc_exe

    # Check PATH
    which_result = shutil.which("phreeqc")
    if which_result:
        logger.info(f"Found PHREEQC executable on PATH: {which_result}")
        return which_result

    # Check standard Linux paths
    for path in ["/usr/local/bin/phreeqc", "/usr/bin/phreeqc"]:
        if os.path.exists(path):
            logger.info(f"Found PHREEQC executable at: {path}")
            return path

    # Glob-based discovery for Windows installs (accessible from WSL via /mnt/c)
    import glob

    win_patterns = [
        "/mnt/c/Program Files/USGS/phreeqc-*/bin/phreeqc.exe",
        "/mnt/c/Program Files (x86)/USGS/phreeqc-*/bin/phreeqc.exe",
        "C:\\Program Files\\USGS\\phreeqc-*\\bin\\phreeqc.exe",
    ]
    for pattern in win_patterns:
        matches = sorted(glob.glob(pattern), reverse=True)  # newest version first
        if matches:
            logger.info(f"Found PHREEQC executable via glob: {matches[0]}")
            return matches[0]

    logger.warning("PHREEQC executable not found")
    return None


def _is_running_in_wsl() -> bool:
    """Check if we're running inside WSL."""
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower() or "wsl" in f.read().lower()
    except (FileNotFoundError, IOError):
        return False


def _convert_wsl_path_to_windows(path: str) -> str:
    """Convert WSL path to Windows path for passing to Windows executables."""
    if path.startswith("/mnt/"):
        # /mnt/c/Users/... -> C:\Users\...
        drive_letter = path[5].upper()
        rest_of_path = path[7:].replace("/", "\\")
        return f"{drive_letter}:\\{rest_of_path}"
    return path


def _get_wsl_temp_dir() -> str:
    """Get a Windows-accessible temp directory for WSL subprocess execution."""
    wsl_temp = os.environ.get("PHREEQC_WSL_TEMP")
    if not wsl_temp:
        # Detect WSL and use a Windows-accessible path if available
        if os.path.exists("/mnt/c"):
            wsl_temp = os.path.join(tempfile.gettempdir(), "phreeqc_temp")
        else:
            wsl_temp = os.path.join(tempfile.gettempdir(), "phreeqc_temp")
    os.makedirs(wsl_temp, exist_ok=True)
    return wsl_temp


async def run_phreeqc_subprocess(
    input_string: str,
    database_path: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Run PHREEQC simulation using a standalone PHREEQC executable via subprocess.

    This provides full database compatibility for databases that are
    incompatible with the bundled VIPhreeqc in phreeqpython.

    Args:
        input_string: PHREEQC input script as a string
        database_path: Path to the PHREEQC database file
        timeout: Maximum time in seconds to wait for PHREEQC to complete

    Returns:
        Dictionary with simulation results

    Raises:
        PhreeqcError: If simulation fails or PHREEQC executable not found
    """
    phreeqc_exe = _get_phreeqc_executable()
    if not phreeqc_exe:
        raise PhreeqcError("PHREEQC executable not found. Set PHREEQC_EXECUTABLE environment variable.")

    # Determine if we're in WSL - affects how we handle paths
    in_wsl = _is_running_in_wsl()

    # Create temp directory for input/output files
    if in_wsl:
        temp_base = _get_wsl_temp_dir()
        temp_dir = os.path.join(temp_base, f"phreeqc_{uuid.uuid4().hex[:8]}")
        os.makedirs(temp_dir, exist_ok=True)
        temp_context = None
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="phreeqc_")
        temp_dir = temp_context.name

    try:
        # File paths (WSL paths)
        input_file = os.path.join(temp_dir, "input.pqi")
        output_file = os.path.join(temp_dir, "output.pqo")
        selected_output_file = os.path.join(temp_dir, "selected_output.txt")

        # Modify input string to set selected output file
        if in_wsl:
            win_selected_output = _convert_wsl_path_to_windows(selected_output_file)
        else:
            win_selected_output = selected_output_file
        modified_input = _inject_selected_output_file(input_string, win_selected_output)

        # Write input file
        with open(input_file, "w") as f:
            f.write(modified_input)

        # Build command - PHREEQC uses: phreeqc input_file output_file database_file
        if in_wsl:
            cmd_exe = phreeqc_exe
            cmd_input = _convert_wsl_path_to_windows(input_file)
            cmd_output = _convert_wsl_path_to_windows(output_file)
            cmd_db = _convert_wsl_path_to_windows(database_path)
        else:
            cmd_exe = phreeqc_exe
            cmd_input = input_file
            cmd_output = output_file
            cmd_db = database_path

        cmd = [cmd_exe, cmd_input, cmd_output, cmd_db]

        logger.debug(f"Running PHREEQC command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                if os.path.exists(output_file):
                    with open(output_file, "r") as f:
                        output_content = f.read()
                    if "ERROR" in output_content:
                        error_lines = [l for l in output_content.split("\n") if "ERROR" in l]
                        error_msg = "\n".join(error_lines[:10])
                raise PhreeqcError(f"PHREEQC subprocess failed (exit code {process.returncode}): {error_msg}")

            # Parse results from output file and selected output
            results = _parse_phreeqc_output_files(output_file, selected_output_file)

            return results

        except asyncio.TimeoutError:
            raise PhreeqcError(f"PHREEQC subprocess timed out after {timeout} seconds")
        except OSError as e:
            raise PhreeqcError(f"Failed to run PHREEQC subprocess: {e}")
    finally:
        if in_wsl:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        elif temp_context is not None:
            temp_context.cleanup()


def _inject_selected_output_file(input_string: str, output_file: str) -> str:
    """
    Inject -file directive into SELECTED_OUTPUT block to capture output.

    Args:
        input_string: Original PHREEQC input
        output_file: Path to write selected output (should already be in correct format)

    Returns:
        Modified PHREEQC input string
    """
    lines = input_string.split("\n")
    result_lines = []
    in_selected_output = False

    for line in lines:
        result_lines.append(line)
        if line.strip().upper().startswith("SELECTED_OUTPUT"):
            in_selected_output = True
            result_lines.append(f"    -file {output_file}")
        elif (
            in_selected_output
            and line.strip()
            and not line.strip().startswith("-")
            and not line.strip().startswith("#")
        ):
            in_selected_output = False

    return "\n".join(result_lines)


def _parse_phreeqc_output_files(
    output_file: str,
    selected_output_file: str,
) -> Dict[str, Any]:
    """
    Parse PHREEQC output files and extract results.

    Args:
        output_file: Path to main PHREEQC output file (.pqo)
        selected_output_file: Path to selected output file

    Returns:
        Dictionary with parsed results
    """
    results = {
        "solution_summary": {},
        "saturation_indices": {},
        "element_totals_molality": {},
        "species_molalities": {},
    }

    if os.path.exists(output_file):
        results.update(_parse_main_output(output_file))

    if os.path.exists(selected_output_file):
        selected_data = _parse_selected_output(selected_output_file)
        if selected_data:
            for key, value in selected_data.items():
                if key in results and isinstance(results[key], dict) and isinstance(value, dict):
                    results[key].update(value)
                else:
                    results[key] = value

    return results


def _parse_main_output(output_file: str) -> Dict[str, Any]:
    """
    Parse the main PHREEQC output file for solution properties.

    Args:
        output_file: Path to .pqo file

    Returns:
        Dictionary with solution summary and other properties
    """
    results = {"solution_summary": {}}
    summary = results["solution_summary"]

    try:
        with open(output_file, "r") as f:
            content = f.read()

        ph_match = re.search(r"pH\s*=\s*([\d.]+)", content)
        if ph_match:
            summary["pH"] = float(ph_match.group(1))

        pe_match = re.search(r"pe\s*=\s*([\d.+-eE]+)", content)
        if pe_match:
            summary["pe"] = float(pe_match.group(1))

        temp_match = re.search(r"Temperature.*?=\s*([\d.]+)", content)
        if temp_match:
            summary["temperature_celsius"] = float(temp_match.group(1))

        mu_match = re.search(r"Ionic strength.*?=\s*([\d.eE+-]+)", content)
        if mu_match:
            summary["ionic_strength_molal"] = float(mu_match.group(1))

        sc_match = re.search(r"Specific Conductance.*?=\s*([\d.]+)", content)
        if sc_match:
            summary["specific_conductance_uS_cm"] = float(sc_match.group(1))

        if "ERROR" in content.upper():
            error_lines = [l for l in content.split("\n") if "ERROR" in l.upper()]
            results["errors"] = error_lines[:5]

    except Exception as e:
        logger.warning(f"Error parsing main PHREEQC output: {e}")

    return results


# Flag to control which execution mode to use
# Set via environment variable: USE_PHREEQC_SUBPROCESS=1
USE_SUBPROCESS = os.environ.get("USE_PHREEQC_SUBPROCESS", "1").lower() in ("1", "true", "yes")


def get_engine_status() -> Dict[str, Any]:
    """
    Get the status of the PHREEQC simulation engine and database availability.

    Returns:
        Dictionary with engine status information
    """
    status = {
        "phreeqpython_available": PHREEQPYTHON_AVAILABLE,
        "subprocess_mode_available": False,
        "active_engine": "none",
        "phreeqc_version": None,
        "database_loadability": {},
        "known_limitations": [],
    }

    phreeqc_exe = _get_phreeqc_executable()
    if phreeqc_exe:
        status["subprocess_mode_available"] = True
        status["phreeqc_executable_path"] = phreeqc_exe

    if USE_SUBPROCESS and status["subprocess_mode_available"]:
        status["active_engine"] = "subprocess"
    elif PHREEQPYTHON_AVAILABLE:
        status["active_engine"] = "phreeqpython"

    if PHREEQPYTHON_AVAILABLE:
        try:
            import phreeqpython

            status["phreeqc_version"] = getattr(phreeqpython, "__version__", "unknown")
        except Exception:
            pass

    from utils.database_management import database_manager

    common_databases = ["minteq.v4.dat", "minteq.dat", "phreeqc.dat", "wateq4f.dat"]
    for db_name in common_databases:
        try:
            db_path = database_manager.get_database_path(db_name)
            status["database_loadability"][db_name] = db_path is not None and os.path.exists(db_path)
        except Exception:
            status["database_loadability"][db_name] = False

    limitations = []

    if not status["subprocess_mode_available"] and status["active_engine"] == "phreeqpython":
        limitations.append(
            "Using PhreeqPython (VIPhreeqc): Some database features may not work. "
            "Set PHREEQC_EXECUTABLE environment variable for full compatibility."
        )

    if status["active_engine"] == "none":
        limitations.append("No PHREEQC engine available. Install phreeqpython or set PHREEQC_EXECUTABLE.")

    if status["database_loadability"].get("minteq.v4.dat"):
        limitations.append(
            "Al-P modeling: Standard databases lack AlPO4/Variscite phases and HAO surface data. "
            "Al coagulants require custom database extensions for accurate P removal modeling."
        )

    if status["database_loadability"].get("minteq.v4.dat"):
        limitations.append(
            "Struvite (MgNH4PO4·6H2O): Not available in standard PHREEQC databases. "
            "Requires custom PHASES block for Mg-based P recovery modeling."
        )

    status["known_limitations"] = limitations
    status["ready"] = status["active_engine"] != "none"

    return status
