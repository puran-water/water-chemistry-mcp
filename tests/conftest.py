"""
Shared pytest fixtures for ferric phosphate test suite.

Uses existing constants from the codebase - no hardcoded molecular weights.
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import existing constants from the codebase
from tools.schemas_ferric import MOLECULAR_WEIGHTS, mg_l_to_mmol, mmol_to_mg_l
from utils.constants import MOLAR_MASS, MG_L_TO_MOL_KGW


# =============================================================================
# STANDARD SOLUTION FIXTURES
# =============================================================================

@pytest.fixture
def typical_wwtp_solution():
    """Typical municipal WWTP secondary effluent."""
    return {
        "ph": 7.0,
        "temperature_celsius": 20.0,
        "analysis": {
            "P": 5.0,
            "Ca": 50,
            "Mg": 20,
            "Na": 100,
            "Cl": 150,
            "Alkalinity": "as CaCO3 150",
        },
        "units": "mg/L",
    }


@pytest.fixture
def high_p_digester_solution():
    """High-P anaerobic digester supernatant."""
    return {
        "ph": 7.2,
        "temperature_celsius": 35.0,
        "analysis": {
            "P": 200.0,
            "Ca": 100,
            "Mg": 50,
            "Na": 500,
            "Cl": 300,
            "S(-2)": 20.0,
            "Alkalinity": "as CaCO3 3000",
        },
        "units": "mg/L",
    }


@pytest.fixture
def low_alkalinity_solution():
    """Low alkalinity water prone to pH crash."""
    return {
        "ph": 6.8,
        "temperature_celsius": 25.0,
        "analysis": {
            "P": 3.0,
            "Ca": 20,
            "Mg": 5,
            "Na": 10,
            "Cl": 20,
            "Alkalinity": "as CaCO3 30",
        },
        "units": "mg/L",
    }


@pytest.fixture
def high_sulfide_solution():
    """High sulfide anaerobic solution."""
    return {
        "ph": 7.0,
        "temperature_celsius": 30.0,
        "analysis": {
            "P": 50.0,
            "Ca": 80,
            "Mg": 30,
            "Na": 200,
            "Cl": 100,
            "S(-2)": 50.0,
            "Alkalinity": "as CaCO3 500",
        },
        "units": "mg/L",
    }


@pytest.fixture
def minimal_solution():
    """Minimal valid solution with only required fields."""
    return {
        "ph": 7.0,
        "analysis": {
            "P": 5.0,
        },
        "units": "mg/L",
    }


# =============================================================================
# STANDARD INPUT FIXTURES
# =============================================================================

@pytest.fixture
def basic_ferric_input(typical_wwtp_solution):
    """Basic ferric dose calculation input."""
    return {
        "initial_solution": typical_wwtp_solution,
        "target_residual_p_mg_l": 0.5,
        "iron_source": "FeCl3",
        "database": "minteq.v4.dat",
    }


@pytest.fixture
def anaerobic_input(high_p_digester_solution):
    """Anaerobic ferric dose calculation input."""
    return {
        "initial_solution": high_p_digester_solution,
        "target_residual_p_mg_l": 50.0,
        "iron_source": "FeSO4",
        "redox": {"mode": "anaerobic"},
        "database": "minteq.v4.dat",
    }


# =============================================================================
# PARSER TEST DATA FIXTURES
# =============================================================================

@pytest.fixture
def sample_selected_output_headers():
    """Sample PHREEQC selected output headers for parser tests."""
    return [
        "sim", "state", "soln", "dist_x", "time", "step",
        "pH", "pe", "si_Calcite", "si_Strengite",
        '"tot_P"', '"tot_Fe"', "P", "Fe", "P(5)", "Fe(2)"
    ]


@pytest.fixture
def sample_selected_output_values():
    """Sample PHREEQC selected output values matching headers."""
    return [
        "1", "react", "1", "-99", "-99", "1",
        "5.5", "8.0", "-0.5", "0.2",
        "1.5e-05", "2.3e-07", "1.5e-05", "2.3e-07", "1.5e-05", "2.3e-07"
    ]


@pytest.fixture
def sample_selected_output_content(sample_selected_output_headers, sample_selected_output_values):
    """Complete sample selected output file content."""
    headers = "\t".join(sample_selected_output_headers)
    values = "\t".join(sample_selected_output_values)
    return f"{headers}\n{values}\n"


@pytest.fixture
def sample_main_output_content():
    """Sample PHREEQC main output file content for parser tests."""
    return """
-----------------------------Solution composition------------------------------

	Elements           Molality       Moles

	Ca                1.249e-03   1.249e-03
	Cl                4.231e-03   4.231e-03
	Fe                2.914e-07   2.914e-07
	Mg                4.115e-04   4.115e-04
	Na                8.696e-04   8.696e-04
	P                 1.603e-05   1.603e-05

----------------------------Description of solution----------------------------

                                       pH  =   5.168
                                       pe  =  13.115
       Specific Conductance (uS/cm,  25C)  = 525
                          Density (g/cm3)  =   0.99736
                               Volume (L)  =   1.00000
                        Viscosity (mPa s)  =   0.89002
                        Activity of water  =   1.000
                 Ionic strength (mol/kgw)  =   5.258e-03
                       Mass of water (kg)  =   1.000e+00
                         Temperature (C)  =  25.00
"""


# =============================================================================
# EXPOSE EXISTING CONSTANTS FOR TESTS
# =============================================================================

@pytest.fixture
def molecular_weights():
    """Expose MOLECULAR_WEIGHTS from schemas_ferric for tests."""
    return MOLECULAR_WEIGHTS


@pytest.fixture
def molar_mass():
    """Expose MOLAR_MASS from constants for tests."""
    return MOLAR_MASS


# =============================================================================
# ASYNC TEST HELPER
# =============================================================================

@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
