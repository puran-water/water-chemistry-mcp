@echo off
REM Comprehensive test script for Water Chemistry MCP Server - CORRECTED VERSION
REM Run from parent directory: C:\Users\hvksh\mcp-servers
REM This script activates venv and runs all corrected tool tests

echo ========================================
echo Water Chemistry MCP Server Test Suite - CORRECTED
echo ========================================
echo.
echo CORRECTIONS APPLIED:
echo - C(4) notation for carbonate chemistry (not Alkalinity)
echo - solution_summary key access (not summary)
echo - Full database paths for reliability
echo - Calcite availability confirmed for lime softening
echo.

REM Check if we're in the right directory
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: venv not found. Please run from C:\Users\hvksh\mcp-servers
    exit /b 1
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Change to water-chemistry-mcp directory
cd water-chemistry-mcp

REM Run corrected test scripts
echo.
echo Running Corrected Tool Tests...
echo ========================================

REM Test 1: Solution Speciation (PROVEN WORKING - 7/7 tests pass)
echo.
echo [1/5] Testing calculate_solution_speciation (CORRECTED)...
python tests\test_solution_speciation_corrected.py
if %errorlevel% neq 0 (
    echo ERROR: Solution speciation tests failed!
    goto :error
)

REM Test 2: Chemical Addition (CORRECTED)
echo.
echo [2/5] Testing simulate_chemical_addition (CORRECTED)...
python tests\test_chemical_addition_corrected.py
if %errorlevel% neq 0 (
    echo ERROR: Chemical addition tests failed!
    goto :error
)

REM Test 3: Dosing Requirement (CORRECTED)
echo.
echo [3/5] Testing calculate_dosing_requirement (CORRECTED)...
python tests\test_dosing_requirement_corrected.py
if %errorlevel% neq 0 (
    echo ERROR: Dosing requirement tests failed!
    goto :error
)

REM Test 4: Solution Mixing (CORRECTED)
echo.
echo [4/5] Testing simulate_solution_mixing (CORRECTED)...
python tests\test_solution_mixing_corrected.py
if %errorlevel% neq 0 (
    echo ERROR: Solution mixing tests failed!
    goto :error
)

REM Test 5: Scaling Potential (CORRECTED)
echo.
echo [5/5] Testing predict_scaling_potential (CORRECTED)...
python tests\test_scaling_potential_corrected.py
if %errorlevel% neq 0 (
    echo ERROR: Scaling potential tests failed!
    goto :error
)

echo.
echo ========================================
echo All corrected tests completed successfully!
echo ========================================
echo.
echo CRITICAL FIXES VERIFIED:
echo - Calcite available for lime softening applications
echo - C(4) notation enables proper carbonate chemistry
echo - All tools support both equilibrium and kinetic precipitation
echo - Database loading and wrapper functioning correctly
echo.
goto :end

:error
echo.
echo ========================================
echo Corrected test suite failed!
echo ========================================
echo Please check the output above for specific errors.
exit /b 1

:end
cd ..
echo Test suite completed successfully - all critical issues resolved!
exit /b 0