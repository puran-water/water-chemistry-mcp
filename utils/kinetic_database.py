"""
Comprehensive kinetic parameter database for common minerals in water treatment.

This module provides rate constants, activation energies, and other kinetic parameters
for precipitation and dissolution reactions of minerals commonly encountered in
industrial water treatment applications.

References:
- Plummer & Wigley (1976) - Calcite kinetics
- Morse & Arvidson (2002) - Carbonate mineral kinetics review
- Appelo & Postma (2005) - Geochemistry textbook
- Various industrial water treatment literature
"""

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Kinetic parameters database
# All rate constants are at 25°C in mol/m²/s
# Activation energies in J/mol
KINETIC_DATABASE = {
    # ============== CARBONATE MINERALS ==============
    "Calcite": {
        "rate_constant": 1.55e-6,  # Plummer et al. (1978)
        "activation_energy": 41800,  # 41.8 kJ/mol
        "surface_area": 1.0,  # m²/L - typical for water treatment
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.5,  # Typical for seeded systems
        "notes": "Most common carbonate scale, fast kinetics",
        "references": ["Plummer et al. (1978)", "Morse & Arvidson (2002)"],
    },
    "Aragonite": {
        "rate_constant": 1.9e-6,  # Slightly faster than calcite
        "activation_energy": 46000,
        "surface_area": 1.0,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.7,  # Higher than calcite
        "notes": "Metastable CaCO3 polymorph, forms at higher temps/Mg",
        "references": ["Morse et al. (2007)"],
    },
    "Dolomite": {
        "rate_constant": 1e-9,  # Very slow at 25°C
        "activation_energy": 52300,
        "surface_area": 0.1,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 2.0,  # High supersaturation needed
        "notes": "Extremely slow precipitation kinetics at ambient temp",
        "references": ["Arvidson & Mackenzie (1999)"],
    },
    "Magnesite": {
        "rate_constant": 8e-10,  # Very slow
        "activation_energy": 62800,
        "surface_area": 0.1,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 3.0,  # Very high SI needed
        "notes": "MgCO3 - extremely slow at low temps",
        "references": ["Saldi et al. (2009)"],
    },
    "Siderite": {
        "rate_constant": 3.2e-8,  # FeCO3
        "activation_energy": 48000,
        "surface_area": 0.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 1.0,
        "notes": "Iron carbonate, forms in reducing conditions",
        "references": ["Singer & Stumm (1970)"],
    },
    # ============== SULFATE MINERALS ==============
    "Gypsum": {
        "rate_constant": 2.5e-8,  # CaSO4·2H2O
        "activation_energy": 28000,  # Lower activation energy
        "surface_area": 0.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.2,  # Low threshold
        "notes": "Common sulfate scale, moderate kinetics",
        "references": ["Liu & Nancollas (1970)", "Christoffersen et al. (1982)"],
    },
    "Anhydrite": {
        "rate_constant": 1e-8,  # CaSO4
        "activation_energy": 35000,
        "surface_area": 0.3,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.5,
        "notes": "Forms at higher temps than gypsum",
        "references": ["Kontrec et al. (2002)"],
    },
    "Barite": {
        "rate_constant": 5e-9,  # BaSO4 - slower than gypsum
        "activation_energy": 40000,
        "surface_area": 0.2,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 1.0,
        "notes": "Very low solubility, persistent scale",
        "references": ["Risthaus et al. (2001)"],
    },
    "Celestite": {
        "rate_constant": 8e-9,  # SrSO4
        "activation_energy": 38000,
        "surface_area": 0.3,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.8,
        "notes": "Strontium sulfate scale",
        "references": ["Reardon & Armstrong (1987)"],
    },
    # ============== HYDROXIDE MINERALS ==============
    "Brucite": {
        "rate_constant": 1e-7,  # Mg(OH)2
        "activation_energy": 42000,
        "surface_area": 2.0,  # High surface area
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 1.5,  # Needs high pH
        "notes": "Critical for high-pH Mg removal in ZLD",
        "references": ["Pokrovsky & Schott (2004)"],
    },
    "Fe(OH)3(a)": {
        "rate_constant": 1e-6,  # Amorphous ferric hydroxide - fast
        "activation_energy": 35000,
        "surface_area": 5.0,  # Very high surface area
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.0,  # Precipitates readily
        "notes": "Rapid precipitation, high surface area",
        "references": ["Cornell & Schwertmann (2003)"],
    },
    "Al(OH)3(a)": {
        "rate_constant": 5e-7,  # Amorphous aluminum hydroxide
        "activation_energy": 40000,
        "surface_area": 4.0,
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.5,
        "notes": "Forms rapidly in coagulation",
        "references": ["Hem & Roberson (1967)"],
    },
    "Gibbsite": {
        "rate_constant": 1e-8,  # Crystalline Al(OH)3 - slower
        "activation_energy": 60000,
        "surface_area": 0.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 2.0,
        "notes": "Crystalline form, slower than amorphous",
        "references": ["Nagy (1995)"],
    },
    # ============== PHOSPHATE MINERALS ==============
    "Hydroxyapatite": {
        "rate_constant": 2e-8,  # Ca5(PO4)3OH
        "activation_energy": 55000,
        "surface_area": 1.0,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 5.0,  # Very high SI needed
        "notes": "Slow formation, important for P removal",
        "references": ["Nancollas & Mohan (1970)"],
    },
    "Strengite": {
        "rate_constant": 3e-7,  # FePO4·2H2O
        "activation_energy": 45000,
        "surface_area": 2.0,
        "surface_area_exponent": 0.7,
        "nucleation_si_threshold": 1.0,
        "notes": "Important for chemical P removal with Fe",
        "references": ["Mao & Yue (2016)"],
    },
    "Vivianite": {
        "rate_constant": 5e-8,  # Fe3(PO4)2·8H2O
        "activation_energy": 50000,
        "surface_area": 1.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 2.0,
        "notes": "Fe(II) phosphate, forms in anaerobic conditions",
        "references": ["Rothe et al. (2016)"],
    },
    "Struvite": {
        "rate_constant": 1e-6,  # MgNH4PO4·6H2O - relatively fast
        "activation_energy": 40000,
        "surface_area": 2.0,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.5,
        "notes": "Important for nutrient recovery, fast kinetics",
        "references": ["Ohlinger et al. (1998)", "Bhuiyan et al. (2008)"],
    },
    # ============== SILICATE MINERALS ==============
    "SiO2(a)": {
        "rate_constant": 1e-9,  # Amorphous silica
        "activation_energy": 60000,
        "surface_area": 3.0,
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.2,
        "notes": "Polymerizes rather than precipitates",
        "references": ["Iler (1979)", "Alexander et al. (1954)"],
    },
    "Quartz": {
        "rate_constant": 1e-13,  # Extremely slow
        "activation_energy": 87000,
        "surface_area": 0.1,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 1.0,
        "notes": "Essentially no precipitation at ambient temp",
        "references": ["Rimstidt & Barnes (1980)"],
    },
    "Sepiolite": {
        "rate_constant": 5e-10,  # Mg4Si6O15(OH)2·6H2O
        "activation_energy": 65000,
        "surface_area": 0.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 3.0,
        "notes": "Mg silicate, very slow formation",
        "references": ["Stoessell (1988)"],
    },
    # ============== FLUORIDE MINERALS ==============
    "Fluorite": {
        "rate_constant": 2e-8,  # CaF2
        "activation_energy": 35000,
        "surface_area": 0.5,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 0.5,
        "notes": "Important for fluoride removal",
        "references": ["Godinho et al. (2012)"],
    },
    # ============== SULFIDE MINERALS ==============
    "FeS(am)": {
        "rate_constant": 1e-6,  # Amorphous iron sulfide - very fast
        "activation_energy": 25000,  # Low activation energy
        "surface_area": 5.0,
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.0,
        "notes": "Rapid precipitation in reducing conditions",
        "references": ["Rickard (1995)"],
    },
    "Pyrite": {
        "rate_constant": 1e-10,  # FeS2 - much slower than FeS(am)
        "activation_energy": 65000,
        "surface_area": 0.2,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 5.0,
        "notes": "Forms slowly from FeS precursor",
        "references": ["Schoonen & Barnes (1991)"],
    },
    "ZnS(am)": {
        "rate_constant": 5e-7,  # Amorphous zinc sulfide
        "activation_energy": 30000,
        "surface_area": 4.0,
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.0,
        "notes": "Fast precipitation for metal removal",
        "references": ["Luther et al. (1996)"],
    },
    "CdS": {
        "rate_constant": 3e-7,  # Cadmium sulfide
        "activation_energy": 32000,
        "surface_area": 3.0,
        "surface_area_exponent": 0.8,
        "nucleation_si_threshold": 0.0,
        "notes": "Rapid precipitation",
        "references": ["Parkman et al. (1998)"],
    },
}


def get_kinetic_parameters(mineral: str) -> Optional[Dict[str, Any]]:
    """
    Get kinetic parameters for a specific mineral.

    Args:
        mineral: Mineral name

    Returns:
        Dictionary of kinetic parameters or None if not found
    """
    # Try exact match first
    if mineral in KINETIC_DATABASE:
        return KINETIC_DATABASE[mineral].copy()

    # Try case-insensitive match
    mineral_lower = mineral.lower()
    for db_mineral, params in KINETIC_DATABASE.items():
        if db_mineral.lower() == mineral_lower:
            return params.copy()

    # Try partial matches for common variations
    variations = {
        "Mg(OH)2": "Brucite",
        "Ca(OH)2": None,  # Not in database, but could add
        "CaCO3": "Calcite",
        "CaSO4:2H2O": "Gypsum",
        "CaSO4": "Anhydrite",
        "BaSO4": "Barite",
        "SrSO4": "Celestite",
        "CaF2": "Fluorite",
        "FePO4:2H2O": "Strengite",
        "MgNH4PO4:6H2O": "Struvite",
    }

    if mineral in variations and variations[mineral]:
        return KINETIC_DATABASE[variations[mineral]].copy()

    logger.warning(f"No kinetic parameters found for mineral: {mineral}")
    return None


def get_default_kinetic_parameters() -> Dict[str, Any]:
    """
    Get default kinetic parameters for unknown minerals.

    Returns:
        Dictionary with conservative default parameters
    """
    return {
        "rate_constant": 1e-8,  # Conservative default
        "activation_energy": 50000,  # 50 kJ/mol
        "surface_area": 1.0,
        "surface_area_exponent": 0.67,
        "nucleation_si_threshold": 1.0,
        "notes": "Default parameters - use with caution",
    }


def get_minerals_by_category(category: str) -> Dict[str, Dict[str, Any]]:
    """
    Get all minerals in a specific category.

    Args:
        category: Category name (carbonate, sulfate, hydroxide, phosphate, silicate, fluoride, sulfide)

    Returns:
        Dictionary of minerals and their parameters
    """
    category_keywords = {
        "carbonate": ["Calcite", "Aragonite", "Dolomite", "Magnesite", "Siderite"],
        "sulfate": ["Gypsum", "Anhydrite", "Barite", "Celestite"],
        "hydroxide": ["Brucite", "Fe(OH)3(a)", "Al(OH)3(a)", "Gibbsite"],
        "phosphate": ["Hydroxyapatite", "Strengite", "Vivianite", "Struvite"],
        "silicate": ["SiO2(a)", "Quartz", "Sepiolite"],
        "fluoride": ["Fluorite"],
        "sulfide": ["FeS(am)", "Pyrite", "ZnS(am)", "CdS"],
    }

    category_lower = category.lower()
    if category_lower not in category_keywords:
        return {}

    result = {}
    for mineral in category_keywords[category_lower]:
        if mineral in KINETIC_DATABASE:
            result[mineral] = KINETIC_DATABASE[mineral]

    return result


def estimate_induction_time(si: float, rate_constant: float, temperature_c: float = 25.0) -> float:
    """
    Estimate induction time before precipitation begins.

    Based on classical nucleation theory:
    t_ind = A * exp(B / (ln(S))²)

    Args:
        si: Saturation index
        rate_constant: Rate constant at reference temperature
        temperature_c: Temperature in Celsius

    Returns:
        Estimated induction time in seconds
    """
    import numpy as np

    if si <= 0:
        return float("inf")  # No precipitation

    S = 10**si  # Saturation ratio

    # Empirical parameters (can be adjusted based on system)
    # Faster kinetics = shorter induction time
    A = 1e-10 / rate_constant  # Scale with rate constant
    B = 16.0  # Related to interfacial energy

    # Temperature correction
    T = temperature_c + 273.15
    T_ref = 298.15
    temp_factor = T_ref / T  # Higher temp = shorter induction

    t_ind = A * temp_factor * np.exp(B / (np.log(S)) ** 2)

    return t_ind


# Utility function to format kinetic data for reports
def format_kinetic_data_for_report(mineral: str) -> Dict[str, Any]:
    """
    Format kinetic data for inclusion in reports.

    Args:
        mineral: Mineral name

    Returns:
        Formatted dictionary suitable for reports
    """
    params = get_kinetic_parameters(mineral)
    if not params:
        params = get_default_kinetic_parameters()
        params["mineral"] = mineral
        params["data_quality"] = "default"
    else:
        params["mineral"] = mineral
        params["data_quality"] = "literature"

    # Add human-readable values
    params["rate_constant_log"] = f"{math.log10(params['rate_constant']):.1f}"
    params["activation_energy_kJ_mol"] = f"{params['activation_energy']/1000:.1f}"

    # Estimate precipitation timescales at different SI values
    timescales = {}
    for si in [0.5, 1.0, 2.0]:
        t_ind = estimate_induction_time(si, params["rate_constant"])
        if t_ind < 60:
            timescales[f"SI_{si}"] = f"{t_ind:.1f} seconds"
        elif t_ind < 3600:
            timescales[f"SI_{si}"] = f"{t_ind/60:.1f} minutes"
        elif t_ind < 86400:
            timescales[f"SI_{si}"] = f"{t_ind/3600:.1f} hours"
        else:
            timescales[f"SI_{si}"] = f"{t_ind/86400:.1f} days"

    params["typical_timescales"] = timescales

    return params
