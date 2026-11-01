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
from typing import Union, List, Any, Dict, Optional
from water_chemistry_mcp.utils.exceptions import PhreeqcError
from .parser import _parse_selected_output
logger = logging.getLogger(__name__)

@lru_cache(maxsize=128)
def _get_phreeqc_rates_path() -> Optional[str]:
    """
    Find the phreeqc_rates.dat file with caching.

    Returns:
        Path to phreeqc_rates.dat or None if not found
    """
    env_db = os.environ.get('PHREEQC_DATABASE')
    if env_db:
        rates_path = os.path.join(env_db, 'phreeqc_rates.dat')
        if os.path.exists(rates_path):
            return rates_path
    repo_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'databases', 'official', 'phreeqc_rates.dat')
    potential_paths = [os.path.abspath(repo_local)]
    for path in potential_paths:
        if os.path.exists(path):
            logger.debug(f'Found phreeqc_rates.dat at: {path}')
            return path
    return None

@lru_cache(maxsize=1)
def _get_phreeqc_executable() -> Optional[str]:
    'The standalone PHREEQC executable DECLARED for this deployment, or None.'
    named = os.environ.get('PHREEQC_EXECUTABLE')
    if named:
        if os.path.isfile(named) and os.access(named, os.X_OK):
            logger.info(f'Using PHREEQC executable from PHREEQC_EXECUTABLE: {named}')
            return named
        logger.warning('PHREEQC_EXECUTABLE names %s, which is not an executable file; no other binary runs', named)
        return None
    which_result = shutil.which('phreeqc')
    if which_result:
        logger.info(f'Found PHREEQC executable on PATH: {which_result}')
        return which_result
    logger.warning('No PHREEQC executable: PHREEQC_EXECUTABLE is not set and phreeqc is not on PATH')
    return None

def undeclared_engine_message(database_name: Optional[str]=None) -> str:
    """The refusal when the declared engine is the standalone PHREEQC and no executable is declared."""
    from water_chemistry_mcp.waterchem.input_key_rulings import ENGINE_DECLARATION_RULING_ID
    named = os.environ.get('PHREEQC_EXECUTABLE')
    where = f'PHREEQC_EXECUTABLE names {named}, which is not an executable file' if named else 'PHREEQC_EXECUTABLE is not set and no phreeqc is on PATH'
    on = f' on {database_name}' if database_name else ''
    return f"The declared PHREEQC engine is the standalone PHREEQC (USE_PHREEQC_SUBPROCESS=1, the default) and {where}, so the run{on} is refused: phreeqpython's engine does not answer in its place. Set PHREEQC_EXECUTABLE to the binary, put phreeqc on the service's PATH, or declare USE_PHREEQC_SUBPROCESS=0 in the deployment's env file to run phreeqpython's engine (E1 of {ENGINE_DECLARATION_RULING_ID})."

def _is_running_in_wsl() -> bool:
    """Check if we're running inside WSL."""
    try:
        with open('/proc/version', 'r') as f:
            return 'microsoft' in f.read().lower() or 'wsl' in f.read().lower()
    except (FileNotFoundError, IOError):
        return False

def _convert_wsl_path_to_windows(path: str) -> str:
    """Convert WSL path to Windows path for passing to Windows executables."""
    if path.startswith('/mnt/'):
        drive_letter = path[5].upper()
        rest_of_path = path[7:].replace('/', '\\')
        return f'{drive_letter}:\\{rest_of_path}'
    return path

def _get_wsl_temp_dir() -> str:
    """Get a Windows-accessible temp directory for WSL subprocess execution."""
    wsl_temp = os.environ.get('PHREEQC_WSL_TEMP')
    if not wsl_temp:
        if os.path.exists('/mnt/c'):
            wsl_temp = os.path.join(tempfile.gettempdir(), 'phreeqc_temp')
        else:
            wsl_temp = os.path.join(tempfile.gettempdir(), 'phreeqc_temp')
    os.makedirs(wsl_temp, exist_ok=True)
    return wsl_temp

async def run_phreeqc_subprocess(input_string: str, database_path: str, timeout: int=60, num_steps: int=1) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
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
        raise PhreeqcError(undeclared_engine_message(os.path.basename(database_path) if database_path else None))
    in_wsl = _is_running_in_wsl()
    if in_wsl:
        temp_base = _get_wsl_temp_dir()
        temp_dir = os.path.join(temp_base, f'phreeqc_{uuid.uuid4().hex[:8]}')
        os.makedirs(temp_dir, exist_ok=True)
        temp_context = None
    else:
        temp_context = tempfile.TemporaryDirectory(prefix='phreeqc_')
        temp_dir = temp_context.name
    try:
        input_file = os.path.join(temp_dir, 'input.pqi')
        output_file = os.path.join(temp_dir, 'output.pqo')
        selected_output_file = os.path.join(temp_dir, 'selected_output.txt')
        if in_wsl:
            win_selected_output = _convert_wsl_path_to_windows(selected_output_file)
        else:
            win_selected_output = selected_output_file
        modified_input = _inject_selected_output_file(input_string, win_selected_output)
        with open(input_file, 'w') as f:
            f.write(modified_input)
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
        logger.debug(f'Running PHREEQC command: {' '.join(cmd)}')
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else 'Unknown error'
                if os.path.exists(output_file):
                    with open(output_file, 'r') as f:
                        output_content = f.read()
                    if 'ERROR' in output_content:
                        error_lines = [l for l in output_content.split('\n') if 'ERROR' in l]
                        error_msg = '\n'.join(error_lines[:10])
                raise PhreeqcError(f'PHREEQC subprocess failed (exit code {process.returncode}): {error_msg}')
            results = _parse_phreeqc_output_files(output_file, selected_output_file)
            if num_steps > 1:
                results = _selected_output_steps(selected_output_file)
            first = results[0] if isinstance(results, list) and results else results
            if isinstance(first, dict):
                first['_phreeqc_warnings'] = _run_warnings(output_file, stdout, stderr)
                first['_selected_output_rows'] = _SelectedOutputRows(selected_output_file).get_selected_output_array() if os.path.exists(selected_output_file) else []
            return results
        except asyncio.TimeoutError:
            raise PhreeqcError(f'PHREEQC subprocess timed out after {timeout} seconds')
        except OSError as e:
            raise PhreeqcError(f'Failed to run PHREEQC subprocess: {e}')
    finally:
        if in_wsl:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        elif temp_context is not None:
            temp_context.cleanup()

def _run_warnings(output_file: str, stdout: bytes, stderr: bytes) -> str:
    """Every WARNING line the standalone run wrote, to its output file and to its screen (PHREEQC writes each to both
    unless the input limits them), once each, in order."""
    lines = []
    texts = [(stderr or b'').decode('utf-8', 'replace'), (stdout or b'').decode('utf-8', 'replace')]
    if os.path.exists(output_file):
        with open(output_file, 'r', errors='replace') as handle:
            texts.insert(0, handle.read())
    for text in texts:
        for line in text.splitlines():
            if 'WARNING' in line and line.strip() not in lines:
                lines.append(line.strip())
    return '\n'.join(lines)

def _inject_selected_output_file(input_string: str, output_file: str) -> str:
    """
    Inject -file directive into SELECTED_OUTPUT block to capture output.

    Args:
        input_string: Original PHREEQC input
        output_file: Path to write selected output (should already be in correct format)

    Returns:
        Modified PHREEQC input string
    """
    lines = input_string.split('\n')
    result_lines = []
    in_selected_output = False
    for line in lines:
        result_lines.append(line)
        if line.strip().upper().startswith('SELECTED_OUTPUT'):
            in_selected_output = True
            result_lines.append(f'    -file {output_file}')
        elif in_selected_output and line.strip() and (not line.strip().startswith('-')) and (not line.strip().startswith('#')):
            in_selected_output = False
    return '\n'.join(result_lines)

class _SelectedOutputRows:
    """The standalone run's SELECTED_OUTPUT file in the shape phreeqpython's ``ip.get_selected_output_array`` returns, so
    one parser reads the steps of either engine (`tools.phreeqc.parser._parse_selected_output_time_series`)."""

    def __init__(self, path: str):
        with open(path, 'r') as handle:
            lines = [line.rstrip('\n') for line in handle if line.strip()]
        self._rows = []
        for index, line in enumerate(lines):
            cells = [cell.strip() for cell in line.split('\t')]
            if index:
                cells = [_number_or_text(cell) for cell in cells]
            self._rows.append(cells)
        self.ip = self

    def get_selected_output_array(self):
        return self._rows

def _number_or_text(cell: str):
    try:
        return float(cell)
    except ValueError:
        return cell

def _selected_output_steps(selected_output_file: str) -> List[Dict[str, Any]]:
    from .parser import _parse_selected_output_time_series
    if not os.path.exists(selected_output_file):
        raise PhreeqcError('The multi-step run wrote no SELECTED_OUTPUT: its steps cannot be read (review M20)')
    steps = _parse_selected_output_time_series(_SelectedOutputRows(selected_output_file), 0)
    if not steps:
        raise PhreeqcError("The multi-step run's SELECTED_OUTPUT carries no step rows (review M20)")
    return steps

def _parse_phreeqc_output_files(output_file: str, selected_output_file: str) -> Dict[str, Any]:
    """
    Parse PHREEQC output files and extract results.

    Args:
        output_file: Path to main PHREEQC output file (.pqo)
        selected_output_file: Path to selected output file

    Returns:
        Dictionary with parsed results
    """
    results = {'solution_summary': {}, 'saturation_indices': {}, 'element_totals_molality': {}, 'species_molalities': {}}
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
    results = {'solution_summary': {}}
    summary = results['solution_summary']
    try:
        with open(output_file, 'r') as f:
            content = f.read()
        ph_match = re.search('pH\\s*=\\s*([\\d.]+)', content)
        if ph_match:
            summary['pH'] = float(ph_match.group(1))
        pe_match = re.search('pe\\s*=\\s*([\\d.+-eE]+)', content)
        if pe_match:
            summary['pe'] = float(pe_match.group(1))
        temp_match = re.search('Temperature.*?=\\s*([\\d.]+)', content)
        if temp_match:
            summary['temperature_celsius'] = float(temp_match.group(1))
        mu_match = re.search('Ionic strength.*?=\\s*([\\d.eE+-]+)', content)
        if mu_match:
            summary['ionic_strength_molal'] = float(mu_match.group(1))
        sc_match = re.search('Specific Conductance.*?=\\s*([\\d.]+)', content)
        if sc_match:
            summary['specific_conductance_uS_cm'] = float(sc_match.group(1))
        error_pattern = re.compile('^\\s*(?:\\*\\*\\s*)?ERRORS?(?:\\s|:)', re.IGNORECASE)
        error_lines = [line for line in content.splitlines() if error_pattern.match(line)]
        if error_lines:
            results['errors'] = error_lines[:5]
    except Exception as e:
        logger.warning(f'Error parsing main PHREEQC output: {e}')
    return results
USE_SUBPROCESS = os.environ.get('USE_PHREEQC_SUBPROCESS', '1').lower() in ('1', 'true', 'yes')

def get_engine_status() -> Dict[str, Any]:
    """
    Get the status of the PHREEQC simulation engine and database availability.

    Returns:
        Dictionary with engine status information
    """
    from water_chemistry_mcp.waterchem.engine import get_engine_status as get_refactored_engine_status
    return get_refactored_engine_status()
