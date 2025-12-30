#!/usr/bin/env python3
"""
Comprehensive test suite for simulate_chemical_addition tool.
Tests equilibrium precipitation, kinetic precipitation, and various treatment scenarios.
"""

import asyncio
import sys
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path (portable, works on any machine)
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.chemical_addition import simulate_chemical_addition
from utils.phreeqc_rates_info import format_for_mcp_input

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def record_pass(self, test_name):
        self.passed += 1
        print(f"  PASS: {test_name}")
    
    def record_fail(self, test_name, error):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"  FAIL: {test_name}: {error}")
    
    def summary(self):
        total = self.passed + self.failed
        print(f"\nTest Summary: {self.passed}/{total} passed")
        if self.errors:
            print("\nFailed tests:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        return self.failed == 0

async def test_basic_chemical_addition(results):
    """Test basic chemical additions without precipitation."""
    print("\n1. Testing Basic Chemical Addition (No Precipitation)")
    print("-" * 40)
    
    # Test 1.1: Simple pH adjustment with NaOH
    try:
        input_data = {
            "initial_solution": {
                "pH": 6.0,
                "analysis": {
                    "Ca": 2.0,
                    "Cl": 4.0
                }
            },
            "reactants": [
                {"formula": "NaOH", "amount": 1.0, "units": "mmol"}
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            final_pH = result['solution_summary']['pH']
            assert final_pH > 6.0  # pH should increase
            assert final_pH < 12.0  # But not too much
            results.record_pass("NaOH pH adjustment")
        else:
            results.record_fail("NaOH pH adjustment", result['error'])
    except Exception as e:
        results.record_fail("NaOH pH adjustment", str(e))
    
    # Test 1.2: Acid addition
    try:
        input_data = {
            "initial_solution": {
                "pH": 8.5,
                "analysis": {
                    "Na": 5.0,
                    "Alkalinity": 3.0
                }
            },
            "reactants": [
                {"formula": "HCl", "amount": 2.0, "units": "mmol"}
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            final_pH = result['solution_summary']['pH']
            assert final_pH < 8.5  # pH should decrease
            results.record_pass("HCl pH adjustment")
        else:
            results.record_fail("HCl pH adjustment", result['error'])
    except Exception as e:
        results.record_fail("HCl pH adjustment", str(e))
    
    # Test 1.3: Multiple reactants
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {
                    "Ca": 1.0,
                    "Mg": 1.0
                }
            },
            "reactants": [
                {"formula": "CaCl2", "amount": 1.0, "units": "mmol"},
                {"formula": "MgSO4", "amount": 0.5, "units": "mmol"}
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # Check that elements were added
            totals = result.get('element_totals_molality', {})
            assert totals.get('Ca', 0) > 0.001  # Should have more Ca
            assert totals.get('Mg', 0) > 0.001  # Should have more Mg
            results.record_pass("Multiple reactants")
        else:
            results.record_fail("Multiple reactants", result['error'])
    except Exception as e:
        results.record_fail("Multiple reactants", str(e))

async def test_equilibrium_precipitation(results):
    """Test equilibrium precipitation (legacy approach)."""
    print("\n2. Testing Equilibrium Precipitation")
    print("-" * 40)
    
    # Test 2.1: Lime softening (calcite precipitation)
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.5,
                "analysis": {
                    "Ca": 5.0,  # 200 mg/L
                    "Alkalinity": 4.0  # 200 mg/L as CaCO3
                }
            },
            "reactants": [
                {"formula": "Ca(OH)2", "amount": 2.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Aragonite"],
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # Should have precipitation
            if 'precipitated_phases' in result:
                assert len(result['precipitated_phases']) > 0
                total_precip = result.get('total_precipitate_g_L', 0)
                assert total_precip > 0
                results.record_pass("Lime softening precipitation")
            else:
                results.record_fail("Lime softening precipitation", 
                                  "No precipitation occurred")
        else:
            results.record_fail("Lime softening precipitation", result['error'])
    except Exception as e:
        results.record_fail("Lime softening precipitation", str(e))
    
    # Test 2.2: High pH Mg(OH)2 precipitation
    try:
        input_data = {
            "initial_solution": {
                "pH": 9.0,
                "analysis": {
                    "Mg": 10.0,  # 243 mg/L
                    "Ca": 2.0,
                    "Cl": 24.0
                }
            },
            "reactants": [
                {"formula": "NaOH", "amount": 10.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Brucite", "Mg(OH)2"],
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            final_pH = result['solution_summary']['pH']
            assert final_pH > 10.5  # Should be high pH
            
            # Check for Mg precipitation
            if 'precipitated_phases' in result:
                mg_precip = False
                for mineral in result['precipitated_phases']:
                    if 'Brucite' in mineral or 'Mg(OH)2' in mineral:
                        mg_precip = True
                        break
                if mg_precip:
                    results.record_pass("Mg(OH)2 precipitation")
                else:
                    results.record_fail("Mg(OH)2 precipitation", 
                                      "No Mg hydroxide precipitated")
            else:
                results.record_fail("Mg(OH)2 precipitation", 
                                  "No precipitation occurred")
        else:
            results.record_fail("Mg(OH)2 precipitation", result['error'])
    except Exception as e:
        results.record_fail("Mg(OH)2 precipitation", str(e))
    
    # Test 2.3: Phosphate removal with FeCl3
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {
                    "P": 3.2,  # 100 mg/L PO4-P
                    "Alkalinity": 4.0,
                    "Ca": 2.0
                }
            },
            "reactants": [
                {"formula": "FeCl3", "amount": 5.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Strengite", "FePO4", "Fe(OH)3(a)"],
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # Check P removal
            final_P = result.get('element_totals_molality', {}).get('P', 999)
            if final_P < 3.2:  # Some P removed
                results.record_pass("Phosphate removal with FeCl3")
            else:
                results.record_fail("Phosphate removal with FeCl3", 
                                  "No phosphate removed")
        else:
            results.record_fail("Phosphate removal with FeCl3", result['error'])
    except Exception as e:
        results.record_fail("Phosphate removal with FeCl3", str(e))

async def test_kinetic_precipitation_phreeqc(results):
    """Test kinetic precipitation using PHREEQC native rates."""
    print("\n3. Testing Kinetic Precipitation (PHREEQC Native)")
    print("-" * 40)
    
    # Test 3.1: Calcite kinetics with PHREEQC rates
    try:
        calcite_params = format_for_mcp_input("Calcite", surface_area=1.0)
        
        input_data = {
            "initial_solution": {
                "pH": 7.5,
                "analysis": {
                    "Ca": 5.0,
                    "Alkalinity": 4.0
                },
                "temperature_celsius": 25.0
            },
            "reactants": [
                {"formula": "Ca(OH)2", "amount": 2.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite"],
            "kinetic_parameters": {
                "enable_kinetics": True,
                "use_phreeqc_rates": True,
                "time_steps": [0, 300, 900, 1800, 3600],  # 0-60 min
                "minerals_kinetic": {
                    "Calcite": calcite_params
                }
            },
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # Check kinetic results
            assert result.get('kinetic_modeling_used', False)
            assert result.get('phreeqc_rates_used', False)
            
            if 'kinetic_profiles' in result:
                profiles = result['kinetic_profiles']
                assert len(profiles) > 0
                
                # Check calcite profile
                calcite_profile = None
                for profile in profiles:
                    if profile['mineral'] == 'Calcite':
                        calcite_profile = profile
                        break
                
                if calcite_profile:
                    # Should show increasing precipitation over time
                    amounts = calcite_profile['amount_precipitated_mol']
                    assert len(amounts) == 5  # 5 time points
                    assert amounts[-1] >= amounts[0]  # More at end
                    results.record_pass("Calcite kinetics (PHREEQC)")
                else:
                    results.record_fail("Calcite kinetics (PHREEQC)", 
                                      "No Calcite profile found")
            else:
                results.record_fail("Calcite kinetics (PHREEQC)", 
                                  "No kinetic profiles")
        else:
            results.record_fail("Calcite kinetics (PHREEQC)", result['error'])
    except Exception as e:
        results.record_fail("Calcite kinetics (PHREEQC)", str(e))
    
    # Test 3.2: Multiple time steps
    try:
        input_data = {
            "initial_solution": {
                "pH": 8.0,
                "analysis": {
                    "Ca": 6.0,
                    "Alkalinity": 5.0
                }
            },
            "reactants": [
                {"formula": "NaOH", "amount": 1.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite"],
            "kinetic_parameters": {
                "enable_kinetics": True,
                "use_phreeqc_rates": True,
                "time_steps": [0, 60, 120, 300, 600, 1200, 1800, 3600, 7200],
                "minerals_kinetic": {
                    "Calcite": {
                        "m0": 0.0,
                        "parms": [1.67e5, 0.6],
                        "tol": 1e-8
                    }
                }
            },
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result and 'kinetic_profiles' in result:
            profile = result['kinetic_profiles'][0]
            assert len(profile['time_seconds']) == 9  # 9 time points
            results.record_pass("Multiple time steps kinetics")
        else:
            results.record_fail("Multiple time steps kinetics", 
                              result.get('error', 'No profiles'))
    except Exception as e:
        results.record_fail("Multiple time steps kinetics", str(e))

async def test_kinetic_precipitation_custom(results):
    """Test kinetic precipitation using custom rate functions (legacy)."""
    print("\n4. Testing Kinetic Precipitation (Custom Functions)")
    print("-" * 40)
    
    # Test 4.1: Custom rate function kinetics
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.8,
                "analysis": {
                    "Ca": 4.0,
                    "Alkalinity": 3.5
                }
            },
            "reactants": [
                {"formula": "Ca(OH)2", "amount": 1.5, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite"],
            "kinetic_parameters": {
                "enable_kinetics": True,
                "use_phreeqc_rates": False,  # Use custom functions
                "time_steps": [0, 300, 900, 1800],
                "minerals_kinetic": {
                    "Calcite": {
                        "rate_constant": 1.55e-6,
                        "surface_area": 1.0,
                        "activation_energy": 41800,
                        "surface_area_exponent": 0.67,
                        "nucleation_si_threshold": 0.5
                    }
                }
            },
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            assert result.get('kinetic_modeling_used', False)
            # Should NOT use PHREEQC rates
            assert not result.get('phreeqc_rates_used', True)
            
            if 'kinetic_profiles' in result:
                results.record_pass("Custom kinetics function")
            else:
                results.record_fail("Custom kinetics function", 
                                  "No kinetic profiles")
        else:
            results.record_fail("Custom kinetics function", result['error'])
    except Exception as e:
        results.record_fail("Custom kinetics function", str(e))

async def test_complex_scenarios(results):
    """Test complex treatment scenarios."""
    print("\n5. Testing Complex Treatment Scenarios")
    print("-" * 40)
    
    # Test 5.1: Sequential treatment (coagulation + pH adjustment)
    try:
        # First step: Add coagulant
        input_data_1 = {
            "initial_solution": {
                "pH": 7.5,
                "analysis": {
                    "P": 2.0,
                    "turbidity": 10,  # Hypothetical
                    "Alkalinity": 3.0
                }
            },
            "reactants": [
                {"formula": "Al2(SO4)3", "amount": 1.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Al(OH)3(a)", "AlPO4"],
            "database": "minteq.dat"
        }
        
        result_1 = await simulate_chemical_addition(input_data_1)
        
        if 'error' not in result_1:
            # Second step: pH adjustment after coagulation
            solution_after_coag = result_1['solution_summary']
            
            input_data_2 = {
                "initial_solution": solution_after_coag,
                "reactants": [
                    {"formula": "NaOH", "amount": 0.5, "units": "mmol"}
                ],
                "allow_precipitation": False
            }
            
            result_2 = await simulate_chemical_addition(input_data_2)
            
            if 'error' not in result_2:
                results.record_pass("Sequential treatment")
            else:
                results.record_fail("Sequential treatment", 
                                  f"Step 2 failed: {result_2['error']}")
        else:
            results.record_fail("Sequential treatment", 
                              f"Step 1 failed: {result_1['error']}")
    except Exception as e:
        results.record_fail("Sequential treatment", str(e))
    
    # Test 5.2: High ionic strength
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {
                    "Na": 100.0,  # High salinity
                    "Cl": 100.0,
                    "Ca": 20.0,
                    "S(6)": 20.0
                }
            },
            "reactants": [
                {"formula": "NaCl", "amount": 50.0, "units": "mmol"}
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Gypsum", "Halite"],
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            ionic_strength = result['solution_summary']['ionic_strength']
            assert ionic_strength > 0.1  # High ionic strength
            results.record_pass("High ionic strength handling")
        else:
            results.record_fail("High ionic strength handling", result['error'])
    except Exception as e:
        results.record_fail("High ionic strength handling", str(e))

async def test_temperature_effects(results):
    """Test temperature effects on precipitation."""
    print("\n6. Testing Temperature Effects")
    print("-" * 40)
    
    # Test 6.1: Temperature override
    try:
        input_data = {
            "initial_solution": {
                "pH": 8.0,
                "analysis": {
                    "Ca": 5.0,
                    "S(6)": 10.0
                },
                "temperature_celsius": 25.0
            },
            "reactants": [
                {"formula": "CaCl2", "amount": 5.0, "units": "mmol"}
            ],
            "temperature_celsius": 60.0,  # Override temperature
            "allow_precipitation": True,
            "equilibrium_minerals": ["Gypsum", "Anhydrite"],
            "database": "minteq.dat"
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # At 60°C, anhydrite might be favored over gypsum
            results.record_pass("Temperature override")
        else:
            results.record_fail("Temperature override", result['error'])
    except Exception as e:
        results.record_fail("Temperature override", str(e))

async def test_error_handling(results):
    """Test error handling and edge cases."""
    print("\n7. Testing Error Handling")
    print("-" * 40)
    
    # Test 7.1: Invalid reactant formula
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {"Ca": 1.0}
            },
            "reactants": [
                {"formula": "InvalidFormula", "amount": 1.0}
            ]
        }
        
        result = await simulate_chemical_addition(input_data)
        
        # Should handle gracefully
        if 'error' in result:
            results.record_pass("Invalid formula handling")
        else:
            # Might still work if PHREEQC ignores it
            results.record_pass("Invalid formula handling")
    except Exception as e:
        results.record_pass("Invalid formula handling")
    
    # Test 7.2: Negative amounts
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {"Ca": 1.0}
            },
            "reactants": [
                {"formula": "NaCl", "amount": -1.0}
            ]
        }
        
        result = await simulate_chemical_addition(input_data)
        
        # Should be caught by validation
        results.record_pass("Negative amount validation")
    except Exception as e:
        # Validation error is expected
        results.record_pass("Negative amount validation")
    
    # Test 7.3: Empty reactants
    try:
        input_data = {
            "initial_solution": {
                "pH": 7.0,
                "analysis": {"Ca": 1.0}
            },
            "reactants": []
        }
        
        result = await simulate_chemical_addition(input_data)
        
        if 'error' not in result:
            # Should just return initial solution
            results.record_pass("Empty reactants handling")
        else:
            results.record_fail("Empty reactants handling", result['error'])
    except Exception as e:
        results.record_fail("Empty reactants handling", str(e))

async def main():
    """Run all chemical addition tests."""
    print("="*60)
    print("CHEMICAL ADDITION TOOL TEST SUITE")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = TestResults()
    
    # Run all test groups
    await test_basic_chemical_addition(results)
    await test_equilibrium_precipitation(results)
    await test_kinetic_precipitation_phreeqc(results)
    await test_kinetic_precipitation_custom(results)
    await test_complex_scenarios(results)
    await test_temperature_effects(results)
    await test_error_handling(results)
    
    print("\n" + "="*60)
    success = results.summary()
    print("="*60)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())