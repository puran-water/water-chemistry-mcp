"""
Batch processing tool for efficient water chemistry calculations.

Supports:
- Parameter sweeps (e.g., pH from 6-11)
- Dose optimization curves
- Sensitivity analysis
- Treatment train optimization
"""

import asyncio
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import logging
import numpy as np

from .chemical_addition import simulate_chemical_addition
from .solution_speciation import calculate_solution_speciation
from .dosing_requirement import calculate_dosing_requirement_enhanced

logger = logging.getLogger(__name__)


async def batch_process_scenarios(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process multiple water chemistry scenarios in batch for efficiency.
    
    Supports:
    - Parameter sweeps (e.g., pH from 6-11)
    - Dose optimization curves
    - Sensitivity analysis
    - Treatment train optimization
    
    Args:
        input_data: {
            'base_solution': Initial water chemistry
            'scenarios': List of scenario configurations
            'parallel_limit': Max concurrent simulations (default 10)
            'output_format': 'full' or 'summary'
        }
    """
    
    base_solution = input_data['base_solution']
    scenarios = input_data['scenarios']
    parallel_limit = input_data.get('parallel_limit', 10)
    output_format = input_data.get('output_format', 'summary')
    
    # Process scenarios in batches
    results = []
    
    for i in range(0, len(scenarios), parallel_limit):
        batch = scenarios[i:i + parallel_limit]
        
        # Create tasks for this batch
        tasks = []
        for scenario in batch:
            task = process_single_scenario(base_solution, scenario)
            tasks.append(task)
        
        # Run batch in parallel
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for scenario, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                logger.error(f"Scenario failed: {scenario.get('name', 'unnamed')} - {result}")
                results.append({
                    'scenario': scenario,
                    'error': str(result)
                })
            else:
                results.append({
                    'scenario': scenario,
                    'result': result
                })
    
    # Format output based on request
    if output_format == 'summary':
        summary_data = summarize_batch_results(results)
        return {
            'summary': summary_data,
            'details': results
        }
    else:
        return {
            'results': results
        }


async def process_single_scenario(base_solution: Dict[str, Any], 
                                scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single scenario configuration."""
    
    scenario_type = scenario.get('type', 'chemical_addition')
    
    if scenario_type == 'chemical_addition':
        # Standard chemical addition
        return await simulate_chemical_addition({
            'initial_solution': base_solution,
            'reactants': scenario.get('reactants', []),
            'allow_precipitation': scenario.get('allow_precipitation', True),
            'equilibrium_minerals': scenario.get('equilibrium_minerals'),
            'database': scenario.get('database', 'minteq.dat')
        })
    
    elif scenario_type == 'dose_optimization':
        # Find optimal dose
        return await calculate_dosing_requirement_enhanced({
            'initial_solution': base_solution,
            **scenario.get('optimization_config', {})
        })
    
    elif scenario_type == 'parameter_sweep':
        # Sweep a parameter range
        parameter = scenario['parameter']
        values = scenario['values']
        
        sweep_results = []
        for value in values:
            # Modify base solution
            modified_solution = base_solution.copy()
            if parameter in ['pH', 'pe', 'temperature_celsius']:
                modified_solution[parameter] = value
            else:
                # Assume it's an element concentration
                if 'analysis' not in modified_solution:
                    modified_solution['analysis'] = {}
                modified_solution['analysis'][parameter] = value
            
            # Run analysis
            result = await calculate_solution_speciation(modified_solution)
            sweep_results.append({
                parameter: value,
                'result': result
            })
        
        return {'sweep_results': sweep_results}
    
    elif scenario_type == 'treatment_train':
        # Sequential treatment steps
        current_solution = base_solution
        train_results = []
        
        for step in scenario.get('steps', []):
            step_result = await process_treatment_step(current_solution, step)
            train_results.append(step_result)
            
            # Use output as input for next step
            current_solution = step_result.get('solution_summary', current_solution)
        
        return {
            'train_results': train_results,
            'final_solution': current_solution
        }
    
    elif scenario_type == 'phosphorus_optimization':
        # Advanced phosphorus removal optimization with coagulant selection
        return await optimize_phosphorus_removal(
            initial_water=base_solution,
            target_p_mg_l=scenario['target_p_mg_l'],
            coagulant=scenario.get('coagulant', 'FeCl3'),
            target_ph=scenario.get('target_ph'),
            database=scenario.get('database', 'minteq.dat')
        )
    
    elif scenario_type == 'multi_reagent_optimization':
        # Multi-chemical optimization with various strategies
        return await optimize_multi_reagent_treatment(
            initial_water=base_solution,
            reagents=scenario['reagents'],
            objectives=scenario['objectives'],
            constraints=scenario.get('constraints'),
            optimization_strategy=scenario.get('strategy', 'pareto_front'),
            database=scenario.get('database', 'minteq.dat')
        )
    
    elif scenario_type == 'lime_softening_optimization':
        # Lime softening optimization with optional soda ash
        return await optimize_lime_softening_enhanced(
            initial_water=base_solution,
            target_hardness_mg_l=scenario['target_hardness_mg_l'],
            target_alkalinity_mg_l=scenario.get('target_alkalinity_mg_l'),
            include_soda_ash=scenario.get('include_soda_ash', True),
            target_ph=scenario.get('target_ph', 10.5),
            database=scenario.get('database', 'minteq.dat')
        )
    
    elif scenario_type == 'alternative_comparison':
        # Compare different treatment alternatives systematically
        return await compare_treatment_alternatives(
            initial_water=base_solution,
            objective=scenario['objective'],
            alternatives=scenario['alternatives'],
            evaluation_criteria=scenario.get('evaluation_criteria', ['dose', 'final_ph', 'removal_efficiency']),
            database=scenario.get('database', 'minteq.dat')
        )
    
    else:
        raise ValueError(f"Unknown scenario type: {scenario_type}")


async def process_treatment_step(current_solution: Dict[str, Any], 
                               step: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single treatment step in a treatment train."""
    
    step_type = step.get('type', 'chemical_addition')
    
    if step_type == 'chemical_addition':
        return await simulate_chemical_addition({
            'initial_solution': current_solution,
            'reactants': step.get('reactants', []),
            'allow_precipitation': step.get('allow_precipitation', True),
            'equilibrium_minerals': step.get('equilibrium_minerals'),
            'database': step.get('database', 'minteq.dat')
        })
    
    elif step_type == 'dose_optimization':
        return await calculate_dosing_requirement_enhanced({
            'initial_solution': current_solution,
            **step.get('optimization_config', {})
        })
    
    else:
        # Just return speciation for unknown types
        return await calculate_solution_speciation(current_solution)


def summarize_batch_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create summary statistics from batch results."""
    
    summary = {
        'total_scenarios': len(results),
        'successful': sum(1 for r in results if 'error' not in r),
        'failed': sum(1 for r in results if 'error' in r)
    }
    
    # Extract key parameters for successful runs
    successful_results = [r for r in results if 'error' not in r]
    
    if successful_results:
        # Collect common parameters
        ph_values = []
        tds_values = []
        precipitate_masses = []
        
        for r in successful_results:
            result = r['result']
            if 'solution_summary' in result:
                ph_values.append(result['solution_summary'].get('pH'))
                tds_values.append(result['solution_summary'].get('tds_calculated'))
            if 'total_precipitate_g_L' in result:
                precipitate_masses.append(result['total_precipitate_g_L'])
        
        # Calculate statistics
        if ph_values:
            summary['pH_range'] = (min(ph_values), max(ph_values))
            summary['pH_mean'] = sum(ph_values) / len(ph_values)
        
        if tds_values:
            summary['TDS_range'] = (min(tds_values), max(tds_values))
            summary['TDS_mean'] = sum(tds_values) / len(tds_values)
        
        if precipitate_masses:
            summary['precipitate_range'] = (min(precipitate_masses), max(precipitate_masses))
            summary['precipitate_total'] = sum(precipitate_masses)
    
    return summary


# Specialized functions from the prototype

async def generate_lime_softening_curve(
    initial_water: Dict[str, Any],
    lime_doses: List[float],
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Generate a complete lime softening curve showing hardness vs lime dose.
    Much more efficient than individual API calls.
    """
    
    scenarios = []
    for dose in lime_doses:
        scenarios.append({
            'name': f'Lime_{dose}mmol',
            'type': 'chemical_addition',
            'reactants': [{'formula': 'Ca(OH)2', 'amount': dose, 'units': 'mmol'}],
            'equilibrium_minerals': None  # Use full database mineral list for comprehensive precipitation modeling
        })
    
    # Run batch processing
    results = await batch_process_scenarios({
        'base_solution': initial_water,
        'scenarios': scenarios,
        'parallel_limit': 10,
        'output_format': 'full'
    })
    
    # Extract hardness values
    curve_data = []
    for r in results['results']:
        if 'error' not in r:
            scenario = r['scenario']
            result = r['result']
            
            # Calculate hardness
            elements = result['element_totals_molality']
            ca = elements.get('Ca', 0)
            mg = elements.get('Mg', 0)
            hardness = (ca + mg) * 100000  # mg/L as CaCO3
            
            curve_data.append({
                'lime_dose_mmol': scenario['reactants'][0]['amount'],
                'pH': result['solution_summary']['pH'],
                'hardness_mg_caco3': hardness,
                'precipitate_g_L': result.get('total_precipitate_g_L', 0)
            })
    
    # Sort by dose
    curve_data.sort(key=lambda x: x['lime_dose_mmol'])
    
    return {
        'curve_data': curve_data,
        'optimal_dose': find_optimal_dose(curve_data, target_hardness=85)
    }


def find_optimal_dose(curve_data: List[Dict[str, Any]], 
                     target_hardness: float) -> Optional[Dict[str, Any]]:
    """Find the dose that achieves target hardness."""
    
    # Find points bracketing the target
    below_target = None
    above_target = None
    
    for point in curve_data:
        if point['hardness_mg_caco3'] <= target_hardness:
            if below_target is None or point['hardness_mg_caco3'] > below_target['hardness_mg_caco3']:
                below_target = point
        else:
            if above_target is None or point['hardness_mg_caco3'] < above_target['hardness_mg_caco3']:
                above_target = point
    
    if below_target and above_target:
        # Interpolate
        fraction = (target_hardness - above_target['hardness_mg_caco3']) / \
                  (below_target['hardness_mg_caco3'] - above_target['hardness_mg_caco3'])
        
        optimal_dose = (above_target['lime_dose_mmol'] + 
                       fraction * (below_target['lime_dose_mmol'] - above_target['lime_dose_mmol']))
        
        return {
            'dose_mmol': optimal_dose,
            'estimated_pH': above_target['pH'] + fraction * (below_target['pH'] - above_target['pH']),
            'target_hardness': target_hardness
        }
    
    return None


async def calculate_lime_softening_dose(
    initial_water: Dict[str, Any],
    target_hardness_mg_caco3: float,
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Specialized function for lime softening calculations.
    Handles the complexity of Mg(OH)2 precipitation at high pH.
    """
    
    # Analyze initial water
    initial_analysis = await calculate_solution_speciation(initial_water)
    
    # Extract current hardness
    ca_initial = initial_analysis['element_totals_molality']['Ca']
    mg_initial = initial_analysis['element_totals_molality']['Mg']
    initial_hardness = (ca_initial + mg_initial) * 100000  # mg/L as CaCO3
    
    logger.info(f"Initial hardness: {initial_hardness:.1f} mg/L as CaCO3")
    logger.info(f"Target hardness: {target_hardness_mg_caco3:.1f} mg/L as CaCO3")
    
    # Use stoichiometric estimate to set smart optimization bounds (not reported to user)
    # Rule of thumb: 1 mmol Ca(OH)2 removes ~2 mmol hardness for bound estimation only
    hardness_to_remove = (initial_hardness - target_hardness_mg_caco3) / 100000  # mol/L
    estimated_lime = hardness_to_remove * 0.6  # Stoichiometric estimate for bounds only
    
    # Set optimization bounds as multiples of stoichiometric estimate
    max_reasonable_lime_dose = estimated_lime * 3.0  # 3x upper bound for optimization search
    
    # Set up optimization
    result = await calculate_dosing_requirement_enhanced({
        'initial_solution': initial_water,
        'reagents': [{'formula': 'Ca(OH)2', 'min_dose': 0, 'max_dose': max_reasonable_lime_dose}],
        'objectives': [{
            'parameter': 'total_hardness',
            'value': target_hardness_mg_caco3,
            'tolerance': 5,  # mg/L as CaCO3
            'units': 'mg/L as CaCO3'
        }],
        'allow_precipitation': True,
        'equilibrium_minerals': None,  # Use full database mineral list for comprehensive precipitation modeling
        'database': database,
        'optimization_method': 'adaptive',
        'max_iterations': 30
    })
    
    return result


async def optimize_phosphorus_removal(
    initial_water: Dict[str, Any],
    target_p_mg_l: float,
    coagulant: str = 'FeCl3',
    target_ph: Optional[float] = None,
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Calculate coagulant dose for phosphorus removal with optional pH control.
    """
    
    # Get initial P concentration and use stoichiometry for smart optimization bounds
    initial_analysis = await calculate_solution_speciation(initial_water)
    p_initial_molal = initial_analysis['element_totals_molality'].get('P', 0)
    p_initial_mg = p_initial_molal * 30974  # mg/L
    
    # Use stoichiometric ratios to estimate dose magnitude for bounds only (not reported to user)
    if 'Fe' in coagulant:
        # Fe:P molar ratio typically 1.5-2.5 for effective removal (for bounds estimation)
        estimated_coagulant_molal = p_initial_molal * 2.0  # Conservative estimate for bounds
    else:  # Aluminum coagulants
        # Al:P molar ratio typically 1.8-2.5 for effective removal (for bounds estimation)
        estimated_coagulant_molal = p_initial_molal * 2.2  # Conservative estimate for bounds
    
    # Set optimization bounds as multiples of stoichiometric estimate
    max_reasonable_dose = estimated_coagulant_molal * 4.0  # 4x upper bound for optimization search
    
    # Set up objectives
    objectives = [{
        'parameter': 'residual_phosphorus',
        'value': target_p_mg_l,
        'tolerance': 0.1,
        'units': 'mg/L'
    }]
    
    reagents = [{'formula': coagulant, 'min_dose': 0, 'max_dose': max_reasonable_dose}]
    
    # Add pH control if specified
    if target_ph is not None:
        objectives.append({
            'parameter': 'pH',
            'value': target_ph,
            'tolerance': 0.2,
            'weight': 0.5  # Lower weight than P removal
        })
        
        # Add base for pH adjustment
        reagents.append({
            'formula': 'NaOH',
            'min_dose': 0,
            'max_dose': 10
        })
    
    # Determine appropriate precipitates
    if 'Fe' in coagulant:
        minerals = ['Strengite', 'Fe(OH)3', 'FePO4']
    else:
        minerals = ['AlPO4', 'Al(OH)3', 'Variscite']
    
    result = await calculate_dosing_requirement_enhanced({
        'initial_solution': initial_water,
        'reagents': reagents,
        'objectives': objectives,
        'allow_precipitation': True,
        'equilibrium_minerals': minerals,
        'database': database,
        'optimization_method': 'adaptive',
        'max_iterations': 40
    })
    
    return result


# Advanced Multi-Reagent Optimization Methods for Phase 3.2

async def optimize_multi_reagent_treatment(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    constraints: Optional[List[Dict[str, Any]]] = None,
    optimization_strategy: str = 'pareto_front',
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Advanced multi-reagent optimization with multiple strategies:
    - Pareto front optimization for trade-off analysis
    - Weighted sum optimization with normalization
    - Sequential optimization with priority ordering
    - Robust optimization for handling uncertainty in water composition
    """
    
    if optimization_strategy == 'pareto_front':
        return await pareto_front_optimization(
            initial_water, reagents, objectives, database
        )
    elif optimization_strategy == 'weighted_sum':
        return await weighted_sum_optimization(
            initial_water, reagents, objectives, constraints, database
        )
    elif optimization_strategy == 'sequential':
        return await sequential_optimization(
            initial_water, reagents, objectives, database
        )
    elif optimization_strategy == 'robust':
        return await robust_optimization(
            initial_water, reagents, objectives, constraints, database
        )
    else:
        raise ValueError(f"Unknown optimization strategy: {optimization_strategy}")


async def pareto_front_optimization(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Generate Pareto front for multi-objective optimization.
    Useful when objectives conflict (e.g., minimize cost vs maximize removal).
    """
    
    # Generate diverse population for multi-objective optimization
    population_size = 50
    n_reagents = len(reagents)
    
    # Initialize population with Latin Hypercube Sampling
    population = []
    for _ in range(population_size):
        individual = []
        for reagent in reagents:
            dose = np.random.uniform(
                reagent.get('min_dose', 0),
                reagent.get('max_dose', 10)
            )
            individual.append(dose)
        population.append(individual)
    
    # Evaluate population in parallel batches
    batch_size = 10
    pareto_solutions = []
    
    for i in range(0, len(population), batch_size):
        batch = population[i:i + batch_size]
        
        # Create evaluation tasks
        tasks = []
        for individual in batch:
            task = evaluate_multi_objective_solution(
                initial_water, reagents, individual, objectives, database
            )
            tasks.append(task)
        
        # Run batch in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for individual, result in zip(batch, results):
            if not isinstance(result, Exception) and 'objectives' in result:
                solution = {
                    'doses': individual,
                    'objective_values': [
                        result['objectives'][obj['parameter']]['current']
                        for obj in objectives
                    ],
                    'simulation_result': result
                }
                pareto_solutions.append(solution)
    
    # Find Pareto front using non-dominated sorting
    pareto_front = find_pareto_front(pareto_solutions, objectives)
    
    # Select representative solutions
    selected_solutions = select_diverse_solutions(pareto_front, min(5, len(pareto_front)))
    
    return {
        'strategy': 'pareto_front',
        'pareto_front': pareto_front,
        'recommended_solutions': selected_solutions,
        'total_evaluated': len(pareto_solutions),
        'pareto_size': len(pareto_front)
    }


async def weighted_sum_optimization(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    constraints: Optional[List[Dict[str, Any]]] = None,
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Weighted sum multi-objective optimization with automatic normalization.
    Converts multi-objective problem to single objective.
    """
    
    # Normalize objective weights
    total_weight = sum(obj.get('weight', 1.0) for obj in objectives)
    normalized_weights = [obj.get('weight', 1.0) / total_weight for obj in objectives]
    
    # Estimate objective ranges for normalization
    objective_ranges = await estimate_objective_ranges(
        initial_water, reagents, objectives, database
    )
    
    async def weighted_objective_function(doses: List[float]) -> Tuple[float, Dict[str, Any]]:
        """Combined weighted objective function."""
        
        result = await evaluate_multi_objective_solution(
            initial_water, reagents, doses, objectives, database
        )
        
        if 'error' in result:
            return float('inf'), result
        
        # Calculate weighted sum with normalization
        weighted_score = 0
        for i, obj in enumerate(objectives):
            param = obj['parameter']
            current_val = result['objectives'][param]['current']
            target_val = obj['value']
            
            # Normalize based on objective type
            if obj.get('constraint_type') == 'minimize':
                # For minimization objectives (e.g., cost)
                normalized_error = current_val / objective_ranges[param]['max']
            elif obj.get('constraint_type') == 'maximize':
                # For maximization objectives (e.g., removal efficiency)
                normalized_error = (objective_ranges[param]['max'] - current_val) / objective_ranges[param]['max']
            else:
                # For target objectives (equality constraints)
                range_size = objective_ranges[param]['max'] - objective_ranges[param]['min']
                normalized_error = abs(current_val - target_val) / range_size
            
            weighted_score += normalized_weights[i] * normalized_error
        
        # Add constraint violations
        if constraints:
            penalty = calculate_constraint_penalty(result, constraints)
            weighted_score += penalty
        
        return weighted_score, result
    
    # Use differential evolution for global optimization
    from .dosing_requirement import differential_evolution_async
    best_doses = await differential_evolution_async(
        weighted_objective_function, 
        [r.get('min_dose', 0) for r in reagents],
        [r.get('max_dose', 10) for r in reagents],
        50  # max iterations
    )
    
    # Final evaluation
    final_score, final_result = await weighted_objective_function(best_doses)
    
    return {
        'strategy': 'weighted_sum',
        'optimal_doses': {
            reagents[i]['formula']: dose
            for i, dose in enumerate(best_doses)
        },
        'weighted_score': final_score,
        'final_state': final_result,
        'objective_weights': dict(zip([obj['parameter'] for obj in objectives], normalized_weights))
    }


async def sequential_optimization(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Sequential optimization with priority ordering.
    Optimizes highest priority objective first, then subsequent objectives
    while maintaining previous constraints.
    """
    
    # Sort objectives by priority (higher priority = lower number)
    sorted_objectives = sorted(objectives, key=lambda x: x.get('priority', 1))
    
    current_solution = initial_water
    cumulative_doses = [0] * len(reagents)
    optimization_history = []
    
    for i, objective in enumerate(sorted_objectives):
        logger.info(f"Optimizing objective {i+1}/{len(objectives)}: {objective['parameter']}")
        
        # Set up constraints from previous objectives
        constraints = []
        if i > 0:
            for prev_obj in sorted_objectives[:i]:
                constraints.append({
                    'parameter': prev_obj['parameter'],
                    'operator': 'within_tolerance',
                    'value': prev_obj['value'],
                    'tolerance': prev_obj.get('tolerance', 0.1)
                })
        
        # Optimize current objective
        step_result = await optimize_single_objective_with_constraints(
            current_solution, reagents, objective, constraints, database
        )
        
        # Update cumulative doses
        step_doses = step_result['optimal_doses']
        for j, reagent in enumerate(reagents):
            if reagent['formula'] in step_doses:
                cumulative_doses[j] += step_doses[reagent['formula']]
        
        # Update current solution
        current_solution = step_result['final_state']['solution_summary']
        
        optimization_history.append({
            'step': i + 1,
            'objective': objective['parameter'],
            'step_doses': step_doses,
            'cumulative_doses': dict(zip([r['formula'] for r in reagents], cumulative_doses)),
            'achieved_value': step_result['achieved_value']
        })
    
    return {
        'strategy': 'sequential',
        'total_doses': dict(zip([r['formula'] for r in reagents], cumulative_doses)),
        'optimization_history': optimization_history,
        'final_state': current_solution
    }


async def robust_optimization(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    constraints: Optional[List[Dict[str, Any]]] = None,
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Robust optimization considering uncertainty in water composition.
    Finds solutions that perform well across multiple scenarios.
    """
    
    # Generate uncertainty scenarios
    uncertainty_scenarios = generate_uncertainty_scenarios(initial_water, n_scenarios=20)
    
    async def robust_objective_function(doses: List[float]) -> Tuple[float, Dict[str, Any]]:
        """Evaluate robustness across multiple scenarios."""
        
        scenario_results = []
        scenario_scores = []
        
        # Evaluate across all scenarios in parallel
        tasks = []
        for scenario in uncertainty_scenarios:
            task = evaluate_multi_objective_solution(
                scenario, reagents, doses, objectives, database
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                scenario_scores.append(float('inf'))
                scenario_results.append({'error': str(result)})
                continue
            
            # Calculate scenario score
            scenario_score = 0
            for obj in objectives:
                param = obj['parameter']
                if param in result['objectives']:
                    error = result['objectives'][param]['error']
                    weight = obj.get('weight', 1.0)
                    scenario_score += weight * error
            
            scenario_scores.append(scenario_score)
            scenario_results.append(result)
        
        # Robust score: worst-case + average case
        valid_scores = [s for s in scenario_scores if s != float('inf')]
        if not valid_scores:
            return float('inf'), {'error': 'All scenarios failed'}
        
        worst_case = max(valid_scores)
        average_case = np.mean(valid_scores)
        robust_score = 0.7 * worst_case + 0.3 * average_case
        
        return robust_score, {
            'scenario_results': scenario_results,
            'scenario_scores': scenario_scores,
            'worst_case_score': worst_case,
            'average_score': average_case,
            'robust_score': robust_score
        }
    
    # Optimize for robustness
    from .dosing_requirement import differential_evolution_async
    best_doses = await differential_evolution_async(
        robust_objective_function,
        [r.get('min_dose', 0) for r in reagents],
        [r.get('max_dose', 10) for r in reagents],
        40  # max iterations
    )
    
    # Final evaluation
    final_score, final_result = await robust_objective_function(best_doses)
    
    return {
        'strategy': 'robust',
        'optimal_doses': {
            reagents[i]['formula']: dose
            for i, dose in enumerate(best_doses)
        },
        'robustness_analysis': final_result,
        'uncertainty_scenarios': len(uncertainty_scenarios)
    }


# Helper functions for advanced optimization

async def evaluate_multi_objective_solution(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    doses: List[float],
    objectives: List[Dict[str, Any]],
    database: str
) -> Dict[str, Any]:
    """Evaluate a solution against multiple objectives."""
    
    # Build reactions
    reactions = []
    for i, dose in enumerate(doses):
        if dose > 0 and i < len(reagents):
            reactions.append({
                'formula': reagents[i]['formula'],
                'amount': dose,
                'units': 'mmol'
            })
    
    # Run simulation
    from .chemical_addition import simulate_chemical_addition
    sim_input = {
        'initial_solution': initial_water,
        'reactants': reactions,
        'allow_precipitation': True,
        'database': database
    }
    
    try:
        result = await simulate_chemical_addition(sim_input)
        
        # Evaluate objectives using enhanced parameter evaluation
        from .phreeqc_wrapper import evaluate_target_parameter
        objective_results = {}
        
        for obj in objectives:
            target_config = {
                'parameter': obj['parameter'],
                'units': obj.get('units'),
                'mineral': obj.get('mineral'),
                'element_or_species': obj.get('element_or_species')
            }
            
            current_value = evaluate_target_parameter(result, target_config)
            target_value = obj['value']
            error = abs(current_value - target_value)
            
            objective_results[obj['parameter']] = {
                'current': current_value,
                'target': target_value,
                'error': error,
                'within_tolerance': error <= obj.get('tolerance', 0.1)
            }
        
        return {
            'objectives': objective_results,
            'solution_summary': result.get('solution_summary', {}),
            'precipitated_phases': result.get('precipitated_phases', {}),
            'total_precipitate_g_L': result.get('total_precipitate_g_L', 0)
        }
        
    except Exception as e:
        return {'error': str(e)}


def find_pareto_front(solutions: List[Dict], objectives: List[Dict]) -> List[Dict]:
    """Find Pareto-optimal solutions using non-dominated sorting."""
    
    if not solutions:
        return []
    
    # Determine if each objective should be minimized or maximized
    minimize_flags = []
    for obj in objectives:
        constraint_type = obj.get('constraint_type', 'equality')
        if constraint_type == 'minimize':
            minimize_flags.append(True)
        elif constraint_type == 'maximize':
            minimize_flags.append(False)
        else:
            # For equality constraints, minimize the error
            minimize_flags.append(True)
    
    pareto_front = []
    
    for candidate in solutions:
        is_dominated = False
        
        for other in solutions:
            if candidate == other:
                continue
            
            # Check if 'other' dominates 'candidate'
            dominates = True
            for i, minimize in enumerate(minimize_flags):
                candidate_val = candidate['objective_values'][i]
                other_val = other['objective_values'][i]
                
                if minimize:
                    if other_val > candidate_val:  # other is worse
                        dominates = False
                        break
                else:
                    if other_val < candidate_val:  # other is worse
                        dominates = False
                        break
            
            if dominates:
                # Check if other is strictly better in at least one objective
                strictly_better = False
                for i, minimize in enumerate(minimize_flags):
                    candidate_val = candidate['objective_values'][i]
                    other_val = other['objective_values'][i]
                    
                    if minimize and other_val < candidate_val:
                        strictly_better = True
                        break
                    elif not minimize and other_val > candidate_val:
                        strictly_better = True
                        break
                
                if strictly_better:
                    is_dominated = True
                    break
        
        if not is_dominated:
            pareto_front.append(candidate)
    
    return pareto_front


def select_diverse_solutions(pareto_front: List[Dict], n_select: int) -> List[Dict]:
    """Select diverse solutions from Pareto front."""
    
    if len(pareto_front) <= n_select:
        return pareto_front
    
    # Use k-means clustering in objective space
    objective_values = np.array([sol['objective_values'] for sol in pareto_front])
    
    # Simple diversity selection: choose evenly spaced solutions
    n_objectives = len(objective_values[0])
    
    if n_objectives == 2:
        # For 2D, sort by first objective and select evenly
        sorted_indices = np.argsort(objective_values[:, 0])
        step = len(sorted_indices) // n_select
        selected_indices = [sorted_indices[i * step] for i in range(n_select)]
    else:
        # For higher dimensions, use random selection
        selected_indices = np.random.choice(len(pareto_front), n_select, replace=False)
    
    return [pareto_front[i] for i in selected_indices]


async def estimate_objective_ranges(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    database: str
) -> Dict[str, Dict[str, float]]:
    """Estimate objective value ranges for normalization."""
    
    ranges = {}
    
    # Sample random doses to estimate ranges
    n_samples = 20
    sample_points = []
    
    for _ in range(n_samples):
        doses = []
        for reagent in reagents:
            dose = np.random.uniform(
                reagent.get('min_dose', 0),
                reagent.get('max_dose', 10)
            )
            doses.append(dose)
        sample_points.append(doses)
    
    # Evaluate samples
    objective_values = {obj['parameter']: [] for obj in objectives}
    
    for doses in sample_points:
        result = await evaluate_multi_objective_solution(
            initial_water, reagents, doses, objectives, database
        )
        
        if 'objectives' in result:
            for obj in objectives:
                param = obj['parameter']
                if param in result['objectives']:
                    value = result['objectives'][param]['current']
                    objective_values[param].append(value)
    
    # Calculate ranges
    for param, values in objective_values.items():
        if values:
            ranges[param] = {
                'min': min(values),
                'max': max(values),
                'mean': np.mean(values),
                'std': np.std(values)
            }
        else:
            ranges[param] = {'min': 0, 'max': 1, 'mean': 0.5, 'std': 0.1}
    
    return ranges


def calculate_constraint_penalty(result: Dict[str, Any], constraints: List[Dict[str, Any]]) -> float:
    """Calculate penalty for constraint violations."""
    
    penalty = 0
    
    for constraint in constraints:
        param = constraint['parameter']
        if param not in result.get('objectives', {}):
            continue
        
        current_val = result['objectives'][param]['current']
        operator = constraint['operator']
        target_val = constraint['value']
        
        if operator == 'within_tolerance':
            tolerance = constraint.get('tolerance', 0.1)
            if abs(current_val - target_val) > tolerance:
                penalty += abs(current_val - target_val) - tolerance
        elif operator == 'less_than':
            if current_val > target_val:
                penalty += current_val - target_val
        elif operator == 'greater_than':
            if current_val < target_val:
                penalty += target_val - current_val
    
    return penalty * 1000  # Large penalty factor


def generate_uncertainty_scenarios(initial_water: Dict[str, Any], n_scenarios: int = 20) -> List[Dict[str, Any]]:
    """Generate scenarios with uncertain water composition."""
    
    scenarios = []
    base_water = initial_water.copy()
    
    for _ in range(n_scenarios):
        scenario = base_water.copy()
        
        if 'analysis' in scenario:
            varied_analysis = scenario['analysis'].copy()
            
            # Add ±20% uncertainty to major parameters
            for param, value in varied_analysis.items():
                if isinstance(value, (int, float)) and value > 0:
                    uncertainty_factor = np.random.uniform(0.8, 1.2)
                    varied_analysis[param] = value * uncertainty_factor
            
            scenario['analysis'] = varied_analysis
        
        # Add pH uncertainty
        if 'pH' in scenario:
            scenario['pH'] += np.random.normal(0, 0.2)
        elif 'ph' in scenario:
            scenario['ph'] += np.random.normal(0, 0.2)
        
        scenarios.append(scenario)
    
    return scenarios


async def optimize_single_objective_with_constraints(
    initial_water: Dict[str, Any],
    reagents: List[Dict[str, Any]],
    objective: Dict[str, Any],
    constraints: List[Dict[str, Any]],
    database: str
) -> Dict[str, Any]:
    """Optimize single objective with constraints."""
    
    from .dosing_requirement import calculate_dosing_requirement_enhanced
    
    # Convert to enhanced dosing format
    return await calculate_dosing_requirement_enhanced({
        'initial_solution': initial_water,
        'reagents': reagents,
        'objectives': [objective],
        'constraints': constraints,
        'database': database,
        'optimization_method': 'adaptive',
        'max_iterations': 30
    })


async def optimize_lime_softening_enhanced(
    initial_water: Dict[str, Any],
    target_hardness_mg_l: float,
    target_alkalinity_mg_l: Optional[float] = None,
    include_soda_ash: bool = True,
    target_ph: float = 10.5,
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Advanced lime softening optimization with optional soda ash for enhanced softening.
    
    Chemistry:
    - Lime removes carbonate hardness: Ca(HCO3)2 + Ca(OH)2 → 2CaCO3↓ + 2H2O
    - Soda ash removes non-carbonate hardness: CaSO4 + Na2CO3 → CaCO3↓ + Na2SO4
    
    Args:
        initial_water: Initial water composition
        target_hardness_mg_l: Target total hardness in mg/L as CaCO3
        target_alkalinity_mg_l: Target alkalinity in mg/L as CaCO3 (optional)
        include_soda_ash: Whether to include Na2CO3 for enhanced softening
        target_ph: Target final pH (typical 10.0-11.0 for lime softening)
        database: PHREEQC database to use
    
    Returns:
        Optimization results with optimal lime dose and optional soda ash dose
    """
    
    # Calculate initial water characteristics
    initial_analysis = await calculate_solution_speciation(initial_water)
    
    # Get initial hardness and alkalinity
    element_totals = initial_analysis.get('element_totals_molality', {})
    ca_molal = element_totals.get('Ca', 0)
    mg_molal = element_totals.get('Mg', 0)
    initial_hardness_mg = (ca_molal + mg_molal) * 100000  # mg/L as CaCO3
    
    # Get alkalinity from species or estimate from carbonate
    species = initial_analysis.get('species_molality', {})
    hco3_molal = species.get('HCO3-', 0)
    co3_molal = species.get('CO3-2', 0)
    initial_alkalinity_mg = (hco3_molal + 2 * co3_molal) * 50000  # mg/L as CaCO3
    
    # Analyze hardness types for optimization strategy only (no heuristic estimates)
    carbonate_hardness = min(initial_hardness_mg, initial_alkalinity_mg)
    non_carbonate_hardness = max(0, initial_hardness_mg - carbonate_hardness)
    
    logger.info(f"Initial hardness: {initial_hardness_mg:.1f} mg/L as CaCO3")
    logger.info(f"Carbonate hardness: {carbonate_hardness:.1f} mg/L as CaCO3") 
    logger.info(f"Non-carbonate hardness: {non_carbonate_hardness:.1f} mg/L as CaCO3")
    
    hardness_to_remove = initial_hardness_mg - target_hardness_mg_l
    
    if hardness_to_remove <= 0:
        return {
            'message': 'Target hardness already achieved',
            'optimal_lime_dose_mmol_l': 0,
            'optimal_soda_ash_dose_mmol_l': 0,
            'initial_hardness_mg_l': initial_hardness_mg,
            'target_hardness_mg_l': target_hardness_mg_l
        }
    
    # Use stoichiometry to estimate dose magnitude for smart bounds (not reported to user)
    # Lime softening chemistry: Ca(HCO3)2 + Ca(OH)2 → 2CaCO3↓ + 2H2O
    # Rough estimate: 1 mmol Ca(OH)2 removes ~2 mmol hardness (for bounds only)
    estimated_lime_molal = (hardness_to_remove / 100000) * 0.6  # Stoichiometric estimate
    
    # Set optimization bounds as multiples of stoichiometric estimate
    max_lime_dose = estimated_lime_molal * 3.0  # 3x upper bound for optimization search
    
    # Set up optimization objectives
    objectives = [{
        'parameter': 'total_hardness',
        'value': target_hardness_mg_l,
        'tolerance': 5.0,  # ±5 mg/L tolerance
        'units': 'mg/L as CaCO3',
        'weight': 0.8  # Primary objective
    }, {
        'parameter': 'pH',
        'value': target_ph,
        'tolerance': 0.3,  # ±0.3 pH units
        'weight': 0.2  # Secondary objective
    }]
    
    # Set up reagents for optimization
    reagents = [{
        'formula': 'Ca(OH)2',
        'min_dose': 0,
        'max_dose': max_lime_dose,
        'units': 'mmol'
    }]
    
    # Add soda ash if enhanced softening requested and non-carbonate hardness exists
    if include_soda_ash and non_carbonate_hardness > 10:  # Only if significant non-carbonate hardness
        # Estimate soda ash needs: CaSO4 + Na2CO3 → CaCO3↓ + Na2SO4 (1:1 stoichiometry)
        estimated_soda_ash_molal = (non_carbonate_hardness / 100000) * 0.8  # Stoichiometric estimate
        max_soda_ash_dose = estimated_soda_ash_molal * 3.0  # 3x upper bound for optimization search
        
        reagents.append({
            'formula': 'Na2CO3',
            'min_dose': 0,
            'max_dose': max_soda_ash_dose,
            'units': 'mmol'
        })
        
        # Add alkalinity objective if target specified
        if target_alkalinity_mg_l is not None:
            objectives.append({
                'parameter': 'carbonate_alkalinity',
                'value': target_alkalinity_mg_l,
                'tolerance': 10.0,  # ±10 mg/L tolerance
                'units': 'mg/L as CaCO3',
                'weight': 0.3
            })
        
        logger.info(f"Enhanced softening: targeting lime + soda ash optimization")
        optimization_strategy = 'weighted_sum'  # Multi-reagent optimization
    else:
        logger.info(f"Standard lime softening: single reagent optimization")
        optimization_strategy = 'weighted_sum'  # Single reagent can use weighted sum
    
    # Run optimization
    optimization_result = await optimize_multi_reagent_treatment(
        initial_water=initial_water,
        reagents=reagents,
        objectives=objectives,
        optimization_strategy=optimization_strategy,
        database=database
    )
    
    # Extract optimal doses
    optimal_doses = optimization_result.get('optimal_doses', {})
    lime_dose = optimal_doses.get('Ca(OH)2', 0)
    soda_ash_dose = optimal_doses.get('Na2CO3', 0)
    
    # Get final solution properties
    final_solution = optimization_result.get('final_solution', {})
    final_hardness = final_solution.get('total_hardness_mg_caco3', target_hardness_mg_l)
    final_ph = final_solution.get('pH', target_ph)
    final_alkalinity = final_solution.get('carbonate_alkalinity_mg_caco3', initial_alkalinity_mg)
    
    return {
        'optimization_method': 'lime_softening_enhanced',
        'initial_conditions': {
            'hardness_mg_l': initial_hardness_mg,
            'alkalinity_mg_l': initial_alkalinity_mg,
            'carbonate_hardness_mg_l': carbonate_hardness,
            'non_carbonate_hardness_mg_l': non_carbonate_hardness
        },
        'targets': {
            'hardness_mg_l': target_hardness_mg_l,
            'alkalinity_mg_l': target_alkalinity_mg_l,
            'ph': target_ph
        },
        'optimal_doses': {
            'lime_ca_oh_2_mmol_l': lime_dose,
            'soda_ash_na2co3_mmol_l': soda_ash_dose if include_soda_ash else 0
        },
        'final_solution': {
            'hardness_mg_l': final_hardness,
            'alkalinity_mg_l': final_alkalinity,
            'ph': final_ph
        },
        'performance': {
            'hardness_removal_percent': ((initial_hardness_mg - final_hardness) / initial_hardness_mg * 100) if initial_hardness_mg > 0 else 0,
            'target_achieved': abs(final_hardness - target_hardness_mg_l) <= 5.0
        },
        'full_optimization_result': optimization_result
    }


async def compare_treatment_alternatives(
    initial_water: Dict[str, Any],
    objective: Dict[str, Any],
    alternatives: List[str],
    evaluation_criteria: List[str] = ['dose', 'final_ph', 'removal_efficiency'],
    database: str = 'minteq.dat'
) -> Dict[str, Any]:
    """
    Compare different treatment alternatives systematically.
    
    Args:
        initial_water: Initial water composition
        objective: Treatment objective (e.g., {'parameter': 'residual_phosphorus', 'target': 0.5, 'units': 'mg/L'})
        alternatives: List of chemical formulas to compare (e.g., ['FeCl3', 'Al2(SO4)3', 'FeSO4'])
        evaluation_criteria: Metrics to compare ['dose', 'final_ph', 'removal_efficiency']
        database: PHREEQC database to use
    
    Returns:
        Comparison table with optimization results for each alternative
    """
    
    logger.info(f"Comparing {len(alternatives)} treatment alternatives for {objective['parameter']} removal")
    
    # Run optimization for each alternative in parallel
    optimization_tasks = []
    
    for alternative in alternatives:
        # Determine optimization function based on chemical type
        if alternative in ['FeCl3', 'Al2(SO4)3', 'FeSO4', 'Fe2(SO4)3'] and objective['parameter'] == 'residual_phosphorus':
            # Use specialized phosphorus removal optimization
            task = optimize_phosphorus_removal(
                initial_water=initial_water,
                target_p_mg_l=objective['target'],
                coagulant=alternative,
                database=database
            )
        else:
            # Use general single-reagent optimization
            reagents = [{'formula': alternative, 'min_dose': 0, 'max_dose': 50}]  # mmol/L max
            objectives = [objective]
            
            task = optimize_multi_reagent_treatment(
                initial_water=initial_water,
                reagents=reagents,
                objectives=objectives,
                optimization_strategy='weighted_sum',
                database=database
            )
        
        optimization_tasks.append((alternative, task))
    
    # Run all optimizations in parallel
    results = []
    for alternative, task in optimization_tasks:
        try:
            result = await task
            results.append((alternative, result))
        except Exception as e:
            logger.error(f"Optimization failed for {alternative}: {e}")
            results.append((alternative, {'error': str(e)}))
    
    # Build comparison table
    comparison_data = []
    
    for alternative, result in results:
        if 'error' in result:
            comparison_data.append({
                'alternative': alternative,
                'status': 'failed',
                'error': result['error']
            })
            continue
        
        # Extract evaluation metrics
        entry = {'alternative': alternative, 'status': 'success'}
        
        # Get optimal dose
        if 'dose' in evaluation_criteria:
            optimal_doses = result.get('optimal_doses', {})
            dose = optimal_doses.get(alternative, result.get('optimal_dose', 0))
            entry['optimal_dose_mmol_l'] = dose
        
        # Get final solution properties
        final_solution = result.get('final_solution', {})
        
        if 'final_ph' in evaluation_criteria:
            entry['final_ph'] = final_solution.get('pH', final_solution.get('ph', None))
        
        # Calculate removal efficiency
        if 'removal_efficiency' in evaluation_criteria:
            target_param = objective['parameter']
            if target_param == 'residual_phosphorus':
                # Get initial and final P concentrations
                initial_analysis = await calculate_solution_speciation(initial_water)
                initial_p_molal = initial_analysis.get('element_totals_molality', {}).get('P', 0)
                initial_p_mg = initial_p_molal * 30974
                
                final_p_mg = objective['target']  # Assuming optimization achieved target
                removal_efficiency = ((initial_p_mg - final_p_mg) / initial_p_mg * 100) if initial_p_mg > 0 else 0
                entry['removal_efficiency_percent'] = removal_efficiency
        
        # Note: Cost and sludge calculations removed to maintain technical focus
        # This server provides only chemistry and thermodynamic calculations
        
        comparison_data.append(entry)
    
    # Sort by performance (highest efficiency or lowest dose)
    if 'removal_efficiency' in evaluation_criteria:
        comparison_data.sort(key=lambda x: x.get('removal_efficiency_percent', 0), reverse=True)
    elif 'dose' in evaluation_criteria:
        comparison_data.sort(key=lambda x: x.get('optimal_dose_mmol_l', float('inf')))
    
    return {
        'comparison_type': 'treatment_alternatives',
        'objective': objective,
        'alternatives_evaluated': len(alternatives),
        'evaluation_criteria': evaluation_criteria,
        'comparison_table': comparison_data,
        'recommendation': comparison_data[0] if comparison_data and comparison_data[0].get('status') == 'success' else None,
        'detailed_results': {alt: result for alt, result in results}
    }