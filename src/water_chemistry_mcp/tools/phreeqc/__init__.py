'PHREEQC simulation engine package.\n\nPublic API re-exports from submodules:\n  backend    – Subprocess execution, executable discovery, engine status\n  parser     – Output parsing, element normalization\n  simulation – Simulation orchestration (phreeqpython + subprocess)'

from .backend import get_engine_status, run_phreeqc_subprocess, USE_SUBPROCESS
from .parser import (
    _normalize_element_name,
    _is_element_total_column,
    _parse_selected_output,
    parse_phreeqc_results,
)
from .simulation import (
    run_phreeqc_simulation,
    run_phreeqc_with_phreeqpython,
    calculate_kinetic_precipitation,
    calculate_kinetic_precipitation_phreeqc_native,
    extract_minerals_from_input,
    get_mineral_alternatives,
    get_truncated_input,
)
