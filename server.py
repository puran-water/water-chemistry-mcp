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
import os
from mcp.server.fastmcp import FastMCP

# Configure logging with debug support
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("water-chemistry-mcp")

# Initialize the MCP server
mcp = FastMCP("water-chemistry-calculator")

# Import core tools and batch processing for client testing
from tools.solution_speciation import calculate_solution_speciation
from tools.chemical_addition import simulate_chemical_addition
from tools.dosing_requirement import calculate_dosing_requirement
from tools.solution_mixing import simulate_solution_mixing
from tools.scaling_potential import predict_scaling_potential
from tools.calculation_sheet_generator import generate_calculation_sheet
from tools.batch_processing import batch_process_scenarios

# Enhanced tools are temporarily disabled pending additional work
# from tools.dosing_requirement import calculate_dosing_requirement_enhanced
# from tools.batch_processing import (
#     generate_lime_softening_curve,
#     calculate_lime_softening_dose,
#     optimize_phosphorus_removal,
#     optimize_multi_reagent_treatment
# )

# Register core tools and batch processing for client testing
mcp.tool()(calculate_solution_speciation)       # Tool 1: Solution speciation and equilibrium analysis
mcp.tool()(simulate_chemical_addition)          # Tool 2: Chemical addition with precipitation
mcp.tool()(calculate_dosing_requirement)        # Tool 3: Basic dosing optimization
mcp.tool()(simulate_solution_mixing)            # Tool 4: Solution blending and mixing
mcp.tool()(predict_scaling_potential)           # Tool 5: Scaling risk assessment
mcp.tool()(generate_calculation_sheet)          # Tool 6: Engineering calculation sheets
mcp.tool()(batch_process_scenarios)             # Tool 7: Parallel scenario processing

# Enhanced tools are temporarily disabled pending additional work
# mcp.tool()(calculate_dosing_requirement_enhanced)  # Enhanced: Multi-objective dosing optimization
# mcp.tool()(generate_lime_softening_curve)          # Enhanced: Lime softening dose-response curves
# mcp.tool()(calculate_lime_softening_dose)          # Enhanced: Specialized lime softening optimization
# mcp.tool()(optimize_phosphorus_removal)            # Enhanced: Phosphorus removal optimization
# mcp.tool()(optimize_multi_reagent_treatment)       # Enhanced: Advanced multi-reagent optimization

# Log information about available dependencies
from utils.import_helpers import PHREEQPYTHON_AVAILABLE
from utils.database_management import database_manager

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
                features = ', '.join([f for f, enabled in db_info.get('features', {}).items() if enabled])
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
    logger.info("=== WATER CHEMISTRY MCP SERVER - CLIENT TESTING MODE ===\n")
    logger.info("Registered 7 tools for MCP client testing:")
    logger.info("\nCORE ANALYSIS TOOLS (6):")
    logger.info("  1. calculate_solution_speciation: Water quality analysis and equilibrium speciation")
    logger.info("  2. simulate_chemical_addition: Treatment simulation with precipitation modeling")
    logger.info("  3. calculate_dosing_requirement: Basic optimal dosing calculations")
    logger.info("  4. simulate_solution_mixing: Stream blending and mixing analysis")
    logger.info("  5. predict_scaling_potential: Scaling risk assessment for all systems")
    logger.info("  6. generate_calculation_sheet: Engineering calculation documentation")
    logger.info("\nBATCH PROCESSING TOOL (1):")
    logger.info("  7. batch_process_scenarios: Parallel scenario evaluation and parameter sweeps")
    logger.info("\nDISABLED ENHANCED TOOLS (pending additional work):")
    logger.info("  • 5 enhanced optimization tools temporarily disabled for refinement")
    logger.info("  • Will be re-enabled after core tool validation")
    logger.info("\n=== SERVER READY FOR MCP CLIENT TESTING ===")
    
    # Start the server
    mcp.run()
