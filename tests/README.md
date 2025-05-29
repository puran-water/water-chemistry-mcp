# Water Chemistry MCP Server Tests

This directory contains test scripts for validating the fixes to the Water Chemistry MCP Server.

## Test Overview

The tests validate the following fixed components:

1. **Dosing Requirement** (`test_dosing_convergence.py`)
   - Tests the fix for the convergence error in the calculate_dosing_requirement tool
   - Validates handling of small and large pH adjustments
   - Tests high alkalinity scenarios that challenge convergence
   - Verifies correct handling of mineral precipitation during dosing

2. **Kinetic Reaction** (`test_kinetic_reaction.py`)
   - Tests the fix for the processing error in the simulate_kinetic_reaction tool
   - Validates RATES and KINETICS block generation
   - Tests simple and complex rate laws
   - Verifies handling of multiple concurrent reactions
   - Tests various time step configurations

3. **Thermodynamic Database** (`test_thermodynamic_database.py`)
   - Tests the fix for database path resolution
   - Validates mineral and element queries from different databases
   - Tests case sensitivity handling in queries
   - Verifies error recovery for invalid inputs

## Known Environment Issues

### Database Loading Problems

The most common error you might encounter is:
```
ERROR: RunString: No database is loaded
```

This typically occurs due to:

1. **Path Resolution Issues**: Windows paths in WSL or vice versa
   - **Solution**: Use absolute paths in the native format
   - For Windows: `C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x6\\database\\phreeqc.dat`
   - For WSL: `/mnt/c/Program Files/USGS/phreeqc-3.8.6-17100-x6/database/phreeqc.dat`

2. **Database Location**: PhreeqPython has specific locations it searches for databases
   - **Solution**: Place databases in standard locations like `C:\Program Files\USGS\phreeqc-3.8.6-17100-x6\database\`
   - Or provide absolute paths in test files

3. **PhreeqPython Setup**: The Python wrapper may not be properly initialized
   - **Solution**: Verify PhreeqPython is correctly installed and can load databases directly:
     ```python
     from phreeqpython import PhreeqPython
     pp = PhreeqPython(database="C:\\Path\\To\\phreeqc.dat")
     # Should not raise exceptions if working
     ```

### Database Content Issues

Tests may fail with:
```
Error: Mineral/element not found in database
```

This indicates:

1. **Database Mismatch**: The mineral/element being queried doesn't exist in the specified database
   - **Solution**: Verify the database contains the requested item using the PHREEQC manual
   - Different databases (phreeqc.dat vs. llnl.dat) contain different sets of minerals/elements

2. **Case Sensitivity**: Some queries may be case-sensitive
   - **Solution**: Match the exact case from the database (e.g., "Calcite" vs "CALCITE")

### Convergence Issues

Failures with:
```
Maximum iterations reached without convergence
```

Occur because:

1. **Algorithm Limitations**: The bisection/secant methods have inherent limitations
   - **Solution**: Increase max_iterations in test cases
   - Try adjusting tolerance values

2. **Complex Chemistry**: Some chemical systems are harder to converge
   - **Solution**: Simplify test cases by reducing the number of minerals, reactions, etc.

## Running the Tests

### On Windows

1. Open Command Prompt
2. Navigate to the MCP server directory
3. Run the test batch file:
   ```
   cd C:\Users\hvksh\mcp-servers\water-chemistry-mcp
   tests\run_tests.bat
   ```

> **Note**: Some tests may fail due to environment-specific issues described above. Focus on the error messages to determine if it's a code issue or environment issue.

### On WSL

1. Open WSL terminal
2. Navigate to the MCP server directory
3. Run the test script:
   ```
   cd /mnt/c/Users/hvksh/mcp-servers/water-chemistry-mcp
   python tests/run_all_tests.py
   ```

## Individual Tests

You can also run individual tests if needed:

```
python tests/test_dosing_convergence.py
python tests/test_kinetic_reaction.py
python tests/test_thermodynamic_database.py
```

## Debugging Tests

To get more information when tests fail:

1. Add more logging to test files:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

2. Check database paths directly:
   ```python
   from utils.database_management import DatabaseManager
   db_manager = DatabaseManager()
   print(db_manager.get_database_path("phreeqc.dat"))
   ```

3. Verify PhreeqPython is working:
   ```python
   from phreeqpython import PhreeqPython
   try:
       pp = PhreeqPython()
       print("PhreeqPython initialized")
   except Exception as e:
       print(f"PhreeqPython initialization failed: {e}")
   ```

## Test Development

Each test module has a similar structure:

1. Individual test functions for different scenarios
2. Helper functions for printing and validating results
3. A `run_all_tests()` function that runs all tests in the module
4. Proper exit codes for CI/CD integration

If you want to add new tests, follow the same pattern and add your test function to the appropriate module.