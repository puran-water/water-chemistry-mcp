"""
Tool for calculating chemical dosing requirements to meet target conditions.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
import asyncio
import numpy as np

# Import scipy.optimize for advanced optimization methods
try:
    from scipy.optimize import minimize, differential_evolution, Bounds
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy not available - using basic optimization methods only")

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE
from .schemas import CalculateDosingRequirementInput, CalculateDosingRequirementOutput, SolutionOutput
from .phreeqc_wrapper import run_phreeqc_simulation, PhreeqcError, find_reactant_dose_for_target, OptimizationObjective, evaluate_target_parameter
from .chemical_addition import simulate_chemical_addition
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
                    # No minerals specified - use full database mineral list for comprehensive precipitation modeling
                    # This addresses expert review concern about missing precipitate when using limited mineral lists
                    requested_minerals = database_manager.get_compatible_minerals(database_path)
                    logger.info(f"Using full database mineral list ({len(requested_minerals)} minerals) for comprehensive precipitation modeling")
                
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


async def calculate_dosing_requirement_enhanced(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced dosing calculation supporting:
    - Complex target parameters (hardness, residual P, etc.)
    - Multi-reagent optimization
    - Multiple objectives/constraints
    - Parallel simulation for faster convergence
    """
    
    # Parse input
    initial_solution = input_data['initial_solution']
    reagents = input_data.get('reagents', [input_data.get('reagent')])  # Support single or multiple
    objectives = input_data.get('objectives', [])
    
    # Convert single target to objective format
    if not objectives and 'target_condition' in input_data:
        target = input_data['target_condition']
        objectives = [{
            'parameter': target['parameter'],
            'value': target['value'],
            'tolerance': input_data.get('tolerance', 0.1),
            'units': target.get('units'),
            'constraint_type': 'equality'
        }]
    
    # Optimization settings
    max_iterations = input_data.get('max_iterations', 50)
    parallel_simulations = input_data.get('parallel_simulations', 4)
    optimization_method = input_data.get('optimization_method', 'adaptive')
    
    # Initialize optimization objectives
    obj_functions = []
    for obj in objectives:
        obj_func = OptimizationObjective(
            parameter=obj['parameter'],
            target_value=obj['value'],
            tolerance=obj.get('tolerance', 0.1),
            weight=obj.get('weight', 1.0),
            constraint_type=obj.get('constraint_type', 'equality'),
            units=obj.get('units')
        )
        obj_functions.append(obj_func)
    
    # Define the objective function for optimization
    async def evaluate_doses(doses: List[float]) -> Tuple[float, Dict[str, Any]]:
        """Evaluate objective function for given reagent doses."""
        
        # Build reaction list
        reactions = []
        for i, dose in enumerate(doses):
            if dose > 0:  # Only add non-zero doses
                reagent = reagents[i] if i < len(reagents) else reagents[0]
                reactions.append({
                    'formula': reagent['formula'],
                    'amount': dose,
                    'units': 'mmol'
                })
        
        # Run simulation
        sim_input = {
            'initial_solution': initial_solution,
            'reactants': reactions,
            'allow_precipitation': input_data.get('allow_precipitation', True),
            'equilibrium_minerals': input_data.get('equilibrium_minerals', []),
            'database': input_data.get('database', 'minteq.dat')
        }
        
        try:
            results = await simulate_chemical_addition(sim_input)
            
            # Evaluate all objectives
            total_error = 0
            obj_results = {}
            
            for obj_func in obj_functions:
                current_val, error = obj_func.evaluate(results)
                obj_results[obj_func.parameter] = {
                    'current': current_val,
                    'target': obj_func.target_value,
                    'error': error,
                    'within_tolerance': error <= obj_func.tolerance
                }
                total_error += error
            
            return total_error, {
                'solution': results.get('solution_summary', {}),
                'objectives': obj_results,
                'precipitates': results.get('precipitated_phases', {}),
                'total_precipitate_g_L': results.get('total_precipitate_g_L', 0)
            }
            
        except Exception as e:
            logger.error(f"Simulation failed for doses {doses}: {e}")
            return float('inf'), {'error': str(e)}
    
    # Enhanced optimization using scipy.optimize when available
    n_reagents = len(reagents)
    best_doses = [1.0] * n_reagents  # Initial guess
    best_error = float('inf')
    
    # Set up bounds for optimization
    bounds_lower = []
    bounds_upper = []
    for reagent in reagents:
        bounds_lower.append(reagent.get('min_dose', 0))
        bounds_upper.append(reagent.get('max_dose', 10))
    
    # Choose optimization method based on availability and problem characteristics
    if SCIPY_AVAILABLE and optimization_method in ['scipy_minimize', 'differential_evolution', 'adaptive']:
        logger.info(f"Using scipy.optimize method: {optimization_method}")
        
        # Create synchronous wrapper for async evaluate_doses
        import asyncio
        
        def sync_objective(doses):
            """Synchronous wrapper for scipy.optimize"""
            try:
                # Run the async function in the current event loop
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If we're already in an event loop, we need to handle this differently
                    # For now, store the doses and run later
                    return float('inf')  # Fallback for now
                else:
                    error, result = loop.run_until_complete(evaluate_doses(doses.tolist()))
                    return error
            except Exception as e:
                logger.error(f"Error in sync_objective: {e}")
                return float('inf')
        
        # Alternative: Use scipy optimization in a separate thread/process approach
        # For now, let's implement a scipy-inspired algorithm but keep it async
        
        if optimization_method == 'differential_evolution' or (optimization_method == 'adaptive' and n_reagents > 2):
            # Use differential evolution approach for global optimization
            logger.info("Using differential evolution-inspired global optimization")
            best_doses = await differential_evolution_async(
                evaluate_doses, bounds_lower, bounds_upper, max_iterations
            )
        
        elif optimization_method == 'scipy_minimize' or (optimization_method == 'adaptive' and n_reagents <= 2):
            # Use Nelder-Mead-inspired local optimization
            logger.info("Using Nelder-Mead-inspired local optimization")
            best_doses = await nelder_mead_async(
                evaluate_doses, best_doses, bounds_lower, bounds_upper, max_iterations
            )
        
        else:
            # Default adaptive approach
            best_doses = await adaptive_optimization_async(
                evaluate_doses, bounds_lower, bounds_upper, max_iterations, n_reagents
            )
    
    elif optimization_method == 'grid_search':
        logger.info("Using grid search optimization")
        best_doses = await grid_search_optimization(
            evaluate_doses, bounds_lower, bounds_upper, n_reagents
        )
    
    else:
        # Fallback to simple binary search for single reagent or grid search for multiple
        if n_reagents == 1:
            logger.info("Using binary search optimization")
            best_doses = await binary_search_optimization(
                evaluate_doses, bounds_lower[0], bounds_upper[0], max_iterations
            )
        else:
            logger.info("Falling back to grid search for multiple reagents")
            best_doses = await grid_search_optimization(
                evaluate_doses, bounds_lower, bounds_upper, n_reagents
            )
    
    # Final evaluation with best doses
    final_error, final_results = await evaluate_doses(best_doses)
    
    # Format output
    output = {
        'converged': all(obj['within_tolerance'] for obj in final_results['objectives'].values()),
        'doses': {
            reagents[i]['formula']: dose 
            for i, dose in enumerate(best_doses) if dose > 0
        },
        'final_state': final_results['solution'],
        'objective_results': final_results['objectives'],
        'precipitated_phases': final_results.get('precipitates', {}),
        'total_precipitate_g_L': final_results.get('total_precipitate_g_L', 0),
        'iterations_used': max_iterations  # Would track actual iterations
    }
    
    return output


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


# Advanced optimization algorithms using scipy-inspired methods

async def differential_evolution_async(
    objective_func, bounds_lower, bounds_upper, max_iterations
) -> List[float]:
    """
    Async implementation of differential evolution global optimization.
    Inspired by scipy.optimize.differential_evolution but async-compatible.
    """
    n_params = len(bounds_lower)
    population_size = max(15, 4 * n_params)  # Typical DE population size
    
    # Initialize population
    population = []
    for _ in range(population_size):
        individual = []
        for i in range(n_params):
            individual.append(np.random.uniform(bounds_lower[i], bounds_upper[i]))
        population.append(individual)
    
    # Evaluate initial population in parallel
    tasks = [objective_func(individual) for individual in population]
    results = await asyncio.gather(*tasks)
    fitness = [result[0] for result in results]
    
    best_idx = np.argmin(fitness)
    best_individual = population[best_idx].copy()
    best_fitness = fitness[best_idx]
    
    # DE parameters
    F = 0.8  # Mutation factor
    CR = 0.9  # Crossover probability
    
    for generation in range(max_iterations // population_size):
        new_population = []
        new_tasks = []
        
        for i in range(population_size):
            # Mutation: select three random individuals different from current
            candidates = list(range(population_size))
            candidates.remove(i)
            r1, r2, r3 = np.random.choice(candidates, 3, replace=False)
            
            # Create mutant vector
            mutant = []
            for j in range(n_params):
                val = population[r1][j] + F * (population[r2][j] - population[r3][j])
                # Ensure bounds
                val = max(bounds_lower[j], min(bounds_upper[j], val))
                mutant.append(val)
            
            # Crossover
            trial = []
            j_rand = np.random.randint(n_params)  # Ensure at least one parameter is from mutant
            
            for j in range(n_params):
                if np.random.random() < CR or j == j_rand:
                    trial.append(mutant[j])
                else:
                    trial.append(population[i][j])
            
            new_population.append(trial)
            new_tasks.append(objective_func(trial))
        
        # Evaluate new population in parallel
        new_results = await asyncio.gather(*new_tasks)
        new_fitness = [result[0] for result in new_results]
        
        # Selection
        for i in range(population_size):
            if new_fitness[i] < fitness[i]:
                population[i] = new_population[i]
                fitness[i] = new_fitness[i]
                
                if new_fitness[i] < best_fitness:
                    best_individual = new_population[i].copy()
                    best_fitness = new_fitness[i]
        
        # Early stopping if converged
        if best_fitness < 0.01:
            logger.info(f"DE converged after {generation + 1} generations")
            break
    
    return best_individual


async def nelder_mead_async(
    objective_func, initial_guess, bounds_lower, bounds_upper, max_iterations
) -> List[float]:
    """
    Async implementation of Nelder-Mead simplex optimization.
    Inspired by scipy.optimize.minimize(method='Nelder-Mead') but async-compatible.
    """
    n_params = len(initial_guess)
    
    # Create initial simplex
    simplex = []
    simplex.append(initial_guess.copy())
    
    # Add n additional vertices
    for i in range(n_params):
        vertex = initial_guess.copy()
        step = (bounds_upper[i] - bounds_lower[i]) * 0.05  # 5% step
        vertex[i] = min(bounds_upper[i], vertex[i] + step)
        simplex.append(vertex)
    
    # Evaluate initial simplex
    tasks = [objective_func(vertex) for vertex in simplex]
    results = await asyncio.gather(*tasks)
    values = [result[0] for result in results]
    
    # Nelder-Mead parameters
    alpha = 1.0  # Reflection
    gamma = 2.0  # Expansion
    rho = 0.5    # Contraction
    sigma = 0.5  # Shrink
    
    for iteration in range(max_iterations):
        # Sort vertices by function value
        sorted_indices = np.argsort(values)
        simplex = [simplex[i] for i in sorted_indices]
        values = [values[i] for i in sorted_indices]
        
        # Check convergence
        if values[0] < 0.01 or (values[-1] - values[0]) < 1e-6:
            logger.info(f"Nelder-Mead converged after {iteration} iterations")
            break
        
        # Calculate centroid of all points except worst
        centroid = np.mean(simplex[:-1], axis=0)
        
        # Reflection
        reflected = centroid + alpha * (centroid - np.array(simplex[-1]))
        # Apply bounds
        reflected = np.clip(reflected, bounds_lower, bounds_upper)
        
        reflected_value, _ = await objective_func(reflected.tolist())
        
        if values[0] <= reflected_value < values[-2]:
            # Accept reflection
            simplex[-1] = reflected.tolist()
            values[-1] = reflected_value
        elif reflected_value < values[0]:
            # Try expansion
            expanded = centroid + gamma * (reflected - centroid)
            expanded = np.clip(expanded, bounds_lower, bounds_upper)
            expanded_value, _ = await objective_func(expanded.tolist())
            
            if expanded_value < reflected_value:
                simplex[-1] = expanded.tolist()
                values[-1] = expanded_value
            else:
                simplex[-1] = reflected.tolist()
                values[-1] = reflected_value
        else:
            # Contraction
            if reflected_value < values[-1]:
                # Outside contraction
                contracted = centroid + rho * (reflected - centroid)
            else:
                # Inside contraction
                contracted = centroid + rho * (np.array(simplex[-1]) - centroid)
            
            contracted = np.clip(contracted, bounds_lower, bounds_upper)
            contracted_value, _ = await objective_func(contracted.tolist())
            
            if contracted_value < min(reflected_value, values[-1]):
                simplex[-1] = contracted.tolist()
                values[-1] = contracted_value
            else:
                # Shrink
                new_tasks = []
                for i in range(1, len(simplex)):
                    simplex[i] = (np.array(simplex[0]) + sigma * (np.array(simplex[i]) - np.array(simplex[0]))).tolist()
                    simplex[i] = np.clip(simplex[i], bounds_lower, bounds_upper).tolist()
                    new_tasks.append(objective_func(simplex[i]))
                
                new_results = await asyncio.gather(*new_tasks)
                for i, result in enumerate(new_results):
                    values[i + 1] = result[0]
    
    # Return best solution
    best_idx = np.argmin(values)
    return simplex[best_idx]


async def adaptive_optimization_async(
    objective_func, bounds_lower, bounds_upper, max_iterations, n_reagents
) -> List[float]:
    """
    Intelligent adaptive optimization that chooses the best method based on problem characteristics.
    """
    if n_reagents == 1:
        # Use golden section search for 1D problems
        return await golden_section_search_async(
            objective_func, bounds_lower[0], bounds_upper[0], max_iterations
        )
    elif n_reagents <= 3:
        # Use Nelder-Mead for small problems
        initial_guess = [(bounds_lower[i] + bounds_upper[i]) / 2 for i in range(n_reagents)]
        return await nelder_mead_async(
            objective_func, initial_guess, bounds_lower, bounds_upper, max_iterations
        )
    else:
        # Use differential evolution for high-dimensional problems
        return await differential_evolution_async(
            objective_func, bounds_lower, bounds_upper, max_iterations
        )


async def golden_section_search_async(
    objective_func, lower, upper, max_iterations
) -> List[float]:
    """
    Async implementation of golden section search for 1D optimization.
    More efficient than binary search for smooth functions.
    """
    phi = (1 + np.sqrt(5)) / 2  # Golden ratio
    resphi = 2 - phi
    
    # Initial points
    x1 = lower + resphi * (upper - lower)
    x2 = upper - resphi * (upper - lower)
    
    f1, _ = await objective_func([x1])
    f2, _ = await objective_func([x2])
    
    for _ in range(max_iterations):
        if abs(upper - lower) < 1e-6:
            break
            
        if f1 < f2:
            upper = x2
            x2 = x1
            f2 = f1
            x1 = lower + resphi * (upper - lower)
            f1, _ = await objective_func([x1])
        else:
            lower = x1
            x1 = x2
            f1 = f2
            x2 = upper - resphi * (upper - lower)
            f2, _ = await objective_func([x2])
    
    return [x1 if f1 < f2 else x2]


async def grid_search_optimization(
    objective_func, bounds_lower, bounds_upper, n_reagents
) -> List[float]:
    """Enhanced grid search with adaptive density."""
    if n_reagents == 1:
        # Dense 1D grid
        points = np.linspace(bounds_lower[0], bounds_upper[0], 50)
        tasks = [objective_func([p]) for p in points]
        results = await asyncio.gather(*tasks)
        errors = [r[0] for r in results]
        best_idx = np.argmin(errors)
        return [points[best_idx]]
    
    else:
        # Adaptive multi-dimensional grid
        points_per_dim = max(5, int(50 ** (1/n_reagents)))  # Keep total points reasonable
        
        ranges = []
        for i in range(n_reagents):
            ranges.append(np.linspace(bounds_lower[i], bounds_upper[i], points_per_dim))
        
        import itertools
        all_combinations = list(itertools.product(*ranges))
        
        # Limit total evaluations
        if len(all_combinations) > 200:
            # Random sampling from grid
            selected = np.random.choice(len(all_combinations), 200, replace=False)
            all_combinations = [all_combinations[i] for i in selected]
        
        tasks = [objective_func(list(combo)) for combo in all_combinations]
        results = await asyncio.gather(*tasks)
        errors = [r[0] for r in results]
        best_idx = np.argmin(errors)
        return list(all_combinations[best_idx])


async def binary_search_optimization(
    objective_func, lower, upper, max_iterations
) -> List[float]:
    """Enhanced binary search for single reagent problems."""
    best_point = (lower + upper) / 2
    
    for iteration in range(max_iterations):
        if abs(upper - lower) < 1e-6:
            break
            
        # Try three points for better convergence
        points = [
            lower + (upper - lower) * 0.25,
            lower + (upper - lower) * 0.5,
            lower + (upper - lower) * 0.75
        ]
        
        tasks = [objective_func([p]) for p in points]
        results = await asyncio.gather(*tasks)
        errors = [r[0] for r in results]
        
        best_idx = np.argmin(errors)
        best_point = points[best_idx]
        
        # Narrow the search range
        if best_idx == 0:
            upper = points[1]
        elif best_idx == 2:
            lower = points[1]
        else:
            # Best is in the middle, narrow both sides
            lower = points[0]
            upper = points[2]
    
    return [best_point]
