@echo off
REM Master test script for Water Chemistry MCP Server
REM Run from parent directory: C:\Users\hvksh\mcp-servers
REM This script activates venv and runs all tool tests

echo ========================================
echo Water Chemistry MCP Server Test Suite
echo ========================================
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

REM Run individual test scripts
echo.
echo Running Tool Tests...
echo ========================================

REM Test 1: Solution Speciation
echo.
echo [1/5] Testing calculate_solution_speciation...
python tests\test_solution_speciation.py
if %errorlevel% neq 0 (
    echo ERROR: Solution speciation tests failed!
    goto :error
)

REM Test 2: Chemical Addition (includes kinetics)
echo.
echo [2/5] Testing simulate_chemical_addition...
python tests\test_chemical_addition.py
if %errorlevel% neq 0 (
    echo ERROR: Chemical addition tests failed!
    goto :error
)

REM Test 3: Dosing Requirement
echo.
echo [3/5] Testing calculate_dosing_requirement...
python tests\test_dosing_requirement.py
if %errorlevel% neq 0 (
    echo ERROR: Dosing requirement tests failed!
    goto :error
)

REM Test 4: Solution Mixing
echo.
echo [4/5] Testing simulate_solution_mixing...
python tests\test_solution_mixing.py
if %errorlevel% neq 0 (
    echo ERROR: Solution mixing tests failed!
    goto :error
)

REM Test 5: Scaling Potential
echo.
echo [5/5] Testing predict_scaling_potential...
python tests\test_scaling_potential.py
if %errorlevel% neq 0 (
    echo ERROR: Scaling potential tests failed!
    goto :error
)

echo.
echo ========================================
echo All tests completed successfully!
echo ========================================
goto :end

:error
echo.
echo ========================================
echo Test suite failed!
echo ========================================
exit /b 1

:end
cd ..
exit /b 0