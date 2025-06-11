"""
Main test runner for enhanced features testing.
This script is the entry point for Phase 4.1 testing and validation.

Can be run directly or integrated into CI/CD pipelines.

Usage:
  python tests/run_enhanced_tests.py                    # Run all tests
  python tests/run_enhanced_tests.py --performance      # Run only performance benchmarks
  python tests/run_enhanced_tests.py --functionality    # Run only functionality tests
  python tests/run_enhanced_tests.py --quick            # Run quick subset for development
"""

import argparse
import asyncio
import sys
import json
import time
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_enhanced_features import run_integration_tests
from tests.test_performance_benchmarks import main as run_performance_benchmarks


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Enhanced Features Test Suite Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/run_enhanced_tests.py                    # Run all tests
  python tests/run_enhanced_tests.py --performance      # Performance only
  python tests/run_enhanced_tests.py --functionality    # Functionality only
  python tests/run_enhanced_tests.py --quick            # Quick development tests
  python tests/run_enhanced_tests.py --output report.json  # Save detailed report
        """
    )
    
    # Test selection options
    parser.add_argument(
        '--performance', 
        action='store_true',
        help='Run only performance benchmark tests'
    )
    
    parser.add_argument(
        '--functionality', 
        action='store_true',
        help='Run only functionality tests (no performance benchmarks)'
    )
    
    parser.add_argument(
        '--quick', 
        action='store_true',
        help='Run quick subset of tests for development (faster execution)'
    )
    
    # Output options
    parser.add_argument(
        '--output', 
        type=str,
        help='Output detailed test results to JSON file'
    )
    
    parser.add_argument(
        '--verbose', 
        action='store_true',
        help='Enable verbose output'
    )
    
    parser.add_argument(
        '--no-color',
        action='store_true', 
        help='Disable colored output'
    )
    
    return parser.parse_args()


class TestRunner:
    """Orchestrates test execution and reporting"""
    
    def __init__(self, args):
        self.args = args
        self.start_time = time.time()
        self.results = {
            'start_time': datetime.now().isoformat(),
            'test_suites': [],
            'summary': {},
            'environment': {
                'python_version': sys.version,
                'platform': sys.platform
            }
        }
    
    def log(self, message: str, level: str = "INFO"):
        """Log message with optional coloring"""
        if self.args.no_color:
            print(f"[{level}] {message}")
        else:
            colors = {
                'INFO': '\033[94m',    # Blue
                'SUCCESS': '\033[92m', # Green  
                'WARNING': '\033[93m', # Yellow
                'ERROR': '\033[91m',   # Red
                'RESET': '\033[0m'     # Reset
            }
            color = colors.get(level, colors['RESET'])
            print(f"{color}[{level}]{colors['RESET']} {message}")
    
    async def run_test_suite(self, suite_name: str, test_function, description: str = ""):
        """Run a test suite and record results"""
        self.log(f"Starting {suite_name}...", "INFO")
        if description:
            print(f"  {description}")
        
        suite_start = time.time()
        
        try:
            exit_code = await test_function()
            suite_time = time.time() - suite_start
            
            suite_result = {
                'name': suite_name,
                'description': description,
                'exit_code': exit_code,
                'execution_time': suite_time,
                'status': 'PASSED' if exit_code == 0 else 'FAILED',
                'timestamp': datetime.now().isoformat()
            }
            
            self.results['test_suites'].append(suite_result)
            
            if exit_code == 0:
                self.log(f"{suite_name} completed successfully ({suite_time:.1f}s)", "SUCCESS")
            else:
                self.log(f"{suite_name} failed with exit code {exit_code} ({suite_time:.1f}s)", "ERROR")
            
            return exit_code
            
        except Exception as e:
            suite_time = time.time() - suite_start
            suite_result = {
                'name': suite_name,
                'description': description,
                'exit_code': 1,
                'execution_time': suite_time,
                'status': 'ERROR',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            
            self.results['test_suites'].append(suite_result)
            self.log(f"{suite_name} failed with exception: {e} ({suite_time:.1f}s)", "ERROR")
            return 1
    
    def generate_summary(self):
        """Generate test execution summary"""
        total_time = time.time() - self.start_time
        
        passed_suites = [s for s in self.results['test_suites'] if s['status'] == 'PASSED']
        failed_suites = [s for s in self.results['test_suites'] if s['status'] in ['FAILED', 'ERROR']]
        
        self.results['summary'] = {
            'total_execution_time': total_time,
            'total_suites': len(self.results['test_suites']),
            'passed_suites': len(passed_suites),
            'failed_suites': len(failed_suites),
            'success_rate': len(passed_suites) / max(1, len(self.results['test_suites'])),
            'end_time': datetime.now().isoformat()
        }
        
        # Print summary
        print(f"\n{'='*80}")
        print(f"ENHANCED FEATURES TEST EXECUTION SUMMARY")
        print(f"{'='*80}")
        print(f"Total execution time: {total_time:.1f}s")
        print(f"Test suites run: {self.results['summary']['total_suites']}")
        print(f"Passed: {self.results['summary']['passed_suites']}")
        print(f"Failed: {self.results['summary']['failed_suites']}")
        print(f"Success rate: {self.results['summary']['success_rate']*100:.0f}%")
        
        if failed_suites:
            print(f"\nFailed suites:")
            for suite in failed_suites:
                print(f"  - {suite['name']}: {suite.get('error', 'Exit code ' + str(suite['exit_code']))}")
        
        # Overall status
        overall_success = len(failed_suites) == 0
        status_level = "SUCCESS" if overall_success else "ERROR"
        overall_status = "SUCCESS" if overall_success else "FAILURE" 
        print(f"\nOVERALL STATUS: {overall_status}")
        print(f"{'='*80}")
        
        return overall_success
    
    def save_results(self, filename: str):
        """Save detailed results to JSON file"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.results, f, indent=2)
            self.log(f"Test results saved to {filename}", "INFO")
        except Exception as e:
            self.log(f"Failed to save results to {filename}: {e}", "ERROR")


async def main():
    """Main test execution"""
    args = parse_arguments()
    runner = TestRunner(args)
    
    runner.log("Enhanced Features Test Suite Runner", "INFO")
    runner.log(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "INFO")
    
    if args.quick:
        runner.log("Running in QUICK mode (subset of tests)", "WARNING")
    
    # Determine which test suites to run
    test_suites = []
    
    if args.performance and not args.functionality:
        # Performance tests only
        test_suites = [
            ("Performance Benchmarks", run_performance_benchmarks, "Validate performance improvements vs legacy approach")
        ]
    elif args.functionality and not args.performance:
        # Functionality tests only
        test_suites = [
            ("Enhanced Functionality", run_integration_tests, "Test enhanced dosing and batch processing features")
        ]
    elif args.quick:
        # Quick development tests (functionality only, faster)
        test_suites = [
            ("Enhanced Functionality (Quick)", run_integration_tests, "Quick validation of enhanced features")
        ]
    else:
        # All tests (default)
        test_suites = [
            ("Enhanced Functionality", run_integration_tests, "Test enhanced dosing and batch processing features"),
            ("Performance Benchmarks", run_performance_benchmarks, "Validate performance improvements vs legacy approach")
        ]
    
    # Run selected test suites
    overall_exit_code = 0
    
    for suite_name, test_function, description in test_suites:
        exit_code = await runner.run_test_suite(suite_name, test_function, description)
        if exit_code != 0:
            overall_exit_code = 1
    
    # Generate summary
    success = runner.generate_summary()
    if not success:
        overall_exit_code = 1
    
    # Save results if requested
    if args.output:
        runner.save_results(args.output)
    
    # Final exit code
    final_exit_code = max(overall_exit_code, 0 if success else 1)
    runner.log(f"Test suite completed with exit code: {final_exit_code}", 
               "SUCCESS" if final_exit_code == 0 else "ERROR")
    
    return final_exit_code


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nTest execution interrupted by user")
        sys.exit(130)  # Standard exit code for SIGINT
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)