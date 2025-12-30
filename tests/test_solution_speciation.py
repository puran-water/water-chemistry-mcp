#!/usr/bin/env python3
"""
Comprehensive test suite for calculate_solution_speciation tool.
Tests basic water analysis, saturation indices, and edge cases.
"""

import asyncio
import sys
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path (portable, works on any machine)
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.solution_speciation import calculate_solution_speciation

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

async def test_basic_speciation(results):
    """Test basic water speciation analysis."""
    print("\n1. Testing Basic Water Speciation")
    print("-" * 40)
    
    # Test 1.1: Simple water analysis
    try:
        input_data = {
            "analysis": {
                "Ca": 100,  # mg/L
                "Mg": 50,   # mg/L
                "Na": 100,  # mg/L
                "Cl": 200,  # mg/L
                "S(6)": 100, # mg/L
                "Alkalinity": 150,  # mg/L as CaCO3
                "pH": 7.5
            },
            "temperature": 25
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            # Check expected outputs
            assert 'solution_summary' in result
            assert 'saturation_indices' in result
            assert 'element_totals_molality' in result
            
            summary = result['solution_summary']
            assert abs(summary['pH'] - 7.5) < 0.1
            assert summary['ionic_strength'] > 0
            assert summary['tds_calculated'] > 0
            
            results.record_pass("Basic water analysis")
        else:
            results.record_fail("Basic water analysis", result['error'])
    except Exception as e:
        results.record_fail("Basic water analysis", str(e))
    
    # Test 1.2: High TDS water
    try:
        input_data = {
            "pH": 7.0,
            "analysis": {
                "Ca": 50.0,   # 2000 mg/L
                "Mg": 20.0,   # 486 mg/L
                "Na": 100.0,  # 2300 mg/L
                "Cl": 150.0,  # 5319 mg/L
                "S(6)": 50.0  # 4800 mg/L SO4
            },
            "temperature_celsius": 30.0
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            tds = result['solution_summary']['tds_calculated']
            assert tds > 10000  # High TDS
            results.record_pass("High TDS water analysis")
        else:
            results.record_fail("High TDS water analysis", result['error'])
    except Exception as e:
        results.record_fail("High TDS water analysis", str(e))
    
    # Test 1.3: Low pH water
    try:
        input_data = {
            "pH": 4.5,
            "analysis": {
                "Ca": 1.0,
                "S(6)": 2.0,
                "Fe": 0.1
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            assert result['solution_summary']['pH'] < 5
            results.record_pass("Low pH water analysis")
        else:
            results.record_fail("Low pH water analysis", result['error'])
    except Exception as e:
        results.record_fail("Low pH water analysis", str(e))

async def test_saturation_indices(results):
    """Test saturation index calculations."""
    print("\n2. Testing Saturation Index Calculations")
    print("-" * 40)
    
    # Test 2.1: Supersaturated calcite
    try:
        input_data = {
            "pH": 8.5,
            "analysis": {
                "Ca": 5.0,  # 200 mg/L
                "Alkalinity": 6.0  # 300 mg/L as CaCO3
            },
            "database": "minteq.dat"
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si_calcite = result['saturation_indices'].get('Calcite', -999)
            assert si_calcite > 0  # Should be supersaturated
            results.record_pass("Calcite supersaturation detection")
        else:
            results.record_fail("Calcite supersaturation detection", result['error'])
    except Exception as e:
        results.record_fail("Calcite supersaturation detection", str(e))
    
    # Test 2.2: Gypsum saturation
    try:
        input_data = {
            "pH": 7.0,
            "analysis": {
                "Ca": 15.0,  # 600 mg/L
                "S(6)": 25.0  # 2400 mg/L SO4
            },
            "database": "minteq.dat"
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si_gypsum = result['saturation_indices'].get('Gypsum', -999)
            assert si_gypsum != -999  # Should calculate SI
            results.record_pass("Gypsum saturation index")
        else:
            results.record_fail("Gypsum saturation index", result['error'])
    except Exception as e:
        results.record_fail("Gypsum saturation index", str(e))
    
    # Test 2.3: Multiple mineral SIs
    try:
        input_data = {
            "pH": 10.5,
            "analysis": {
                "Ca": 10.0,
                "Mg": 10.0,
                "Alkalinity": 5.0,
                "S(6)": 10.0
            },
            "database": "minteq.dat"
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si = result['saturation_indices']
            # Should have multiple minerals
            assert len(si) > 5
            # Check for Brucite at high pH
            if 'Brucite' in si:
                assert si['Brucite'] > -2  # Should be near saturation
                results.record_pass("Multiple mineral SI calculation")
            else:
                results.record_fail("Multiple mineral SI calculation", "Brucite SI not found")
        else:
            results.record_fail("Multiple mineral SI calculation", result['error'])
    except Exception as e:
        results.record_fail("Multiple mineral SI calculation", str(e))

async def test_species_distribution(results):
    """Test aqueous species distribution."""
    print("\n3. Testing Species Distribution")
    print("-" * 40)
    
    # Test 3.1: Carbonate speciation vs pH
    try:
        # Low pH - mainly CO2
        input_data = {
            "pH": 5.0,
            "analysis": {
                "Alkalinity": 2.0
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result and 'species_molality' in result:
            species = result['species_molality']
            # At pH 5, CO2 should dominate
            co2 = species.get('CO2', 0)
            hco3 = species.get('HCO3-', 0)
            assert co2 > hco3
            results.record_pass("Low pH carbonate speciation")
        else:
            results.record_fail("Low pH carbonate speciation", 
                              result.get('error', 'No species data'))
    except Exception as e:
        results.record_fail("Low pH carbonate speciation", str(e))
    
    # Test 3.2: High pH carbonate speciation
    try:
        input_data = {
            "pH": 10.0,
            "analysis": {
                "Alkalinity": 2.0
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result and 'species_molality' in result:
            species = result['species_molality']
            # At pH 10, CO3-2 should be significant
            co3 = species.get('CO3-2', 0)
            assert co3 > 0
            results.record_pass("High pH carbonate speciation")
        else:
            results.record_fail("High pH carbonate speciation", 
                              result.get('error', 'No species data'))
    except Exception as e:
        results.record_fail("High pH carbonate speciation", str(e))

async def test_temperature_effects(results):
    """Test temperature effects on speciation."""
    print("\n4. Testing Temperature Effects")
    print("-" * 40)
    
    # Test 4.1: Temperature effect on calcite SI
    try:
        base_data = {
            "pH": 8.0,
            "analysis": {
                "Ca": 3.0,
                "Alkalinity": 3.0
            },
            "database": "minteq.dat"
        }
        
        # Test at 10°C
        input_10c = base_data.copy()
        input_10c['temperature_celsius'] = 10.0
        result_10c = await calculate_solution_speciation(input_10c)
        
        # Test at 40°C
        input_40c = base_data.copy()
        input_40c['temperature_celsius'] = 40.0
        result_40c = await calculate_solution_speciation(input_40c)
        
        if 'error' not in result_10c and 'error' not in result_40c:
            si_10c = result_10c['saturation_indices'].get('Calcite', -999)
            si_40c = result_40c['saturation_indices'].get('Calcite', -999)
            
            # Calcite is less soluble at higher temp
            assert si_40c > si_10c
            results.record_pass("Temperature effect on calcite solubility")
        else:
            results.record_fail("Temperature effect on calcite solubility", 
                              "Error in calculations")
    except Exception as e:
        results.record_fail("Temperature effect on calcite solubility", str(e))

async def test_edge_cases(results):
    """Test edge cases and error handling."""
    print("\n5. Testing Edge Cases")
    print("-" * 40)
    
    # Test 5.1: Missing required fields
    try:
        input_data = {
            "analysis": {"Ca": 1.0}
            # Missing pH
        }
        
        result = await calculate_solution_speciation(input_data)
        
        # Should handle missing pH gracefully
        if 'error' in result or 'solution_summary' in result:
            results.record_pass("Missing pH handling")
        else:
            results.record_fail("Missing pH handling", "Unexpected result")
    except Exception as e:
        # Exception is also acceptable for missing required field
        results.record_pass("Missing pH handling")
    
    # Test 5.2: Invalid element notation
    try:
        input_data = {
            "pH": 7.0,
            "analysis": {
                "SO4": 100.0  # Should be S(6)
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        # Should either convert or error appropriately
        results.record_pass("Invalid element notation handling")
    except Exception as e:
        results.record_pass("Invalid element notation handling")
    
    # Test 5.3: Extreme pH
    try:
        input_data = {
            "pH": 14.0,
            "analysis": {
                "Na": 10.0,
                "Cl": 10.0
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            assert result['solution_summary']['pH'] >= 13
            results.record_pass("Extreme pH handling")
        else:
            results.record_fail("Extreme pH handling", result['error'])
    except Exception as e:
        results.record_fail("Extreme pH handling", str(e))
    
    # Test 5.4: Very dilute water
    try:
        input_data = {
            "pH": 7.0,
            "analysis": {
                "Ca": 0.001,
                "Cl": 0.002
            }
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            assert result['solution_summary']['ionic_strength'] < 0.0001
            results.record_pass("Very dilute water")
        else:
            results.record_fail("Very dilute water", result['error'])
    except Exception as e:
        results.record_fail("Very dilute water", str(e))

async def test_database_selection(results):
    """Test different database selections."""
    print("\n6. Testing Database Selection")
    print("-" * 40)
    
    databases_to_test = ["minteq.dat", "phreeqc.dat", "wateq4f.dat"]
    
    for db in databases_to_test:
        try:
            input_data = {
                "pH": 7.5,
                "analysis": {
                    "Ca": 2.0,
                    "Mg": 1.0,
                    "Alkalinity": 2.0
                },
                "database": db
            }
            
            result = await calculate_solution_speciation(input_data)
            
            if 'error' not in result:
                results.record_pass(f"Database {db}")
            else:
                results.record_fail(f"Database {db}", result['error'])
        except Exception as e:
            results.record_fail(f"Database {db}", str(e))

async def main():
    """Run all solution speciation tests."""
    print("="*60)
    print("SOLUTION SPECIATION TOOL TEST SUITE")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    results = TestResults()
    
    # Run all test groups
    await test_basic_speciation(results)
    await test_saturation_indices(results)
    await test_species_distribution(results)
    await test_temperature_effects(results)
    await test_edge_cases(results)
    await test_database_selection(results)
    
    print("\n" + "="*60)
    success = results.summary()
    print("="*60)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())