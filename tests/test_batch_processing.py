"""
Comprehensive test suite for batch processing functionality.
Tests Phase 1.3 and Phase 3.2 enhancements including:
- Parallel scenario evaluation
- Parameter sweeps and dose-response curves
- Specialized lime softening calculations
- Phosphorus removal optimization
- Advanced multi-reagent optimization strategies

Run from project root with:
python tests/test_batch_processing.py
"""

import asyncio
import sys
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.batch_processing import (
    batch_process_scenarios,
    generate_lime_softening_curve,
    calculate_lime_softening_dose,
    # Note: optimize_multi_reagent_treatment is handled via batch_process_scenarios
    # with scenario_type="multi_reagent_optimization"
)
from tools.ferric_phosphate import calculate_ferric_dose_for_tp


class TestResults:
    """Track test results with performance metrics"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.performance_data = []
        
    def record_pass(self, test_name: str, duration: float = None, details: str = ""):
        self.passed += 1
        duration_str = f" ({duration:.2f}s)" if duration else ""
        details_str = f" - {details}" if details else ""
        print(f"PASS {test_name}{duration_str}{details_str}")
        if duration:
            self.performance_data.append((test_name, duration))
        
    def record_fail(self, test_name: str, error: str):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"FAIL {test_name}: {error}")
        
    def print_summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*80}")
        print(f"Batch Processing Test Summary: {self.passed}/{total} passed")
        
        if self.performance_data:
            print(f"\nPerformance Summary:")
            avg_time = np.mean([t for _, t in self.performance_data])
            print(f"  Average execution time: {avg_time:.2f}s")
            total_time = sum(t for _, t in self.performance_data)
            print(f"  Total test time: {total_time:.1f}s")
            
        if self.errors:
            print(f"\nFailed tests:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        print(f"{'='*80}")


async def test_basic_batch_processing(results: TestResults):
    """Test basic batch scenario processing"""
    test_name = "Basic batch scenario processing"
    start_time = asyncio.get_event_loop().time()
    
    try:
        base_water = {
            'analysis': {
                'Ca': 120,
                'Mg': 45,
                'Alkalinity': 180,
                'Cl': 150,
                'pH': 7.6
            },
            'temperature_celsius': 22
        }
        
        # Create multiple scenarios with different pH adjustments
        scenarios = []
        target_phs = [7.0, 7.5, 8.0, 8.5, 9.0]
        
        for ph in target_phs:
            # Calculate appropriate NaOH dose (positive values only)
            base_dose = max(0.1, (ph - 7.6) * 0.5 + 0.1)  # Ensure minimum 0.1 mmol
            scenarios.append({
                'name': f'pH_{ph}',
                'type': 'chemical_addition',
                'reactants': [{'formula': 'NaOH', 'amount': base_dose, 'units': 'mmol'}],
                'allow_precipitation': True
            })
        
        input_data = {
            'base_solution': base_water,
            'scenarios': scenarios,
            'parallel_limit': 3,
            'output_format': 'full'
        }
        
        result = await batch_process_scenarios(input_data)
        
        # Validate structure
        assert 'results' in result, "Missing results"
        assert len(result['results']) == len(scenarios), f"Expected {len(scenarios)} results, got {len(result['results'])}"
        
        # Validate individual results
        successful_runs = 0
        for scenario_result in result['results']:
            if 'error' not in scenario_result:
                successful_runs += 1
                assert 'result' in scenario_result, "Missing simulation result"
                assert 'solution_summary' in scenario_result['result'], "Missing solution summary"
        
        assert successful_runs >= len(scenarios) // 2, f"Too many failed scenarios: {successful_runs}/{len(scenarios)}"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"{successful_runs}/{len(scenarios)} scenarios successful")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_parameter_sweep(results: TestResults):
    """Test parameter sweep functionality"""
    test_name = "Parameter sweep - pH range"
    start_time = asyncio.get_event_loop().time()
    
    try:
        base_water = {
            'analysis': {
                'Ca': 100,
                'Mg': 30,
                'Alkalinity': 120,
                'Cl': 130,
                'pH': 7.0  # Will be overridden by sweep
            },
            'temperature_celsius': 25
        }
        
        # pH sweep scenario
        pH_sweep_scenario = {
            'name': 'pH_sweep',
            'type': 'parameter_sweep',
            'parameter': 'pH',
            'values': [6.5, 7.0, 7.5, 8.0, 8.5, 9.0]
        }
        
        input_data = {
            'base_solution': base_water,
            'scenarios': [pH_sweep_scenario],
            'parallel_limit': 4,
            'output_format': 'full'
        }
        
        result = await batch_process_scenarios(input_data)
        
        # Validate sweep results
        assert len(result['results']) == 1, "Should have one scenario result"
        sweep_result = result['results'][0]['result']
        assert 'sweep_results' in sweep_result, "Missing sweep results"
        
        sweep_data = sweep_result['sweep_results']
        assert len(sweep_data) == 6, f"Expected 6 pH points, got {len(sweep_data)}"
        
        # Validate pH progression
        ph_values = [point['pH'] for point in sweep_data]
        assert ph_values == sorted(ph_values), "pH values not in order"
        assert min(ph_values) >= 6.4, "pH too low"
        assert max(ph_values) <= 9.1, "pH too high"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"Swept {len(sweep_data)} pH points")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_lime_softening_curve(results: TestResults):
    """Test specialized lime softening curve generation"""
    test_name = "Lime softening curve generation"
    start_time = asyncio.get_event_loop().time()
    
    try:
        hard_water = {
            'analysis': {
                'Ca': 180,  # High hardness water
                'Mg': 85,
                'Alkalinity': 160,
                'Cl': 200,
                'SO4': 120,
                'pH': 7.8
            },
            'temperature_celsius': 18
        }
        
        # Generate curve with 8 lime doses
        lime_doses = np.linspace(0.5, 8.0, 8).tolist()
        
        result = await generate_lime_softening_curve(
            initial_water=hard_water,
            lime_doses=lime_doses,
            database='minteq.dat'
        )
        
        # Validate curve structure
        assert 'curve_data' in result, "Missing curve data"
        assert 'optimal_dose' in result, "Missing optimal dose"
        
        curve_data = result['curve_data']
        assert len(curve_data) >= len(lime_doses) // 2, "Too few successful points"
        
        # Validate curve properties
        lime_doses_result = [point['lime_dose_mmol'] for point in curve_data]
        hardness_values = [point['hardness_mg_caco3'] for point in curve_data]
        
        assert min(lime_doses_result) >= 0.4, "Minimum dose too low"
        assert max(lime_doses_result) <= 8.1, "Maximum dose too high"
        
        # Hardness should generally decrease with lime dose (some exceptions possible)
        initial_hardness = max(hardness_values)
        final_hardness = min(hardness_values)
        reduction = (initial_hardness - final_hardness) / initial_hardness
        assert reduction > 0.1, f"Insufficient hardness reduction: {reduction:.1%}"
        
        # Check optimal dose
        if result['optimal_dose']:
            optimal = result['optimal_dose']
            assert 'dose_mmol' in optimal, "Missing optimal dose value"
            assert optimal['dose_mmol'] > 0, "Optimal dose should be positive"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"{len(curve_data)} points, {reduction:.1%} hardness reduction")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_lime_softening_dose_calculation(results: TestResults):
    """Test specialized lime softening dose calculation"""
    test_name = "Lime softening dose calculation"
    start_time = asyncio.get_event_loop().time()
    
    try:
        hard_water = {
            'analysis': {
                'Ca': 200,
                'Mg': 90,
                'Alkalinity': 180,
                'Cl': 220,
                'pH': 7.7
            },
            'temperature_celsius': 20
        }
        
        target_hardness = 85  # mg/L as CaCO3
        
        result = await calculate_lime_softening_dose(
            initial_water=hard_water,
            target_hardness_mg_caco3=target_hardness,
            database='minteq.dat'
        )
        
        # Validate result structure
        assert 'converged' in result, "Missing convergence status"
        assert 'doses' in result, "Missing dose information"
        assert 'final_state' in result, "Missing final state"
        assert 'objective_results' in result, "Missing objective results"
        
        # Validate lime dosing
        if result['converged']:
            assert 'Ca(OH)2' in result['doses'], "Missing lime dose"
            lime_dose = result['doses']['Ca(OH)2']
            assert lime_dose > 0, "Lime dose should be positive"
            assert lime_dose < 20, "Lime dose too high"
            
            # Check hardness achievement
            hardness_result = result['objective_results']['total_hardness']
            achieved_hardness = hardness_result['current']
            assert achieved_hardness < 150, "Hardness not significantly reduced"
        
        duration = asyncio.get_event_loop().time() - start_time
        convergence_status = "converged" if result['converged'] else "non-converged"
        results.record_pass(test_name, duration, f"Target {target_hardness} mg/L - {convergence_status}")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_phosphorus_removal_optimization(results: TestResults):
    """Test specialized phosphorus removal optimization using calculate_ferric_dose_for_tp"""
    test_name = "Phosphorus removal optimization"
    start_time = asyncio.get_event_loop().time()

    try:
        p_water = {
            'analysis': {
                'P': 8.5,  # High phosphorus
                'Ca': 80,
                'Mg': 25,
                'Alkalinity': "as CaCO3 140",
                'Cl': 160,
            },
            'units': 'mg/L',
            'ph': 7.2,
            'temperature_celsius': 25
        }

        target_p = 0.8  # mg/L
        iron_source = 'FeCl3'
        target_ph = 7.5

        result = await calculate_ferric_dose_for_tp({
            'initial_solution': p_water,
            'target_residual_p_mg_l': target_p,
            'iron_source': iron_source,
            'database': 'minteq.v4.dat',
            'ph_adjustment': {
                'enabled': True,
                'target_ph': target_ph,
                'reagent': 'NaOH',
            },
        })

        # Validate result structure
        assert result.get('status') == 'success', f"Expected success, got: {result.get('error', result.get('status'))}"
        assert 'optimization_summary' in result, "Missing optimization_summary"

        opt = result['optimization_summary']

        # Validate Fe dose
        assert opt.get('optimal_fe_dose_mg_l') is not None, "Missing optimal_fe_dose_mg_l"
        assert opt.get('optimal_fe_dose_mg_l') > 0, "Fe dose should be positive"

        # Check P removal
        achieved_p = opt.get('achieved_p_mg_l')
        assert achieved_p is not None, "Missing achieved_p_mg_l"
        assert achieved_p < 5.0, "Phosphorus not significantly reduced"

        # Check convergence
        convergence = opt.get('convergence_achieved', False)

        duration = asyncio.get_event_loop().time() - start_time
        convergence_status = "converged" if convergence else "non-converged"
        results.record_pass(test_name, duration, f"Target {target_p} mg/L P, achieved {achieved_p:.2f} mg/L - {convergence_status}")

    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multi_reagent_pareto_optimization(results: TestResults):
    """Test Phase 3.2: Multi-reagent Pareto front optimization"""
    test_name = "Multi-reagent Pareto optimization"
    start_time = asyncio.get_event_loop().time()
    
    try:
        complex_water = {
            'analysis': {
                'P': 6.0,
                'Ca': 140,
                'Mg': 60,
                'Alkalinity': 120,
                'Cl': 180,
                'pH': 6.8
            },
            'temperature_celsius': 23
        }
        
        reagents = [
            {'formula': 'FeCl3', 'min_dose': 0, 'max_dose': 15},
            {'formula': 'Ca(OH)2', 'min_dose': 0, 'max_dose': 8}
        ]
        
        # Conflicting objectives: minimize P vs minimize hardness
        objectives = [
            {
                'parameter': 'residual_phosphorus',
                'value': 1.0,
                'constraint_type': 'minimize',
                'weight': 0.6
            },
            {
                'parameter': 'total_hardness', 
                'value': 100,
                'constraint_type': 'minimize',
                'weight': 0.4
            }
        ]
        
        result = await optimize_multi_reagent_treatment(
            initial_water=complex_water,
            reagents=reagents,
            objectives=objectives,
            optimization_strategy='pareto_front',
            database='minteq.dat'
        )
        
        # Validate Pareto optimization structure
        assert 'strategy' in result, "Missing strategy"
        assert result['strategy'] == 'pareto_front', "Wrong strategy"
        assert 'pareto_front' in result, "Missing Pareto front"
        assert 'recommended_solutions' in result, "Missing recommended solutions"
        
        pareto_front = result['pareto_front']
        recommended = result['recommended_solutions']
        
        # Should have some solutions
        assert len(pareto_front) >= 1, "Empty Pareto front"
        assert len(recommended) >= 1, "No recommended solutions"
        assert len(recommended) <= len(pareto_front), "More recommended than Pareto solutions"
        
        # Validate solution structure
        for solution in recommended:
            assert 'doses' in solution, "Missing doses in solution"
            assert 'objective_values' in solution, "Missing objective values"
            assert len(solution['objective_values']) == len(objectives), "Wrong number of objective values"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"{len(pareto_front)} Pareto solutions, {len(recommended)} recommended")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_multi_reagent_weighted_optimization(results: TestResults):
    """Test Phase 3.2: Weighted sum multi-reagent optimization"""
    test_name = "Multi-reagent weighted sum optimization"
    start_time = asyncio.get_event_loop().time()
    
    try:
        water = {
            'analysis': {
                'P': 4.5,
                'pH': 7.0,
                'Ca': 100,
                'Alkalinity': 150,
                'Cl': 120
            },
            'temperature_celsius': 25
        }
        
        reagents = [
            {'formula': 'Al2(SO4)3', 'min_dose': 0, 'max_dose': 12},
            {'formula': 'NaOH', 'min_dose': 0, 'max_dose': 6}
        ]
        
        objectives = [
            {
                'parameter': 'residual_phosphorus',
                'value': 1.5,
                'weight': 0.7,
                'tolerance': 0.3
            },
            {
                'parameter': 'pH',
                'value': 7.8,
                'weight': 0.3,
                'tolerance': 0.2
            }
        ]
        
        result = await optimize_multi_reagent_treatment(
            initial_water=water,
            reagents=reagents,
            objectives=objectives,
            optimization_strategy='weighted_sum',
            database='minteq.dat'
        )
        
        # Validate weighted sum optimization
        assert 'strategy' in result, "Missing strategy"
        assert result['strategy'] == 'weighted_sum', "Wrong strategy"
        assert 'optimal_doses' in result, "Missing optimal doses"
        assert 'weighted_score' in result, "Missing weighted score"
        assert 'objective_weights' in result, "Missing objective weights"
        
        optimal_doses = result['optimal_doses']
        
        # Should have at least one reagent
        assert len(optimal_doses) >= 1, "No optimal doses"
        
        # Validate dose values
        for reagent, dose in optimal_doses.items():
            assert dose >= 0, f"Negative dose for {reagent}"
            assert dose <= 20, f"Excessive dose for {reagent}: {dose}"
        
        # Check objective weights sum to 1
        weights = result['objective_weights']
        total_weight = sum(weights.values())
        assert abs(total_weight - 1.0) < 0.01, f"Weights don't sum to 1: {total_weight}"
        
        duration = asyncio.get_event_loop().time() - start_time
        reagent_count = len(optimal_doses)
        results.record_pass(test_name, duration, f"{reagent_count} reagents optimized")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_robust_optimization(results: TestResults):
    """Test Phase 3.2: Robust optimization with uncertainty"""
    test_name = "Robust optimization with uncertainty"
    start_time = asyncio.get_event_loop().time()
    
    try:
        base_water = {
            'analysis': {
                'Ca': 120,
                'Mg': 50,
                'Alkalinity': 140,
                'pH': 7.5,
                'Cl': 160
            },
            'temperature_celsius': 22
        }
        
        reagents = [
            {'formula': 'Ca(OH)2', 'min_dose': 0, 'max_dose': 10}
        ]
        
        objectives = [
            {
                'parameter': 'total_hardness',
                'value': 90,
                'tolerance': 15,
                'weight': 1.0
            }
        ]
        
        result = await optimize_multi_reagent_treatment(
            initial_water=base_water,
            reagents=reagents,
            objectives=objectives,
            optimization_strategy='robust',
            database='minteq.dat'
        )
        
        # Validate robust optimization
        assert 'strategy' in result, "Missing strategy"
        assert result['strategy'] == 'robust', "Wrong strategy"
        assert 'optimal_doses' in result, "Missing optimal doses"
        assert 'robustness_analysis' in result, "Missing robustness analysis"
        assert 'uncertainty_scenarios' in result, "Missing uncertainty scenarios"
        
        robustness = result['robustness_analysis']
        assert 'worst_case_score' in robustness, "Missing worst case score"
        assert 'average_score' in robustness, "Missing average score"
        assert 'robust_score' in robustness, "Missing robust score"
        
        # Should have tested multiple scenarios
        n_scenarios = result['uncertainty_scenarios']
        assert n_scenarios >= 10, f"Too few uncertainty scenarios: {n_scenarios}"
        
        # Robust score should be reasonable
        robust_score = robustness['robust_score']
        assert robust_score >= 0, "Negative robust score"
        assert robust_score < 1000, "Excessive robust score"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"{n_scenarios} uncertainty scenarios")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_treatment_train_simulation(results: TestResults):
    """Test treatment train simulation with sequential steps"""
    test_name = "Treatment train simulation"
    start_time = asyncio.get_event_loop().time()
    
    try:
        raw_water = {
            'analysis': {
                'P': 12.0,  # High P, needs coagulation
                'Ca': 180,  # High hardness, needs softening
                'Mg': 80,
                'Alkalinity': 160,
                'pH': 6.5,  # Low pH, needs adjustment
                'Cl': 200
            },
            'temperature_celsius': 20
        }
        
        # Multi-step treatment train
        treatment_train = {
            'name': 'phosphorus_hardness_treatment',
            'type': 'treatment_train',
            'steps': [
                {
                    'name': 'coagulation',
                    'type': 'chemical_addition',
                    'reactants': [{'formula': 'FeCl3', 'amount': 8, 'units': 'mmol'}],
                    'allow_precipitation': True,
                    'equilibrium_minerals': ['Strengite', 'Fe(OH)3']
                },
                {
                    'name': 'pH_adjustment',
                    'type': 'chemical_addition', 
                    'reactants': [{'formula': 'Ca(OH)2', 'amount': 2, 'units': 'mmol'}],
                    'allow_precipitation': True,
                    'equilibrium_minerals': ['Calcite', 'Brucite']
                },
                {
                    'name': 'final_softening',
                    'type': 'chemical_addition',
                    'reactants': [{'formula': 'Ca(OH)2', 'amount': 4, 'units': 'mmol'}],
                    'allow_precipitation': True,
                    'equilibrium_minerals': ['Calcite', 'Brucite']
                }
            ]
        }
        
        input_data = {
            'base_solution': raw_water,
            'scenarios': [treatment_train],
            'parallel_limit': 1,
            'output_format': 'full'
        }
        
        result = await batch_process_scenarios(input_data)
        
        # Validate treatment train result
        assert len(result['results']) == 1, "Should have one treatment train result"
        train_result = result['results'][0]['result']
        
        assert 'train_results' in train_result, "Missing train results"
        assert 'final_solution' in train_result, "Missing final solution"
        
        train_steps = train_result['train_results']
        assert len(train_steps) == 3, f"Expected 3 steps, got {len(train_steps)}"
        
        # Validate step progression
        step_names = [step.get('name', 'unknown') for step in train_steps if isinstance(step, dict)]
        print(f"    Treatment steps: {step_names}")
        
        # Each step should have some result
        successful_steps = 0
        for step in train_steps:
            if isinstance(step, dict) and 'error' not in str(step):
                successful_steps += 1
        
        assert successful_steps >= 2, f"Too few successful steps: {successful_steps}/3"
        
        duration = asyncio.get_event_loop().time() - start_time
        results.record_pass(test_name, duration, f"{successful_steps}/3 steps successful")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def test_batch_performance_scalability(results: TestResults):
    """Test batch processing performance and scalability"""
    test_name = "Batch processing performance scalability"
    start_time = asyncio.get_event_loop().time()
    
    try:
        base_water = {
            'analysis': {
                'Ca': 100,
                'Mg': 40,
                'Alkalinity': 120,
                'pH': 7.5,
                'Cl': 140
            },
            'temperature_celsius': 25
        }
        
        # Test scalability with different batch sizes
        batch_sizes = [2, 5, 10]  # Reduced for testing
        performance_results = []
        
        for batch_size in batch_sizes:
            batch_start = asyncio.get_event_loop().time()
            
            # Create scenarios
            scenarios = []
            for i in range(batch_size):
                dose = 0.5 + i * 0.2  # Ensure positive doses
                scenarios.append({
                    'name': f'scenario_{i}',
                    'type': 'chemical_addition',
                    'reactants': [{'formula': 'NaOH', 'amount': dose, 'units': 'mmol'}],
                    'allow_precipitation': False
                })
            
            input_data = {
                'base_solution': base_water,
                'scenarios': scenarios,
                'parallel_limit': min(batch_size, 5),
                'output_format': 'summary'
            }
            
            batch_result = await batch_process_scenarios(input_data)
            batch_time = asyncio.get_event_loop().time() - batch_start
            
            # Validate batch result
            assert 'summary' in batch_result, "Missing summary"
            assert 'details' in batch_result, "Missing details"
            
            summary = batch_result['summary']
            successful = summary['successful']
            
            performance_results.append({
                'batch_size': batch_size,
                'time': batch_time,
                'successful': successful,
                'throughput': successful / batch_time if batch_time > 0 else 0
            })
            
            print(f"    Batch size {batch_size}: {successful}/{batch_size} successful in {batch_time:.2f}s")
        
        # Validate performance scaling
        assert len(performance_results) == len(batch_sizes), "Missing performance data"
        
        # Check that larger batches don't take proportionally longer (parallel benefit)
        if len(performance_results) >= 2:
            small_batch = performance_results[0]
            large_batch = performance_results[-1]
            
            efficiency_ratio = (large_batch['throughput'] / small_batch['throughput']) if small_batch['throughput'] > 0 else 1
            print(f"    Throughput efficiency: {efficiency_ratio:.2f}x")
            
            # Parallel processing should provide some benefit
            assert efficiency_ratio > 0.5, "Poor parallel processing efficiency"
        
        duration = asyncio.get_event_loop().time() - start_time
        max_throughput = max(p['throughput'] for p in performance_results)
        results.record_pass(test_name, duration, f"Max throughput: {max_throughput:.1f} scenarios/s")
        
    except Exception as e:
        results.record_fail(test_name, str(e))


async def main():
    """Run all batch processing tests"""
    print(f"\nBatch Processing Test Suite")
    print(f"Testing Phase 1.3 and Phase 3.2 enhancements")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    results = TestResults()
    
    # Phase 1.3: Basic batch processing
    await test_basic_batch_processing(results)
    await test_parameter_sweep(results)
    await test_treatment_train_simulation(results)
    
    # Phase 1.3: Specialized functions
    await test_lime_softening_curve(results)
    await test_lime_softening_dose_calculation(results)
    await test_phosphorus_removal_optimization(results)
    
    # Phase 3.2: Advanced multi-reagent optimization
    await test_multi_reagent_pareto_optimization(results)
    await test_multi_reagent_weighted_optimization(results)
    await test_robust_optimization(results)
    
    # Performance validation
    await test_batch_performance_scalability(results)
    
    # Print summary
    results.print_summary()
    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Return exit code for CI/CD
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)