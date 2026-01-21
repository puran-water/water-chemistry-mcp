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

from mcp.server.fastmcp import FastMCP

# MCP BEST PRACTICE: Configure logging to use stderr (NOT stdout, which is the MCP channel)
# Also use log rotation to prevent unbounded log file growth
_log_handler = logging.handlers.RotatingFileHandler(
    "debug.log", maxBytes=5 * 1024 * 1024, backupCount=3  # 5MB per file, 3 backups
)
_stderr_handler = logging.StreamHandler(sys.stderr)  # stderr, not stdout!

logging.basicConfig(
    level=logging.INFO,  # INFO for production (DEBUG available via env var)
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[_log_handler, _stderr_handler],
)

# Allow DEBUG level via environment variable
if os.environ.get("WATER_CHEMISTRY_DEBUG", "").lower() in ("1", "true", "yes"):
    logging.getLogger().setLevel(logging.DEBUG)

logger = logging.getLogger("water_chemistry_mcp")

# MCP BEST PRACTICE: Server name follows Python convention: {service}_mcp
mcp = FastMCP("water_chemistry_mcp")

# =============================================================================
# Import all tools
# =============================================================================

from tools.batch_processing import batch_process_scenarios
from tools.chemical_addition import simulate_chemical_addition

# NEW: Dosing requirement (phreeqpython API implementation)
from tools.dosing_requirement import calculate_dosing_requirement

# Unified phosphorus removal tool (supports Fe, Al, Mg, Ca strategies)
from tools.phosphorus_removal import calculate_phosphorus_removal_dose

# NEW: Engine status for diagnostics
from tools.phreeqc_wrapper import get_engine_status

# NEW: Advanced PHREEQC features
from tools.gas_phase import simulate_gas_phase_interaction

# NEW: Kinetic reactions
from tools.kinetic_reaction import simulate_kinetic_reaction

# NEW: Optimization tools
from tools.optimization_tools import (
    calculate_dosing_requirement_enhanced,
    calculate_lime_softening_dose,
    generate_lime_softening_curve,
    optimize_multi_reagent_treatment,
)
from tools.redox_adjustment import simulate_redox_adjustment
from tools.scaling_potential import predict_scaling_potential
from tools.solution_mixing import simulate_solution_mixing

# Core analysis tools
from tools.solution_speciation import calculate_solution_speciation
from tools.surface_interaction import simulate_surface_interaction

# NEW: Thermodynamic database query
from tools.thermodynamic_database import query_thermodynamic_database

# MCP BEST PRACTICE: Register tools with proper annotations
# Annotations help clients understand tool behavior:
# - readOnlyHint: Tool does not modify its environment
# - destructiveHint: Tool may perform destructive updates
# - idempotentHint: Repeated calls with same args have no additional effect
# - openWorldHint: Tool interacts with external entities

mcp.tool(
    annotations={
        "title": "Calculate Solution Speciation",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(calculate_solution_speciation)

mcp.tool(
    annotations={
        "title": "Simulate Chemical Addition",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)(simulate_chemical_addition)

mcp.tool(
    annotations={
        "title": "Simulate Solution Mixing",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)(simulate_solution_mixing)

mcp.tool(
    annotations={
        "title": "Predict Scaling Potential",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(predict_scaling_potential)

mcp.tool(
    annotations={
        "title": "Batch Process Scenarios",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)(batch_process_scenarios)

# =============================================================================
# NEW TOOLS: Dosing, Database Query, Kinetics, Advanced Features
# =============================================================================

mcp.tool(
    annotations={
        "title": "Calculate Dosing Requirement",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(calculate_dosing_requirement)

mcp.tool(
    annotations={
        "title": "Query Thermodynamic Database",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(query_thermodynamic_database)

mcp.tool(
    annotations={
        "title": "Simulate Kinetic Reaction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(simulate_kinetic_reaction)

mcp.tool(
    annotations={
        "title": "Simulate Gas Phase Interaction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(simulate_gas_phase_interaction)

mcp.tool(
    annotations={
        "title": "Simulate Redox Adjustment",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(simulate_redox_adjustment)

mcp.tool(
    annotations={
        "title": "Simulate Surface Interaction",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(simulate_surface_interaction)

# =============================================================================
# OPTIMIZATION TOOLS: Specialized treatment optimization
# =============================================================================

mcp.tool(
    annotations={
        "title": "Generate Lime Softening Curve",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(generate_lime_softening_curve)

mcp.tool(
    annotations={
        "title": "Calculate Lime Softening Dose",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(calculate_lime_softening_dose)

mcp.tool(
    annotations={
        "title": "Calculate Dosing Requirement Enhanced",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(calculate_dosing_requirement_enhanced)

mcp.tool(
    annotations={
        "title": "Optimize Multi-Reagent Treatment",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(optimize_multi_reagent_treatment)

mcp.tool(
    annotations={
        "title": "Calculate Phosphorus Removal Dose",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(calculate_phosphorus_removal_dose)

# =============================================================================
# ENGINE STATUS TOOL: Diagnostics and health check
# =============================================================================


async def get_engine_status_tool(input_data: dict) -> dict:
    """
    Get PHREEQC engine status and database availability.

    This tool reports engine readiness for diagnostics:
    - phreeqpython_available: Whether PhreeqPython library is installed
    - subprocess_mode_available: Whether USGS PHREEQC subprocess is available
    - active_engine: Which engine is currently active
    - database_loadability: Which databases can be loaded
    - known_limitations: Known limitations of the current engine

    Args:
        input_data: Empty dict (no input required)

    Returns:
        Engine status dictionary with readiness information
    """
    return get_engine_status()


mcp.tool(
    annotations={
        "title": "Get Engine Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)(get_engine_status_tool)

from utils.database_management import database_manager

# Log information about available dependencies
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

if __name__ == "__main__":
    logger.info("Starting Water Chemistry MCP server...")
    logger.info(f"PhreeqPython available: {PHREEQPYTHON_AVAILABLE}")

    # Log database information using the database manager
    if PHREEQPYTHON_AVAILABLE:
        default_db = database_manager.default_database
        logger.info(f"Using default database: {default_db}")

        # Log all available databases
        available_dbs = database_manager.available_databases
        if available_dbs:
            logger.info(f"Found {len(available_dbs)} available database files:")
            for i, db_path in enumerate(available_dbs[:5]):
                # Get additional info about the database
                db_info = database_manager.get_database_info(db_path)
                features = ", ".join([f for f, enabled in db_info.get("features", {}).items() if enabled])
                logger.info(
                    f"  {i+1}. {os.path.basename(db_path)} - "
                    f"Elements: {db_info.get('element_count', 'Unknown')}, "
                    f"Minerals: {db_info.get('mineral_count', 'Unknown')}, "
                    f"Features: {features}"
                )
            if len(available_dbs) > 5:
                logger.info(f"  ... and {len(available_dbs)-5} more")
        else:
            logger.warning("No PHREEQC database files found")
    else:
        logger.warning("PhreeqPython not available, cannot use PHREEQC databases")

    # Log which tools are registered
    logger.info("=== WATER CHEMISTRY MCP SERVER v3.0 ===\n")
    logger.info("Registered 17 tools:\n")
    logger.info("CORE ANALYSIS TOOLS (5):")
    logger.info("  1. calculate_solution_speciation: Water quality analysis and equilibrium speciation")
    logger.info("  2. simulate_chemical_addition: Treatment simulation with precipitation modeling")
    logger.info("  3. simulate_solution_mixing: Stream blending and mixing analysis")
    logger.info("  4. predict_scaling_potential: Scaling risk assessment for all systems")
    logger.info("  5. batch_process_scenarios: Parallel scenario processing & optimization")
    logger.info("\nADVANCED PHREEQC TOOLS (6):")
    logger.info("  6. calculate_dosing_requirement: Binary search for target pH/hardness/SI")
    logger.info("  7. query_thermodynamic_database: Query minerals, species, elements")
    logger.info("  8. simulate_kinetic_reaction: Time-dependent reaction modeling")
    logger.info("  9. simulate_gas_phase_interaction: Gas-water equilibration")
    logger.info("  10. simulate_redox_adjustment: pe/Eh/couple adjustment")
    logger.info("  11. simulate_surface_interaction: Surface complexation/adsorption")
    logger.info("\nOPTIMIZATION TOOLS (5):")
    logger.info("  12. generate_lime_softening_curve: Complete dose-response curves")
    logger.info("  13. calculate_lime_softening_dose: Optimal lime softening dose")
    logger.info("  14. calculate_dosing_requirement_enhanced: Multi-objective dosing optimization")
    logger.info("  15. optimize_multi_reagent_treatment: Multi-reagent with 4 strategies")
    logger.info("  16. calculate_phosphorus_removal_dose: Unified P removal (Fe/Al/Mg/Ca strategies)")
    logger.info("\nDIAGNOSTICS TOOLS (1):")
    logger.info("  17. get_engine_status: Engine health check and database availability")
    logger.info("\n✅ FAIL LOUDLY: All errors raise typed exceptions")
    logger.info("✅ PHREEQC thermodynamics via phreeqpython API")
    logger.info("✅ Inline PHREEQC blocks for Struvite, Variscite, HAO surface")
    logger.info("\n=== SERVER READY ===")

    # Start the server
    mcp.run()
