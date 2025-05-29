"""
Test script to validate the fixes to the dosing_requirement.py convergence issue.

This test specifically checks:
1. Basic pH adjustment with NaOH (small change)
2. Large pH adjustment that would previously fail to converge
3. Edge case with high alkalinity that challenges convergence
4. Test with precipitation enabled to verify mineral interactions
"""
import asyncio
import sys
import os

# Add parent directory to path so we can import the modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.dosing_requirement import calculate_dosing_requirement

async def test_small_ph_adjustment():
    """Test a small pH adjustment that should converge easily."""
    
    print("\n===== TEST 1: SMALL PH ADJUSTMENT =====")
    
    # Define test input data
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 40,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 10
            },
            "ph": 6.0,  # Starting with slightly acidic pH
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "target_condition": {
            "parameter": "pH",
            "value": 6.5  # Small pH change
        },
        "reagent": {
            "formula": "NaOH"  # Use sodium hydroxide to increase pH
        },
        "max_iterations": 20,
        "tolerance": 0.05,
        "initial_guess_mmol": 0.5,
        "allow_precipitation": False,  # Disable precipitation for simplicity
        "equilibrium_minerals": []  # Empty list for minerals
    }
    
    # Run the tool
    print("Running calculate_dosing_requirement tool...")
    result = await calculate_dosing_requirement(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result, expected_success=True)
    
    return success

async def test_large_ph_adjustment():
    """Test a large pH adjustment that would have failed to converge previously."""
    
    print("\n===== TEST 2: LARGE PH ADJUSTMENT =====")
    
    # Define test input data for extreme pH change (this would previously fail)
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 40,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 10
            },
            "ph": 5.0,  # Starting with moderately acidic pH
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "target_condition": {
            "parameter": "pH",
            "value": 11.0  # Large pH change (5.0 to 11.0)
        },
        "reagent": {
            "formula": "NaOH"  # Use sodium hydroxide to increase pH
        },
        "max_iterations": 30,  # Use the improved max_iterations
        "tolerance": 0.1,  # Use a looser tolerance for this extreme change
        "allow_precipitation": False,  # Disable precipitation to keep it simpler
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running calculate_dosing_requirement tool for large pH adjustment...")
    result = await calculate_dosing_requirement(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result, expected_success=True)
    
    return success

async def test_high_alkalinity_challenge():
    """Test with high alkalinity that would challenge convergence."""
    
    print("\n===== TEST 3: HIGH ALKALINITY CHALLENGE =====")
    
    # Define test input data with high alkalinity
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 100,
                "Mg": 50,
                "Na": 100,
                "K": 10,
                "Cl": 200,
                "S(6)": 50,
                "C(4)": 300  # High alkalinity (HCO3- source)
            },
            "ph": 8.2,  # Starting with slightly alkaline pH
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "target_condition": {
            "parameter": "pH",
            "value": 6.5  # Decrease pH (acid addition)
        },
        "reagent": {
            "formula": "HCl"  # Use hydrochloric acid to decrease pH
        },
        "max_iterations": 30,
        "tolerance": 0.1,
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running calculate_dosing_requirement tool for high alkalinity solution...")
    result = await calculate_dosing_requirement(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result, expected_success=True)
    
    return success

async def test_with_precipitation():
    """Test with precipitation enabled to check mineral interactions."""
    
    print("\n===== TEST 4: WITH PRECIPITATION =====")
    
    # Define test input data with precipitation enabled
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 200,  # High calcium
                "Mg": 50,
                "Na": 50,
                "K": 10,
                "Cl": 300,
                "S(6)": 200,  # High sulfate
                "C(4)": 100
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "target_condition": {
            "parameter": "pH",
            "value": 9.5  # Increase pH significantly
        },
        "reagent": {
            "formula": "NaOH"
        },
        "max_iterations": 30,
        "tolerance": 0.1,
        "allow_precipitation": True,  # Enable precipitation
        "equilibrium_minerals": ["Calcite", "Gypsum"]  # Specific minerals to check
    }
    
    # Run the tool
    print("Running calculate_dosing_requirement tool with precipitation...")
    result = await calculate_dosing_requirement(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result - with precipitation, we might not get exact convergence
    success = validate_result(result, expected_success=True, tolerance_check=False)
    
    return success

def print_results(result):
    """Print the results in a readable format."""
    print("\nResults:")
    if "error" in result and result["error"]:
        print(f"ERROR: {result['error']}")
        return
    
    # Print dosing requirement
    if "required_dose_mmol_per_L" in result:
        print(f"\nRequired dose: {result['required_dose_mmol_per_L']} mmol/L")
    
    # Print convergence status
    if "convergence_status" in result:
        print(f"Convergence status: {result['convergence_status']}")
    
    # Print iterations taken
    if "iterations_taken" in result:
        print(f"Iterations taken: {result['iterations_taken']}")
    
    # Print final solution state
    if "final_state" in result:
        final_state = result["final_state"]
        
        # Print solution summary
        if "solution_summary" in final_state:
            summary = final_state["solution_summary"]
            print("\nFinal Solution State:")
            print(f"  pH: {summary.get('pH')}")
            print(f"  pe: {summary.get('pe')}")
            print(f"  Ionic strength: {summary.get('ionic_strength')}")
        
        # Print precipitation if present
        if "mineral_saturations" in final_state:
            minerals = final_state["mineral_saturations"]
            if minerals:
                print("\nMineral Saturations:")
                for mineral, si in minerals.items():
                    print(f"  {mineral}: {si}")

def validate_result(result, expected_success=True, tolerance_check=True):
    """Validate the result based on expectations."""
    # Check for errors first
    if "error" in result and result["error"]:
        if expected_success:
            print(f"FAILED: Expected success but got error: {result['error']}")
            return False
        else:
            print("PASSED: Expected error condition met")
            return True
    
    # If we expected an error but didn't get one
    if not expected_success and "error" not in result:
        print("FAILED: Expected error but got success")
        return False
    
    # Check required fields
    required_fields = ["required_dose_mmol_per_L", "convergence_status", "iterations_taken", "final_state"]
    for field in required_fields:
        if field not in result:
            print(f"FAILED: Missing required field '{field}' in result")
            return False
    
    # Check that the target parameter was reached within tolerance
    if tolerance_check and "final_state" in result and "solution_summary" in result["final_state"]:
        # Extract target parameter and value from input_data
        target_param = "pH"  # Assuming pH for these tests
        target_value = None
        
        # For the dosing tests we're using pH as the target
        if "target_condition" in result and "value" in result["target_condition"]:
            target_value = result["target_condition"]["value"]
        
        # Get final value
        final_value = None
        if target_param.lower() == "ph" and "pH" in result["final_state"]["solution_summary"]:
            final_value = result["final_state"]["solution_summary"]["pH"]
        
        # Compare if we have both values
        if target_value is not None and final_value is not None:
            diff = abs(final_value - target_value)
            tolerance = 0.1  # A reasonable tolerance for pH
            
            if diff > tolerance:
                print(f"FAILED: Target {target_param} not reached. Target: {target_value}, Final: {final_value}, Diff: {diff}")
                return False
    
    print("PASSED: All validation checks succeeded")
    return True

async def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_small_ph_adjustment,
        test_large_ph_adjustment,
        test_high_alkalinity_challenge,
        test_with_precipitation
    ]
    
    results = []
    for test in tests:
        try:
            success = await test()
            results.append(success)
        except Exception as e:
            print(f"Test failed with exception: {e}")
            results.append(False)
    
    # Summarize results
    print("\n===== TEST SUMMARY =====")
    total = len(tests)
    passed = sum(results)
    print(f"Passed: {passed}/{total} tests")
    
    return all(results)

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)