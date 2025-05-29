@echo off
echo Running Water Chemistry MCP Server Test Suite
echo =========================================

cd /d %~dp0\..
echo Current directory: %CD%

echo.
echo Activating virtual environment...
call ..\venv\Scripts\activate.bat

echo.
echo Running test suite...
echo NOTE: Some tests may fail due to environment-specific issues.
echo See tests\README.md for troubleshooting information.
echo.

python tests\run_all_tests.py

echo.
if %ERRORLEVEL% NEQ 0 (
    echo Some tests failed. Review the output above for details.
    echo Check tests\README.md for troubleshooting information.
) else (
    echo All tests completed successfully!
)

echo.
echo Tests completed.
pause