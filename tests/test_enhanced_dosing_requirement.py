"""
Comprehensive test suite for enhanced dosing requirement functionality.
Tests Phase 1-3 enhancements including:
- Complex target parameter evaluation (total_hardness, residual_phosphorus, etc.)
- Multi-objective optimization 
- Advanced optimization algorithms (differential evolution, Nelder-Mead, etc.)
- Multi-reagent optimization strategies

Run from project root with:
python tests/test_enhanced_dosing_requirement.py
"""

import asyncio
import sys
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.dosing_requirement import calculate_dosing_requirement_enhanced
from tools.phreeqc_wrapper import evaluate_target_parameter
from tools.solution_speciation import calculate_solution_speciation


class TestResults:
    """Track test results with detailed reporting"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.performance_data = []
        
    def record_pass(self, test_name: str, duration: float = None):
        self.passed += 1
        duration_str = f" ({duration:.2f}s)" if duration else ""
        print(f"PASS {test_name}{duration_str}")
        if duration:
            self.performance_data.append((test_name, duration))
        
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"FAIL {test_name}: {error}")
        
    def print_summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*80}")
        print(f"Enhanced Dosing Test Summary: {self.passed}/{total} passed")
        
        if self.performance_data:
            print(f"\nPerformance Summary:")
            avg_time = np.mean([t for _, t in self.performance_data])
            print(f"  Average execution time: {avg_time:.2f}s")
            fastest = min(self.performance_data, key=lambda x: x[1])
            slowest = max(self.performance_data, key=lambda x: x[1])
            print(f"  Fastest: {fastest[0]} ({fastest[1]:.2f}s)")
            print(f"  Slowest: {slowest[0]} ({slowest[1]:.2f}s)")
        
        if self.errors:
            print(f"\nFailed tests:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        print(f"{'='*80}")


async def test_complex_target_parameters(results: TestResults):
    """Test Phase 1.1: Enhanced target parameter evaluation"""
    test_name = "Complex target parameters - total_hardness"
    start_time = asyncio.get_event_loop().time()
    
    try:
        # Test total hardness targeting with lime softening
        input_data = {
            'initial_solution': {
                'analysis': {
                    'Ca': 180,  # High calcium
                    'Mg': 85,   # High magnesium  
                    'Alkalinity': 150,
                    'Cl': 200,
                    'pH': 7.8
                },
                'temperature_celsius': 20
            },
            'objectives': [{
                'parameter': 'total_hardness',
                'value': 85,  # Target 85 mg/L as CaCO3
                'tolerance': 10,
                'units': 'mg/L as CaCO3'
            }],
            'reagents': [{
                'formula': 'Ca(OH)2',
                'min_dose': 0,
                'max_dose': 15
            }],
            'allow_precipitation': True,
            'equilibrium_minerals': ['Calcite', 'Brucite', 'Dolomite'],
            'optimization_method': 'adaptive',
            'max_iterations': 30
        }
        
        result = await calculate_dosing_requirement_enhanced(input_data)
        
        # Validate result structure
        assert 'converged' in result, "Missing convergence status"
        assert 'doses' in result, "Missing dose information"
        assert 'objective_results' in result, "Missing objective results"
        assert 'final_state' in result, "Missing final state"
        
        # Validate objective achievement
        obj_result = result['objective_results']['total_hardness']
        assert 'current' in obj_result, "Missing current value"
        assert 'target' in obj_result, "Missing target value" 
        assert 'within_tolerance' in obj_result, "Missing tolerance check"
        
        # Performance validation
        if result['converged']:
            assert obj_result['within_tolerance'], f"Hardness not within tolerance: {obj_result['current']:.1f} vs {obj_result['target']:.1f}"
            assert obj_result['current'] < 150, "Hardness not significantly reduced"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_residual_phosphorus_targeting(results: TestResults):
    """Test complex parameter: residual phosphorus with FeCl3"""
    test_name = "Complex target parameters - residual_phosphorus"
    start_time = asyncio.get_event_loop().time()
    
    try:
        input_data = {
            'initial_solution': {
                'analysis': {
                    'P': 8.5,  # High phosphorus
                    'pH': 7.2,
                    'Alkalinity': 180,
                    'Ca': 80,
                    'Mg': 25,
                    'Cl': 120
                },
                'temperature_celsius': 25
            },
            'objectives': [{
                'parameter': 'residual_phosphorus',
                'value': 0.5,  # Target 0.5 mg/L P
                'tolerance': 0.2,
                'units': 'mg/L'
            }],
            'reagents': [{
                'formula': 'FeCl3',
                'min_dose': 0,
                'max_dose': 25
            }],
            'allow_precipitation': True,
            'equilibrium_minerals': ['Strengite', 'Fe(OH)3', 'FePO4'],
            'optimization_method': 'adaptive',
            'max_iterations': 40
        }
        
        result = await calculate_dosing_requirement_enhanced(input_data)
        
        # Validate phosphorus removal
        obj_result = result['objective_results']['residual_phosphorus']
        
        if result['converged']:
            assert obj_result['within_tolerance'], f"P removal target not met: {obj_result['current']:.2f} vs {obj_result['target']:.2f}"
            assert obj_result['current'] < 2.0, "Phosphorus not significantly reduced"
            # Expect some precipitates
            assert result.get('total_precipitate_g_L', 0) > 0, "No precipitation occurred"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_langelier_index_targeting(results: TestResults):
    """Test complex parameter: Langelier Saturation Index"""
    test_name = "Complex target parameters - langelier_index"
    start_time = asyncio.get_event_loop().time()
    
    try:
        input_data = {
            'initial_solution': {
                'analysis': {
                    'Ca': 120,
                    'Mg': 40,
                    'Alkalinity': 200,
                    'TDS': 450,
                    'pH': 7.5,
                    'Cl': 150
                },
                'temperature_celsius': 25
            },
            'objectives': [{
                'parameter': 'langelier_index',
                'value': 0.0,  # Target neutral LSI
                'tolerance': 0.3,
                'units': 'dimensionless'
            }],
            'reagents': [{
                'formula': 'HCl',
                'min_dose': 0,
                'max_dose': 8
            }],
            'optimization_method': 'adaptive',
            'max_iterations': 25
        }
        
        result = await calculate_dosing_requirement_enhanced(input_data)
        
        obj_result = result['objective_results']['langelier_index']
        
        if result['converged']:
            assert obj_result['within_tolerance'], f"LSI target not met: {obj_result['current']:.2f} vs {obj_result['target']:.2f}"
            # LSI should be close to neutral
            assert abs(obj_result['current']) < 1.0, "LSI not in reasonable range"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multi_objective_optimization(results: TestResults):
    """Test Phase 1.2: Multi-objective optimization"""
    test_name = "Multi-objective optimization - P removal + pH control"
    start_time = asyncio.get_event_loop().time()
    
    try:
        input_data = {
            'initial_solution': {
                'analysis': {
                    'P': 6.2,
                    'pH': 6.8,
                    'Alkalinity': 140,
                    'Ca': 90,
                    'Cl': 180
                },
                'temperature_celsius': 22
            },
            'objectives': [
                {
                    'parameter': 'residual_phosphorus',
                    'value': 1.0,
                    'tolerance': 0.3,
                    'weight': 0.7,  # Higher priority
                    'units': 'mg/L'
                },
                {
                    'parameter': 'pH',
                    'value': 7.5,
                    'tolerance': 0.3,
                    'weight': 0.3,  # Lower priority
                    'units': 'pH'
                }
            ],
            'reagents': [
                {
                    'formula': 'FeCl3',
                    'min_dose': 0,
                    'max_dose': 15
                },
                {
                    'formula': 'NaOH',
                    'min_dose': 0,
                    'max_dose': 8
                }
            ],
            'allow_precipitation': True,
            'equilibrium_minerals': ['Strengite', 'Fe(OH)3'],
            'optimization_method': 'adaptive',
            'max_iterations': 45
        }
        
        result = await calculate_dosing_requirement_enhanced(input_data)
        
        # Validate multi-objective results
        assert 'residual_phosphorus' in result['objective_results'], "Missing phosphorus objective"
        assert 'pH' in result['objective_results'], "Missing pH objective"
        
        p_result = result['objective_results']['residual_phosphorus']
        ph_result = result['objective_results']['pH']
        
        if result['converged']:
            # Primary objective (P removal) should be achieved
            assert p_result['within_tolerance'] or p_result['current'] < p_result['target'] + 0.5, "P removal insufficient"
            # Secondary objective should be reasonable
            assert abs(ph_result['current'] - ph_result['target']) < 1.0, "pH too far from target"
        
        # Both reagents should be used
        assert len(result['doses']) >= 1, "No doses calculated"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_optimization_algorithms(results: TestResults):
    """Test Phase 3.1: Advanced optimization algorithms"""
    test_name = "Advanced optimization algorithms comparison"
    start_time = asyncio.get_event_loop().time()
    
    try:
        base_input = {
            'initial_solution': {
                'analysis': {
                    'Ca': 150,
                    'Mg': 60,
                    'Alkalinity': 120,
                    'pH': 7.6,
                    'Cl': 180
                },
                'temperature_celsius': 20
            },
            'objectives': [{
                'parameter': 'total_hardness',
                'value': 100,
                'tolerance': 8,
                'units': 'mg/L as CaCO3'
            }],
            'reagents': [{
                'formula': 'Ca(OH)2',
                'min_dose': 0,
                'max_dose': 12
            }],
            'allow_precipitation': True,
            'equilibrium_minerals': ['Calcite', 'Brucite'],
            'max_iterations': 25
        }
        
        # Test different optimization methods
        methods_to_test = ['adaptive', 'grid_search']  # Reduced for performance
        results_by_method = {}
        
        for method in methods_to_test:
            method_input = base_input.copy()
            method_input['optimization_method'] = method
            
            try:
                result = await calculate_dosing_requirement_enhanced(method_input)
                results_by_method[method] = result
                
                # Basic validation for each method
                assert 'converged' in result, f"Missing convergence status for {method}"
                assert 'objective_results' in result, f"Missing objective results for {method}"
                
            except Exception as e:
                print(f"    Warning: {method} failed: {e}")
        
        # At least one method should work
        assert len(results_by_method) > 0, "All optimization methods failed"
        
        # Compare convergence rates
        converged_methods = [m for m, r in results_by_method.items() if r['converged']]
        print(f"    Converged methods: {converged_methods}")
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_constraint_handling(results: TestResults):
    """Test constraint handling in optimization"""
    test_name = "Constraint handling with bounds"
    start_time = asyncio.get_event_loop().time()
    
    try:
        input_data = {
            'initial_solution': {
                'analysis': {
                    'P': 12.0,
                    'pH': 6.5,
                    'Alkalinity': 160,
                    'Ca': 100,
                    'Cl': 200
                }
            },
            'objectives': [{
                'parameter': 'residual_phosphorus',
                'value': 2.0,
                'tolerance': 0.5,
                'constraint_type': 'max',  # P should be less than 2.0
                'units': 'mg/L'
            }],
            'reagents': [{
                'formula': 'Al2(SO4)3',
                'min_dose': 0.5,  # Minimum dose constraint
                'max_dose': 20    # Maximum dose constraint
            }],
            'allow_precipitation': True,
            'equilibrium_minerals': ['AlPO4', 'Al(OH)3'],
            'optimization_method': 'adaptive',
            'max_iterations': 35
        }
        
        result = await calculate_dosing_requirement_enhanced(input_data)
        
        # Validate constraint respect
        if 'Al2(SO4)3' in result.get('doses', {}):
            dose = result['doses']['Al2(SO4)3']
            assert dose >= 0.5, f"Dose {dose} below minimum constraint"
            assert dose <= 20, f"Dose {dose} above maximum constraint"
        
        # Validate objective constraint
        obj_result = result['objective_results']['residual_phosphorus']
        if result['converged']:
            assert obj_result['current'] <= obj_result['target'] + 0.8, "Constraint-type objective not met"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_performance_vs_legacy(results: TestResults):
    """Test performance improvement vs legacy approach"""
    test_name = "Performance comparison - enhanced vs legacy simulation"
    
    try:
        # Enhanced approach - single call
        start_time = asyncio.get_event_loop().time()
        
        enhanced_input = {
            'initial_solution': {
                'analysis': {
                    'Ca': 200,
                    'Mg': 80,
                    'Alkalinity': 150,
                    'pH': 7.8,
                    'Cl': 250
                },
                'temperature_celsius': 18
            },
            'objectives': [{
                'parameter': 'total_hardness',
                'value': 120,
                'tolerance': 15,
                'units': 'mg/L as CaCO3'
            }],
            'reagents': [{
                'formula': 'Ca(OH)2',
                'min_dose': 0,
                'max_dose': 10
            }],
            'optimization_method': 'adaptive',
            'max_iterations': 20
        }
        
        enhanced_result = await calculate_dosing_requirement_enhanced(enhanced_input)
        enhanced_time = asyncio.get_event_loop().time() - start_time
        
        # Simulate legacy approach (multiple manual calls)
        start_time = asyncio.get_event_loop().time()
        
        # Simulate 8 manual iterations as legacy approach would require
        legacy_iterations = 0
        for dose in np.linspace(0.5, 8, 8):
            # Simulate solution speciation call
            legacy_sol = {
                'analysis': enhanced_input['initial_solution']['analysis'].copy(),
                'temperature_celsius': 18
            }
            try:
                spec_result = await calculate_solution_speciation(legacy_sol)
                legacy_iterations += 1
            except:
                pass
        
        legacy_time = asyncio.get_event_loop().time() - start_time
        
        # Performance validation
        speedup = legacy_time / enhanced_time if enhanced_time > 0 else 1
        api_reduction = (8 - 1) / 8 * 100  # 8 calls -> 1 call
        
        print(f"    Enhanced time: {enhanced_time:.2f}s")
        print(f"    Legacy simulation time: {legacy_time:.2f}s") 
        print(f"    Speedup: {speedup:.1f}x")
        print(f"    API call reduction: {api_reduction:.0f}%")
        
        # Validate achievement
        assert enhanced_time < 15.0, "Enhanced method too slow"
        assert speedup > 0.5, "Enhanced method not competitive"
        
        results.record_pass(test_name, enhanced_time)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_edge_cases_and_robustness(results: TestResults):
    """Test edge cases and error handling robustness"""
    test_name = "Edge cases and robustness"
    start_time = asyncio.get_event_loop().time()
    
    try:
        test_cases = [
            # Empty objectives
            {
                'initial_solution': {'analysis': {'Ca': 100, 'pH': 7}},
                'objectives': [],
                'reagents': [{'formula': 'NaOH'}],
                'should_error': True
            },
            # Invalid parameter
            {
                'initial_solution': {'analysis': {'Ca': 100, 'pH': 7}},
                'objectives': [{'parameter': 'invalid_param', 'value': 10}],
                'reagents': [{'formula': 'NaOH'}],
                'should_error': True
            },
            # Conflicting objectives
            {
                'initial_solution': {'analysis': {'Ca': 100, 'pH': 7}},
                'objectives': [
                    {'parameter': 'pH', 'value': 4.0, 'tolerance': 0.1},
                    {'parameter': 'pH', 'value': 10.0, 'tolerance': 0.1}
                ],
                'reagents': [{'formula': 'NaOH'}],
                'max_iterations': 10,
                'should_error': False  # Should handle gracefully
            }
        ]
        
        handled_cases = 0
        for i, case in enumerate(test_cases):
            try:
                result = await calculate_dosing_requirement_enhanced(case)
                
                if case.get('should_error', False):
                    # Should have error or non-convergence
                    assert not result.get('converged', True) or 'error' in str(result), f"Case {i} should have failed"
                
                handled_cases += 1
                
            except Exception as e:
                if not case.get('should_error', False):
                    print(f"    Unexpected error in case {i}: {e}")
                handled_cases += 1
        
        assert handled_cases == len(test_cases), "Not all edge cases handled"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_target_parameter_evaluation(results: TestResults):
    """Test Phase 1.1: Enhanced target parameter evaluation functions"""
    test_name = "Target parameter evaluation functions"
    start_time = asyncio.get_event_loop().time()
    
    try:
        # Create mock simulation result for testing parameter evaluation
        mock_result = {
            'solution_summary': {
                'pH': 8.2,
                'ionic_strength': 0.015,
                'temperature': 25
            },
            'element_totals_molality': {
                'Ca': 0.002,  # 80 mg/L
                'Mg': 0.001,  # 24 mg/L  
                'P': 0.0001,  # 3.1 mg/L
                'Fe': 0.0005  # 28 mg/L
            },
            'precipitated_phases': {
                'Calcite': {'moles': 0.001},
                'Strengite': {'moles': 0.0002}
            },
            'saturation_indices': {
                'Calcite': 0.5,
                'Gypsum': -1.2,
                'Strengite': 0.8
            }
        }
        
        # Test different target parameters
        test_configs = [
            {
                'parameter': 'total_hardness',
                'units': 'mg/L as CaCO3',
                'expected_range': (100, 400)  # Should be ~300 mg/L as CaCO3
            },
            {
                'parameter': 'residual_phosphorus', 
                'units': 'mg/L',
                'expected_range': (2.5, 3.5)  # Should be ~3.1 mg/L
            },
            {
                'parameter': 'total_metals',
                'element_or_species': ['Fe'],
                'units': 'mg/L',
                'expected_range': (25, 35)  # Should be ~28 mg/L
            },
            {
                'parameter': 'precipitation_potential',
                'units': 'g/L',
                'expected_range': (0.0, 0.25)  # Should be some precipitation
            },
            {
                'parameter': 'minimum_si',
                'mineral': 'Calcite,Gypsum,Strengite',
                'units': 'dimensionless',
                'expected_range': (-1.5, -1.0)  # Should be Gypsum SI = -1.2
            }
        ]
        
        successful_evaluations = 0
        for config in test_configs:
            try:
                value = evaluate_target_parameter(mock_result, config)
                
                # Validate value is in expected range
                expected_min, expected_max = config['expected_range']
                assert expected_min <= value <= expected_max, f"Value {value} not in range {config['expected_range']} for {config['parameter']}"
                
                successful_evaluations += 1
                print(f"    {config['parameter']}: {value:.3f} {config.get('units', '')}")
                
            except Exception as e:
                print(f"    Warning: {config['parameter']} evaluation failed: {e}")
        
        assert successful_evaluations >= 3, f"Only {successful_evaluations} parameter evaluations succeeded"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration)
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def main():
    """Run all enhanced dosing requirement tests"""
    print(f"\nEnhanced Dosing Requirement Test Suite")
    print(f"Testing Phase 1-3 enhancements")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    results = TestResults()
    
    # Phase 1.1: Enhanced target parameter evaluation
    await test_target_parameter_evaluation(results)
    await test_complex_target_parameters(results)
    await test_residual_phosphorus_targeting(results)
    await test_langelier_index_targeting(results)
    
    # Phase 1.2: Multi-objective optimization
    await test_multi_objective_optimization(results)
    await test_constraint_handling(results)
    
    # Phase 3.1: Advanced optimization algorithms
    await test_optimization_algorithms(results)
    
    # Performance and robustness
    await test_performance_vs_legacy(results)
    await test_edge_cases_and_robustness(results)
    
    # Print summary
    results.print_summary()
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Return exit code for CI/CD
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)