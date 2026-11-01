"""
Constants for water chemistry calculations.
"""

import os

# Default database paths for PhreeqPython
DEFAULT_DATABASE_NAMES = ["phreeqc.dat", "wateq4f.dat", "minteq.v4.dat", "pitzer.dat", "sit.dat", "llnl.dat"]

# Common default mineral list for precipitation calculations
# These minerals exist with the SAME NAME in ALL PHREEQC databases
# NOTE: "Brucite" is NOT included because it's called "Mg(OH)2" in phreeqc.dat
# Use database-specific mineral selection for Mg hydroxide
UNIVERSAL_MINERALS = [
    "Calcite",  # Calcium carbonate - ESSENTIAL for lime softening
    "Aragonite",  # Calcium carbonate polymorph
    "Gypsum",  # Calcium sulfate dihydrate
    "Anhydrite",  # Calcium sulfate
]

# Mg hydroxide has different names in different databases
# Use this mapping when needed
# NOTE: phreeqc.dat does NOT have any Mg hydroxide mineral phase defined!
# It only has Mg(OH)2 as a SOLUTION_SPECIES (neutral aqueous species), not as a PHASE.
# So we set it to None to indicate no Mg hydroxide precipitation is possible.
MG_HYDROXIDE_NAMES = {
    "minteq.v4.dat": "Brucite",
    "minteq.dat": "Brucite",
    "wateq4f.dat": "Brucite",
    "phreeqc.dat": None,  # No Mg hydroxide phase in phreeqc.dat!
    "llnl.dat": "Brucite",
}


# Specialized mineral sets for water treatment processes
SOFTENING_MINERALS = ["Calcite", "Aragonite", "Brucite", "Portlandite", "Lime"]  # Ca(OH)2  # CaO

PHOSPHATE_MINERALS = [
    "Hydroxyapatite",  # Ca5(PO4)3OH
    "Fluorapatite",  # Ca5(PO4)3F
    "Vivianite",  # Fe3(PO4)2·8H2O
    "Strengite",  # FePO4·2H2O
    "Variscite",  # AlPO4·2H2O
]

SULFIDE_MINERALS = ["Pyrite", "Sphalerite", "Galena", "Cinnabar", "Chalcocite"]  # FeS2  # ZnS  # PbS  # HgS  # Cu2S

# Minerals for metal removal/recovery
# Note: These are mapped by database in the mineral registry, so use database-aware
# selection functions to ensure compatibility
METAL_REMOVAL_MINERALS = [
    "Goethite",
    "Hematite",
    "Fe(OH)3(a)",  # For phreeqc.dat
    "Ferrihydrite",  # For wateq4f.dat, llnl.dat
    "Al(OH)3(a)",  # For phreeqc.dat
    "Gibbsite",  # For wateq4f.dat, llnl.dat
    "Boehmite",
    "Zn(OH)2(a)",
    "Cu(OH)2",
    "Tenorite",
]

# Database-specific metal removal minerals (prefer these when database is known)
METAL_REMOVAL_MINERALS_BY_DB = {
    "phreeqc.dat": ["Fe(OH)3(a)", "Goethite", "Hematite", "Al(OH)3(a)", "Boehmite", "Zn(OH)2(a)", "Cu(OH)2"],
    "wateq4f.dat": [
        "Ferrihydrite",
        "Goethite",
        "Hematite",
        "Gibbsite",
        "Boehmite",
        "Zn(OH)2(am)",
        "Cu(OH)2",
        "Tenorite",
    ],
    "llnl.dat": ["Ferrihydrite", "Goethite", "Hematite", "Gibbsite", "Boehmite", "Zn(OH)2(am)", "Cu(OH)2", "Tenorite"],
}

# Conversion factors
MOLAR_MASS = {
    "CaCO3": 100.0869,  # g/mol
    "NaOH": 39.9971,  # g/mol
    "HCl": 36.4609,  # g/mol
    "H2SO4": 98.079,  # g/mol
    "NaHCO3": 84.0066,  # g/mol
    # Add more as needed
}

# Unit conversion factors
MG_L_TO_MOL_KGW = {
    "Ca": 1 / 40.078 / 1000,
    "Mg": 1 / 24.305 / 1000,
    "Na": 1 / 22.990 / 1000,
    "K": 1 / 39.098 / 1000,
    "Cl": 1 / 35.453 / 1000,
    "SO4": 1 / 96.06 / 1000,
    "HCO3": 1 / 61.017 / 1000,
    # Add more as needed
}

# Alkalinity conversion
ALKALINITY_MOL_TO_MG_CACO3 = 50044  # 2 × molar mass of CaCO3 / 2 × 1000


def automatic_mineral_candidates(water_analysis, database_name=None):
    """The house's automatic mineral list for a water when the caller names none (the rules as before): the universal
    minerals, then the softening, phosphate, sulfide, metal-removal, iron and aluminium sets the analysis calls for."""
    candidates = list(UNIVERSAL_MINERALS)
    analysis = {str(k).upper(): v for k, v in (water_analysis or {}).items()}

    def amount(key):
        try:
            return float(analysis.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def add(names):
        candidates.extend(name for name in names if name not in candidates)

    if "CA" in analysis and amount("CA") > 50:
        add(SOFTENING_MINERALS)
    if any(p in analysis for p in ["P", "PO4", "PHOSPHATE", "PHOSPHORUS"]):
        add(PHOSPHATE_MINERALS)
    if any(metal in analysis for metal in ["FE", "ZN", "PB", "HG", "CU", "CD", "NI", "AG"]):
        add(SULFIDE_MINERALS)
        add(METAL_REMOVAL_MINERALS_BY_DB.get(database_name, ["Goethite", "Hematite"]))
    if "FE" in analysis and amount("FE") > 1:
        if database_name == "phreeqc.dat":
            add(["Fe(OH)3(a)", "Goethite", "Hematite"])
        elif database_name in ["wateq4f.dat", "llnl.dat", "minteq.dat"]:
            add(["Ferrihydrite", "Goethite", "Hematite"])
        else:
            add(["Goethite", "Hematite"])
    if "AL" in analysis and amount("AL") > 1:
        if database_name == "phreeqc.dat":
            add(["Al(OH)3(a)", "Boehmite"])
        elif database_name in ["wateq4f.dat", "llnl.dat", "minteq.dat"]:
            add(["Gibbsite", "Boehmite"])
        else:
            add(["Boehmite"])
    return candidates



AUTOMATIC_MINERALS_RULING_ID = 'PUBLIC-RULE-1ddf4c4cc59b'


def automatic_mineral_list(water_analysis, database_path=None):
    'The automatic mineral list for a water, as phases the database defines, and the candidates it left out.'
    from water_chemistry_mcp.waterchem.phase_choice import MissingPhaseError, resolve_phases

    if not database_path:
        return [], []
    candidates = automatic_mineral_candidates(water_analysis, os.path.basename(database_path))
    chosen = []
    left_out = []
    for name in candidates:
        try:
            for phase in resolve_phases(database_path, [name], purpose="automatic mineral list"):
                if phase not in chosen:
                    chosen.append(phase)
        except MissingPhaseError:
            left_out.append(name)
    return chosen, left_out


def select_minerals_for_water_chemistry(water_analysis, database_path=None):
    """The automatic mineral list's chosen phases (`automatic_mineral_list`); a caller that answers with the list puts
    the left-out names on its answer through `automatic_mineral_list` itself."""
    return automatic_mineral_list(water_analysis, database_path)[0]


def database_validator_available():
    """Check if database_validator module is available for precise mineral validation."""
    try:
        from . import database_validator

        return True
    except ImportError:
        return False


def mineral_exists_in_database(database_path, mineral_name):
    """
    Check if a mineral exists in the database using database_validator.

    Args:
        database_path: Path to the database file
        mineral_name: Name of the mineral to check

    Returns:
        True if the mineral exists, False otherwise
    """
    try:
        from . import database_validator

        result = database_validator.scan_database_for_mineral(database_path, mineral_name)
        return result.get("found", False)
    except Exception:
        return False


def get_database_minerals(database_name):
    """
    Get all minerals for a database from the mineral registry.

    Args:
        database_name: Name of the database file

    Returns:
        Set of mineral names
    """
    try:
        from .mineral_registry import COMMON_MINERALS, DATABASE_SPECIFIC_MINERALS

        minerals = set(COMMON_MINERALS.keys())
        if database_name in DATABASE_SPECIFIC_MINERALS:
            minerals.update(DATABASE_SPECIFIC_MINERALS[database_name].keys())
        return minerals
    except Exception:
        return set()
