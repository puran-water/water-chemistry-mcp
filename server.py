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

# Import core tools for industrial wastewater applications
from tools.solution_speciation import calculate_solution_speciation
from tools.chemical_addition import simulate_chemical_addition
from tools.dosing_requirement import calculate_dosing_requirement
from tools.solution_mixing import simulate_solution_mixing
from tools.scaling_potential import predict_scaling_potential
from tools.calculation_sheet_generator import generate_calculation_sheet

# Register core tools with MCP
mcp.tool()(calculate_solution_speciation)
mcp.tool()(simulate_chemical_addition)
mcp.tool()(calculate_dosing_requirement)
mcp.tool()(simulate_solution_mixing)
mcp.tool()(predict_scaling_potential)
mcp.tool()(generate_calculation_sheet)

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
    logger.info("Registered core tools for industrial wastewater:")
    logger.info("  - calculate_solution_speciation: Water quality analysis")
    logger.info("  - simulate_chemical_addition: Treatment simulation with precipitation")
    logger.info("  - calculate_dosing_requirement: Optimal dosing calculations")
    logger.info("  - simulate_solution_mixing: Stream blending analysis")
    logger.info("  - predict_scaling_potential: Scaling risk and membrane system analysis")
    logger.info("  - generate_calculation_sheet: Engineering calculation documentation")
    
    # Start the server
    mcp.run()
