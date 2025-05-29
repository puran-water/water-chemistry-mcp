"""
Enhanced scaling potential tool for membrane systems (RO/NF/UF).
Provides deterministic assessment of scaling risk at various recovery rates.
"""

import logging
from typing import Dict, Any, List, Optional
import asyncio
import numpy as np
import os

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE
from .schemas import PredictScalingPotentialInput, PredictScalingPotentialOutput
from .phreeqc_wrapper import PhreeqcError

logger = logging.getLogger(__name__)

# Common scaling minerals in membrane systems
MEMBRANE_SCALING_MINERALS = [
    'Calcite',      # CaCO3
    'Aragonite',    # CaCO3 (polymorph)
    'Gypsum',       # CaSO4·2H2O
    'Anhydrite',    # CaSO4
    'Barite',       # BaSO4
    'Celestite',    # SrSO4
    'Fluorite',     # CaF2
    'SiO2(a)',      # Amorphous silica
    'Mg(OH)2',      # Brucite
    'Fe(OH)3(a)',   # Ferric hydroxide
    'Al(OH)3(a)',   # Aluminum hydroxide
    'Struvite',     # MgNH4PO4·6H2O
]

# Antiscalant effectiveness factors (simplified)
ANTISCALANT_EFFECTIVENESS = {
    'Calcite': 2.0,      # Can operate at 2x higher SI
    'Gypsum': 3.0,       # Very effective
    'Barite': 2.5,
    'Celestite': 2.5,
    'SiO2(a)': 1.5,      # Limited effectiveness
    'Fe(OH)3(a)': 1.2,   # Minimal effectiveness
}


async def predict_membrane_scaling_potential(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Predict scaling potential for membrane systems with concentration effects.
    
    This enhanced version:
    1. Calculates SI at various recovery rates
    2. Determines maximum safe recovery
    3. Assesses antiscalant requirements
    4. Calculates osmotic pressure
    5. Provides comprehensive scaling risk assessment
    """
    logger.info("Running enhanced membrane scaling potential analysis...")
    
    # Validate input
    try:
        input_model = PredictScalingPotentialInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}
    
    if not PHREEQPYTHON_AVAILABLE:
        logger.error("PhreeqPython is not available")
        return {"error": "PhreeqPython library required for membrane scaling analysis"}
    
    # Get database
    database_path = input_model.database or DEFAULT_DATABASE
    
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
            pp = PhreeqPython()
        
        # Create feed solution - extract just the solution parameters
        solution_params = {
            'pH': input_model.ph,  # lowercase!
            'temperature': input_model.temperature_celsius,
            'units': 'mg/L'  # Important!
        }
        
        # Add analysis data
        if hasattr(input_model, 'analysis'):
            for element, conc in input_model.analysis.items():
                solution_params[element] = conc
        
        feed_solution = pp.add_solution(solution_params)
        
        # Get membrane-specific parameters
        target_recovery = input_data.get('target_recovery', 0.75)  # Default 75%
        max_recovery_to_test = input_data.get('max_recovery', 0.95)  # Test up to 95%
        temperature = input_data.get('temperature_celsius', 25)
        pressure_bar = input_data.get('pressure_bar', 15)
        antiscalant_dose = input_data.get('antiscalant_mg_L', 0)
        
        # Test recovery rates
        recovery_rates = np.linspace(0, max_recovery_to_test, 20)
        scaling_profiles = []
        
        first_scaling_recovery = None
        limiting_mineral = None
        
        for recovery in recovery_rates:
            if recovery == 0:
                # Feed water
                concentrate = feed_solution.copy()
            else:
                # Calculate concentration factor
                cf = 1 / (1 - recovery)
                
                # Simulate concentration by removing water
                concentrate = feed_solution.copy()
                # Remove water to achieve concentration factor
                water_to_remove = 1 - (1/cf)
                concentrate.remove('H2O', water_to_remove * 55.5, 'mol')  # 55.5 mol/L water
            
            # Calculate properties at this recovery
            si_values = {}
            max_si = -999
            max_si_mineral = None
            
            for mineral in MEMBRANE_SCALING_MINERALS:
                try:
                    si = concentrate.si(mineral)
                    si_values[mineral] = si
                    
                    # Apply antiscalant factor if present
                    effective_si = si
                    if antiscalant_dose > 0 and mineral in ANTISCALANT_EFFECTIVENESS:
                        effective_si = si - np.log10(ANTISCALANT_EFFECTIVENESS[mineral])
                    
                    if effective_si > max_si:
                        max_si = effective_si
                        max_si_mineral = mineral
                except:
                    pass
            
            # Check if scaling would occur
            if max_si > 0 and first_scaling_recovery is None:
                first_scaling_recovery = recovery
                limiting_mineral = max_si_mineral
            
            # Calculate osmotic pressure using simplified van't Hoff equation
            osmotic_pressure = calculate_osmotic_pressure(concentrate)
            
            scaling_profiles.append({
                'recovery': recovery,
                'concentration_factor': 1/(1-recovery) if recovery < 1 else float('inf'),
                'si_values': si_values,
                'max_si': max_si,
                'limiting_mineral': max_si_mineral,
                'osmotic_pressure_bar': osmotic_pressure,
                'tds_mg_L': concentrate.sc * 0.64,  # Approximate TDS from conductivity
                'pH': concentrate.pH,
                'ionic_strength': concentrate.I
            })
            
            concentrate.forget()  # Clean up
        
        # Determine safe operating conditions
        if first_scaling_recovery is None:
            max_safe_recovery = max_recovery_to_test
            safety_factor = 1.0
        else:
            # Apply safety factor
            safety_factor = 0.9  # 10% safety margin
            max_safe_recovery = first_scaling_recovery * safety_factor
        
        # Calculate conditions at target recovery
        target_cf = 1 / (1 - target_recovery)
        target_concentrate = feed_solution.copy()
        water_to_remove = 1 - (1/target_cf)
        target_concentrate.remove('H2O', water_to_remove * 55.5, 'mol')
        
        target_si_values = {}
        scaling_risk_assessment = []
        
        for mineral in MEMBRANE_SCALING_MINERALS:
            try:
                si = target_concentrate.si(mineral)
                target_si_values[mineral] = si
                
                # Assess risk
                if si > 1:
                    risk = 'High'
                elif si > 0:
                    risk = 'Moderate'
                elif si > -0.5:
                    risk = 'Low'
                else:
                    risk = 'Negligible'
                
                if risk in ['High', 'Moderate']:
                    scaling_risk_assessment.append({
                        'mineral': mineral,
                        'si': si,
                        'risk': risk,
                        'mitigation': get_mitigation_strategy(mineral, si)
                    })
            except:
                pass
        
        # Antiscalant recommendations
        antiscalant_recommendation = recommend_antiscalant(
            target_si_values, 
            target_recovery,
            antiscalant_dose
        )
        
        # Build comprehensive output
        output = {
            'feed_water_analysis': {
                'pH': feed_solution.pH,
                'tds_mg_L': feed_solution.sc * 0.64,
                'hardness_mg_L_CaCO3': calculate_hardness(feed_solution),
                'alkalinity_mg_L_CaCO3': feed_solution.total('Alkalinity', 'mg'),
                'saturation_indices': {
                    mineral: feed_solution.si(mineral) 
                    for mineral in ['Calcite', 'Gypsum', 'Barite', 'SiO2(a)']
                    if mineral in MEMBRANE_SCALING_MINERALS
                }
            },
            'target_recovery_analysis': {
                'recovery_fraction': target_recovery,
                'concentration_factor': target_cf,
                'saturation_indices': target_si_values,
                'scaling_risks': scaling_risk_assessment,
                'osmotic_pressure_bar': calculate_osmotic_pressure(target_concentrate),
                'projected_pH': target_concentrate.pH
            },
            'maximum_safe_recovery': {
                'recovery_fraction': max_safe_recovery,
                'limiting_mineral': limiting_mineral,
                'safety_factor_applied': safety_factor,
                'with_current_antiscalant': antiscalant_dose > 0
            },
            'scaling_profile': scaling_profiles,
            'antiscalant_recommendation': antiscalant_recommendation,
            'operational_recommendations': generate_operational_recommendations(
                target_si_values,
                max_safe_recovery,
                target_recovery,
                limiting_mineral
            )
        }
        
        # Add warnings if needed
        warnings = []
        if target_recovery > max_safe_recovery:
            warnings.append(f"Target recovery ({target_recovery*100:.1f}%) exceeds maximum safe recovery ({max_safe_recovery*100:.1f}%)")
        
        if any(si > 2 for si in target_si_values.values()):
            warnings.append("Very high scaling potential detected - immediate mitigation required")
        
        if output['target_recovery_analysis']['osmotic_pressure_bar'] > pressure_bar:
            warnings.append("Osmotic pressure exceeds operating pressure - recovery not achievable")
        
        if warnings:
            output['warnings'] = warnings
        
        logger.info(f"Membrane scaling analysis complete. Max safe recovery: {max_safe_recovery*100:.1f}%")
        return output
        
    except Exception as e:
        logger.exception("Error in membrane scaling analysis")
        return {"error": str(e)}


def calculate_osmotic_pressure(solution):
    """Calculate osmotic pressure using simplified van't Hoff equation."""
    # π = iMRT where i=2 for most salts, M=molarity, R=0.08314, T=298K
    # Simplified: π (bar) ≈ 0.75 * TDS(g/L) for typical brackish water
    tds_g_L = solution.sc * 0.64 / 1000  # Convert from mg/L to g/L
    return 0.75 * tds_g_L


def calculate_hardness(solution):
    """Calculate total hardness as CaCO3."""
    ca_mg_L = solution.total('Ca', 'mg')
    mg_mg_L = solution.total('Mg', 'mg')
    
    # Convert to CaCO3 equivalents
    ca_as_caco3 = ca_mg_L * (100.1 / 40.1)
    mg_as_caco3 = mg_mg_L * (100.1 / 24.3)
    
    return ca_as_caco3 + mg_as_caco3


def get_mitigation_strategy(mineral, si):
    """Get mitigation strategy for specific mineral scaling."""
    strategies = {
        'Calcite': {
            'high': 'pH adjustment (target 6.5-7.0), antiscalant injection, or acid dosing',
            'moderate': 'Antiscalant injection (3-5 mg/L) or mild acid dosing'
        },
        'Gypsum': {
            'high': 'Antiscalant injection (5-10 mg/L), reduce recovery, or ion exchange softening',
            'moderate': 'Antiscalant injection (2-5 mg/L)'
        },
        'Barite': {
            'high': 'Specialized antiscalant, reduce recovery, or pre-treatment for barium removal',
            'moderate': 'Antiscalant injection (3-5 mg/L)'
        },
        'SiO2(a)': {
            'high': 'Reduce recovery, increase temperature, or pH adjustment to >10',
            'moderate': 'Silica-specific antiscalant or mild pH increase'
        },
        'Fe(OH)3(a)': {
            'high': 'Pre-oxidation and filtration, maintain pH <6.5',
            'moderate': 'Dispersant addition or mild acid dosing'
        }
    }
    
    risk_level = 'high' if si > 1 else 'moderate'
    
    return strategies.get(mineral, {}).get(
        risk_level, 
        'Consult water treatment specialist'
    )


def recommend_antiscalant(si_values, recovery, current_dose):
    """Recommend antiscalant type and dosage."""
    
    # Identify primary scaling concerns
    scaling_concerns = []
    for mineral, si in si_values.items():
        if si > 0:
            scaling_concerns.append((mineral, si))
    
    if not scaling_concerns:
        return {
            'required': False,
            'message': 'No antiscalant required at this recovery'
        }
    
    # Sort by SI value
    scaling_concerns.sort(key=lambda x: x[1], reverse=True)
    primary_scalant = scaling_concerns[0][0]
    
    # Recommend based on primary scalant
    recommendations = {
        'Calcite': {
            'type': 'Phosphonate-based',
            'dose_mg_L': 3 + (recovery - 0.75) * 10,
            'products': ['Hypersperse MDC-220', 'Flocon 260']
        },
        'Gypsum': {
            'type': 'Polyacrylate or phosphonate',
            'dose_mg_L': 5 + (recovery - 0.75) * 15,
            'products': ['Hypersperse MDC-150', 'Flocon 135']
        },
        'Barite': {
            'type': 'Phosphonate with sulfate inhibitor',
            'dose_mg_L': 5 + (recovery - 0.75) * 20,
            'products': ['Hypersperse MDC-714', 'Flocon 190']
        },
        'SiO2(a)': {
            'type': 'Silica-specific dispersant',
            'dose_mg_L': 10 + (recovery - 0.75) * 20,
            'products': ['Hypersperse SI-300', 'SpectraGuard 100']
        }
    }
    
    default_rec = {
        'type': 'Broad-spectrum antiscalant',
        'dose_mg_L': 5 + (recovery - 0.75) * 15,
        'products': ['Generic phosphonate blend']
    }
    
    rec = recommendations.get(primary_scalant, default_rec)
    
    return {
        'required': True,
        'primary_scalant': primary_scalant,
        'antiscalant_type': rec['type'],
        'recommended_dose_mg_L': max(rec['dose_mg_L'], current_dose * 1.5),
        'example_products': rec['products'],
        'notes': f"Dose may need adjustment based on actual performance. Monitor normalized permeate flow."
    }


def generate_operational_recommendations(si_values, max_recovery, target_recovery, limiting_mineral):
    """Generate operational recommendations based on scaling analysis."""
    
    recommendations = []
    
    # Recovery recommendations
    if target_recovery > max_recovery:
        recommendations.append({
            'priority': 'High',
            'category': 'Recovery',
            'action': f'Reduce system recovery to below {max_recovery*100:.1f}%',
            'reason': f'{limiting_mineral} scaling risk'
        })
    elif target_recovery > max_recovery * 0.95:
        recommendations.append({
            'priority': 'Medium',
            'category': 'Recovery',
            'action': 'Consider reducing recovery by 2-3% for operational margin',
            'reason': 'Operating near scaling limit'
        })
    
    # pH adjustment recommendations
    if 'Calcite' in si_values and si_values['Calcite'] > 0.5:
        recommendations.append({
            'priority': 'High',
            'category': 'pH Control',
            'action': 'Implement acid dosing to maintain pH 6.5-7.0',
            'reason': 'Calcium carbonate scaling control'
        })
    
    # Pre-treatment recommendations
    if 'Fe(OH)3(a)' in si_values and si_values['Fe(OH)3(a)'] > 0:
        recommendations.append({
            'priority': 'High',
            'category': 'Pre-treatment',
            'action': 'Install iron removal (oxidation + filtration)',
            'reason': 'Prevent iron fouling of membranes'
        })
    
    if 'SiO2(a)' in si_values and si_values['SiO2(a)'] > -0.1:
        recommendations.append({
            'priority': 'Medium',
            'category': 'Pre-treatment',
            'action': 'Consider warm lime softening or increase temperature',
            'reason': 'Silica scaling prevention'
        })
    
    # Monitoring recommendations
    recommendations.append({
        'priority': 'Medium',
        'category': 'Monitoring',
        'action': 'Monitor normalized permeate flow and differential pressure daily',
        'reason': 'Early detection of scaling'
    })
    
    if any(si > 0.5 for si in si_values.values()):
        recommendations.append({
            'priority': 'High',
            'category': 'Monitoring',
            'action': 'Implement online conductivity profiling of concentrate',
            'reason': 'Real-time scaling risk assessment'
        })
    
    return recommendations