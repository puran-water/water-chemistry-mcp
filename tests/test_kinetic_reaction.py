"""
Test script to validate the fixes to the kinetic_reaction.py processing issue.

This test specifically checks:
1. Basic first-order kinetic reaction
2. More complex kinetic reaction with formula
3. Multiple kinetic reactions
4. Various time step configurations
"""
import asyncio
import sys
import os
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add parent directory to path so we can import the modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.kinetic_reaction import simulate_kinetic_reaction

async def test_simple_first_order_reaction():
    """Test a simple first-order kinetic reaction."""
    
    print("\n===== TEST 1: SIMPLE FIRST-ORDER REACTION =====")
    
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
                "C(4)": 10,
                "N(5)": 10  # Add nitrate for the reaction
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Nitrate_reduction",
                    "formula": {"N(5)": -1, "N(3)": 1},  # Nitrate to nitrite
                    "parameters": {
                        "initial_moles": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Nitrate_reduction",
                    "rate_law": """
                        rate = -0.1 * TOT("N(5)")  # First-order decay
                        moles = rate * TIME
                        SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "values": [3600, 7200, 14400],  # 1, 2, and 4 hours in seconds
            "units": "seconds"
        },
        "database": "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database\\phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running simulate_kinetic_reaction tool...")
    result = await simulate_kinetic_reaction(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result)
    
    return success

async def test_complex_reaction_with_formula():
    """Test a more complex kinetic reaction with formula."""
    
    print("\n===== TEST 2: COMPLEX REACTION WITH FORMULA =====")
    
    # Define test input data for a more complex reaction
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 40,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 10,
                "N(5)": 10,
                "O(0)": 8  # Dissolved oxygen
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Organic_oxidation",
                    "formula": {"C": -1, "O(0)": -1, "C(4)": 1},  # Carbon oxidation
                    "parameters": {
                        "initial_moles": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Organic_oxidation",
                    "rate_law": """
                        # More complex rate law with multiple factors
                        k_rate = 0.02  # Rate constant
                        # Rate depends on both organic carbon and oxygen
                        org_C = 10  # Assumed initial organic carbon (mg/L)
                        rate = -k_rate * org_C * TOT("O(0)") / (TOT("O(0)") + 0.5)  # Monod-type kinetics
                        moles = rate * TIME
                        SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "values": [3600, 7200, 14400, 28800, 86400],  # Up to 1 day
            "units": "seconds"
        },
        "database": "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database\\phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running simulate_kinetic_reaction tool...")
    result = await simulate_kinetic_reaction(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result)
    
    return success

async def test_multiple_kinetic_reactions():
    """Test multiple concurrent kinetic reactions."""
    
    print("\n===== TEST 3: MULTIPLE KINETIC REACTIONS =====")
    
    # Define test input data with multiple reactions
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 40,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 10,
                "N(5)": 10,
                "Fe(2)": 5,
                "O(0)": 8
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Nitrate_reduction",
                    "formula": {"N(5)": -1, "N(3)": 1},
                    "parameters": {
                        "initial_moles": 0
                    }
                },
                {
                    "name": "Iron_oxidation",
                    "formula": {"Fe(2)": -1, "Fe(3)": 1, "O(0)": -0.25},
                    "parameters": {
                        "initial_moles": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Nitrate_reduction",
                    "rate_law": """
                        rate = -0.05 * TOT("N(5)")
                        moles = rate * TIME
                        SAVE moles
                    """
                },
                {
                    "name": "Iron_oxidation",
                    "rate_law": """
                        k = 0.1
                        rate = -k * TOT("Fe(2)") * (TOT("O(0)") / (TOT("O(0)") + 0.2))
                        moles = rate * TIME
                        SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "values": [3600, 7200, 14400, 28800],
            "units": "seconds"
        },
        "database": "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database\\phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running simulate_kinetic_reaction tool...")
    result = await simulate_kinetic_reaction(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result)
    
    return success

async def test_various_time_steps():
    """Test various time step configurations."""
    
    print("\n===== TEST 4: VARIOUS TIME STEPS =====")
    
    # Define test input data with different time step configuration
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 40,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 10,
                "N(5)": 10
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Simple_first_order",
                    "formula": {"N(5)": -1},  # Just removing nitrate
                    "parameters": {
                        "initial_moles": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Simple_first_order",
                    "rate_law": """
                        rate = -0.01 * TOT("N(5)")
                        moles = rate * TIME
                        SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "duration": 86400,  # 1 day
            "count": 10,
            "duration_units": "seconds"
        },
        "database": "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database\\phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }
    
    # Run the tool
    print("Running simulate_kinetic_reaction tool...")
    result = await simulate_kinetic_reaction(input_data)
    
    # Check and print results
    print_results(result)
    
    # Validate the result
    success = validate_result(result)
    
    return success

def print_results(result):
    """Print the results in a readable format."""
    print("\nResults:")
    if "error" in result and result["error"]:
        print(f"ERROR: {result['error']}")
        return
    
    # Print time steps
    if "time_series" in result:
        time_series = result["time_series"]
        print(f"\nNumber of time steps: {len(time_series)}")
        
        # Print a few key time steps
        steps_to_print = min(3, len(time_series))
        print(f"\nFirst {steps_to_print} time steps:")
        for i in range(steps_to_print):
            step = time_series[i]
            print(f"\nTime Step {i+1} ({step.get('time_seconds', 'unknown')} seconds):")
            
            # Print solution summary
            if "solution_summary" in step:
                summary = step["solution_summary"]
                print(f"  pH: {summary.get('pH')}")
                print(f"  pe: {summary.get('pe')}")
                
            # Print key concentrations if available
            if "total_element_molalities" in step:
                elements = step["total_element_molalities"]
                print("\n  Key element concentrations (mol/kgw):")
                for element in ["N(5)", "N(3)", "Fe(2)", "Fe(3)", "O(0)", "C(4)"]:
                    if element in elements:
                        print(f"    {element}: {elements[element]}")
        
        # Print last time step
        if len(time_series) > steps_to_print:
            last_step = time_series[-1]
            print(f"\nLast Time Step ({last_step.get('time_seconds', 'unknown')} seconds):")
            
            # Print solution summary
            if "solution_summary" in last_step:
                summary = last_step["solution_summary"]
                print(f"  pH: {summary.get('pH')}")
                print(f"  pe: {summary.get('pe')}")
                
            # Print key concentrations if available
            if "total_element_molalities" in last_step:
                elements = last_step["total_element_molalities"]
                print("\n  Key element concentrations (mol/kgw):")
                for element in ["N(5)", "N(3)", "Fe(2)", "Fe(3)", "O(0)", "C(4)"]:
                    if element in elements:
                        print(f"    {element}: {elements[element]}")

def validate_result(result):
    """Validate the result against expectations."""
    # Check for errors first
    if "error" in result and result["error"]:
        print(f"FAILED: Got error: {result['error']}")
        return False
    
    # Check for required fields
    required_fields = ["time_series"]
    for field in required_fields:
        if field not in result:
            print(f"FAILED: Missing required field '{field}' in result")
            return False
    
    # Check that we have at least one time step
    if len(result["time_series"]) == 0:
        print("FAILED: No time steps in result")
        return False
    
    # Check the first and last time step have proper data
    first_step = result["time_series"][0]
    last_step = result["time_series"][-1]
    
    required_step_fields = ["time_seconds", "solution_summary"]
    for step in [first_step, last_step]:
        for field in required_step_fields:
            if field not in step:
                print(f"FAILED: Missing required field '{field}' in time step")
                return False
    
    # Check that time is increasing
    if len(result["time_series"]) > 1:
        time_values = [step.get("time_seconds", 0) for step in result["time_series"]]
        if not all(time_values[i] < time_values[i+1] for i in range(len(time_values)-1)):
            print("FAILED: Time values are not strictly increasing")
            return False
    
    print("PASSED: All validation checks succeeded")
    return True

async def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_simple_first_order_reaction,
        test_complex_reaction_with_formula,
        test_multiple_kinetic_reactions,
        test_various_time_steps
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