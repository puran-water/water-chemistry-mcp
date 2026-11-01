"""
Enhanced MCP Server for water chemistry calculations.

This server provides water chemistry calculation tools using PHREEQC including speciation,
chemical additions, dosing requirements, solution mixing, scaling potential analysis,
and various equilibrium simulations.

This is a modular version where each tool is in its own module for easier maintenance
and future development.

Author: Claude AI
"""
import logging
import logging.handlers
import os
import sys
from fastmcp import FastMCP
_stderr_handler = logging.StreamHandler(sys.stderr)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[_stderr_handler])
if os.environ.get('WATER_CHEMISTRY_DEBUG', '').lower() in ('1', 'true', 'yes'):
    logging.getLogger().setLevel(logging.DEBUG)
logger = logging.getLogger('water_chemistry_mcp')
mcp = FastMCP('water_chemistry_mcp')
from water_chemistry_mcp.waterchem.answered_by import names_its_database
from water_chemistry_mcp.tools.batch_processing import batch_process_scenarios
from water_chemistry_mcp.tools.chemical_addition import simulate_chemical_addition
from water_chemistry_mcp.tools.dosing_requirement import calculate_dosing_requirement
from water_chemistry_mcp.tools.gas_phase import simulate_gas_phase_interaction
from water_chemistry_mcp.tools.kinetic_reaction import simulate_kinetic_reaction
from water_chemistry_mcp.tools.reactor_sizing import size_precipitation_reactor
from water_chemistry_mcp.tools.optimization_tools import calculate_dosing_requirement_enhanced, calculate_lime_softening_dose, generate_lime_softening_curve, optimize_multi_reagent_treatment
from water_chemistry_mcp.tools.phosphorus_removal import screen_equilibrium_phosphorus_removal
from water_chemistry_mcp.tools.phreeqc import get_engine_status
from water_chemistry_mcp.tools.redox_adjustment import simulate_redox_adjustment
from water_chemistry_mcp.tools.scaling_potential import predict_scaling_potential
from water_chemistry_mcp.tools.solution_mixing import simulate_solution_mixing
from water_chemistry_mcp.tools.solution_speciation import calculate_solution_speciation
from water_chemistry_mcp.tools.surface_interaction import simulate_surface_interaction
from water_chemistry_mcp.tools.shared_kernel_validation import run_shared_kernel_validation_suite
from water_chemistry_mcp.tools.thermodynamic_audit import audit_process_chemistry_endpoint
from water_chemistry_mcp.tools.thermodynamic_database import query_thermodynamic_database
mcp.tool(annotations={'title': 'Calculate Solution Speciation', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(calculate_solution_speciation))
mcp.tool(annotations={'title': 'Simulate Chemical Addition', 'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': False})(names_its_database(simulate_chemical_addition))
mcp.tool(annotations={'title': 'Simulate Solution Mixing', 'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': False})(names_its_database(simulate_solution_mixing))
mcp.tool(annotations={'title': 'Predict Scaling Potential', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(predict_scaling_potential))
mcp.tool(annotations={'title': 'Batch Process Scenarios', 'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': False})(names_its_database(batch_process_scenarios))
mcp.tool(annotations={'title': 'Calculate Dosing Requirement', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(calculate_dosing_requirement))
mcp.tool(annotations={'title': 'Query Thermodynamic Database', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(query_thermodynamic_database))
mcp.tool(annotations={'title': 'Simulate Kinetic Reaction', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(simulate_kinetic_reaction))
mcp.tool(annotations={'title': 'Size Precipitation Reactor', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(size_precipitation_reactor))
mcp.tool(annotations={'title': 'Simulate Gas Phase Interaction', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(simulate_gas_phase_interaction))
mcp.tool(annotations={'title': 'Simulate Redox Adjustment', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(simulate_redox_adjustment))
mcp.tool(annotations={'title': 'Simulate Surface Interaction', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(simulate_surface_interaction))
mcp.tool(annotations={'title': 'Generate Lime Softening Curve', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(generate_lime_softening_curve))
mcp.tool(annotations={'title': 'Calculate Lime Softening Dose', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(calculate_lime_softening_dose))
mcp.tool(annotations={'title': 'Calculate Dosing Requirement Enhanced', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(calculate_dosing_requirement_enhanced))
mcp.tool(annotations={'title': 'Optimize Multi-Reagent Treatment', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(optimize_multi_reagent_treatment))
mcp.tool(annotations={'title': 'Screen Equilibrium Phosphorus Removal', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(screen_equilibrium_phosphorus_removal))
mcp.tool(annotations={'title': 'Audit Process Chemistry Endpoint', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(audit_process_chemistry_endpoint))
mcp.tool(annotations={'title': 'Run Shared Fe-S-P Kernel Validation Suite', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(run_shared_kernel_validation_suite))

async def get_engine_status_tool(input_data: dict) -> dict:
    """
    Get PHREEQC engine status and database availability.

    This tool reports engine readiness for diagnostics:
    - phreeqpython_available: Whether PhreeqPython library is installed
    - subprocess_mode_available: Whether standalone PHREEQC subprocess is available
    - active_engine: Which engine is currently active
    - database_loadability: Which databases can be loaded
    - known_limitations: Known limitations of the current engine

    Args:
        input_data: Empty dict (no input required)

    Returns:
        Engine status dictionary with readiness information
    """
    return get_engine_status()
mcp.tool(annotations={'title': 'Get Engine Status', 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})(names_its_database(get_engine_status_tool))
from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.utils.import_helpers import PHREEQPYTHON_AVAILABLE

def main():
    mcp.run(transport='stdio')
if __name__ == '__main__':
    main()
