"""
CORRECTED - Applied systematic fixes for C(4) notation and proper database paths.
"""
Comprehensive test script for predict_scaling_potential tool.
Tests both equilibrium and kinetic approaches to scale prediction.

Run from Windows cmd.exe with:
cd C:\Users\hvksh\mcp-servers
venv\Scripts\activate
cd water-chemistry-mcp
python tests\test_scaling_potential.py
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.scaling_potential import predict_scaling_potential


class TestResults:
    """Track test results"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        
    def record_pass(self, test_name: str):
        self.passed += 1
        print(f"PASS: {test_name}")
        
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"FAIL: {test_name}: {error}")
        
    def print_summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"Test Summary: {self.passed}/{total} passed")
        if self.errors:
            print("\nFailed tests:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        print(f"{'='*60}")


async def test_basic_scaling_potential(results: TestResults):
    """Test basic scaling potential calculation"""
    test_name = "Basic scaling potential - equilibrium"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 200,  # High calcium
                    "Mg": 50,
                    "Na": 100,
                    "C(4)": 250,  # High alkalinity
                    "S(6)": 100,
                    "Cl": 200,
                    "pH": 8.0
                },
                "temperature": 25
            },
            "minerals": ["Calcite", "Aragonite", "Gypsum", "Brucite"]
        }
        
        result = await predict_scaling_potential(input_data)
        
        # Assertions
        assert "error" not in result, f"Error in calculation: {result.get('error')}"
        assert "scaling_indices" in result, "No scaling indices in result"
        assert "saturation_indices" in result, "No saturation indices in result"
        
        # Check expected minerals
        si = result["saturation_indices"]
        assert "Calcite" in si, "Calcite not in saturation indices"
        assert si["Calcite"] > 0, "Calcite should be supersaturated"
        
        # Check scaling risk
        indices = result["scaling_indices"]
        assert any(idx["risk_level"] in ["HIGH", "MODERATE"] for idx in indices), "Expected scaling risk"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_langelier_saturation_index(results: TestResults):
    """Test Langelier Saturation Index calculation"""
    test_name = "Langelier Saturation Index (LSI)"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 100,
                    "C(4)": 150,
                    "pH": 7.5,
                    "Cl": 100,
                    "ionic_strength": 0.01
                },
                "temperature": 25
            },
            "minerals": ["Calcite"],
            "include_lsi": True
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        assert "langelier_index" in result or "scaling_indices" in result
        
        # LSI interpretation:
        # LSI > 0: Scale forming
        # LSI = 0: Neutral
        # LSI < 0: Corrosive
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_temperature_effects(results: TestResults):
    """Test temperature effects on scaling potential"""
    test_name = "Temperature effects on scaling"
    
    try:
        base_solution = {
            "analysis": {
                "Ca": 150,
                "C(4)": 200,
                "S(6)": 150,
                "pH": 8.0,
                "Cl": 100
            }
        }
        
        scaling_risks = []
        
        for temp in [10, 25, 40, 60]:
            input_data = {
                "solution": {**base_solution, "temperature": temp},
                "minerals": ["Calcite", "Gypsum", "Anhydrite"]
            }
            
            result = await predict_scaling_potential(input_data)
            if "error" not in result:
                si = result["saturation_indices"]
                scaling_risks.append((temp, si.get("Calcite", 0), si.get("Gypsum", 0)))
        
        # Generally, calcite scaling increases with temperature
        # Gypsum/anhydrite behavior is more complex
        assert len(scaling_risks) >= 3, "Not enough successful calculations"
        
        # Check that temperature has an effect
        calcite_sis = [si[1] for si in scaling_risks]
        assert max(calcite_sis) - min(calcite_sis) > 0.1, "Temperature should affect scaling"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_kinetic_scaling_prediction(results: TestResults):
    """Test kinetic approach to scaling prediction"""
    test_name = "Kinetic scaling prediction"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 300,  # High calcium
                    "C(4)": 300,  # High alkalinity
                    "pH": 8.5,
                    "Cl": 200
                },
                "temperature": 30
            },
            "minerals": ["Calcite", "Aragonite"],
            "kinetic_parameters": {
                "time_hours": 24.0,  # 24 hour prediction
                "use_phreeqc_rates": True,
                "minerals": {
                    "Calcite": {
                        "m0": 0.0,  # No initial seed
                        "parms": [1.0, 0.67, 0.0, 1.0, 2.0]  # Surface area parameters
                    }
                }
            }
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        assert "kinetic_results" in result or "predicted_precipitation" in result
        
        # Kinetic prediction should show time-dependent scaling
        if "kinetic_results" in result:
            assert "time_series" in result["kinetic_results"] or "final_minerals" in result["kinetic_results"]
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multiple_scaling_minerals(results: TestResults):
    """Test prediction for multiple potential scale minerals"""
    test_name = "Multiple scaling minerals"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 200,
                    "Mg": 100,
                    "Ba": 0.5,
                    "Sr": 2.0,
                    "C(4)": 200,
                    "S(6)": 500,  # High sulfate
                    "pH": 7.8,
                    "Cl": 300
                },
                "temperature": 25
            },
            "minerals": [
                "Calcite", "Aragonite", "Dolomite",  # Carbonates
                "Gypsum", "Anhydrite",  # Calcium sulfates
                "Barite", "Celestite",  # Barium/Strontium sulfates
                "Brucite"  # Magnesium hydroxide
            ]
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        assert "saturation_indices" in result
        
        si = result["saturation_indices"]
        # Check multiple minerals evaluated
        assert len(si) >= 5, "Should evaluate multiple minerals"
        
        # Barite should be highly supersaturated with Ba and SO4
        if "Barite" in si:
            assert si["Barite"] > 0, "Barite should show scaling potential"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_ryznar_stability_index(results: TestResults):
    """Test Ryznar Stability Index calculation"""
    test_name = "Ryznar Stability Index (RSI)"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 120,
                    "C(4)": 100,
                    "pH": 7.8,
                    "Cl": 150,
                    "tds": 500  # Total dissolved solids
                },
                "temperature": 25
            },
            "minerals": ["Calcite"],
            "include_rsi": True
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        # RSI = 2(pHs) - pH
        # RSI < 6: Scale forming
        # RSI 6-7: Neutral
        # RSI > 7: Corrosive
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_industrial_cooling_water(results: TestResults):
    """Test scaling prediction for industrial cooling water"""
    test_name = "Industrial cooling water scaling"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 400,  # Concentrated by evaporation
                    "Mg": 150,
                    "Na": 300,
                    "C(4)": 150,
                    "S(6)": 600,
                    "Cl": 500,
                    "Si": 20,  # Silica
                    "pH": 8.5
                },
                "temperature": 45  # Elevated temperature
            },
            "minerals": [
                "Calcite", "Aragonite",
                "Gypsum", "Anhydrite",
                "Brucite",
                "SiO2(a)",  # Amorphous silica
                "Sepiolite"  # Mg silicate
            ],
            "cycles_of_concentration": 4.0
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        assert "scaling_indices" in result
        
        # Multiple minerals should show scaling potential
        high_risk = [idx for idx in result["scaling_indices"] if idx["risk_level"] == "HIGH"]
        assert len(high_risk) > 0, "Expected high scaling risk in concentrated cooling water"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_reverse_osmosis_concentrate(results: TestResults):
    """Test scaling in RO concentrate stream"""
    test_name = "RO concentrate scaling potential"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 800,  # 4x concentrated
                    "Mg": 400,
                    "Na": 2000,
                    "C(4)": 400,
                    "S(6)": 2000,
                    "Cl": 3000,
                    "Si": 40,
                    "Ba": 0.2,
                    "Sr": 8,
                    "F": 4,
                    "pH": 8.0
                },
                "temperature": 25
            },
            "minerals": [
                "Calcite", "Gypsum", "Barite", "Celestite",
                "Fluorite", "SiO2(a)", "Brucite"
            ],
            "recovery": 75.0  # 75% recovery = 4x concentration
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        
        # RO concentrate should show multiple scaling risks
        si = result["saturation_indices"]
        supersaturated = [m for m, idx in si.items() if idx > 0]
        assert len(supersaturated) >= 3, "Multiple minerals should be supersaturated"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_scale_inhibitor_effect(results: TestResults):
    """Test the effect of scale inhibitors on kinetic predictions"""
    test_name = "Scale inhibitor effects (future feature)"
    
    try:
        # Note: Scale inhibitors are planned for Phase 2
        # This test demonstrates the intended API
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 250,
                    "C(4)": 250,
                    "pH": 8.2,
                    "Cl": 200
                },
                "temperature": 30
            },
            "minerals": ["Calcite"],
            "kinetic_parameters": {
                "time_hours": 48.0,
                "use_phreeqc_rates": True,
                "minerals": {
                    "Calcite": {
                        "m0": 0.0,
                        "parms": [1.0, 0.67, 0.0, 1.0, 2.0]
                    }
                },
                # Future: Scale inhibitor parameters
                # "inhibitors": {
                #     "HEDP": {"concentration": 5.0, "efficiency": 0.8}
                # }
            }
        }
        
        result = await predict_scaling_potential(input_data)
        
        # Currently runs without inhibitors
        assert "error" not in result
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_oil_field_scaling(results: TestResults):
    """Test scaling prediction for oil field produced water"""
    test_name = "Oil field produced water scaling"
    
    try:
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 5000,  # Very high TDS
                    "Mg": 1000,
                    "Na": 20000,
                    "Ba": 50,  # High barium
                    "Sr": 200,
                    "S(6)": 100,  # Low sulfate (typical of formation water)
                    "Cl": 35000,
                    "pH": 6.5
                },
                "temperature": 60,  # Reservoir temperature
                "pressure": 100  # bar
            },
            "minerals": ["Barite", "Celestite", "Calcite", "Halite", "Anhydrite"],
            "mixing_scenario": {
                "description": "Formation water mixed with seawater",
                "seawater_fraction": 0.1  # 10% seawater injection
            }
        }
        
        result = await predict_scaling_potential(input_data)
        
        assert "error" not in result
        
        # Barite scaling is major concern when mixing waters
        si = result["saturation_indices"]
        if "Barite" in si:
            assert si["Barite"] > 1.0, "Barite should be highly supersaturated"
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_edge_cases(results: TestResults):
    """Test edge cases and error handling"""
    test_name = "Edge cases and error handling"
    
    try:
        # Test 1: Very low concentrations
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 0.1,
                    "C(4)": 0.1,
                    "pH": 7.0
                },
                "temperature": 25
            },
            "minerals": ["Calcite"]
        }
        
        result = await predict_scaling_potential(input_data)
        assert "error" not in result
        assert result["saturation_indices"]["Calcite"] < -2, "Should be very undersaturated"
        
        # Test 2: Invalid mineral names
        input_data = {
            "solution": {
                "analysis": {"Ca": 100, "pH": 7.5, "C(4)": 100}
            },
            "minerals": ["Calcite", "InvalidMineral", "Gypsum"]
        }
        
        result = await predict_scaling_potential(input_data)
        # Should still work for valid minerals
        assert "Calcite" in result.get("saturation_indices", {})
        
        # Test 3: Extreme pH
        input_data = {
            "solution": {
                "analysis": {
                    "Ca": 100,
                    "pH": 12.0,  # Very high pH
                    "Cl": 100
                },
                "temperature": 25
            },
            "minerals": ["Portlandite", "Brucite"]
        }
        
        result = await predict_scaling_potential(input_data)
        assert "error" not in result
        
        results.record_pass(test_name)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def main():
    """Run all tests"""
    print(f"\nPredict Scaling Potential Tool Test Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = TestResults()
    
    # Run all tests
    await test_basic_scaling_potential(results)
    await test_langelier_saturation_index(results)
    await test_temperature_effects(results)
    await test_kinetic_scaling_prediction(results)
    await test_multiple_scaling_minerals(results)
    await test_ryznar_stability_index(results)
    await test_industrial_cooling_water(results)
    await test_reverse_osmosis_concentrate(results)
    await test_scale_inhibitor_effect(results)
    await test_oil_field_scaling(results)
    await test_edge_cases(results)
    
    # Print summary
    results.print_summary()
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())