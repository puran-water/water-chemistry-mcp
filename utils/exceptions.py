"""
Custom exceptions for the Water Chemistry MCP Server.

This module implements a "FAIL LOUDLY" philosophy:
- All errors raise typed exceptions
- No silent fallbacks
- No returning {"error": ...} patterns
- Exceptions are converted to MCP isError=True by FastMCP

Exception Hierarchy:
    WaterChemistryError (base)
    ├── InputValidationError
    ├── DatabaseError
    │   ├── DatabaseNotFoundError
    │   ├── DatabaseLoadError
    │   └── DatabaseQueryError
    ├── FeatureNotSupportedError
    ├── PhreeqcSimulationError
    ├── ConvergenceError
    │   ├── DosingConvergenceError
    │   └── OptimizationConvergenceError
    ├── KineticsDefinitionError
    ├── SurfaceDefinitionError
    ├── RedoxSpecificationError
    ├── GasPhaseError
    ├── ThermoQueryError
    │   ├── TermNotFoundError
    │   └── AmbiguousQueryError
    ├── ParameterNotFoundError
    └── BatchSimulationError
"""

from typing import Any, Dict, List, Optional


class WaterChemistryError(Exception):
    """Base exception for all water chemistry errors.

    All exceptions in this module inherit from this class,
    allowing for broad exception handling when needed.
    """
    pass


class InputValidationError(WaterChemistryError):
    """Invalid input data provided to a tool.

    Raised when:
    - Required fields are missing
    - Field values are out of valid range
    - Field types are incorrect
    - Incompatible field combinations
    """
    pass


# =============================================================================
# Database Errors
# =============================================================================

class DatabaseError(WaterChemistryError):
    """Base class for database-related errors."""
    pass


class DatabaseNotFoundError(DatabaseError):
    """Database file not found at the specified path.

    Attributes:
        database_path: The path that was searched
        available_databases: List of available database paths (if known)
    """
    def __init__(
        self,
        message: str,
        database_path: Optional[str] = None,
        available_databases: Optional[List[str]] = None
    ):
        super().__init__(message)
        self.database_path = database_path
        self.available_databases = available_databases or []


class DatabaseLoadError(DatabaseError):
    """Failed to load database (PHREEQC parsing error).

    This is raised when phreeqpython's load_database fails.
    Note: phreeqpython.load_database() does NOT raise on failure,
    it only sets phc_database_error_count. We must check and raise.

    Attributes:
        database_path: Path to the database that failed to load
        phreeqc_error: The error message from PHREEQC
        error_count: Number of errors reported by PHREEQC
    """
    def __init__(
        self,
        message: str,
        database_path: Optional[str] = None,
        phreeqc_error: Optional[str] = None,
        error_count: int = 0
    ):
        super().__init__(message)
        self.database_path = database_path
        self.phreeqc_error = phreeqc_error
        self.error_count = error_count


class DatabaseQueryError(DatabaseError):
    """Error querying the thermodynamic database.

    Raised when a database query fails for reasons other than
    the term not being found.
    """
    pass


# =============================================================================
# Feature and Simulation Errors
# =============================================================================

class FeatureNotSupportedError(WaterChemistryError):
    """Feature requires capabilities not available without explicit opt-in.

    This is raised when a feature requires raw PHREEQC input but
    allow_raw_phreeqc=False (the default). Users must explicitly
    opt-in to raw PHREEQC fallback.

    Attributes:
        feature: Name of the unsupported feature
        requires_raw_phreeqc: Whether the feature requires raw PHREEQC input
        remediation: How to enable or work around this
    """
    def __init__(
        self,
        message: str,
        feature: Optional[str] = None,
        requires_raw_phreeqc: bool = False,
        remediation: Optional[str] = None
    ):
        super().__init__(message)
        self.feature = feature
        self.requires_raw_phreeqc = requires_raw_phreeqc
        self.remediation = remediation or (
            "Set allow_raw_phreeqc=True to enable this feature"
            if requires_raw_phreeqc else None
        )


class PhreeqcSimulationError(WaterChemistryError):
    """PHREEQC simulation failed during execution.

    Raised when VIPhreeqc.run_string() returns errors.

    Attributes:
        phreeqc_input: The PHREEQC input that caused the error
        phreeqc_error: The error message from PHREEQC
    """
    def __init__(
        self,
        message: str,
        phreeqc_input: Optional[str] = None,
        phreeqc_error: Optional[str] = None
    ):
        super().__init__(message)
        self.phreeqc_input = phreeqc_input
        self.phreeqc_error = phreeqc_error


# =============================================================================
# Convergence Errors
# =============================================================================

class ConvergenceError(WaterChemistryError):
    """Base class for optimization/search convergence failures."""
    pass


class DosingConvergenceError(ConvergenceError):
    """Binary search for dose failed to converge.

    Raised when the dosing binary search cannot find a dose that
    achieves the target within the specified tolerance.

    Attributes:
        last_dose: The last dose tried before giving up
        target_param: The parameter being targeted (e.g., 'pH', 'hardness')
        target_value: The target value for the parameter
        achieved_value: The value achieved with last_dose
        iterations: Number of iterations performed
        tolerance: The tolerance that was not met
    """
    def __init__(
        self,
        message: str,
        last_dose: float,
        target_param: str,
        target_value: float,
        achieved_value: float,
        iterations: int,
        tolerance: Optional[float] = None
    ):
        super().__init__(message)
        self.last_dose = last_dose
        self.target_param = target_param
        self.target_value = target_value
        self.achieved_value = achieved_value
        self.iterations = iterations
        self.tolerance = tolerance

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for structured error reporting."""
        return {
            "error_type": "dosing_convergence_error",
            "last_dose": self.last_dose,
            "target_param": self.target_param,
            "target_value": self.target_value,
            "achieved_value": self.achieved_value,
            "iterations": self.iterations,
            "tolerance": self.tolerance,
            "message": str(self)
        }


class OptimizationConvergenceError(ConvergenceError):
    """Multi-objective optimization failed to converge.

    Raised when optimization algorithms cannot find a solution
    that satisfies the objectives within constraints.

    Attributes:
        strategy: The optimization strategy used
        objectives: The objectives that were being optimized
        best_solution: The best solution found before failure
        reason: Why the optimization failed
    """
    def __init__(
        self,
        message: str,
        strategy: Optional[str] = None,
        objectives: Optional[List[Dict[str, Any]]] = None,
        best_solution: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None
    ):
        super().__init__(message)
        self.strategy = strategy
        self.objectives = objectives
        self.best_solution = best_solution
        self.reason = reason


# =============================================================================
# Definition Errors (for building PHREEQC blocks)
# =============================================================================

class KineticsDefinitionError(WaterChemistryError):
    """Invalid kinetics definition provided.

    Raised when build_kinetics_block() receives invalid input.
    Previously this returned empty strings - now it fails loudly.

    Attributes:
        missing_fields: Fields that were expected but missing
        invalid_fields: Fields that had invalid values
    """
    def __init__(
        self,
        message: str,
        missing_fields: Optional[List[str]] = None,
        invalid_fields: Optional[Dict[str, str]] = None
    ):
        super().__init__(message)
        self.missing_fields = missing_fields or []
        self.invalid_fields = invalid_fields or {}


class SurfaceDefinitionError(WaterChemistryError):
    """Invalid surface definition provided.

    Raised when build_surface_block() receives invalid input.
    Previously this returned empty strings - now it fails loudly.

    Attributes:
        missing_fields: Fields that were expected but missing
        invalid_fields: Fields that had invalid values
    """
    def __init__(
        self,
        message: str,
        missing_fields: Optional[List[str]] = None,
        invalid_fields: Optional[Dict[str, str]] = None
    ):
        super().__init__(message)
        self.missing_fields = missing_fields or []
        self.invalid_fields = invalid_fields or {}


class RedoxSpecificationError(WaterChemistryError):
    """Invalid redox specification.

    Raised when redox adjustment parameters are invalid.

    Attributes:
        parameter: The redox parameter (pe, Eh, couple)
        value: The value provided
        issue: Description of what's wrong
    """
    def __init__(
        self,
        message: str,
        parameter: Optional[str] = None,
        value: Optional[Any] = None,
        issue: Optional[str] = None
    ):
        super().__init__(message)
        self.parameter = parameter
        self.value = value
        self.issue = issue


class GasPhaseError(WaterChemistryError):
    """Error in gas phase interaction.

    Raised when gas phase simulation fails.

    Attributes:
        gas_components: The gas components specified
        issue: Description of what went wrong
    """
    def __init__(
        self,
        message: str,
        gas_components: Optional[Dict[str, float]] = None,
        issue: Optional[str] = None
    ):
        super().__init__(message)
        self.gas_components = gas_components
        self.issue = issue


# =============================================================================
# Thermodynamic Query Errors
# =============================================================================

class ThermoQueryError(WaterChemistryError):
    """Base class for thermodynamic database query errors."""
    pass


class TermNotFoundError(ThermoQueryError):
    """The queried term was not found in the database.

    Attributes:
        term: The term that was searched for
        query_type: The type of query (mineral, element, species)
        database: The database that was searched
        suggestions: Similar terms that might be what the user meant
    """
    def __init__(
        self,
        message: str,
        term: Optional[str] = None,
        query_type: Optional[str] = None,
        database: Optional[str] = None,
        suggestions: Optional[List[str]] = None
    ):
        super().__init__(message)
        self.term = term
        self.query_type = query_type
        self.database = database
        self.suggestions = suggestions or []


class AmbiguousQueryError(ThermoQueryError):
    """The query matched multiple items and needs clarification.

    Attributes:
        term: The term that was searched for
        matches: The items that matched
    """
    def __init__(
        self,
        message: str,
        term: Optional[str] = None,
        matches: Optional[List[str]] = None
    ):
        super().__init__(message)
        self.term = term
        self.matches = matches or []


# =============================================================================
# Other Errors
# =============================================================================

class ParameterNotFoundError(WaterChemistryError):
    """A requested parameter was not found in the solution/result.

    Raised when trying to extract a parameter (pH, pe, hardness, etc.)
    that doesn't exist in the simulation results.

    Attributes:
        parameter: The parameter that was requested
        available_parameters: Parameters that are available
    """
    def __init__(
        self,
        message: str,
        parameter: Optional[str] = None,
        available_parameters: Optional[List[str]] = None
    ):
        super().__init__(message)
        self.parameter = parameter
        self.available_parameters = available_parameters or []


class BatchSimulationError(WaterChemistryError):
    """Batch processing failed.

    Raised when allow_partial=False (default) and any scenario fails.
    Contains details about which scenarios failed.

    Attributes:
        failed_scenarios: List of scenario names that failed
        errors: Dict mapping scenario name to error message
        completed_scenarios: List of scenarios that completed successfully
    """
    def __init__(
        self,
        message: str,
        failed_scenarios: Optional[List[str]] = None,
        errors: Optional[Dict[str, str]] = None,
        completed_scenarios: Optional[List[str]] = None
    ):
        super().__init__(message)
        self.failed_scenarios = failed_scenarios or []
        self.errors = errors or {}
        self.completed_scenarios = completed_scenarios or []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for structured error reporting."""
        return {
            "error_type": "batch_simulation_error",
            "failed_scenarios": self.failed_scenarios,
            "errors": self.errors,
            "completed_scenarios": self.completed_scenarios,
            "message": str(self)
        }
