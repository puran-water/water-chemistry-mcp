"""
Tool for predicting mineral scaling potential in water.
"""

import logging
import os
from typing import Dict, Any, List, Optional
import asyncio

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE
from .schemas import PredictScalingPotentialInput, PredictScalingPotentialOutput
from .phreeqc_wrapper import run_phreeqc_simulation, PhreeqcError
from utils.helpers import (
    build_solution_block, 
    build_equilibrium_phases_block,
    build_selected_output_block
)

logger = logging.getLogger(__name__)

# Import enhanced membrane scaling analysis if available
try:
    from .membrane_scaling_potential import predict_membrane_scaling_potential
    MEMBRANE_SCALING_AVAILABLE = True
except ImportError:
    MEMBRANE_SCALING_AVAILABLE = False
    logger.warning("Enhanced membrane scaling analysis not available")

async def predict_scaling_potential_legacy(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Predicts mineral scaling potential (saturation indices) and optionally calculates
    precipitation amounts by forcing equilibrium with specified minerals.
    
    Args:
        input_data: Dictionary containing water analysis parameters and optionally:
            - force_equilibrium_minerals: List of mineral names to force equilibrium with
            - database: Path to PHREEQC database file
    
    Returns:
        Dictionary containing saturation indices and precipitation amounts if requested
    """
    logger.info("Running predict_scaling_potential tool...")
    
    # Create pydantic model from input data for validation
    try:
        input_model = PredictScalingPotentialInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}
    
    # Validate database if provided
    database_path = input_model.database
    if database_path:
        resolved_path = database_manager.resolve_database_path(database_path)
        if resolved_path and database_manager.validate_database_path(resolved_path):
            database_path = resolved_path
            logger.info(f"Using resolved database path: {database_path}")
        else:
            logger.warning(f"Invalid database path: {database_path}, using recommended database instead")
            # For scaling potential, use a database with extensive mineral coverage
            recommended_db = database_manager.recommend_database('minerals')
            logger.info(f"Using recommended database for minerals: {recommended_db}")
            database_path = recommended_db
    else:
        # No database specified, use recommended for minerals
        database_path = database_manager.recommend_database('minerals')
        logger.info(f"No database specified, using recommended database for minerals: {database_path}")
    
    try:
        # Build solution block
        solution_str = build_solution_block(input_model.model_dump(exclude_defaults=True))
        phreeqc_input = solution_str
        
        # Add equilibrium phases if requested
        equilibrium_phases_str = ""
        use_equilibrium = False
        
        if input_model.force_equilibrium_minerals:
            # Get compatible minerals for the selected database
            mineral_mapping = database_manager.get_compatible_minerals(
                database_path, 
                input_model.force_equilibrium_minerals
            )
            
            # Filter out incompatible minerals and use alternatives where possible
            compatible_minerals = []
            for requested_mineral, compatible_mineral in mineral_mapping.items():
                if compatible_mineral:
                    compatible_minerals.append(compatible_mineral)
                else:
                    logger.warning(
                        f"Mineral '{requested_mineral}' is not compatible with database "
                        f"'{os.path.basename(database_path)}' and no alternative was found."
                    )
            
            # Build equilibrium phases block with compatible minerals
            if compatible_minerals:
                phases_to_force = [{'name': name} for name in compatible_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(phases_to_force, block_num=1)
                if equilibrium_phases_str:
                    phreeqc_input += equilibrium_phases_str
                    phreeqc_input += "USE solution 1\n"  # Need to explicitly use initial solution
                    phreeqc_input += "USE equilibrium_phases 1\n"
                    use_equilibrium = True
        
        # Add selected output
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True, 
            phases=True, 
            molalities=True, 
            totals=True
        ) + "END\n"
        
        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)
        
        # If we got a list, extract the single result
        if isinstance(results, list) and results:
            results = results[0]
            
        # Convert to output model
        output_model = PredictScalingPotentialOutput(**results)
        
        logger.info("predict_scaling_potential tool finished successfully.")
        return output_model.model_dump(exclude_defaults=True)
        
    except PhreeqcError as e:
        logger.error(f"Scaling potential tool failed: {e}")
        return {"error": str(e)}
        
    except Exception as e:
        logger.exception("Unexpected error in predict_scaling_potential")
        return {"error": f"Unexpected server error: {e}"}


async def predict_scaling_potential(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main scaling potential prediction function.
    Routes to membrane-specific analysis if recovery parameters are provided.
    """
    # Check if this is a membrane system analysis
    if any(key in input_data for key in ['target_recovery', 'recovery_rate', 'concentration_factor']):
        if PHREEQPYTHON_AVAILABLE and MEMBRANE_SCALING_AVAILABLE:
            logger.info("Using enhanced membrane scaling analysis")
            try:
                return await predict_membrane_scaling_potential(input_data)
            except Exception as e:
                logger.warning(f"Membrane scaling analysis failed: {e}, falling back to standard analysis")
                return await predict_scaling_potential_legacy(input_data)
        else:
            logger.warning("Membrane scaling requested but enhanced analysis not available")
            # Add warning to standard output
            result = await predict_scaling_potential_legacy(input_data)
            if 'warnings' not in result:
                result['warnings'] = []
            result['warnings'].append("Enhanced membrane scaling analysis not available. Results show feed water only.")
            return result
    else:
        # Standard scaling analysis
        return await predict_scaling_potential_legacy(input_data)
