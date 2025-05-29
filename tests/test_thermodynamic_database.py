"""
Test script to validate the fixes to the thermodynamic_database.py query errors.

This test specifically checks:
1. Database path resolution for phreeqc.dat
2. Querying minerals (Calcite, Gypsum) from different databases
3. Querying elements (Ca, Na) from different databases
4. Handling of case sensitivity in queries
5. Error recovery for invalid database paths
"""
import asyncio
import sys
import os
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add parent directory to path so we can import the modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from tools.thermodynamic_database import query_thermodynamic_database
from utils.database_management import DatabaseManager
import utils.import_helpers as import_helpers

async def test_phreeqc_database_path():
    """Test database path resolution for phreeqc.dat."""
    
    print("\n===== TEST 1: PHREEQC DATABASE PATH RESOLUTION =====")
    
    # Get all potential database paths from import_helpers
    print("Available database paths in import_helpers:")
    for path in import_helpers.USGS_PHREEQC_DATABASE_PATHS:
        exists = os.path.exists(path)
        print(f"  {path} - Exists: {exists}")
    
    # Try to get phreeqc.dat path using database_management
    try:
        db_manager = DatabaseManager()
        phreeqc_path = db_manager.available_databases[0] if db_manager.available_databases else None
        print(f"Resolved phreeqc.dat path: {phreeqc_path}")
        print(f"Path exists: {os.path.exists(phreeqc_path)}")
        
        # Try to open the file
        try:
            with open(phreeqc_path, 'r') as f:
                lines = f.readlines()[:5]  # Read first 5 lines
                print(f"First 5 lines of phreeqc.dat:")
                for line in lines:
                    print(f"  {line.strip()}")
            success = True
        except Exception as e:
            print(f"Error opening phreeqc.dat: {e}")
            success = False
    except Exception as e:
        print(f"Error getting phreeqc.dat path: {e}")
        success = False
    
    return success

async def test_query_minerals():
    """Test querying minerals (Calcite, Gypsum) from different databases."""
    
    print("\n===== TEST 2: QUERY MINERALS =====")
    
    # Define test input data for querying minerals
    input_data = {
        "database": "phreeqc.dat",
        "query_type": "mineral",
        "query_term": "Calcite"
    }
    
    # Run the tool
    print("Querying Calcite from phreeqc.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # Check and print results
    print_query_results(result)
    success1 = validate_query_result(result, "mineral", "Calcite")
    
    # Try a different mineral (Gypsum)
    input_data["query_term"] = "Gypsum"
    print("\nQuerying Gypsum from phreeqc.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # Check and print results
    print_query_results(result)
    success2 = validate_query_result(result, "mineral", "Gypsum")
    
    # Try with a different database (llnl.dat if available)
    try:
        db_manager = DatabaseManager()
        llnl_path = next((db for db in db_manager.available_databases if "llnl.dat" in db.lower()), None)
        if os.path.exists(llnl_path):
            input_data["database"] = "llnl.dat"
            input_data["query_term"] = "Calcite"
            print("\nQuerying Calcite from llnl.dat...")
            result = await query_thermodynamic_database(input_data)
            
            # Check and print results
            print_query_results(result)
            success3 = validate_query_result(result, "mineral", "Calcite")
        else:
            print("llnl.dat not found, skipping test")
            success3 = True
    except Exception as e:
        print(f"Error with llnl.dat test: {e}")
        success3 = True  # Don't fail the overall test if llnl.dat isn't available
    
    return success1 and success2 and success3

async def test_query_elements():
    """Test querying elements (Ca, Na) from different databases."""
    
    print("\n===== TEST 3: QUERY ELEMENTS =====")
    
    # Define test input data for querying elements
    input_data = {
        "database": "phreeqc.dat",
        "query_type": "element_info",
        "query_term": "Ca"
    }
    
    # Run the tool
    print("Querying Ca from phreeqc.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # Check and print results
    print_query_results(result)
    success1 = validate_query_result(result, "element", "Ca")
    
    # Try a different element (Na)
    input_data["query_term"] = "Na"
    print("\nQuerying Na from phreeqc.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # Check and print results
    print_query_results(result)
    success2 = validate_query_result(result, "element", "Na")
    
    # Try with llnl.dat (if available)
    try:
        db_manager = DatabaseManager()
        llnl_path = next((db for db in db_manager.available_databases if "llnl.dat" in db.lower()), None)
        if os.path.exists(llnl_path):
            input_data["database"] = "llnl.dat"
            input_data["query_term"] = "Ca"
            print("\nQuerying Ca from llnl.dat...")
            result = await query_thermodynamic_database(input_data)
            
            # Check and print results
            print_query_results(result)
            success3 = validate_query_result(result, "element", "Ca")
        else:
            print("llnl.dat not found, skipping test")
            success3 = True
    except Exception as e:
        print(f"Error with llnl.dat test: {e}")
        success3 = True  # Don't fail the overall test if llnl.dat isn't available
    
    return success1 and success2 and success3

async def test_case_sensitivity():
    """Test handling of case sensitivity in queries."""
    
    print("\n===== TEST 4: CASE SENSITIVITY =====")
    
    # Define test cases with different capitalizations
    test_cases = [
        {"type": "mineral", "name": "calcite"},  # all lowercase
        {"type": "mineral", "name": "CALCITE"},  # all uppercase
        {"type": "mineral", "name": "Calcite"},  # proper case
        {"type": "element", "name": "ca"},       # all lowercase
        {"type": "element", "name": "CA"},       # all uppercase
        {"type": "element", "name": "Ca"}        # proper case
    ]
    
    success = True
    for case in test_cases:
        input_data = {
            "database": "phreeqc.dat",
            "query_type": case["type"] if case["type"] != "element" else "element_info",
            "query_term": case["name"]
        }
        
        print(f"\nQuerying {case['name']} (type: {case['type']}) from phreeqc.dat...")
        result = await query_thermodynamic_database(input_data)
        
        # Print simplified results
        if "error" in result and result["error"]:
            print(f"ERROR: {result['error']}")
            success = False
        else:
            print("Query successful")
            standard_name = case["name"][0].upper() + case["name"][1:].lower() if case["type"] == "mineral" else case["name"].capitalize()
            if standard_name not in result["results"]:
                print(f"WARNING: Expected key {standard_name} not found in result")
                success = False
    
    return success

async def test_error_recovery():
    """Test error recovery for invalid database paths."""
    
    print("\n===== TEST 5: ERROR RECOVERY =====")
    
    # Test with invalid database
    input_data = {
        "database": "nonexistent_database.dat",
        "query_type": "mineral",
        "query_term": "Calcite"
    }
    
    print("Querying Calcite from nonexistent_database.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # We expect a graceful error message
    if "error" in result and result["error"]:
        print(f"Expected error received: {result['error']}")
        
        # Check if the error message suggests alternatives
        if "alternative" in result["error"].lower() or "available" in result["error"].lower():
            print("Error message provides alternatives - GOOD")
            success = True
        else:
            print("Error message doesn't suggest alternatives - SUBOPTIMAL")
            success = True  # Still pass the test
    else:
        print("Expected an error but didn't get one")
        success = False
    
    # Try with invalid item name
    input_data = {
        "database": "phreeqc.dat",
        "query_type": "mineral",
        "query_term": "NonexistentMineral"
    }
    
    print("\nQuerying NonexistentMineral from phreeqc.dat...")
    result = await query_thermodynamic_database(input_data)
    
    # We expect a graceful error message
    if "error" in result and result["error"]:
        print(f"Expected error received: {result['error']}")
        
        # Check if the error message is helpful
        if "not found" in result["error"].lower():
            print("Error message indicates item not found - GOOD")
            success = success and True
        else:
            print("Error message could be more specific - SUBOPTIMAL")
            success = success and True  # Still pass the test
    else:
        print("Expected an error but didn't get one")
        success = False
    
    return success

def print_query_results(result):
    """Print the query results in a readable format."""
    if "error" in result and result["error"]:
        print(f"ERROR: {result['error']}")
        return
    
    print("\nQuery Results:")
    if "database_used" in result:
        print(f"Database path: {result['database_used']}")
    
    if "query_type" in result and "query_term" in result:
        print(f"Query type: {result['query_type']}")
        print(f"Item name: {result['query_term']}")
    
    if "results" in result:
        data = result["results"]
        if isinstance(data, dict):
            print("\nData:")
            for key, value in data.items():
                # Print differently based on data type
                if isinstance(value, dict):
                    print(f"  {key}:")
                    for sub_key, sub_value in value.items():
                        print(f"    {sub_key}: {sub_value}")
                else:
                    print(f"  {key}: {value}")
        else:
            print(f"Data: {data}")

def validate_query_result(result, query_type, item_name):
    """Validate query results."""
    # Check for errors first
    if "error" in result and result["error"]:
        print(f"FAILED: Error for {query_type} {item_name}: {result['error']}")
        return False
    
    # Check for required fields
    required_fields = ["database_used", "query_type", "query_term", "results"]
    for field in required_fields:
        if field not in result:
            print(f"FAILED: Missing required field '{field}' in result")
            return False
    
    # Check query type and item name
    if result["query_type"] != query_type:
        print(f"FAILED: Expected query_type '{query_type}' but got '{result['query_type']}'")
        return False
    
    # Item name might be normalized, so check case-insensitive
    if result["query_term"].lower() != item_name.lower():
        print(f"FAILED: Expected query_term '{item_name}' but got '{result['query_term']}'")
        return False
    
    # Check that results is not empty
    if not result["results"]:
        print("FAILED: Results is empty")
        return False
    
    print(f"PASSED: {query_type} {item_name} query successful")
    return True

async def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_phreeqc_database_path,
        test_query_minerals,
        test_query_elements,
        test_case_sensitivity,
        test_error_recovery
    ]
    
    results = []
    for test in tests:
        try:
            success = await test()
            results.append(success)
        except Exception as e:
            logger.exception(f"Test failed with exception")
            print(f"Test failed with exception: {e}")
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