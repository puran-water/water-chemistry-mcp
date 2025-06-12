"""
Comprehensive test script for calculate_dosing_requirement tool.
Tests both equilibrium and kinetic precipitation modeling.

Run from Windows cmd.exe with:
cd C:/Users/hvksh/mcp-servers
venv/Scripts/activate
cd water-chemistry-mcp
python tests/test_dosing_requirement.py
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.dosing_requirement import calculate_dosing_requirement
from tools.phreeqc_wrapper import find_reactant_dose_for_target, build_solution_block


class TestResults:
    """Track test results"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        
    def record_pass(self, test_name: str):
        self.passed += 1
        print(f"✓ {test_name}")
        
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"✗ {test_name}: {error}")
        
    def print_summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"Test Summary: {self.passed}/{total} passed")
        if self.errors:
            print("\nFailed tests:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        print(f"{'='*60}")


async def test_basic_ph_adjustment_caustic(results: TestResults):
    """Test caustic dosing for pH adjustment"""
    test_name = "Basic pH adjustment with NaOH"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Ca": 100,
                    "Mg": 50,
                    "Cl": 200,
                    "S(6)": 100,
                    "ph": 5.5
                },
                "temperature": 25
            },
            "target_condition": {
                "parameter": "ph",
                "value": 8.5
            },
            "reagent": {
                "formula": "NaOH"
            },
            "max_iterations": 50,
            "tolerance": 0.1
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        # Assertions
        assert "error" not in result, f"Error in calculation: {result.get('error')}"
        assert result["convergence_status"] == "Converged", "Failed to converge"
        assert result["optimal_dose"] > 0, "Dose should be positive"
        assert abs(result["final_value"] - 8.5) < 0.1, f"Final pH {result['final_value']} not close to target"
        assert result["dose_unit"] == "mmol/L", "Incorrect dose unit"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_acid_dosing(results: TestResults):
    """Test acid dosing for pH reduction"""
    test_name = "pH reduction with HCl"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Na": 200,
                    "Cl": 100,
                    "Alkalinity": 300,
                    "ph": 9.5
                },
                "temperature": 20
            },
            "target_condition": {
                "parameter": "ph",
                "value": 7.0
            },
            "reagent": {
                "formula": "HCl"
            },
            "max_iterations": 50,
            "tolerance": 0.05
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        assert abs(result["final_value"] - 7.0) < 0.05
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_phosphorus_removal_equilibrium(results: TestResults):
    """Test ferric chloride dosing for phosphorus removal - equilibrium mode"""
    test_name = "Phosphorus removal with FeCl3 (equilibrium)"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "P": 10,  # 10 mg/L as P
                    "ph": 7.0,
                    "Alkalinity": 200,
                    "Cl": 100
                },
                "temperature": 25
            },
            "target_condition": {
                "parameter": "P",
                "value": 0.5  # Target 0.5 mg/L
            },
            "reagent": {
                "formula": "FeCl3"
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Strengite", "FePO4"],
            "max_iterations": 50,
            "tolerance": 0.1
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        assert result["final_value"] < 1.0, "Phosphorus not adequately removed"
        assert "precipitated_minerals" in result
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_phosphorus_removal_kinetic(results: TestResults):
    """Test ferric chloride dosing for phosphorus removal - kinetic mode"""
    test_name = "Phosphorus removal with FeCl3 (kinetic)"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "P": 10,  # 10 mg/L as P
                    "ph": 7.0,
                    "Alkalinity": 200,
                    "Cl": 100
                },
                "temperature": 25
            },
            "target_condition": {
                "parameter": "P",
                "value": 0.5  # Target 0.5 mg/L
            },
            "reagent": {
                "formula": "FeCl3"
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Strengite"],
            "kinetic_precipitation": {
                "enable": True,
                "time_hours": 2.0,  # 2 hour reaction time
                "minerals": {
                    "Strengite": {
                        "m0": 0.0,
                        "parms": [1.0, 0.67, 0.0, 1.0, 2.0]  # Surface area parameters
                    }
                }
            },
            "max_iterations": 50,
            "tolerance": 0.1
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        # Kinetic dose might be higher than equilibrium
        assert "kinetic_results" in result or "precipitated_minerals" in result
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_lime_softening_equilibrium(results: TestResults):
    """Test lime dosing for hardness removal - equilibrium"""
    test_name = "Lime softening (equilibrium)"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Ca": 200,  # High hardness
                    "Mg": 100,
                    "Alkalinity": 150,
                    "ph": 7.5,
                    "Cl": 300
                },
                "temperature": 15
            },
            "target_condition": {
                "parameter": "Ca",
                "value": 50  # Target Ca < 50 mg/L
            },
            "reagent": {
                "formula": "Ca(OH)2"  # Lime
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Brucite", "Dolomite"],
            "max_iterations": 50,
            "tolerance": 5.0  # mg/L tolerance
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        assert result["final_value"] < 100, "Calcium not adequately reduced"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_lime_softening_kinetic(results: TestResults):
    """Test lime dosing for hardness removal - kinetic with PHREEQC rates"""
    test_name = "Lime softening (kinetic PHREEQC)"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Ca": 200,
                    "Mg": 100,
                    "Alkalinity": 150,
                    "ph": 7.5,
                    "Cl": 300
                },
                "temperature": 15
            },
            "target_condition": {
                "parameter": "Ca",
                "value": 50
            },
            "reagent": {
                "formula": "Ca(OH)2"
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Brucite"],
            "kinetic_precipitation": {
                "enable": True,
                "time_hours": 4.0,
                "use_phreeqc_rates": True,
                "minerals": {
                    "Calcite": {
                        "m0": 0.0,
                        "parms": [1.0, 0.67, 0.0, 1.0, 2.0]
                    }
                }
            },
            "max_iterations": 50,
            "tolerance": 5.0
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_alkalinity_adjustment(results: TestResults):
    """Test dosing to achieve target alkalinity"""
    test_name = "Alkalinity adjustment with Na2CO3"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Ca": 50,
                    "Mg": 20,
                    "Cl": 100,
                    "Alkalinity": 50,  # Low alkalinity
                    "ph": 7.0
                },
                "temperature": 25
            },
            "target_condition": {
                "parameter": "Alkalinity",
                "value": 150  # Target 150 mg/L as CaCO3
            },
            "reagent": {
                "formula": "Na2CO3"  # Soda ash
            },
            "max_iterations": 50,
            "tolerance": 5.0
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        assert result["convergence_status"] == "Converged"
        assert result["optimal_dose"] > 0
        assert abs(result["final_value"] - 150) < 10, "Alkalinity target not achieved"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multiple_constraints(results: TestResults):
    """Test dosing with consideration of multiple water quality parameters"""
    test_name = "Complex dosing with multiple constraints"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Ca": 150,
                    "Mg": 80,
                    "Na": 100,
                    "Cl": 200,
                    "S(6)": 150,
                    "Alkalinity": 100,
                    "ph": 6.5
                },
                "temperature": 20
            },
            "target_condition": {
                "parameter": "ph",
                "value": 8.3
            },
            "reagent": {
                "formula": "NaOH"
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Brucite", "Gypsum"],
            "secondary_targets": [
                {"parameter": "Ca", "max_value": 200},
                {"parameter": "S(6)", "max_value": 250}
            ],
            "max_iterations": 75,
            "tolerance": 0.1
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        # May or may not converge depending on constraints
        if result["convergence_status"] == "Converged":
            assert abs(result["final_value"] - 8.3) < 0.1
        else:
            assert "iterations" in result
            
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_heavy_metal_removal(results: TestResults):
    """Test dosing for heavy metal precipitation"""
    test_name = "Heavy metal removal with lime"
    
    try:
        input_data = {
            "initial_solution": {
                "analysis": {
                    "Cu": 5.0,  # 5 mg/L copper
                    "Zn": 10.0,  # 10 mg/L zinc
                    "Pb": 2.0,   # 2 mg/L lead
                    "ph": 5.5,
                    "Cl": 100,
                    "S(6)": 50
                },
                "temperature": 25
            },
            "target_condition": {
                "parameter": "Cu",
                "value": 0.1  # Target < 0.1 mg/L
            },
            "reagent": {
                "formula": "Ca(OH)2"
            },
            "allow_precipitation": True,
            "equilibrium_minerals": ["Tenorite", "Zincite", "Cerussite"],
            "max_iterations": 50,
            "tolerance": 0.05
        }
        
        result = await calculate_dosing_requirement(input_data)
        
        assert "error" not in result
        # Heavy metals typically precipitate at high pH
        if result["convergence_status"] == "Converged":
            assert result["optimal_dose"] > 0
            assert result["final_value"] < 0.5
            
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_edge_cases(results: TestResults):
    """Test edge cases and error handling"""
    test_name = "Edge cases and error handling"
    
    try:
        # Test 1: Invalid target parameter
        input_data = {
            "initial_solution": {
                "analysis": {"Ca": 100, "ph": 7.0}
            },
            "target_condition": {
                "parameter": "InvalidParam",
                "value": 10
            },
            "reagent": {"formula": "NaOH"}
        }
        
        result = await calculate_dosing_requirement(input_data)
        assert "error" in result or result["convergence_status"] != "Converged"
        
        # Test 2: Unreachable target
        input_data = {
            "initial_solution": {
                "analysis": {"Ca": 100, "ph": 7.0}
            },
            "target_condition": {
                "parameter": "ph",
                "value": 14.5  # Unreachable pH
            },
            "reagent": {"formula": "NaOH"},
            "max_iterations": 10
        }
        
        result = await calculate_dosing_requirement(input_data)
        assert result["convergence_status"] != "Converged"
        
        # Test 3: Zero tolerance
        input_data = {
            "initial_solution": {
                "analysis": {"Ca": 100, "ph": 7.0}
            },
            "target_condition": {
                "parameter": "ph",
                "value": 8.0
            },
            "reagent": {"formula": "NaOH"},
            "tolerance": 0.0,
            "max_iterations": 20
        }
        
        result = await calculate_dosing_requirement(input_data)
        # Should still work but might need more iterations
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_temperature_effects(results: TestResults):
    """Test temperature effects on dosing calculations"""
    test_name = "Temperature effects on dosing"
    
    try:
        # Compare dosing at different temperatures
        doses = []
        
        for temp in [5, 15, 25, 35]:
            input_data = {
                "initial_solution": {
                    "analysis": {
                        "Ca": 200,
                        "Alkalinity": 150,
                        "ph": 7.5,
                        "Cl": 100
                    },
                    "temperature": temp
                },
                "target_condition": {
                    "parameter": "ph",
                    "value": 10.5
                },
                "reagent": {
                    "formula": "Ca(OH)2"
                },
                "allow_precipitation": True,
                "equilibrium_minerals": ["Calcite"],
                "max_iterations": 50,
                "tolerance": 0.1
            }
            
            result = await calculate_dosing_requirement(input_data)
            if result["convergence_status"] == "Converged":
                doses.append((temp, result["optimal_dose"]))
        
        # Dosing requirements should vary with temperature
        assert len(doses) >= 2, "Not enough successful calculations"
        assert len(set(dose for _, dose in doses)) > 1, "Temperature has no effect"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_composite_parameters_phreeqc_selected_output(results: TestResults):
    """Test that composite parameters use PHREEQC SELECTED_OUTPUT calculations"""
    test_name = "Composite Parameters PHREEQC SELECTED_OUTPUT"
    
    try:
        # Hard water with high Ca and Mg
        hard_water = {
            'temperature_celsius': 25.0,
            'pressure_atm': 1.0,
            'pH': 7.5,
            'pe': 4.0,
            'units': 'mg/L',
            'analysis': {
                'Ca': 200,     # High calcium
                'Mg': 50,      # High magnesium 
                'HCO3': 300,   # High bicarbonate
                'SO4': 100,
                'Cl': 50
            }
        }
        
        solution_str = build_solution_block(hard_water)
        
        # Test total hardness target using PHREEQC calculations
        dose, final_results, iterations = await find_reactant_dose_for_target(
            initial_solution_str=solution_str,
            target_parameter='total_hardness',
            target_value=85.0,  # Target hardness in mg/L as CaCO3
            reagent_formula='Ca(OH)2',
            target_units='mg/L as CaCO3',
            initial_guess_mmol=1.0,
            max_iterations=15,
            tolerance=5.0,  # 5 mg/L tolerance
            database_path='databases/official/minteq.dat',
            allow_precipitation=True
        )
        
        # Verify that dose was found
        assert dose is not None, "No dose found for total hardness target"
        assert 0.1 <= dose <= 50.0, f"Unrealistic dose: {dose} mmol/L"
        assert iterations <= 15, f"Too many iterations: {iterations}"
        
        # Verify that selected_output_data exists in results
        assert 'selected_output_data' in final_results, "SELECTED_OUTPUT data not found in results"
        selected_output = final_results['selected_output_data']
        
        # Check if PHREEQC calculated total hardness directly
        if 'Total_Hardness_CaCO3' in selected_output:
            phreeqc_hardness = selected_output['Total_Hardness_CaCO3']
            assert isinstance(phreeqc_hardness, (int, float)), "PHREEQC hardness not numeric"
            assert 50 <= phreeqc_hardness <= 150, f"PHREEQC hardness out of range: {phreeqc_hardness}"
            print(f"  PHREEQC calculated hardness: {phreeqc_hardness:.1f} mg/L as CaCO3")
        
        # Also verify manual calculation still works as fallback
        if 'element_totals_molality' in final_results:
            totals = final_results['element_totals_molality']
            ca_molal = totals.get('Ca', 0)
            mg_molal = totals.get('Mg', 0)
            manual_hardness = (ca_molal + mg_molal) * 100000  # Convert to mg/L as CaCO3
            print(f"  Manual calculation hardness: {manual_hardness:.1f} mg/L as CaCO3")
            
            # If both exist, they should be similar
            if 'Total_Hardness_CaCO3' in selected_output:
                phreeqc_hardness = selected_output['Total_Hardness_CaCO3']
                diff = abs(phreeqc_hardness - manual_hardness)
                assert diff < 20, f"PHREEQC vs manual hardness differ too much: {diff:.1f} mg/L"
        
        print(f"  Final dose: {dose:.3f} mmol/L Ca(OH)2 after {iterations} iterations")
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def main():
    """Run all tests"""
    print(f"\nCalculate Dosing Requirement Tool Test Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = TestResults()
    
    # Run all tests
    await test_basic_ph_adjustment_caustic(results)
    await test_acid_dosing(results)
    await test_phosphorus_removal_equilibrium(results)
    await test_phosphorus_removal_kinetic(results)
    await test_lime_softening_equilibrium(results)
    await test_lime_softening_kinetic(results)
    await test_alkalinity_adjustment(results)
    await test_multiple_constraints(results)
    await test_heavy_metal_removal(results)
    await test_edge_cases(results)
    await test_temperature_effects(results)
    await test_composite_parameters_phreeqc_selected_output(results)
    
    # Print summary
    results.print_summary()
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())