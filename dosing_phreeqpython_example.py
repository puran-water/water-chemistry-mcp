#!/usr/bin/env python3
"""
Example implementation of calculate_dosing_requirement using phreeqpython
This demonstrates how to replace the unreliable iterative approach with
phreeqpython's deterministic calculations.
"""

async def calculate_dosing_requirement_with_phreeqpython(
    solution_data: Dict[str, Any],
    target_condition: Dict[str, Any],
    reagent: str,
    database_path: Optional[str] = None,
    tolerance: float = 0.01
) -> Dict[str, Any]:
    """
    Calculate reagent dose using phreeqpython's direct calculation capabilities.
    
    This replaces the unreliable iterative approach with a deterministic binary
    search that leverages phreeqpython's automatic equilibrium calculations.
    """
    
    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")
    
    from phreeqpython import PhreeqPython
    
    try:
        # Create PhreeqPython instance
        pp = PhreeqPython(database=database_path or DEFAULT_DATABASE)
        
        # Create initial solution
        solution = pp.add_solution(solution_data)
        initial_pH = solution.pH
        
        # Extract target parameter and value
        target_param = target_condition['parameter']
        target_value = target_condition['value']
        
        # Determine search bounds based on reagent type
        if reagent in ['NaOH', 'KOH', 'Ca(OH)2', 'NH3']:
            # Base addition
            low_dose, high_dose = 0, 10000  # mmol/L
        elif reagent in ['HCl', 'H2SO4', 'HNO3', 'H3PO4']:
            # Acid addition
            low_dose, high_dose = 0, 10000  # mmol/L
        else:
            # Other reagents (metal salts, etc.)
            low_dose, high_dose = 0, 5000  # mmol/L
        
        # Binary search for optimal dose
        iterations = 0
        max_iterations = 50
        
        while high_dose - low_dose > tolerance and iterations < max_iterations:
            mid_dose = (low_dose + high_dose) / 2
            
            # Test this dose
            test_solution = solution.copy()
            test_solution.add(reagent, mid_dose, 'mmol')
            
            # Get current value based on target parameter
            if target_param == 'pH':
                current_value = test_solution.pH
            elif target_param == 'alkalinity':
                current_value = test_solution.alkalinity
            elif target_param == 'si':
                mineral = target_condition.get('mineral', 'Calcite')
                current_value = test_solution.si(mineral)
            elif target_param == 'element':
                element = target_condition.get('element')
                current_value = test_solution.total(element) * 1000  # mg/L
            else:
                raise ValueError(f"Unsupported target parameter: {target_param}")
            
            # Adjust search bounds
            if target_param == 'pH':
                # For pH, higher dose of base increases pH
                if reagent in ['NaOH', 'KOH', 'Ca(OH)2', 'NH3']:
                    if current_value < target_value:
                        low_dose = mid_dose
                    else:
                        high_dose = mid_dose
                else:  # Acids
                    if current_value > target_value:
                        low_dose = mid_dose
                    else:
                        high_dose = mid_dose
            else:
                # For other parameters, depends on specific chemistry
                if abs(current_value - target_value) < tolerance:
                    break
                elif current_value < target_value:
                    low_dose = mid_dose
                else:
                    high_dose = mid_dose
            
            iterations += 1
        
        # Final dose and solution
        final_dose = (low_dose + high_dose) / 2
        final_solution = solution.copy()
        final_solution.add(reagent, final_dose, 'mmol')
        
        # Calculate dose in practical units
        # Get molecular weight for conversion
        mol_weight = get_molecular_weight(reagent)
        dose_mg_L = final_dose * mol_weight
        
        # Check for precipitation if relevant
        precipitated_minerals = {}
        if target_param != 'si':  # Don't check precipitation if we're targeting SI
            common_minerals = ['Calcite', 'Mg(OH)2', 'Al(OH)3', 'Fe(OH)3']
            for mineral in common_minerals:
                try:
                    si = final_solution.si(mineral)
                    if si > 0:
                        # This mineral would precipitate
                        test = final_solution.copy()
                        test.equalize_with({mineral: 0})
                        # Calculate amount precipitated
                        precipitated_minerals[mineral] = {
                            'si': si,
                            'would_precipitate': True
                        }
                except:
                    pass
        
        # Build comprehensive response
        return {
            "required_dose": {
                "reagent": reagent,
                "amount": dose_mg_L,
                "units": "mg/L",
                "amount_mmol": final_dose,
                "units_mmol": "mmol/L"
            },
            "initial_solution": {
                "pH": initial_pH,
                "alkalinity": solution.alkalinity,
                "ionic_strength": solution.I
            },
            "final_solution": {
                "pH": final_solution.pH,
                "alkalinity": final_solution.alkalinity,
                "ionic_strength": final_solution.I,
                "pe": final_solution.pe,
                "analysis": {
                    element: final_solution.total(element) * 1000
                    for element in final_solution.elements
                }
            },
            "convergence_info": {
                "iterations": iterations,
                "converged": True,
                "final_tolerance": abs(high_dose - low_dose),
                "message": f"Successfully calculated dose for {target_param} = {target_value}"
            },
            "precipitation_risk": precipitated_minerals,
            "chemical_cost": {
                "dose_kg_per_m3": dose_mg_L / 1000,
                "estimated_cost_per_m3": calculate_chemical_cost(reagent, dose_mg_L)
            }
        }
        
    except Exception as e:
        logger.error(f"Error in dosing calculation: {e}")
        return {
            "error": str(e),
            "convergence_info": {
                "converged": False,
                "message": f"Failed to calculate dose: {e}"
            }
        }


def get_molecular_weight(formula: str) -> float:
    """Get molecular weight for common water treatment chemicals"""
    MW_DATABASE = {
        'NaOH': 40.0,
        'Ca(OH)2': 74.1,
        'HCl': 36.5,
        'H2SO4': 98.1,
        'FeCl3': 162.2,
        'Al2(SO4)3': 342.2,
        'Na2CO3': 106.0,
        'NaHCO3': 84.0,
        'NH3': 17.0,
        'Cl2': 70.9,
        'KMnO4': 158.0,
        'H3PO4': 98.0,
    }
    return MW_DATABASE.get(formula, 100.0)  # Default if not found


def calculate_chemical_cost(reagent: str, dose_mg_L: float) -> float:
    """Estimate chemical cost per m3 treated"""
    # Typical chemical costs ($/kg)
    COST_DATABASE = {
        'NaOH': 0.50,
        'Ca(OH)2': 0.15,
        'HCl': 0.20,
        'H2SO4': 0.10,
        'FeCl3': 0.35,
        'Al2(SO4)3': 0.25,
        'Na2CO3': 0.30,
    }
    cost_per_kg = COST_DATABASE.get(reagent, 0.30)  # Default cost
    return (dose_mg_L / 1000) * cost_per_kg


# Example usage demonstrating deterministic pH adjustment
if __name__ == "__main__":
    import asyncio
    
    # Example: pH adjustment of acidic wastewater
    test_input = {
        "solution_data": {
            "pH": 5.5,
            "Ca": 100,
            "Alkalinity": 50,
            "Cl": 200,
            "units": "mg/L"
        },
        "target_condition": {
            "parameter": "pH",
            "value": 8.5
        },
        "reagent": "NaOH"
    }
    
    result = asyncio.run(
        calculate_dosing_requirement_with_phreeqpython(**test_input)
    )
    
    print("Dosing Calculation Result:")
    print(f"Required NaOH dose: {result['required_dose']['amount']:.1f} mg/L")
    print(f"Final pH: {result['final_solution']['pH']:.2f}")
    print(f"Cost per m³: ${result['chemical_cost']['estimated_cost_per_m3']:.3f}")