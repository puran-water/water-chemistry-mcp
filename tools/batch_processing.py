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
import logging
import numpy as np

from .chemical_addition import simulate_chemical_addition
from .solution_speciation import calculate_solution_speciation

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
        # Use parameter sweep for dose optimization instead of broken tool
        logger.warning("dose_optimization type deprecated - use parameter_sweep instead")
        
        # Convert to parameter sweep if possible
        config = scenario.get('optimization_config', {})
        if 'reagent' in config and 'target_condition' in config:
            # Create dose sweep around estimated range
            doses = np.linspace(0.5, 5.0, 10)  # Default dose range
            
            sweep_results = []
            for dose in doses:
                try:
                    result = await simulate_chemical_addition({
                        'initial_solution': base_solution,
                        'reactants': [{'formula': config['reagent']['formula'], 'amount': dose, 'units': 'mmol'}],
                        'allow_precipitation': True,
                        'database': base_solution.get('database', 'minteq.dat')
                    })
                    sweep_results.append({'dose': dose, 'result': result})
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")
            
            return {'dose_sweep_results': sweep_results}
        else:
            raise ValueError("dose_optimization requires 'reagent' and 'target_condition' in optimization_config")
    
    elif scenario_type == 'parameter_sweep':
        # Sweep a parameter range
        parameter = scenario['parameter']
        # Normalize temperature alias
        if parameter in ['temperature', 'temp', 'Temperature', 'Temp']:
            parameter = 'temperature_celsius'
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

    elif scenario_type in ('ph_sweep', 'temperature_sweep', 'dose_response'):
        # Map common scenario types to supported behavior
        if scenario_type == 'ph_sweep':
            parameter = 'pH'
            values = scenario.get('values')
            if values is None and all(k in scenario for k in ('start', 'stop', 'step')):
                try:
                    import numpy as np
                    values = list(np.arange(scenario['start'], scenario['stop'] + 1e-9, scenario['step']))
                except Exception:
                    values = [scenario['start'], scenario['stop']]
            if values is None:
                raise ValueError("ph_sweep requires 'values' or 'start'/'stop'/'step'")
            sweep_results = []
            for value in values:
                modified_solution = dict(base_solution)
                modified_solution[parameter] = value
                result = await calculate_solution_speciation(modified_solution)
                sweep_results.append({parameter: value, 'result': result})
            return {'sweep_results': sweep_results}
        elif scenario_type == 'temperature_sweep':
            parameter = 'temperature_celsius'
            values = scenario.get('values')
            if values is None and all(k in scenario for k in ('start', 'stop', 'step')):
                try:
                    import numpy as np
                    values = list(np.arange(scenario['start'], scenario['stop'] + 1e-9, scenario['step']))
                except Exception:
                    values = [scenario['start'], scenario['stop']]
            if values is None:
                raise ValueError("temperature_sweep requires 'values' or 'start'/'stop'/'step'")
            sweep_results = []
            for value in values:
                modified_solution = dict(base_solution)
                modified_solution[parameter] = value
                result = await calculate_solution_speciation(modified_solution)
                sweep_results.append({parameter: value, 'result': result})
            return {'sweep_results': sweep_results}
        else:
            # dose_response
            reagent = scenario.get('reagent') or scenario.get('reactant')
            if not reagent or 'formula' not in reagent:
                raise ValueError("dose_response requires 'reagent': {'formula': ...}")
            doses = scenario.get('doses') or scenario.get('values') or []
            units = scenario.get('units', 'mmol')
            sweep_results = []
            for dose in doses:
                try:
                    result = await simulate_chemical_addition({
                        'initial_solution': base_solution,
                        'reactants': [{'formula': reagent['formula'], 'amount': dose, 'units': units}],
                        'allow_precipitation': scenario.get('allow_precipitation', True),
                        'equilibrium_minerals': scenario.get('equilibrium_minerals'),
                        'database': scenario.get('database', base_solution.get('database', 'minteq.dat'))
                    })
                    sweep_results.append({'dose': dose, 'result': result})
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")
            return {'dose_response_results': sweep_results}
    
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
        # Simplified multi-reagent optimization using parameter sweeps
        logger.warning("multi_reagent_optimization simplified - use parameter_sweeps instead")
        
        reagents = scenario.get('reagents', [])
        objectives = scenario.get('objectives', [])
        
        if len(reagents) <= 2:
            # Simple grid search for 1-2 reagents
            sweep_results = []
            
            # Create dose ranges for each reagent
            dose_ranges = []
            for reagent in reagents:
                min_dose = reagent.get('min_dose', 0.1)
                max_dose = reagent.get('max_dose', 5.0)
                dose_ranges.append(np.linspace(min_dose, max_dose, 5))
            
            # Grid search
            if len(reagents) == 1:
                for dose1 in dose_ranges[0]:
                    try:
                        result = await simulate_chemical_addition({
                            'initial_solution': base_solution,
                            'reactants': [{'formula': reagents[0]['formula'], 'amount': dose1, 'units': 'mmol'}],
                            'allow_precipitation': True,
                            'database': base_solution.get('database', 'minteq.dat')
                        })
                        sweep_results.append({'doses': [dose1], 'result': result})
                    except Exception as e:
                        logger.error(f"Multi-reagent dose failed: {e}")
            elif len(reagents) == 2:
                for dose1 in dose_ranges[0]:
                    for dose2 in dose_ranges[1]:
                        try:
                            result = await simulate_chemical_addition({
                                'initial_solution': base_solution,
                                'reactants': [
                                    {'formula': reagents[0]['formula'], 'amount': dose1, 'units': 'mmol'},
                                    {'formula': reagents[1]['formula'], 'amount': dose2, 'units': 'mmol'}
                                ],
                                'allow_precipitation': True,
                                'database': base_solution.get('database', 'minteq.dat')
                            })
                            sweep_results.append({'doses': [dose1, dose2], 'result': result})
                        except Exception as e:
                            logger.error(f"Multi-reagent dose failed: {e}")
            
            return {'multi_reagent_sweep_results': sweep_results}
        else:
            raise ValueError("Simplified multi-reagent optimization supports max 2 reagents")
    
    elif scenario_type == 'lime_softening_optimization':
        # Use existing lime softening function
        return await calculate_lime_softening_dose(
            initial_water=base_solution,
            target_hardness_mg_caco3=scenario['target_hardness_mg_l'],
            database=scenario.get('database', 'minteq.dat')
        )
    
    elif scenario_type == 'alternative_comparison':
        # Simplified alternative comparison
        logger.warning("alternative_comparison simplified - use multiple scenarios instead")
        
        alternatives = scenario.get('alternatives', [])
        comparison_results = []
        
        for alt in alternatives:
            try:
                if alt.get('type') == 'chemical_addition':
                    result = await simulate_chemical_addition({
                        'initial_solution': base_solution,
                        'reactants': alt.get('reactants', []),
                        'allow_precipitation': True,
                        'database': base_solution.get('database', 'minteq.dat')
                    })
                    comparison_results.append({
                        'alternative': alt.get('name', 'unnamed'),
                        'result': result
                    })
            except Exception as e:
                logger.error(f"Alternative {alt.get('name', 'unnamed')} failed: {e}")
        
        return {'alternative_comparison_results': comparison_results}
    
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
        # Use parameter sweep for dose optimization instead of broken tool
        logger.warning("dose_optimization step deprecated - use parameter_sweep instead")
        
        # Create a simple dose sweep
        config = step.get('optimization_config', {})
        if 'reagent' in config:
            doses = np.linspace(0.5, 3.0, 6)  # Simple dose range
            best_result = None
            
            for dose in doses:
                try:
                    result = await simulate_chemical_addition({
                        'initial_solution': current_solution,
                        'reactants': [{'formula': config['reagent']['formula'], 'amount': dose, 'units': 'mmol'}],
                        'allow_precipitation': True,
                        'database': current_solution.get('database', 'minteq.dat')
                    })
                    if best_result is None:
                        best_result = result
                except Exception as e:
                    logger.error(f"Dose {dose} failed: {e}")
            
            return best_result if best_result else await calculate_solution_speciation(current_solution)
        else:
            return await calculate_solution_speciation(current_solution)
    
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
    
    # Use parameter sweep for dose optimization (replaces broken enhanced tool)
    logger.info(f"Using parameter sweep with estimated max dose: {max_reasonable_lime_dose:.3f} mmol/L")
    
    # Create dose sweep around estimated range
    doses = np.linspace(0.1, max_reasonable_lime_dose, 15)
    best_dose = None
    best_result = None
    best_hardness_diff = float('inf')
    
    for dose in doses:
        try:
            result = await simulate_chemical_addition({
                'initial_solution': initial_water,
                'reactants': [{'formula': 'Ca(OH)2', 'amount': dose, 'units': 'mmol'}],
                'allow_precipitation': True,
                'database': database
            })
            
            # Calculate hardness from result
            ca_final = result['element_totals_molality']['Ca']
            mg_final = result['element_totals_molality']['Mg']
            final_hardness = (ca_final + mg_final) * 100000  # mg/L as CaCO3
            
            # Check if this is closer to target
            hardness_diff = abs(final_hardness - target_hardness_mg_caco3)
            if hardness_diff < best_hardness_diff:
                best_hardness_diff = hardness_diff
                best_dose = dose
                best_result = result
                
            logger.debug(f"Dose {dose:.3f} mmol: hardness {final_hardness:.1f} mg/L CaCO3")
            
        except Exception as e:
            logger.error(f"Dose {dose:.3f} failed: {e}")
    
    if best_result:
        # Add optimization details to result
        best_result['optimization_summary'] = {
            'optimal_dose_mmol': best_dose,
            'target_hardness_mg_caco3': target_hardness_mg_caco3,
            'achieved_hardness_mg_caco3': (best_result['element_totals_molality']['Ca'] + 
                                         best_result['element_totals_molality']['Mg']) * 100000,
            'hardness_removal_efficiency': ((initial_hardness - 
                                           (best_result['element_totals_molality']['Ca'] + 
                                            best_result['element_totals_molality']['Mg']) * 100000) / 
                                          (initial_hardness - target_hardness_mg_caco3)) * 100
        }
        logger.info(f"Optimal lime dose: {best_dose:.3f} mmol/L")
        return best_result
    else:
        raise ValueError("All doses failed - check water chemistry and database")


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
    # Safely extract initial dissolved P from PHREEQC results
    p_initial_molal = 0.0
    try:
        p_initial_molal = float(initial_analysis.get('element_totals_molality', {}).get('P', 0.0) or 0.0)
    except Exception:
        p_initial_molal = 0.0
    p_initial_mg = p_initial_molal * 30974  # mg/L as P

    # Fallback: infer initial P directly from input if PHREEQC reports ~0
    p_input_mg = None
    if p_initial_mg <= 0:
        try:
            analysis = (initial_water or {}).get('analysis', {}) or {}
            # Try common phosphorus keys (case-insensitive)
            p_keys = ['P', 'p', 'phosphorus', 'phosphate', 'PO4', 'P(5)']
            found_key = None
            for k in analysis.keys():
                if k in p_keys or k.lower() in [kk.lower() for kk in p_keys]:
                    found_key = k
                    break
            if found_key is not None:
                val = analysis[found_key]
                # Support numeric or dict with value
                if isinstance(val, (int, float)):
                    base_units = (initial_water or {}).get('units', 'mg/L').lower()
                    if base_units in ('mg/l', 'mg\u002fl', 'ppm'):
                        p_input_mg = float(val)
                    elif base_units in ('ug/l', 'µg/l', 'ug\u002fl'):
                        p_input_mg = float(val) / 1000.0
                    elif base_units in ('mmol/l', 'mmol\u002fl'):
                        p_input_mg = float(val) * 30.974
                    elif base_units in ('mol/l', 'mol\u002fl'):
                        p_input_mg = float(val) * 30974.0
                    else:
                        # Assume mg/L if unknown
                        p_input_mg = float(val)
                elif isinstance(val, dict) and 'value' in val:
                    p_input_mg = float(val['value'])
        except Exception:
            # If anything goes wrong, leave p_input_mg as None
            p_input_mg = None
    
    # Use stoichiometric ratios to estimate dose magnitude for bounds only (not reported to user)
    if 'Fe' in coagulant:
        # Fe:P molar ratio typically 1.5-2.5 for effective removal (for bounds estimation)
        estimated_coagulant_molal = p_initial_molal * 2.0  # Conservative estimate for bounds
    else:  # Aluminum coagulants
        # Al:P molar ratio typically 1.8-2.5 for effective removal (for bounds estimation)
        estimated_coagulant_molal = p_initial_molal * 2.2  # Conservative estimate for bounds
    
    # Set optimization bounds as multiples of stoichiometric estimate
    max_reasonable_dose = estimated_coagulant_molal * 4.0  # 4x upper bound for optimization search
    # Ensure we have a sane positive upper bound even if initial P parsed as zero
    if max_reasonable_dose <= 0:
        max_reasonable_dose = 5.0  # fallback sweep upper bound (mmol/L)
    
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
    
    # Use parameter sweep for phosphorus removal optimization (replaces broken enhanced tool)
    logger.info(f"Using parameter sweep for P removal with estimated max dose: {max_reasonable_dose:.3f} mmol/L")

    # Create coagulant dose sweep
    coagulant_doses = np.linspace(0.1, max_reasonable_dose, 12)

    # Track iterations for debugging/inspection
    optimization_path: List[Dict[str, Any]] = []
    
    if target_ph is not None:
        # Grid search for coagulant + NaOH
        naoh_doses = np.linspace(0.5, 8.0, 8)
        best_result = None
        best_p_diff = float('inf')
        best_ph_diff = float('inf')
        best_coag_dose = None
        best_naoh_dose = None
        
        for coag_dose in coagulant_doses:
            for naoh_dose in naoh_doses:
                try:
                    result = await simulate_chemical_addition({
                        'initial_solution': initial_water,
                        'reactants': [
                            {'formula': coagulant, 'amount': coag_dose, 'units': 'mmol'},
                            {'formula': 'NaOH', 'amount': naoh_dose, 'units': 'mmol'}
                        ],
                        'allow_precipitation': True,
                        'equilibrium_minerals': minerals,
                        'database': database
                    })
                    
                    # Skip errored results
                    if isinstance(result, dict) and result.get('error'):
                        logger.error(f"Dose combination produced error: {result.get('error')}")
                        optimization_path.append({
                            'coagulant_mmol': coag_dose,
                            'naoh_mmol': naoh_dose,
                            'error': result.get('error')
                        })
                        continue

                    # Calculate phosphorus and pH
                    p_final_molal = result['element_totals_molality'].get('P', 0)
                    p_final_mg = p_final_molal * 30974  # mg/L
                    final_ph = result.get('solution_summary', {}).get('pH')
                    # Handle missing pH gracefully
                    if final_ph is None:
                        final_ph = float('inf')
                    
                    # Check if this is better
                    p_diff = abs(p_final_mg - target_p_mg_l)
                    ph_diff = abs(final_ph - target_ph) if (target_ph is not None and final_ph != float('inf')) else 0
                    
                    # Combined score (prioritize P removal)
                    if p_diff < best_p_diff or (p_diff <= best_p_diff * 1.2 and ph_diff < best_ph_diff):
                        best_p_diff = p_diff
                        best_ph_diff = ph_diff
                        best_result = result
                        best_coag_dose = coag_dose
                        best_naoh_dose = naoh_dose
                        
                    try:
                        logger.debug(f"Coag {coag_dose:.2f}, NaOH {naoh_dose:.2f}: P={p_final_mg:.3f}, pH={float(final_ph):.2f}")
                    except Exception:
                        logger.debug(f"Coag {coag_dose:.2f}, NaOH {naoh_dose:.2f}: P={p_final_mg:.3f}, pH={final_ph}")
                    # Record iteration
                    optimization_path.append({
                        'coagulant_mmol': coag_dose,
                        'naoh_mmol': naoh_dose,
                        'p_mg_l': p_final_mg,
                        'pH': None if final_ph == float('inf') else final_ph
                    })
                    
                except Exception as e:
                    logger.error(f"Dose combination failed: {e}")
                    optimization_path.append({
                        'coagulant_mmol': coag_dose,
                        'naoh_mmol': naoh_dose,
                        'error': str(e)
                    })
    else:
        # Single coagulant optimization
        best_result = None
        best_p_diff = float('inf')
        best_coag_dose = None
        
        for coag_dose in coagulant_doses:
            try:
                result = await simulate_chemical_addition({
                    'initial_solution': initial_water,
                    'reactants': [{'formula': coagulant, 'amount': coag_dose, 'units': 'mmol'}],
                    'allow_precipitation': True,
                    'equilibrium_minerals': minerals,
                    'database': database
                })
                
                if isinstance(result, dict) and result.get('error'):
                    logger.error(f"Dose {coag_dose:.2f} produced error: {result.get('error')}")
                    optimization_path.append({
                        'coagulant_mmol': coag_dose,
                        'error': result.get('error')
                    })
                    continue

                # Calculate phosphorus
                p_final_molal = result['element_totals_molality'].get('P', 0)
                p_final_mg = p_final_molal * 30974  # mg/L
                
                # Check if this is better
                p_diff = abs(p_final_mg - target_p_mg_l)
                if p_diff < best_p_diff:
                    best_p_diff = p_diff
                    best_result = result
                    best_coag_dose = coag_dose
                    
                logger.debug(f"Coag {coag_dose:.2f}: P={p_final_mg:.3f} mg/L")
                optimization_path.append({
                    'coagulant_mmol': coag_dose,
                    'p_mg_l': p_final_mg
                })
                
            except Exception as e:
                logger.error(f"Dose {coag_dose:.2f} failed: {e}")
                optimization_path.append({
                    'coagulant_mmol': coag_dose,
                    'error': str(e)
                })
    
    if best_result:
        # Add optimization summary
        achieved_p_mg = best_result['element_totals_molality'].get('P', 0) * 30974
        # Choose a safe denominator for efficiency
        denom = None
        if p_initial_mg and p_initial_mg > 0:
            denom = p_initial_mg
        elif p_input_mg and p_input_mg > 0:
            denom = p_input_mg
        p_removal_eff = None if not denom else ((denom - achieved_p_mg) / denom) * 100

        best_result['optimization_summary'] = {
            'optimal_coagulant_dose_mmol': best_coag_dose,
            'optimal_naoh_dose_mmol': best_naoh_dose if target_ph else None,
            'target_p_mg_l': target_p_mg_l,
            'achieved_p_mg_l': achieved_p_mg,
            'target_ph': target_ph,
            'achieved_ph': best_result.get('solution_summary', {}).get('pH'),
            'p_removal_efficiency': p_removal_eff,
            'initial_p_mg_l_from_sim': p_initial_mg,
            'initial_p_mg_l_from_input': p_input_mg,
            'optimization_path': optimization_path
        }
        # If we could not compute efficiency, provide a helpful note
        if p_removal_eff is None:
            best_result['optimization_summary']['note'] = (
                'Initial dissolved phosphorus was zero or unavailable; '
                'removal efficiency not computed.'
            )
        logger.info(f"Optimal doses - {coagulant}: {best_coag_dose:.3f} mmol/L" + 
                   (f", NaOH: {best_naoh_dose:.3f} mmol/L" if best_naoh_dose else ""))
        return best_result
    else:
        # Return structured error with iterations for debugging
        return {
            'error': 'All dose combinations failed - check water chemistry and database',
            'optimization_path': optimization_path,
            'coagulant': coagulant,
            'target_p_mg_l': target_p_mg_l,
            'target_ph': target_ph
        }
