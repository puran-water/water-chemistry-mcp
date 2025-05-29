"""
Script to run all test scripts for the water chemistry MCP server.

This script:
1. Runs each test suite
2. Collects results
3. Provides a summary of all tests

Environment-specific notes:
- Database loading errors will be shown in STDERR if they occur
- Some tests may pass on Windows but fail on WSL or vice versa
- See README.md for detailed troubleshooting information
"""
import asyncio
import sys
import os
import importlib.util
import subprocess
import time

def print_separator():
    """Print a separator line."""
    print("\n" + "="*80 + "\n")

def import_module_from_file(module_name, file_path):
    """Import a module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

async def run_test_suites():
    """Run all test suites and collect results."""
    print_separator()
    print("RUNNING ALL TEST SUITES FOR WATER CHEMISTRY MCP SERVER")
    print_separator()
    
    # Get all test files
    test_dir = os.path.dirname(os.path.abspath(__file__))
    test_files = [f for f in os.listdir(test_dir) if f.startswith('test_') and f.endswith('.py') and f != 'test_api.py']
    
    # Sort test files to run in a sensible order
    test_files.sort()
    
    # Print test plan
    print("Test plan:")
    for i, file in enumerate(test_files, 1):
        print(f"{i}. {file}")
    print_separator()
    
    # Run each test suite
    results = {}
    
    for test_file in test_files:
        test_name = test_file[:-3]  # Remove .py extension
        print(f"Running test suite: {test_name}")
        print("-" * 40)
        
        # Build full path to test file
        test_path = os.path.join(test_dir, test_file)
        
        # Option 1: Import and run (more reliable)
        try:
            # Import the module
            module = import_module_from_file(test_name, test_path)
            
            # If it has run_all_tests function, use it
            if hasattr(module, 'run_all_tests'):
                start_time = time.time()
                success = await module.run_all_tests()
                elapsed_time = time.time() - start_time
                
                results[test_name] = {
                    'success': success,
                    'elapsed_time': elapsed_time
                }
            else:
                print(f"WARNING: {test_name} doesn't have run_all_tests function")
                results[test_name] = {
                    'success': False,
                    'elapsed_time': 0,
                    'error': "No run_all_tests function"
                }
        
        # Option 2: Use subprocess as fallback
        except Exception as e:
            print(f"Error importing {test_name}: {e}")
            print("Trying to run as subprocess...")
            
            # Run as subprocess
            start_time = time.time()
            result = subprocess.run([sys.executable, test_path], capture_output=True, text=True)
            elapsed_time = time.time() - start_time
            
            # Print output
            print("STDOUT:")
            print(result.stdout)
            
            if result.stderr:
                print("STDERR:")
                print(result.stderr)
            
            results[test_name] = {
                'success': result.returncode == 0,
                'elapsed_time': elapsed_time,
                'returncode': result.returncode
            }
        
        print_separator()
    
    return results

def print_summary(results):
    """Print a summary of all test results."""
    print("TEST SUMMARY")
    print("-" * 40)
    
    total_tests = len(results)
    passed_tests = sum(1 for result in results.values() if result['success'])
    
    for test_name, result in results.items():
        status = "PASSED" if result['success'] else "FAILED"
        time_str = f"{result['elapsed_time']:.2f}s"
        print(f"{test_name}: {status} ({time_str})")
    
    print("-" * 40)
    print(f"OVERALL: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("\nAll tests passed successfully!")
        return True
    else:
        print("\nSome tests failed. See details above.")
        return False

async def main():
    """Main function to run all tests."""
    try:
        results = await run_test_suites()
        success = print_summary(results)
        return 0 if success else 1
    except Exception as e:
        print(f"Error running tests: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)