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

# Import core tools - batch processing proven most reliable
from tools.solution_speciation import calculate_solution_speciation
from tools.chemical_addition import simulate_chemical_addition
from tools.solution_mixing import simulate_solution_mixing
from tools.scaling_potential import predict_scaling_potential
from tools.batch_processing import batch_process_scenarios

# Note: Removed broken tools based on testing:
# - calculate_dosing_requirement (database errors with minteq.dat)
# - generate_calculation_sheet (remove as requested)
# - Enhanced optimization tools (replaced by batch_process_scenarios parameter sweeps)

# Register working tools only - tested and validated
mcp.tool()(calculate_solution_speciation)       # Tool 1: Solution speciation and equilibrium analysis
mcp.tool()(simulate_chemical_addition)          # Tool 2: Chemical addition with precipitation
mcp.tool()(simulate_solution_mixing)            # Tool 3: Solution blending and mixing  
mcp.tool()(predict_scaling_potential)           # Tool 4: Scaling risk assessment
mcp.tool()(batch_process_scenarios)             # Tool 5: Parallel scenario processing & optimization

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
    logger.info("=== WATER CHEMISTRY MCP SERVER - TESTED & VALIDATED ===\n")
    logger.info("Registered 5 working tools (broken tools removed based on testing):")
    logger.info("\nCORE ANALYSIS TOOLS (4):")
    logger.info("  1. calculate_solution_speciation: Water quality analysis and equilibrium speciation")
    logger.info("  2. simulate_chemical_addition: Treatment simulation with precipitation modeling")
    logger.info("  3. simulate_solution_mixing: Stream blending and mixing analysis")
    logger.info("  4. predict_scaling_potential: Scaling risk assessment for all systems")
    logger.info("\nOPTIMIZATION TOOL (1):")
    logger.info("  5. batch_process_scenarios: Parallel scenario processing & all optimization tasks")
    logger.info("\n❌ REMOVED BROKEN TOOLS:")
    logger.info("  • calculate_dosing_requirement: Database errors with minteq.dat")
    logger.info("  • generate_calculation_sheet: Removed as requested")
    logger.info("  • Enhanced optimization tools: Replaced by batch_process_scenarios parameter sweeps")
    logger.info("\n✅ TESTED & WORKING - PHREEQC THERMODYNAMICS ONLY")
    logger.info("✅ BATCH PROCESSING HANDLES ALL OPTIMIZATION NEEDS")
    logger.info("\n=== SERVER READY FOR RELIABLE USE ===")
    
    # Start the server
    mcp.run()
