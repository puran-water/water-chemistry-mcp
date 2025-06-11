"""
Comprehensive test suite runner for all enhanced features.
Runs all Phase 1-3 enhancement tests and provides detailed reporting.

This is the main test runner for Phase 4.1 validation.

Run from project root with:
python tests/test_enhanced_features.py
"""

import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import individual test modules
from tests.test_enhanced_dosing_requirement import main as test_enhanced_dosing
from tests.test_batch_processing import main as test_batch_processing


class EnhancedTestSuite:
    """Main test suite coordinator for enhanced features"""
    
    def __init__(self):
        self.suite_start_time = time.time()
        self.test_modules = []
        self.overall_results = {
            'total_passed': 0,
            'total_failed': 0,
            'modules_run': 0,
            'modules_passed': 0,
            'execution_times': [],
            'errors': []
        }
    
    async def run_test_module(self, module_name: str, test_function):
        """Run a test module and capture results"""
        print(f"\n{'='*60}")
        print(f"Running {module_name}")
        print(f"{'='*60}")
        
        module_start = time.time()
        
        try:
            # Run the test module
            exit_code = await test_function()
            module_time = time.time() - module_start
            
            self.overall_results['modules_run'] += 1
            self.overall_results['execution_times'].append((module_name, module_time))
            
            if exit_code == 0:
                self.overall_results['modules_passed'] += 1
                print(f"\nPASS {module_name} completed successfully ({module_time:.1f}s)")
            else:
                self.overall_results['errors'].append(f"{module_name}: Non-zero exit code {exit_code}")
                print(f"\nFAIL {module_name} failed with exit code {exit_code} ({module_time:.1f}s)")
            
            return exit_code
            
        except Exception as e:
            module_time = time.time() - module_start
            self.overall_results['modules_run'] += 1
            self.overall_results['execution_times'].append((module_name, module_time))
            self.overall_results['errors'].append(f"{module_name}: {str(e)}")
            print(f"\nFAIL {module_name} failed with exception: {e} ({module_time:.1f}s)")
            return 1
    
    def print_comprehensive_summary(self):
        """Print comprehensive test suite summary"""
        total_time = time.time() - self.suite_start_time
        
        print(f"\n{'='*80}")
        print(f"ENHANCED FEATURES TEST SUITE SUMMARY")
        print(f"{'='*80}")
        
        # Overall statistics
        print(f"\nOverall Results:")
        print(f"  Modules run: {self.overall_results['modules_run']}")
        print(f"  Modules passed: {self.overall_results['modules_passed']}")
        print(f"  Module success rate: {self.overall_results['modules_passed']}/{self.overall_results['modules_run']} ({self.overall_results['modules_passed']/max(1,self.overall_results['modules_run'])*100:.0f}%)")
        print(f"  Total execution time: {total_time:.1f}s")
        
        # Execution time breakdown
        if self.overall_results['execution_times']:
            print(f"\nExecution Time Breakdown:")
            for module_name, exec_time in self.overall_results['execution_times']:
                percentage = (exec_time / total_time) * 100
                print(f"  {module_name}: {exec_time:.1f}s ({percentage:.0f}%)")
        
        # Performance analysis
        if len(self.overall_results['execution_times']) > 1:
            times = [t for _, t in self.overall_results['execution_times']]
            avg_time = sum(times) / len(times)
            print(f"\nPerformance Analysis:")
            print(f"  Average module time: {avg_time:.1f}s")
            print(f"  Fastest module: {min(times):.1f}s")
            print(f"  Slowest module: {max(times):.1f}s")
        
        # Error summary
        if self.overall_results['errors']:
            print(f"\nErrors Encountered:")
            for error in self.overall_results['errors']:
                print(f"  - {error}")
        
        # Success criteria evaluation
        print(f"\nSuccess Criteria Evaluation:")
        success_rate = self.overall_results['modules_passed'] / max(1, self.overall_results['modules_run'])
        
        criteria = [
            ("Module Success Rate >= 80%", success_rate >= 0.8, f"{success_rate*100:.0f}%"),
            ("Total Execution Time < 300s", total_time < 300, f"{total_time:.1f}s"),
            ("No Critical Errors", len(self.overall_results['errors']) == 0, f"{len(self.overall_results['errors'])} errors"),
            ("All Modules Attempted", self.overall_results['modules_run'] >= 2, f"{self.overall_results['modules_run']} modules")
        ]
        
        all_passed = True
        for criterion, passed, value in criteria:
            status = "PASS" if passed else "FAIL"
            print(f"  {criterion}: {status} ({value})")
            if not passed:
                all_passed = False
        
        print(f"\n{'='*80}")
        overall_status = "SUCCESS" if all_passed else "FAILURE"
        print(f"OVERALL TEST SUITE STATUS: {overall_status}")
        print(f"{'='*80}")
        
        return 0 if all_passed else 1


async def test_environment_validation():
    """Validate test environment before running main tests"""
    print("Validating test environment...")
    
    # Test imports
    try:
        from tools.dosing_requirement import calculate_dosing_requirement_enhanced
        from tools.batch_processing import batch_process_scenarios
        from tools.phreeqc_wrapper import evaluate_target_parameter
        print("PASS Enhanced tool imports successful")
    except ImportError as e:
        print(f"FAIL Import error: {e}")
        return False
    
    # Test basic functionality
    try:
        # Simple parameter evaluation test
        mock_result = {
            'element_totals_molality': {'Ca': 0.001, 'Mg': 0.0005},
            'solution_summary': {'pH': 7.5}
        }
        hardness = evaluate_target_parameter(mock_result, {
            'parameter': 'total_hardness',
            'units': 'mg/L as CaCO3'
        })
        assert hardness > 0, "Hardness calculation failed"
        print("PASS Target parameter evaluation functional")
    except Exception as e:
        print(f"FAIL Target parameter evaluation error: {e}")
        return False
    
    return True


async def main():
    """Main test suite execution"""
    print(f"Enhanced Features Comprehensive Test Suite")
    print(f"Phase 4.1: Testing and Validation")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Initialize test suite
    suite = EnhancedTestSuite()
    
    # Validate environment first
    if not await test_environment_validation():
        print("\nFAIL Environment validation failed. Cannot proceed with tests.")
        return 1
    
    print("\nPASS Environment validation passed. Proceeding with test suite.")
    
    # Run all test modules
    test_modules = [
        ("Enhanced Dosing Requirement", test_enhanced_dosing),
        ("Batch Processing", test_batch_processing)
    ]
    
    overall_exit_code = 0
    
    for module_name, test_function in test_modules:
        exit_code = await suite.run_test_module(module_name, test_function)
        if exit_code != 0:
            overall_exit_code = 1
    
    # Print comprehensive summary
    final_exit_code = suite.print_comprehensive_summary()
    
    # Use the most restrictive exit code
    return max(overall_exit_code, final_exit_code)


async def run_integration_tests():
    """Async wrapper for running integration tests"""
    return await main()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\nTest suite completed with exit code: {exit_code}")
    sys.exit(exit_code)