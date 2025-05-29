"""
Tool for calculating chemical dosing requirements to meet target conditions.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
import asyncio

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE
from .schemas import CalculateDosingRequirementInput, CalculateDosingRequirementOutput, SolutionOutput
from .phreeqc_wrapper import run_phreeqc_simulation, PhreeqcError, find_reactant_dose_for_target
from utils.helpers import build_solution_block

logger = logging.getLogger(__name__)

# Import the enhanced phreeqpython-based implementation
try:
    from .dosing_requirement_phreeqpython import calculate_dosing_requirement as calculate_dosing_requirement_phreeqpython
    PHREEQPYTHON_DOSING_AVAILABLE = True
except ImportError:
    PHREEQPYTHON_DOSING_AVAILABLE = False
    logger.warning("Enhanced phreeqpython dosing not available")

async def calculate_dosing_requirement_legacy(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates the required dose of a reagent to meet a target condition in a solution.
    Uses an iterative approach to find the optimal dose.
    
    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting solution composition
            - target_condition: The desired final state (parameter, value, etc.)
            - reagent: Chemical to dose (formula)
            - max_iterations: Maximum iterations for the search (optional, default 30)
            - tolerance: Acceptable tolerance for reaching the target (optional, default 0.05)
            - initial_guess_mmol: Initial guess for the dose (optional, default 1.0)
            - database: Path to PHREEQC database file (optional)
            - allow_precipitation: Whether to allow mineral precipitation (optional, default True)
            - equilibrium_minerals: List of minerals to allow to precipitate (optional)
    
    Returns:
        Dictionary containing the required dose, final solution state, and convergence information
    """
    logger.info("Running calculate_dosing_requirement tool...")
    
    # Create pydantic model from input data for validation
    try:
        input_model = CalculateDosingRequirementInput(**input_data)
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
            # For dosing calculation, use a general purpose database with good acid/base equilibria
            recommended_db = database_manager.recommend_database('general')
            logger.info(f"Using recommended database: {recommended_db}")
            database_path = recommended_db
    else:
        # No database specified, use recommended
        database_path = database_manager.recommend_database('general')
        logger.info(f"No database specified, using recommended database: {database_path}")
    
    try:
        # Build initial solution string
        initial_sol_str = build_solution_block(
            input_model.initial_solution.model_dump(exclude_defaults=True), 
            solution_num=1
        )
        
        # Check if we're targeting pH - if so, we can use a more direct method
        if input_model.target_condition.parameter == 'pH':
            try:
                # Run once with initial solution
                initial_input_str = initial_sol_str + "END\n"
                initial_results = await run_phreeqc_simulation(initial_input_str, database_path=database_path)
                
                if isinstance(initial_results, list):
                    initial_results = initial_results[0]
                
                # Check if the simulation was successful
                if 'error' in initial_results and initial_results['error']:
                    raise PhreeqcError(initial_results['error'])
                
                # Now create a new PhreeqPython instance and use change_ph
                from phreeqpython import PhreeqPython
                pp = PhreeqPython(database=database_path)
                
                # Add the initial solution
                solution_data = input_model.initial_solution.model_dump(exclude_defaults=True)
                solution = pp.add_solution(solution_data)
                
                # Use change_ph to reach the target pH
                target_ph = input_model.target_condition.value
                reagent = input_model.reagent.formula
                
                # Record the solution before the change
                initial_ph = solution.pH
                
                # Change the pH
                solution.change_ph(target_ph, with_chemical=reagent)
                
                # Get the final state
                final_ph = solution.pH
                
                # Calculate approximate dosing requirement
                # This is a rough estimate as we don't know exactly how much reagent was added
                if reagent == 'NaOH':
                    # For pH increase, estimate based on hydroxide addition
                    # Each unit of pH requires approximately 10x more OH-
                    ph_change = final_ph - initial_ph
                    if ph_change > 0:
                        dose_mmol = 10**(ph_change) - 1  # Simple approximation
                    else:
                        dose_mmol = 0
                elif reagent == 'HCl':
                    # For pH decrease, estimate based on hydrogen addition
                    ph_change = initial_ph - final_ph
                    if ph_change > 0:
                        dose_mmol = 10**(ph_change) - 1  # Simple approximation
                    else:
                        dose_mmol = 0
                else:
                    # For other reagents, we don't have a good estimation method
                    dose_mmol = None
                
                # Create output in the expected format
                final_results_dict = {
                    'solution_summary': {
                        'pH': final_ph,
                        'pe': solution.pe,
                        'ionic_strength': solution.mu() if callable(solution.mu) else solution.I
                    }
                }
                
                # Indicate success
                iterations_done = 1
                status = "Direct pH adjustment - Converged"
                
            except Exception as e:
                logger.warning(f"Direct pH adjustment failed: {e}. Falling back to iterative method.")
                # Fall back to iterative method
                # IMPORTANT: Precipitation is ALWAYS enabled by default
                # The only exception is if it's explicitly disabled in the input model
                allow_precipitation = getattr(input_model, 'allow_precipitation', True)
                
                # Get minerals for precipitation
                requested_minerals = None
                if hasattr(input_model, 'equilibrium_minerals') and input_model.equilibrium_minerals:
                    # User specified minerals - use those
                    requested_minerals = input_model.equilibrium_minerals
                    logger.info(f"Using user-specified minerals: {', '.join(requested_minerals)}")
                else:
                    # No minerals specified - select based on water chemistry
                    from utils.constants import select_minerals_for_water_chemistry
                    
                    # Extract water chemistry from the input model
                    water_analysis = {}
                    if hasattr(input_model, 'initial_solution') and hasattr(input_model.initial_solution, 'analysis'):
                        water_analysis = input_model.initial_solution.analysis
                    
                    # Get appropriate minerals based on water chemistry
                    requested_minerals = select_minerals_for_water_chemistry(water_analysis)
                    logger.info(f"Automatically selected minerals based on water chemistry: {', '.join(requested_minerals)}")
                
                dose_mmol, final_results_dict, iterations_done = await find_reactant_dose_for_target(
                    initial_solution_str=initial_sol_str,
                    target_parameter=input_model.target_condition.parameter,
                    target_value=input_model.target_condition.value,
                    reagent_formula=input_model.reagent.formula,
                    mineral_name=input_model.target_condition.mineral,
                    element_or_species=input_model.target_condition.element_or_species,
                    target_units=input_model.target_condition.units,
                    initial_guess_mmol=input_model.initial_guess_mmol,
                    max_iterations=input_model.max_iterations,
                    tolerance=input_model.tolerance,
                    database_path=database_path,
                    allow_precipitation=allow_precipitation,
                    equilibrium_minerals=requested_minerals,
                )
        else:
            # For non-pH targets, use the iterative dose-finding function
            # IMPORTANT: Precipitation is ALWAYS enabled by default
            # The only exception is if it's explicitly disabled in the input model
            allow_precipitation = getattr(input_model, 'allow_precipitation', True)
            
            # Get minerals for precipitation
            requested_minerals = None
            if hasattr(input_model, 'equilibrium_minerals') and input_model.equilibrium_minerals:
                # User specified minerals - use those
                requested_minerals = input_model.equilibrium_minerals
                logger.info(f"Using user-specified minerals: {', '.join(requested_minerals)}")
            else:
                # No minerals specified - select based on water chemistry
                from utils.constants import select_minerals_for_water_chemistry
                
                # Extract water chemistry from the input model
                water_analysis = {}
                if hasattr(input_model, 'initial_solution') and hasattr(input_model.initial_solution, 'analysis'):
                    water_analysis = input_model.initial_solution.analysis
                
                # Get appropriate minerals based on water chemistry
                requested_minerals = select_minerals_for_water_chemistry(water_analysis)
                logger.info(f"Automatically selected minerals based on water chemistry: {', '.join(requested_minerals)}")
            
            dose_mmol, final_results_dict, iterations_done = await find_reactant_dose_for_target(
                initial_solution_str=initial_sol_str,
                target_parameter=input_model.target_condition.parameter,
                target_value=input_model.target_condition.value,
                reagent_formula=input_model.reagent.formula,
                mineral_name=input_model.target_condition.mineral,
                element_or_species=input_model.target_condition.element_or_species,
                target_units=input_model.target_condition.units,
                initial_guess_mmol=input_model.initial_guess_mmol,
                max_iterations=input_model.max_iterations,
                tolerance=input_model.tolerance,
                database_path=database_path,
                allow_precipitation=allow_precipitation,
                equilibrium_minerals=requested_minerals,
            )
        
        # Determine status based on iteration results
        status = "Converged"
        top_level_error = None
        
        iter_error = final_results_dict.get('error')
        if iter_error:
            if "Maximum iterations reached" in iter_error:
                status = "Max iterations reached"
            elif "Bounds converged" in iter_error:
                status = "Converged without tolerance"
            elif "Simulation failed" in iter_error:
                status = f"Error during iteration: {iter_error}"
            else:
                status = f"Iteration Error: {iter_error}"
                
            # Keep the error for the main output, but not in final_state schema
            top_level_error = iter_error
            final_state_data = final_results_dict.copy()
            if 'error' in final_state_data:
                final_state_data.pop('error')
        else:
            final_state_data = final_results_dict
            
        # Format output
        final_state_output = SolutionOutput(**final_state_data)
        
        output = CalculateDosingRequirementOutput(
            required_dose_mmol_per_L=dose_mmol,
            final_state=final_state_output,
            iterations_taken=iterations_done,
            convergence_status=status,
            error=top_level_error
        )
        
        logger.info(f"calculate_dosing_requirement tool finished with status: {status}")
        return output.model_dump(exclude_defaults=True)
        
    except PhreeqcError as e:
        logger.error(f"Dosing requirement tool failed: {e}")
        return {
            "final_state": {"error": str(e)}, 
            "convergence_status": "Error", 
            "error": str(e)
        }
        
    except Exception as e:
        logger.exception("Unexpected error in calculate_dosing_requirement")
        return {
            "final_state": {"error": f"Unexpected server error: {e}"}, 
            "convergence_status": "Error", 
            "error": f"Unexpected server error: {e}"
        }


async def calculate_dosing_requirement(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main dosing requirement calculation function.
    Uses phreeqpython implementation if available, otherwise falls back to legacy.
    """
    if PHREEQPYTHON_AVAILABLE and PHREEQPYTHON_DOSING_AVAILABLE:
        logger.info("Using enhanced phreeqpython dosing calculation")
        try:
            return await calculate_dosing_requirement_phreeqpython(input_data)
        except Exception as e:
            logger.warning(f"PhreeqPython dosing failed: {e}, falling back to legacy method")
            return await calculate_dosing_requirement_legacy(input_data)
    else:
        logger.info("Using legacy iterative dosing calculation")
        return await calculate_dosing_requirement_legacy(input_data)
