"""
Comprehensive test script for simulate_solution_mixing tool.
Tests both equilibrium and kinetic precipitation modeling during mixing.

Run from Windows cmd.exe with:
cd C:\Users\hvksh\mcp-servers
venv\Scripts\activate
cd water-chemistry-mcp
python tests\test_solution_mixing.py
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.solution_mixing import simulate_solution_mixing


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


async def test_basic_mixing_two_solutions(results: TestResults):
    """Test basic mixing of two solutions without precipitation"""
    test_name = "Basic mixing of two solutions"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 50,
                        "Na": 100,
                        "Cl": 150,
                        "pH": 7.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                },
                {
                    "analysis": {
                        "Ca": 150,
                        "Na": 50,
                        "Cl": 200,
                        "pH": 8.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                }
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_solution_mixing(input_data)
        
        # Assertions
        assert "error" not in result, f"Error in calculation: {result.get('error')}"
        assert "mixed_solution" in result, "No mixed solution in result"
        
        mixed = result["mixed_solution"]
        # Check mass balance (roughly)
        assert 90 < mixed["Ca"] < 110, f"Ca concentration {mixed['Ca']} not in expected range"
        assert 70 < mixed["Na"] < 80, f"Na concentration {mixed['Na']} not in expected range"
        assert 7.0 < mixed["pH"] < 8.0, f"pH {mixed['pH']} not in expected range"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_mixing_with_temperature_difference(results: TestResults):
    """Test mixing solutions at different temperatures"""
    test_name = "Mixing with temperature difference"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 100,
                        "Alkalinity": 200,
                        "pH": 7.5
                    },
                    "temperature": 10,  # Cold water
                    "volume_fraction": 0.3
                },
                {
                    "analysis": {
                        "Ca": 100,
                        "Alkalinity": 200,
                        "pH": 7.5
                    },
                    "temperature": 40,  # Hot water
                    "volume_fraction": 0.7
                }
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        mixed = result["mixed_solution"]
        
        # Temperature should be weighted average
        expected_temp = 10 * 0.3 + 40 * 0.7  # = 31°C
        assert 30 < mixed["temperature"] < 32, f"Temperature {mixed['temperature']} not as expected"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_mixing_with_equilibrium_precipitation(results: TestResults):
    """Test mixing that causes equilibrium precipitation"""
    test_name = "Mixing with equilibrium precipitation"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 500,  # High calcium
                        "Cl": 1000,
                        "pH": 7.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                },
                {
                    "analysis": {
                        "Na": 500,
                        "C(4)": 300,  # High carbonate
                        "pH": 10.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                }
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Aragonite"]
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        assert "precipitated_minerals" in result, "No precipitation occurred"
        assert len(result["precipitated_minerals"]) > 0, "Expected precipitation"
        
        # Calcium should be reduced due to precipitation
        mixed = result["mixed_solution"]
        assert mixed["Ca"] < 250, "Calcium not reduced by precipitation"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_mixing_with_kinetic_precipitation(results: TestResults):
    """Test mixing with kinetic precipitation using PHREEQC rates"""
    test_name = "Mixing with kinetic precipitation (PHREEQC)"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 400,
                        "Cl": 800,
                        "pH": 7.5
                    },
                    "temperature": 20,
                    "volume_fraction": 0.6
                },
                {
                    "analysis": {
                        "Na": 400,
                        "Alkalinity": 400,
                        "pH": 9.5
                    },
                    "temperature": 20,
                    "volume_fraction": 0.4
                }
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite"],
            "kinetic_precipitation": {
                "enable": True,
                "time_hours": 2.0,
                "use_phreeqc_rates": True,
                "minerals": {
                    "Calcite": {
                        "m0": 0.0,
                        "parms": [1.0, 0.67, 0.0, 1.0, 2.0]  # Surface area parameters
                    }
                }
            }
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        assert "kinetic_results" in result or "mixed_solution" in result
        
        # With kinetics, precipitation may be incomplete
        mixed = result["mixed_solution"]
        assert mixed["Ca"] < 400 * 0.6, "Some calcium removal expected"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multiple_solution_mixing(results: TestResults):
    """Test mixing of more than two solutions"""
    test_name = "Multiple solution mixing (3+ sources)"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 100,
                        "Na": 50,
                        "Cl": 150,
                        "pH": 6.5
                    },
                    "temperature": 15,
                    "volume_fraction": 0.4
                },
                {
                    "analysis": {
                        "Ca": 50,
                        "Na": 200,
                        "Cl": 250,
                        "pH": 7.5
                    },
                    "temperature": 20,
                    "volume_fraction": 0.3
                },
                {
                    "analysis": {
                        "Ca": 150,
                        "Na": 100,
                        "Cl": 200,
                        "pH": 8.5
                    },
                    "temperature": 25,
                    "volume_fraction": 0.3
                }
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        mixed = result["mixed_solution"]
        
        # Check weighted averages
        expected_ca = 100*0.4 + 50*0.3 + 150*0.3  # = 100
        assert 95 < mixed["Ca"] < 105, f"Ca {mixed['Ca']} not as expected"
        
        expected_temp = 15*0.4 + 20*0.3 + 25*0.3  # = 19.5
        assert 18 < mixed["temperature"] < 21, f"Temperature not as expected"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_seawater_freshwater_mixing(results: TestResults):
    """Test mixing of seawater and freshwater (desalination concentrate management)"""
    test_name = "Seawater-freshwater mixing"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Na": 10800,  # Seawater
                        "Cl": 19400,
                        "Mg": 1290,
                        "Ca": 420,
                        "K": 390,
                        "S(6)": 2700,
                        "pH": 8.2
                    },
                    "temperature": 25,
                    "volume_fraction": 0.1  # 10% seawater
                },
                {
                    "analysis": {
                        "Ca": 50,  # Freshwater
                        "Mg": 10,
                        "Na": 20,
                        "Cl": 30,
                        "Alkalinity": 100,
                        "pH": 7.5
                    },
                    "temperature": 20,
                    "volume_fraction": 0.9  # 90% freshwater
                }
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Calcite", "Aragonite", "Gypsum"]
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        mixed = result["mixed_solution"]
        
        # Check salinity reduction
        assert mixed["Cl"] < 2000, "Chloride should be diluted"
        assert mixed["Na"] < 1100, "Sodium should be diluted"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_industrial_wastewater_mixing(results: TestResults):
    """Test mixing of different industrial waste streams"""
    test_name = "Industrial wastewater mixing"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Cu": 50,  # Metal plating waste
                        "Ni": 30,
                        "Zn": 20,
                        "pH": 3.0,
                        "Cl": 500
                    },
                    "temperature": 30,
                    "volume_fraction": 0.2
                },
                {
                    "analysis": {
                        "Ca": 200,  # Lime treatment overflow
                        "OH": 100,
                        "pH": 11.5
                    },
                    "temperature": 25,
                    "volume_fraction": 0.3
                },
                {
                    "analysis": {
                        "Na": 100,  # General process water
                        "Cl": 150,
                        "S(6)": 200,
                        "pH": 7.0
                    },
                    "temperature": 20,
                    "volume_fraction": 0.5
                }
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Tenorite", "Bunsenite", "Zincite", "Gypsum"]
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        assert "precipitated_minerals" in result
        
        # Metals should precipitate at elevated pH
        mixed = result["mixed_solution"]
        assert mixed["pH"] > 7.0, "pH should be elevated"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_mixing_with_gases(results: TestResults):
    """Test mixing with gas phase equilibration"""
    test_name = "Mixing with CO2 equilibration"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 100,
                        "Alkalinity": 50,  # Low alkalinity
                        "pH": 6.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                },
                {
                    "analysis": {
                        "Ca": 100,
                        "Alkalinity": 300,  # High alkalinity
                        "pH": 9.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                }
            ],
            "gas_phases": [
                {
                    "name": "CO2(g)",
                    "pressure": 0.0003,  # Atmospheric CO2
                    "fixed": True
                }
            ],
            "allow_precipitation": False
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        mixed = result["mixed_solution"]
        
        # pH should be buffered by CO2
        assert 7.0 < mixed["pH"] < 8.5, f"pH {mixed['pH']} not in expected range"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_volume_fraction_validation(results: TestResults):
    """Test volume fraction validation and normalization"""
    test_name = "Volume fraction validation"
    
    try:
        # Test 1: Fractions don't sum to 1.0
        input_data = {
            "solutions": [
                {
                    "analysis": {"Ca": 100, "pH": 7.0},
                    "volume_fraction": 0.3
                },
                {
                    "analysis": {"Ca": 200, "pH": 8.0},
                    "volume_fraction": 0.4
                }
                # Sum = 0.7, should be normalized
            ]
        }
        
        result = await simulate_solution_mixing(input_data)
        assert "error" not in result or "normalized" in str(result)
        
        # Test 2: Missing volume fractions (should default to equal)
        input_data = {
            "solutions": [
                {"analysis": {"Ca": 100, "pH": 7.0}},
                {"analysis": {"Ca": 200, "pH": 8.0}}
            ]
        }
        
        result = await simulate_solution_mixing(input_data)
        if "error" not in result:
            mixed = result["mixed_solution"]
            assert 145 < mixed["Ca"] < 155, "Should be average with equal fractions"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_edge_cases(results: TestResults):
    """Test edge cases and error handling"""
    test_name = "Edge cases and error handling"
    
    try:
        # Test 1: Single solution (no mixing)
        input_data = {
            "solutions": [
                {
                    "analysis": {"Ca": 100, "pH": 7.0},
                    "volume_fraction": 1.0
                }
            ]
        }
        
        result = await simulate_solution_mixing(input_data)
        # Should either work or give meaningful error
        
        # Test 2: Very different pH values
        input_data = {
            "solutions": [
                {
                    "analysis": {"pH": 2.0, "Cl": 100},
                    "volume_fraction": 0.1
                },
                {
                    "analysis": {"pH": 12.0, "Na": 100},
                    "volume_fraction": 0.9
                }
            ]
        }
        
        result = await simulate_solution_mixing(input_data)
        assert "error" not in result
        
        # Test 3: Zero volume fraction
        input_data = {
            "solutions": [
                {
                    "analysis": {"Ca": 100, "pH": 7.0},
                    "volume_fraction": 0.0
                },
                {
                    "analysis": {"Ca": 200, "pH": 8.0},
                    "volume_fraction": 1.0
                }
            ]
        }
        
        result = await simulate_solution_mixing(input_data)
        if "error" not in result:
            assert result["mixed_solution"]["Ca"] == 200
            
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_mixing_with_kinetic_custom(results: TestResults):
    """Test mixing with custom kinetic functions"""
    test_name = "Mixing with custom kinetic functions"
    
    try:
        input_data = {
            "solutions": [
                {
                    "analysis": {
                        "Ca": 300,
                        "S(6)": 300,
                        "pH": 7.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                },
                {
                    "analysis": {
                        "Ca": 100,
                        "S(6)": 100,
                        "pH": 8.0
                    },
                    "temperature": 25,
                    "volume_fraction": 0.5
                }
            ],
            "allow_precipitation": True,
            "equilibrium_minerals": ["Gypsum"],
            "kinetic_precipitation": {
                "enable": True,
                "time_hours": 1.0,
                "use_phreeqc_rates": False,  # Use custom
                "minerals": {
                    "Gypsum": {
                        "rate_constant": 1e-8,
                        "activation_energy": 45000,
                        "exponents": {"H+": 0.5}
                    }
                }
            }
        }
        
        result = await simulate_solution_mixing(input_data)
        
        assert "error" not in result
        # Custom kinetics might give different results than PHREEQC
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def main():
    """Run all tests"""
    print(f"\nSimulate Solution Mixing Tool Test Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = TestResults()
    
    # Run all tests
    await test_basic_mixing_two_solutions(results)
    await test_mixing_with_temperature_difference(results)
    await test_mixing_with_equilibrium_precipitation(results)
    await test_mixing_with_kinetic_precipitation(results)
    await test_multiple_solution_mixing(results)
    await test_seawater_freshwater_mixing(results)
    await test_industrial_wastewater_mixing(results)
    await test_mixing_with_gases(results)
    await test_volume_fraction_validation(results)
    await test_edge_cases(results)
    await test_mixing_with_kinetic_custom(results)
    
    # Print summary
    results.print_summary()
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())