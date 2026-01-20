"""
Information about minerals available in PHREEQC's phreeqc_rates.dat database.

This module provides parameter information for minerals with kinetic rate equations
in the official PHREEQC kinetic rates database.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Information about minerals in phreeqc_rates.dat
PHREEQC_RATES_MINERALS = {
    "Quartz": {
        "description": "SiO2 - Silicon dioxide",
        "rate_reference": "Rimstidt and Barnes, 1980, GCA 44, 1683",
        "rate_constant": "10^-13.7 mol/m²/s at 25°C",
        "activation_energy": "90 kJ/mol",
        "parameters": {
            "parm1": "Specific area of Quartz, m²/mol Quartz",
            "parm2": "Salt correction factor (1 + 1.5 * c_Na in mM), default 1.0",
        },
        "example_parms": [0.146, 1.5],
        "notes": "Rate increases with salt concentration",
    },
    "K-feldspar": {
        "description": "KAlSi3O8 - Potassium feldspar",
        "rate_reference": "Sverdrup and Warfvinge, 1995",
        "mechanisms": ["H+", "H2O", "OH-", "CO2"],
        "parameters": {
            "parm1": "Specific area of K-feldspar, m²/mol",
            "parm2": "Field rate adjustment factor (typically 0.1)",
        },
        "example_parms": [6.41, 0.1],
        "notes": "Multiple rate mechanisms, field rates typically 10x slower than lab",
    },
    "Albite": {
        "description": "NaAlSi3O8 - Sodium feldspar",
        "rate_reference": "Sverdrup and Warfvinge, 1995",
        "mechanisms": ["H+", "H2O", "OH-", "CO2"],
        "parameters": {"parm1": "Specific area, m²/mol", "parm2": "Field rate adjustment factor"},
        "example_parms": [6.41, 0.1],
        "notes": "Similar to K-feldspar kinetics",
    },
    "Calcite": {
        "description": "CaCO3 - Calcium carbonate",
        "rate_reference": "Plummer et al., 1978, AJS 278, 179",
        "mechanisms": ["H+", "CO2", "H2O"],
        "rate_units": "mmol/cm²/s",
        "parameters": {"parm1": "Specific surface area, cm²/mol calcite", "parm2": "Exponent for M/M0 (typically 0.6)"},
        "example_parms": [1.67e5, 0.6],
        "notes": "PWP rate law, includes (1 - Ω^(2/3)) term",
    },
    "Pyrite": {
        "description": "FeS2 - Iron disulfide",
        "rate_reference": "Williamson and Rimstidt, 1994, GCA 58, 5443",
        "rate_units": "mol/m²/s",
        "parameters": {
            "parm1": "log10(specific area), log10(m² per mole pyrite)",
            "parm2": "Exponent for (M/M0)",
            "parm3": "Exponent for O2",
            "parm4": "Exponent for H+",
        },
        "example_parms": [0.3, 0.67, 0.5, -0.11],
        "notes": "Oxidative dissolution, requires dissolved oxygen",
    },
    "Organic_C": {
        "description": "Sediment organic carbon",
        "rate_reference": "Monod kinetics",
        "mechanisms": ["O2", "NO3", "SO4"],
        "parameters": {"parm1": "Initial concentration C_0, mol/kgw", "parm2": "Rate constant, 1/s"},
        "notes": "Multiple electron acceptors with Monod kinetics",
    },
    "Pyrolusite": {
        "description": "MnO2 - Manganese dioxide",
        "rate_reference": "Postma and Appelo, 2000, GCA 64, 1237",
        "parameters": {"parm1": "Specific surface area, m²/g pyrolusite", "parm2": "Exponent for (M/M0)"},
        "example_parms": [50, 0.67],
    },
    "Dolomite": {
        "description": "CaMg(CO3)2 - Calcium magnesium carbonate",
        "rate_reference": "Based on calcite kinetics",
        "parameters": {"parm1": "Specific surface area", "parm2": "Exponent for M/M0"},
        "notes": "Slower than calcite",
    },
}


def get_mineral_info(mineral: str) -> Dict[str, Any]:
    """
    Get information about a mineral in phreeqc_rates.dat.

    Args:
        mineral: Mineral name

    Returns:
        Dictionary with mineral information or None if not found
    """
    # Try exact match
    if mineral in PHREEQC_RATES_MINERALS:
        return PHREEQC_RATES_MINERALS[mineral].copy()

    # Try case-insensitive match
    mineral_lower = mineral.lower()
    for db_mineral, info in PHREEQC_RATES_MINERALS.items():
        if db_mineral.lower() == mineral_lower:
            return info.copy()

    return None


def get_available_minerals() -> List[str]:
    """
    Get list of minerals available in phreeqc_rates.dat.

    Returns:
        List of mineral names
    """
    return list(PHREEQC_RATES_MINERALS.keys())


def get_mineral_parameters(mineral: str) -> Dict[str, str]:
    """
    Get parameter descriptions for a mineral.

    Args:
        mineral: Mineral name

    Returns:
        Dictionary of parameter descriptions
    """
    info = get_mineral_info(mineral)
    if info and "parameters" in info:
        return info["parameters"]
    return {}


def get_example_kinetics_block(mineral: str, m0: float = 0.0) -> str:
    """
    Generate an example KINETICS block for a mineral.

    Args:
        mineral: Mineral name
        m0: Initial moles

    Returns:
        KINETICS block string
    """
    info = get_mineral_info(mineral)
    if not info:
        return f"# {mineral} not found in phreeqc_rates.dat"

    lines = [f"KINETICS 1"]
    lines.append(f"{mineral}")
    lines.append(f"    -m0 {m0}")
    lines.append(f"    -m {m0}")

    if "example_parms" in info:
        parms_str = " ".join(str(p) for p in info["example_parms"])
        lines.append(f"    -parms {parms_str}")

    lines.append(f"    -tol 1e-8")
    lines.append(f"    -steps 3600 in 10  # 1 hour in 10 steps")

    # Add comments
    lines.append(f"")
    lines.append(f"# {info.get('description', mineral)}")
    lines.append(f"# Reference: {info.get('rate_reference', 'See phreeqc_rates.dat')}")

    if "parameters" in info:
        lines.append(f"# Parameters:")
        for param, desc in info["parameters"].items():
            lines.append(f"#   {param}: {desc}")

    return "\n".join(lines)


def format_for_mcp_input(mineral: str, surface_area: float = 1.0, field_factor: float = 1.0) -> Dict[str, Any]:
    """
    Format mineral kinetic parameters for MCP server input.

    Args:
        mineral: Mineral name
        surface_area: Surface area (units depend on mineral)
        field_factor: Field rate adjustment factor

    Returns:
        Dictionary ready for MCP input
    """
    info = get_mineral_info(mineral)
    if not info:
        raise ValueError(f"Mineral {mineral} not found in phreeqc_rates.dat")

    # Build parms array based on mineral
    if mineral == "Calcite":
        parms = [surface_area * 1.67e5, 0.6]  # Convert to cm²/mol
    elif mineral == "Quartz":
        parms = [surface_area, field_factor]
    elif mineral in ["K-feldspar", "Albite"]:
        parms = [surface_area, field_factor * 0.1]  # Field adjustment
    elif mineral == "Pyrite":
        parms = [0.3, 0.67, 0.5, -0.11]  # Standard oxidation params
    else:
        # Default parameters
        parms = info.get("example_parms", [surface_area, 0.67])

    return {"m0": 0.0, "parms": parms, "tol": 1e-8}  # Starting with no solid


# Convenience function to check if a mineral has kinetic rates
def has_kinetic_rates(mineral: str) -> bool:
    """Check if a mineral has kinetic rate equations in phreeqc_rates.dat."""
    return get_mineral_info(mineral) is not None
