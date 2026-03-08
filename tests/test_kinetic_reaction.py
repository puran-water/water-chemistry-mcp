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
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.kinetic_reaction import simulate_kinetic_reaction_time_series

async def test_simple_first_order_reaction():
    """Test a simple first-order kinetic reaction (denitrification)."""

    print("\n===== TEST 1: SIMPLE FIRST-ORDER REACTION =====")

    # Define test input data
    # Denitrification: removes dissolved nitrogen from solution
    # Formula uses bare element symbol "N" (not redox species "N(5)" / "N(3)")
    # because PHREEQC -formula only accepts element symbols.
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
                "N(5)": 10  # Nitrate for the reaction
            },
            "ph": 7.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Denitrification",
                    "formula": {"N": -1},  # Remove N from solution per mol of reaction
                    "parameters": {
                        "m0": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Denitrification",
                    "rate_law": """
                        rate = -0.1 * TOT("N(5)")
                        moles = rate * TIME
                        SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "time_values": [3600, 7200, 14400],  # 1, 2, and 4 hours in seconds
            "units": "seconds"
        },
        "database": "phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }

    # Run the tool
    print("Running simulate_kinetic_reaction_time_series tool...")
    result = await simulate_kinetic_reaction_time_series(input_data)

    # Check and print results
    print_results(result)

    # Validate the result
    validate_result(result)



async def test_complex_reaction_with_formula():
    """Test a more complex kinetic reaction with formula (oxygen consumption)."""

    print("\n===== TEST 2: COMPLEX REACTION WITH FORMULA =====")

    # Calcite dissolution: a well-supported kinetic reaction in phreeqc.dat.
    # Uses Monod-type rate law referencing SI("Calcite") for saturation control.
    input_data = {
        "initial_solution": {
            "analysis": {
                "Ca": 20,
                "Mg": 10,
                "Na": 20,
                "K": 5,
                "Cl": 70,
                "S(6)": 30,
                "C(4)": 5
            },
            "ph": 6.0,
            "temperature_celsius": 25.0,
            "pressure_atm": 1.0,
            "units": "mg/L"
        },
        "kinetic_reactions": {
            "reactions": [
                {
                    "name": "Calcite_dissolution",
                    "formula": {"Ca": 1, "C": 1},  # Dissolving CaCO3 adds Ca and C
                    "parameters": {
                        "m0": 5.0,
                        "tol": 1e-8
                    }
                }
            ],
            "rates": [
                {
                    "name": "Calcite_dissolution",
                    "rate_law": """
10 si_cc = SI("Calcite")
20 IF (M <= 0 AND si_cc < 0) THEN GOTO 200
30 k1 = 10^(0.198 - 444.0 / TK)
40 k2 = 10^(2.84 - 2177.0 / TK)
50 IF (-si_cc >= 14) THEN si_cc = -14
60 rate = k1 * ACT("H+") + k2 * ACT("CO2")
70 rate = rate * (1 - 10^(2.0*si_cc))
80 moles = rate * TIME
90 IF (moles > M) THEN moles = M
200 SAVE moles
                    """
                }
            ]
        },
        "time_steps": {
            "time_values": [3600, 7200, 14400, 28800],
            "units": "seconds"
        },
        "database": "phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }

    # Run the tool
    print("Running simulate_kinetic_reaction_time_series tool...")
    result = await simulate_kinetic_reaction_time_series(input_data)

    # Check and print results
    print_results(result)

    # Validate the result
    validate_result(result)



async def test_multiple_kinetic_reactions():
    """Test multiple concurrent kinetic reactions."""

    print("\n===== TEST 3: MULTIPLE KINETIC REACTIONS =====")

    # Two reactions: denitrification (remove N) and oxygen consumption.
    # Formulas use bare element symbols only.
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
                    "name": "Denitrification",
                    "formula": {"N": -1},  # Remove N from solution
                    "parameters": {
                        "m0": 0
                    }
                },
                {
                    "name": "Oxygen_consumption",
                    "formula": {"O": -0.25},  # Consume O (e.g., for Fe oxidation)
                    "parameters": {
                        "m0": 0
                    }
                }
            ],
            "rates": [
                {
                    "name": "Denitrification",
                    "rate_law": """
                        rate = -0.05 * TOT("N(5)")
                        moles = rate * TIME
                        SAVE moles
                    """
                },
                {
                    "name": "Oxygen_consumption",
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
            "time_values": [3600, 7200, 14400, 28800],
            "units": "seconds"
        },
        "database": "phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }

    # Run the tool
    print("Running simulate_kinetic_reaction_time_series tool...")
    result = await simulate_kinetic_reaction_time_series(input_data)

    # Check and print results
    print_results(result)

    # Validate the result
    validate_result(result)



async def test_various_time_steps():
    """Test various time step configurations (duration/count instead of explicit values)."""

    print("\n===== TEST 4: VARIOUS TIME STEPS =====")

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
                    "formula": {"N": -1},  # Remove N from solution
                    "parameters": {
                        "m0": 0
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
        "database": "phreeqc.dat",
        "allow_precipitation": False,
        "equilibrium_minerals": []
    }

    # Run the tool
    print("Running simulate_kinetic_reaction_time_series tool...")
    result = await simulate_kinetic_reaction_time_series(input_data)

    # Check and print results
    print_results(result)

    # Validate the result
    validate_result(result)



def print_results(result):
    """Print the results in a readable format."""
    print("\nResults:")
    if "error" in result and result["error"]:
        print(f"ERROR: {result['error']}")
        return

    # Print time series info
    if "time_series" in result:
        time_series = result["time_series"]
        print(f"\nNumber of time steps: {len(time_series)}")

        # Print a few key time steps
        steps_to_print = min(3, len(time_series))
        print(f"\nFirst {steps_to_print} time steps:")
        for i in range(steps_to_print):
            step = time_series[i]
            print(f"\nTime Step {i+1}:")

            if isinstance(step, dict):
                # Print solution summary
                if "solution_summary" in step:
                    summary = step["solution_summary"]
                    print(f"  pH: {summary.get('pH')}")
                    print(f"  pe: {summary.get('pe')}")

                # Print key concentrations if available
                if "total_element_molalities" in step:
                    elements = step["total_element_molalities"]
                    print("\n  Key element concentrations (mol/kgw):")
                    for element in ["N", "Fe", "O", "C"]:
                        if element in elements:
                            print(f"    {element}: {elements[element]}")

    # Print final state info
    if "final_state" in result:
        final = result["final_state"]
        print(f"\nFinal state:")
        if isinstance(final, dict):
            if "solution_summary" in final:
                summary = final["solution_summary"]
                print(f"  pH: {summary.get('pH')}")
                print(f"  pe: {summary.get('pe')}")

def validate_result(result):
    """Validate the result against expectations. Raises AssertionError on failure."""
    assert "error" not in result or not result["error"], f"Got error: {result.get('error')}"

    has_time_series = "time_series" in result and result["time_series"]
    has_final_state = "final_state" in result and result["final_state"]

    assert has_time_series or has_final_state, "Missing both 'time_series' and 'final_state' in result"

    if has_time_series:
        time_series = result["time_series"]
        assert len(time_series) > 0, "Empty time_series"
        print(f"  time_series has {len(time_series)} entries")

    if has_final_state:
        final = result["final_state"]
        if isinstance(final, dict) and "solution_summary" in final:
            print(f"  final_state has solution_summary")
        else:
            print(f"  final_state present (type: {type(final).__name__})")

    print("PASSED: All validation checks succeeded")

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
            import traceback
            traceback.print_exc()
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
