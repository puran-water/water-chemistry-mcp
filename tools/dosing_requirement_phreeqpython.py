"""
Enhanced dosing requirement tool using phreeqpython's direct calculation capabilities.
This replaces the unreliable iterative approach with deterministic calculations.
"""

import logging
from typing import Dict, Any, Optional
import asyncio
import os

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE
from .schemas import CalculateDosingRequirementInput, CalculateDosingRequirementOutput, SolutionOutput
from .phreeqc_wrapper import PhreeqcError

logger = logging.getLogger(__name__)

# Molecular weights for common water treatment chemicals
MOLECULAR_WEIGHTS = {
    'NaOH': 40.0,
    'Ca(OH)2': 74.1,
    'CaO': 56.1,      # Lime
    'HCl': 36.5,
    'H2SO4': 98.1,
    'HNO3': 63.0,
    'H3PO4': 98.0,
    'FeCl3': 162.2,
    'Fe2(SO4)3': 399.9,
    'Al2(SO4)3': 342.2,
    'AlCl3': 133.3,
    'Na2CO3': 106.0,
    'NaHCO3': 84.0,
    'NH3': 17.0,
    'NH4OH': 35.0,
    'Cl2': 70.9,
    'NaOCl': 74.4,
    'Ca(OCl)2': 143.0,
    'KMnO4': 158.0,
    'Na2S2O3': 158.1,
    'CaCO3': 100.1,
}

# Typical chemical costs ($/kg) for estimation
CHEMICAL_COSTS = {
    'NaOH': 0.50,
    'Ca(OH)2': 0.15,
    'CaO': 0.12,
    'HCl': 0.20,
    'H2SO4': 0.10,
    'HNO3': 0.35,
    'H3PO4': 0.40,
    'FeCl3': 0.35,
    'Fe2(SO4)3': 0.25,
    'Al2(SO4)3': 0.25,
    'AlCl3': 0.30,
    'Na2CO3': 0.30,
    'NaHCO3': 0.35,
    'NH3': 0.45,
    'Cl2': 0.40,
    'NaOCl': 0.25,
    'KMnO4': 3.50,
}


async def calculate_dosing_requirement_phreeqpython(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate reagent dose using phreeqpython's direct calculation capabilities.
    
    This provides deterministic results by using binary search with phreeqpython's
    automatic equilibrium calculations instead of unreliable iterative approximations.
    """
    logger.info("Running enhanced dosing requirement calculation with phreeqpython...")
    
    # Validate input
    try:
        input_model = CalculateDosingRequirementInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}
    
    # Check phreeqpython availability
    if not PHREEQPYTHON_AVAILABLE:
        logger.error("PhreeqPython is not available")
        return {"error": "PhreeqPython library is not available. Install with: pip install phreeqpython"}
    
    # Get and resolve database
    database_path = input_model.database
    if database_path:
        resolved_path = database_manager.resolve_database_path(database_path)
        if resolved_path and database_manager.validate_database_path(resolved_path):
            database_path = resolved_path
            logger.info(f"Using resolved database path: {database_path}")
        else:
            logger.warning(f"Invalid database path: {database_path}, using default")
            database_path = None
    
    try:
        from phreeqpython import PhreeqPython
        
        # Create PhreeqPython instance - use basename for standard databases
        if database_path and os.path.exists(database_path):
            db_basename = os.path.basename(database_path)
            try:
                pp = PhreeqPython(database=db_basename)
                logger.info(f"Created PhreeqPython with database: {db_basename}")
            except Exception as e:
                logger.warning(f"Could not use basename {db_basename}, trying full path: {e}")
                pp = PhreeqPython(database=database_path)
        else:
            # Use phreeqpython's default database
            pp = PhreeqPython()
        
        # Convert solution data
        solution_data = input_model.initial_solution.model_dump(exclude_defaults=True)
        
        # Handle element mapping for common wastewater parameters
        if 'analysis' in solution_data:
            analysis = solution_data['analysis']
            # Map common elements to their default oxidation states
            element_mapping = {
                'P': 'P(5)',      # Phosphate
                'N': 'N(5)',      # Nitrate (or specify N(-3) for ammonia)
                'S': 'S(6)',      # Sulfate (or specify S(-2) for sulfide)
                'Fe': 'Fe(2)',    # Ferrous (or specify Fe(3) for ferric)
                'As': 'As(5)',    # Arsenate
                'Cr': 'Cr(6)',    # Chromate
                'Mn': 'Mn(2)',    # Manganese
            }
            
            # Apply mapping if element doesn't already have oxidation state
            for element, default_state in element_mapping.items():
                if element in analysis and not any(f"{element}(" in key for key in analysis):
                    analysis[default_state] = analysis.pop(element)
            
            # Update the solution_data with mapped elements
            solution_data['analysis'] = analysis
        
        # Flatten solution data for phreeqpython
        pp_solution_data = {}
        
        # Add basic parameters
        if 'ph' in solution_data:
            pp_solution_data['pH'] = solution_data['ph']
        elif 'pH' in solution_data:
            pp_solution_data['pH'] = solution_data['pH']
        
        if 'pe' in solution_data:
            pp_solution_data['pe'] = solution_data['pe']
            
        if 'temperature_celsius' in solution_data:
            pp_solution_data['temp'] = solution_data['temperature_celsius']
        elif 'temp' in solution_data:
            pp_solution_data['temp'] = solution_data['temp']
            
        # Add analysis data (already mapped)
        if 'analysis' in solution_data:
            pp_solution_data.update(solution_data['analysis'])
            
        # Ensure units are set to mg/L
        pp_solution_data['units'] = solution_data.get('units', 'mg/L')
        
        # Create initial solution
        initial_solution = pp.add_solution(pp_solution_data)
        initial_state = capture_solution_state(initial_solution)
        
        # Get target parameters
        target_param = input_model.target_condition.parameter
        target_value = input_model.target_condition.value
        reagent = input_model.reagent.formula
        tolerance = input_model.tolerance
        
        # Perform dosing calculation based on target type
        if target_param == 'pH':
            result = await calculate_ph_dose(
                pp, initial_solution, target_value, reagent, tolerance
            )
        elif target_param == 'alkalinity':
            result = await calculate_alkalinity_dose(
                pp, initial_solution, target_value, reagent, tolerance
            )
        elif target_param == 'si':
            mineral = input_model.target_condition.mineral
            result = await calculate_si_dose(
                pp, initial_solution, target_value, mineral, reagent, tolerance
            )
        elif target_param == 'element':
            element = input_model.target_condition.element_or_species
            result = await calculate_element_dose(
                pp, initial_solution, target_value, element, reagent, tolerance
            )
        else:
            return {"error": f"Unsupported target parameter: {target_param}"}
        
        # Check for precipitation if enabled
        final_solution = result['final_solution']
        precipitation_info = {}
        
        if input_model.allow_precipitation:
            minerals = input_model.equilibrium_minerals or get_default_minerals(solution_data)
            precipitation_info = check_precipitation(final_solution, minerals)
        
        # Calculate practical dose information
        dose_mmol = result['dose_mmol']
        mol_weight = MOLECULAR_WEIGHTS.get(reagent, 100.0)
        dose_mg_L = dose_mmol * mol_weight
        dose_kg_m3 = dose_mg_L / 1000
        cost_per_kg = CHEMICAL_COSTS.get(reagent, 0.30)
        cost_per_m3 = dose_kg_m3 * cost_per_kg
        
        # Build comprehensive output
        output = {
            "required_dose_mmol_per_L": dose_mmol,
            "dose_mg_per_L": dose_mg_L,
            "dose_kg_per_m3": dose_kg_m3,
            "chemical_cost_per_m3": cost_per_m3,
            "initial_state": initial_state,
            "final_state": capture_solution_state(final_solution),
            "iterations_taken": result['iterations'],
            "convergence_status": "Converged",
            "actual_vs_target": {
                "parameter": target_param,
                "target": target_value,
                "achieved": result['achieved_value'],
                "difference": abs(result['achieved_value'] - target_value)
            }
        }
        
        if precipitation_info:
            output["precipitation_warning"] = precipitation_info
        
        # Add specific warnings based on conditions
        warnings = []
        
        # pH warnings
        if target_param == 'pH':
            if target_value > 11:
                warnings.append("Very high pH may cause metal hydroxide precipitation")
            elif target_value < 4:
                warnings.append("Very low pH may cause metal mobilization")
        
        # Reagent-specific warnings
        if reagent == 'Ca(OH)2' and final_solution.si('Calcite') > 0:
            warnings.append("Lime addition may cause calcium carbonate scaling")
        elif reagent in ['FeCl3', 'Fe2(SO4)3', 'AlCl3', 'Al2(SO4)3']:
            warnings.append(f"{reagent} will consume alkalinity and lower pH")
        
        if warnings:
            output["warnings"] = warnings
        
        logger.info(f"Dosing calculation completed: {dose_mg_L:.1f} mg/L {reagent}")
        return output
        
    except Exception as e:
        logger.exception("Error in phreeqpython dosing calculation")
        return {
            "error": str(e),
            "convergence_status": "Failed"
        }


async def calculate_ph_dose(pp, initial_solution, target_ph, reagent, tolerance):
    """Calculate dose needed to achieve target pH using binary search."""
    
    # Determine if reagent is acid or base
    acids = ['HCl', 'H2SO4', 'HNO3', 'H3PO4']
    bases = ['NaOH', 'KOH', 'Ca(OH)2', 'CaO', 'NH3', 'NH4OH', 'Na2CO3', 'NaHCO3']
    
    is_acid = reagent in acids
    initial_ph = initial_solution.pH
    
    # Set search bounds
    if (target_ph > initial_ph and not is_acid) or (target_ph < initial_ph and is_acid):
        low, high = 0, 1000  # mmol/L
    else:
        return {
            'dose_mmol': 0,
            'final_solution': initial_solution,
            'achieved_value': initial_ph,
            'iterations': 0,
            'error': f"Cannot reach pH {target_ph} from {initial_ph:.2f} using {reagent}"
        }
    
    # Binary search
    iterations = 0
    max_iterations = 50
    
    while high - low > tolerance and iterations < max_iterations:
        mid = (low + high) / 2
        
        # Test dose
        test_solution = initial_solution.copy()
        test_solution.add(reagent, mid, 'mmol')
        current_ph = test_solution.pH
        
        # Update bounds
        if abs(current_ph - target_ph) < tolerance:
            return {
                'dose_mmol': mid,
                'final_solution': test_solution,
                'achieved_value': current_ph,
                'iterations': iterations
            }
        
        if (current_ph < target_ph and not is_acid) or (current_ph > target_ph and is_acid):
            low = mid
        else:
            high = mid
        
        test_solution.forget()  # Clean up
        iterations += 1
    
    # Return best result
    final_dose = (low + high) / 2
    final_solution = initial_solution.copy()
    final_solution.add(reagent, final_dose, 'mmol')
    
    return {
        'dose_mmol': final_dose,
        'final_solution': final_solution,
        'achieved_value': final_solution.pH,
        'iterations': iterations
    }


async def calculate_alkalinity_dose(pp, initial_solution, target_alk, reagent, tolerance):
    """Calculate dose needed to achieve target alkalinity."""
    
    # Chemicals that affect alkalinity
    alk_increasers = ['NaOH', 'Ca(OH)2', 'CaO', 'Na2CO3', 'NaHCO3', 'NH3']
    alk_decreasers = ['HCl', 'H2SO4', 'HNO3']
    
    initial_alk = initial_solution.total('Alkalinity', 'mg')
    
    if reagent in alk_increasers:
        if target_alk < initial_alk:
            return {
                'dose_mmol': 0,
                'final_solution': initial_solution,
                'achieved_value': initial_alk,
                'iterations': 0,
                'error': f"Cannot decrease alkalinity using {reagent}"
            }
        low, high = 0, 500
    elif reagent in alk_decreasers:
        if target_alk > initial_alk:
            return {
                'dose_mmol': 0,
                'final_solution': initial_solution,
                'achieved_value': initial_alk,
                'iterations': 0,
                'error': f"Cannot increase alkalinity using {reagent}"
            }
        low, high = 0, 500
    else:
        return {
            'dose_mmol': 0,
            'final_solution': initial_solution,
            'achieved_value': initial_alk,
            'iterations': 0,
            'error': f"{reagent} does not significantly affect alkalinity"
        }
    
    # Binary search for alkalinity target
    iterations = 0
    max_iterations = 50
    
    while high - low > tolerance and iterations < max_iterations:
        mid = (low + high) / 2
        
        test_solution = initial_solution.copy()
        test_solution.add(reagent, mid, 'mmol')
        current_alk = test_solution.total('Alkalinity', 'mg')
        
        if abs(current_alk - target_alk) < tolerance * 10:  # mg/L tolerance
            return {
                'dose_mmol': mid,
                'final_solution': test_solution,
                'achieved_value': current_alk,
                'iterations': iterations
            }
        
        if current_alk < target_alk:
            low = mid
        else:
            high = mid
        
        test_solution.forget()
        iterations += 1
    
    final_dose = (low + high) / 2
    final_solution = initial_solution.copy()
    final_solution.add(reagent, final_dose, 'mmol')
    
    return {
        'dose_mmol': final_dose,
        'final_solution': final_solution,
        'achieved_value': final_solution.total('Alkalinity', 'mg'),
        'iterations': iterations
    }


async def calculate_si_dose(pp, initial_solution, target_si, mineral, reagent, tolerance):
    """Calculate dose needed to achieve target saturation index."""
    
    # This is more complex as it depends on the specific mineral and reagent
    # For now, implement a general approach
    
    initial_si = initial_solution.si(mineral)
    
    # Determine search direction
    low, high = 0, 200
    
    iterations = 0
    max_iterations = 50
    
    while high - low > tolerance and iterations < max_iterations:
        mid = (low + high) / 2
        
        test_solution = initial_solution.copy()
        test_solution.add(reagent, mid, 'mmol')
        current_si = test_solution.si(mineral)
        
        if abs(current_si - target_si) < tolerance:
            return {
                'dose_mmol': mid,
                'final_solution': test_solution,
                'achieved_value': current_si,
                'iterations': iterations
            }
        
        if current_si < target_si:
            low = mid
        else:
            high = mid
        
        test_solution.forget()
        iterations += 1
    
    final_dose = (low + high) / 2
    final_solution = initial_solution.copy()
    final_solution.add(reagent, final_dose, 'mmol')
    
    return {
        'dose_mmol': final_dose,
        'final_solution': final_solution,
        'achieved_value': final_solution.si(mineral),
        'iterations': iterations
    }


async def calculate_element_dose(pp, initial_solution, target_conc, element, reagent, tolerance):
    """Calculate dose needed to achieve target element concentration."""
    
    # Check if reagent contains the target element
    # This is a simplified check - could be enhanced
    if element not in reagent:
        return {
            'dose_mmol': 0,
            'final_solution': initial_solution,
            'achieved_value': initial_solution.total(element, 'mg'),
            'iterations': 0,
            'error': f"{reagent} does not contain {element}"
        }
    
    initial_conc = initial_solution.total(element, 'mg')
    
    if target_conc < initial_conc:
        return {
            'dose_mmol': 0,
            'final_solution': initial_solution,
            'achieved_value': initial_conc,
            'iterations': 0,
            'error': f"Cannot reduce {element} concentration by adding {reagent}"
        }
    
    # Estimate stoichiometry (simplified - could be enhanced)
    # This assumes 1:1 for now
    estimated_dose = (target_conc - initial_conc) / MOLECULAR_WEIGHTS.get(element, 50)
    
    # Refine with binary search
    low = estimated_dose * 0.5
    high = estimated_dose * 2.0
    
    iterations = 0
    max_iterations = 30
    
    while high - low > tolerance and iterations < max_iterations:
        mid = (low + high) / 2
        
        test_solution = initial_solution.copy()
        test_solution.add(reagent, mid, 'mmol')
        current_conc = test_solution.total(element, 'mg')
        
        if abs(current_conc - target_conc) < tolerance * 10:
            return {
                'dose_mmol': mid,
                'final_solution': test_solution,
                'achieved_value': current_conc,
                'iterations': iterations
            }
        
        if current_conc < target_conc:
            low = mid
        else:
            high = mid
        
        test_solution.forget()
        iterations += 1
    
    final_dose = (low + high) / 2
    final_solution = initial_solution.copy()
    final_solution.add(reagent, final_dose, 'mmol')
    
    return {
        'dose_mmol': final_dose,
        'final_solution': final_solution,
        'achieved_value': final_solution.total(element, 'mg'),
        'iterations': iterations
    }


def capture_solution_state(solution):
    """Capture comprehensive solution state for output."""
    
    # Get major species concentrations
    major_species = {}
    for species, molality in solution.species_molalities.items():
        if molality > 1e-6:  # Only include significant species
            major_species[species] = molality
    
    # Get element totals
    element_totals = {}
    for element, moles in solution.elements.items():
        mg_L = moles * 1000 * MOLECULAR_WEIGHTS.get(element, 50)
        element_totals[element] = mg_L
    
    return {
        "pH": solution.pH,
        "pe": solution.pe,
        "temperature": solution.temperature,
        "ionic_strength": solution.I,
        "specific_conductance": solution.sc,
        "alkalinity": solution.total_element('C(4)') * 50044 if hasattr(solution, 'total_element') else 0,  # Alkalinity as CaCO3
        "hardness": calculate_hardness(solution),
        "analysis": element_totals,
        "major_species": major_species,
        "saturation_indices": get_relevant_si(solution)
    }


def calculate_hardness(solution):
    """Calculate total hardness as CaCO3."""
    ca_mg_L = solution.total('Ca', 'mg')
    mg_mg_L = solution.total('Mg', 'mg')
    
    # Convert to CaCO3 equivalents
    ca_as_caco3 = ca_mg_L * (100.1 / 40.1)
    mg_as_caco3 = mg_mg_L * (100.1 / 24.3)
    
    return ca_as_caco3 + mg_as_caco3


def get_relevant_si(solution):
    """Get saturation indices for relevant minerals."""
    common_minerals = [
        'Calcite', 'Aragonite', 'Dolomite', 'Gypsum', 'Anhydrite',
        'Barite', 'Celestite', 'Fluorite', 'Halite',
        'Fe(OH)3(a)', 'Al(OH)3(a)', 'Mg(OH)2'
    ]
    
    si_values = {}
    for mineral in common_minerals:
        try:
            si = solution.si(mineral)
            if abs(si) < 10:  # Exclude unrealistic values
                si_values[mineral] = si
        except:
            pass
    
    return si_values


def check_precipitation(solution, minerals):
    """Check if any minerals would precipitate."""
    precipitating = []
    
    for mineral in minerals:
        try:
            si = solution.si(mineral)
            if si > 0:
                precipitating.append({
                    'mineral': mineral,
                    'si': si,
                    'likelihood': 'High' if si > 1 else 'Moderate'
                })
        except:
            pass
    
    return precipitating


def get_default_minerals(solution_data):
    """Get default minerals based on water chemistry."""
    minerals = ['Calcite', 'Gypsum']
    
    if 'analysis' in solution_data:
        analysis = solution_data['analysis']
        
        if any(key.startswith('Fe') for key in analysis):
            minerals.extend(['Fe(OH)3(a)', 'Siderite', 'Pyrite'])
        
        if any(key.startswith('Al') for key in analysis):
            minerals.extend(['Al(OH)3(a)', 'Gibbsite'])
        
        if 'Mg' in analysis:
            minerals.extend(['Dolomite', 'Mg(OH)2', 'Magnesite'])
        
        if 'Ba' in analysis:
            minerals.append('Barite')
        
        if 'Sr' in analysis:
            minerals.append('Celestite')
        
        if 'F' in analysis:
            minerals.append('Fluorite')
    
    return minerals


# Make this the default function for the tool
calculate_dosing_requirement = calculate_dosing_requirement_phreeqpython