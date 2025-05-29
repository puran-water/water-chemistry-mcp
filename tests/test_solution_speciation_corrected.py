#!/usr/bin/env python3
"""
CORRECTED - Comprehensive test suite for calculate_solution_speciation tool.
Tests basic water analysis, saturation indices, and edge cases.

FIXES APPLIED:
1. Use C(4) instead of Alkalinity for carbonate chemistry
2. Use solution_summary instead of summary in assertions
3. Remove Unicode symbols for Windows cmd.exe compatibility
4. Use full database paths
5. Expect Calcite to be available with proper notation
"""

import asyncio
import sys
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path
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
    """Test basic water speciation analysis with corrected chemical notation."""
    print("\n1. Testing Basic Water Speciation")
    print("-" * 40)
    
    # Test 1.1: Simple water analysis with C(4) notation
    try:
        input_data = {
            "analysis": {
                "Ca": 100,  # mg/L
                "Mg": 50,   # mg/L
                "Na": 100,  # mg/L
                "Cl": 200,  # mg/L
                "S(6)": 100, # mg/L - correct PHREEQC notation
                "C(4)": 150,  # mg/L - CORRECTED: Use C(4) instead of Alkalinity
                "pH": 7.5
            },
            "temperature": 25,
            # Use full path to avoid basename resolution issues
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            # Check expected outputs - CORRECTED key names
            assert 'solution_summary' in result, "Missing solution_summary"
            assert 'saturation_indices' in result, "Missing saturation_indices"
            assert 'element_totals_molality' in result, "Missing element_totals_molality"
            
            summary = result['solution_summary']  # CORRECTED: was 'summary'
            assert 'pH' in summary, "Missing pH in solution_summary"
            assert 'ionic_strength' in summary, "Missing ionic_strength"
            assert 'tds_calculated' in summary, "Missing tds_calculated"
            
            # pH should be close to input (allowing for PHREEQC adjustments)
            assert 7.0 < summary['pH'] < 8.0, f"pH {summary['pH']} not in expected range"
            assert summary['ionic_strength'] > 0, "Ionic strength should be positive"
            assert summary['tds_calculated'] > 0, "TDS should be positive"
            
            results.record_pass("Basic water analysis with C(4)")
        else:
            results.record_fail("Basic water analysis with C(4)", result['error'])
    except Exception as e:
        results.record_fail("Basic water analysis with C(4)", str(e))
    
    # Test 1.2: High TDS water
    try:
        input_data = {
            "analysis": {
                "Ca": 2000,   # mg/L - High concentration
                "Mg": 500,    # mg/L
                "Na": 2000,   # mg/L
                "Cl": 5000,   # mg/L
                "S(6)": 2000, # mg/L
                "C(4)": 300,  # CORRECTED: Use C(4)
                "pH": 7.0
            },
            "temperature": 30,
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            summary = result['solution_summary']  # CORRECTED
            assert summary['tds_calculated'] > 5000, f"TDS {summary['tds_calculated']} should be high"
            assert summary['ionic_strength'] > 0.1, f"Ionic strength {summary['ionic_strength']} should be high"
            results.record_pass("High TDS water analysis")
        else:
            results.record_fail("High TDS water analysis", result['error'])
    except Exception as e:
        results.record_fail("High TDS water analysis", str(e))

async def test_calcite_availability(results):
    """Test that Calcite is now available with C(4) notation."""
    print("\n2. Testing Calcite Availability (Lime Softening)")
    print("-" * 40)
    
    # Test 2.1: Conditions favorable for calcite formation
    try:
        input_data = {
            "analysis": {
                "Ca": 300,      # High calcium
                "C(4)": 400,    # CORRECTED: High carbonate using C(4)
                "pH": 8.5,      # High pH for precipitation
                "Cl": 100
            },
            "temperature": 25,
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si = result['saturation_indices']
            assert 'Calcite' in si, "Calcite should be in saturation indices"
            
            calcite_si = si['Calcite']
            assert calcite_si > 0, f"Calcite SI {calcite_si} should be supersaturated"
            
            results.record_pass("Calcite availability for lime softening")
        else:
            results.record_fail("Calcite availability for lime softening", result['error'])
    except Exception as e:
        results.record_fail("Calcite availability for lime softening", str(e))
    
    # Test 2.2: Check other essential minerals
    try:
        input_data = {
            "analysis": {
                "Ca": 200,
                "Mg": 150,      # For brucite formation
                "C(4)": 300,    # CORRECTED: Use C(4)
                "S(6)": 200,    # For gypsum formation
                "pH": 9.0,      # High pH for multiple mineral formation
                "Cl": 100
            },
            "temperature": 25,
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si = result['saturation_indices']
            
            # Check for essential water treatment minerals
            essential_minerals = ['Calcite', 'Gypsum', 'Aragonite']
            found_essential = [m for m in essential_minerals if m in si]
            
            assert len(found_essential) >= 2, f"Should find multiple essential minerals, found: {found_essential}"
            assert 'Calcite' in found_essential, "Calcite must be available for lime softening"
            
            results.record_pass("Multiple essential minerals available")
        else:
            results.record_fail("Multiple essential minerals available", result['error'])
    except Exception as e:
        results.record_fail("Multiple essential minerals available", str(e))

async def test_realistic_lime_softening(results):
    """Test realistic lime softening scenario."""
    print("\n3. Testing Realistic Lime Softening Scenario")
    print("-" * 40)
    
    try:
        # Typical hard groundwater composition
        input_data = {
            "analysis": {
                "Ca": 180,      # Hard water - 180 mg/L Ca
                "Mg": 95,       # Hard water - 95 mg/L Mg  
                "C(4)": 220,    # CORRECTED: 220 mg/L as carbonate
                "S(6)": 85,     # 85 mg/L sulfate
                "Na": 45,       # 45 mg/L sodium
                "Cl": 65,       # 65 mg/L chloride
                "pH": 7.6       # Typical groundwater pH
            },
            "temperature": 15,   # Typical treatment plant temperature
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        
        if 'error' not in result:
            si = result['saturation_indices']
            summary = result['solution_summary']  # CORRECTED
            
            # Verify this is hard water
            total_hardness_approx = (180/40.08 + 95/24.31) * 50000  # Approximate mg/L as CaCO3
            assert total_hardness_approx > 300, "Should be hard water (>300 mg/L as CaCO3)"
            
            # Check for lime softening minerals
            lime_softening_minerals = ['Calcite', 'Brucite', 'Aragonite', 'Portlandite']
            found_lime_minerals = [m for m in lime_softening_minerals if m in si]
            
            assert 'Calcite' in found_lime_minerals, "Calcite essential for calcium removal"
            
            if len(found_lime_minerals) >= 2:
                results.record_pass("Realistic lime softening scenario ready")
            else:
                results.record_pass("Calcite available for lime softening")
        else:
            results.record_fail("Realistic lime softening scenario", result['error'])
    except Exception as e:
        results.record_fail("Realistic lime softening scenario", str(e))

async def test_edge_cases(results):
    """Test edge cases and error handling."""
    print("\n4. Testing Edge Cases")
    print("-" * 40)
    
    # Test 4.1: Missing pH (should work with defaults)
    try:
        input_data = {
            "analysis": {
                "Ca": 100,
                "C(4)": 100,  # CORRECTED
                "Cl": 100
                # No pH specified
            },
            "temperature": 25,
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        # Should work with default pH
        assert 'error' not in result, "Should handle missing pH"
        assert 'solution_summary' in result, "Should return valid result"
        results.record_pass("Missing pH handling")
    except Exception as e:
        results.record_fail("Missing pH handling", str(e))
    
    # Test 4.2: Very dilute water
    try:
        input_data = {
            "analysis": {
                "Ca": 1,     # Very low concentrations
                "C(4)": 5,   # CORRECTED
                "Cl": 2,
                "pH": 7.0
            },
            "temperature": 25,
            "database": str(Path(__file__).parent.parent / "databases" / "official" / "minteq.v4.dat")
        }
        
        result = await calculate_solution_speciation(input_data)
        if 'error' not in result:
            summary = result['solution_summary']  # CORRECTED
            assert summary['ionic_strength'] < 0.001, "Should handle dilute water"
            results.record_pass("Very dilute water")
        else:
            results.record_fail("Very dilute water", result['error'])
    except Exception as e:
        results.record_fail("Very dilute water", str(e))

async def main():
    """Run all tests"""
    print("="*60)
    print("SOLUTION SPECIATION TOOL TEST SUITE - CORRECTED")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    print("CORRECTIONS APPLIED:")
    print("- Using C(4) instead of Alkalinity for carbonate chemistry")
    print("- Using solution_summary instead of summary in assertions")
    print("- Removed Unicode symbols for Windows compatibility")
    print("- Using full database paths")
    print("- Expecting Calcite availability with proper notation")
    
    results = TestResults()
    
    await test_basic_speciation(results)
    await test_calcite_availability(results)
    await test_realistic_lime_softening(results)
    await test_edge_cases(results)
    
    print("="*60)
    success = results.summary()
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    if success:
        print("SUCCESS: All critical fixes are working!")
        print("- Calcite is available for lime softening")
        print("- C(4) notation enables proper carbonate chemistry")
        print("- Database and wrapper are functioning correctly")
    else:
        print("Some tests failed - need additional investigation")

if __name__ == "__main__":
    asyncio.run(main())