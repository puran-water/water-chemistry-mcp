"""
PHREEQC simulation engine package.

Public API re-exports from submodules:
  backend    – Subprocess execution, executable discovery, engine status
  parser     – Output parsing, element normalization
  simulation – Simulation orchestration (phreeqpython + subprocess)
  optimizer  – Dosing search / binary-secant optimization
"""

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
from .optimizer import (
    evaluate_target_parameter,
    OptimizationObjective,
    find_reactant_dose_for_target,
)
