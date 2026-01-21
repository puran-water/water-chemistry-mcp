"""
Inline PHREEQC phase and surface definitions for wastewater phosphorus modeling.

This module provides PHREEQC PHASES and SURFACE blocks for minerals and surfaces
not available in standard PHREEQC databases. These blocks can be prepended to
PHREEQC input strings to enable modeling of:

- Struvite (MgNH4PO4·6H2O) - Mg-based P recovery
- Variscite (AlPO4·2H2O) - Al-P precipitation
- HAO surface complexation - Al-P adsorption on hydrous aluminum oxide
- Brushite (CaHPO4·2H2O) - Ca-P precipitation (kinetically favored)

IMPORTANT: Data Source Notes
============================
USGS PHREEQC databases (phreeqc.dat, minteq.v4.dat, wateq4f.dat) explicitly state:
"the minerals glauconite and Struvite are not present in the current database
due to lack of thermodynamic data" (see usgs-coupled/phreeqc3 Kinec_v3.dat).

Thermodynamic constants are from peer-reviewed literature consensus:

1. Struvite (MgNH4PO4·6H2O):
   - pKsp = 13.26 at 25°C (Ohlinger et al. 1998, J. Environ. Eng. 124:25)
   - This is the most widely cited value in wastewater literature
   - Literature range: 9.41 to 13.36; recent experimental work found 13.36 ±0.07
   - delta_H = 27 kJ/mol (6.5 kcal) for temperature correction (Van't Hoff analysis)
   - Reference: https://pubmed.ncbi.nlm.nih.gov/17910254/
   - Cross-validation available in reference_gitignore/Struvite Precipitation Potential/struvite_07_rev20230309.xlam

2. Variscite (AlPO4·2H2O):
   - log_k sourced from thermoddem.brgm.fr (as referenced by USGS Kinec databases)
   - Original data: Lindsay (1979) "Chemical Equilibria in Soils"
   - Reference: https://thermoddem.brgm.fr/databases/phreeqc

3. HAO surface complexation:
   - Derived from Goldberg & Sposito (1984), Dzombak & Morel (1990)
   - Analogous to HFO surface in minteq.v4.dat

ALTERNATIVE: For production use, consider downloading the Thermoddem PHREEQC database
from https://thermoddem.brgm.fr/databases/phreeqc which may contain these phases
with full temperature corrections.

Usage:
    from utils.inline_phases import get_struvite_phases_block, get_hao_surface_block

    # Prepend to PHREEQC input
    phreeqc_input = get_struvite_phases_block() + existing_input
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# STRUVITE PHASE DEFINITION
# =============================================================================

STRUVITE_PHASES_BLOCK = """
# ==== STRUVITE PHASE (Inline Definition) ====
# Struvite: MgNH4PO4·6H2O - Magnesium ammonium phosphate hexahydrate
# Used for phosphorus recovery from wastewater, requires Mg and NH4
#
# DATA SOURCE: NOT in USGS PHREEQC databases (explicitly excluded)
# Thermodynamic data from peer-reviewed literature consensus:
#   pKsp = 13.26 at 25°C (Ohlinger et al. 1998, J. Environ. Eng. 124:25)
#   Literature range: 9.41 to 13.36; recent work found 13.36 ±0.07
#   Reference: https://pubmed.ncbi.nlm.nih.gov/17910254/
#
# Temperature correction: delta_H ~ 27 kJ/mol (6.5 kcal) from Van't Hoff
#
# IMPORTANT: For struvite to precipitate, the solution must have:
#   - Mg > 0 (add via MgCl2 or existing Mg in wastewater)
#   - N(-3) > 0 (ammonia/ammonium from digester liquor)
#   - P > 0 (phosphate)
#   - pH typically 8.5-9.5 for optimal precipitation

PHASES
Struvite
    MgNH4PO4:6H2O = Mg+2 + NH4+ + PO4-3 + 6H2O
    log_k     -13.26
    -delta_h  6.5 kcal
    # Temperature correction: Ksp increases ~3x per 25C decrease
    # Van't Hoff: d(ln K)/d(1/T) = -delta_H/R
"""


def get_struvite_phases_block() -> str:
    """
    Get the PHREEQC PHASES block for struvite.

    Returns:
        PHREEQC PHASES block string defining struvite
    """
    return STRUVITE_PHASES_BLOCK


# =============================================================================
# VARISCITE (AlPO4) PHASE DEFINITION
# =============================================================================

VARISCITE_PHASES_BLOCK = """
# ==== VARISCITE PHASE (Inline Definition) ====
# Variscite: AlPO4·2H2O - Aluminum phosphate dihydrate
# Primary Al-P precipitate phase (not in standard PHREEQC databases)
#
# DATA SOURCE: thermoddem.brgm.fr (as referenced by USGS Kinec databases)
# Thermodynamic data:
#   log_k = -22.1 at 25°C
#   Original source: Lindsay (1979) "Chemical Equilibria in Soils"
#   Reference: https://thermoddem.brgm.fr/databases/phreeqc
#
# NOTE: Related mineral AlPO4·1.5H2O has Ksp = 10^-20.46 (more soluble)
# Variscite is thermodynamically stable Al-P phase at pH < 5.5
# At higher pH, Al(OH)3 is more stable and P removal is via adsorption

PHASES
Variscite
    AlPO4:2H2O = Al+3 + PO4-3 + 2H2O
    log_k     -22.1
    -delta_h  0.0 kcal
    # delta_h not well characterized in literature; assume isothermal
"""


def get_variscite_phases_block() -> str:
    """
    Get the PHREEQC PHASES block for variscite (AlPO4·2H2O).

    Returns:
        PHREEQC PHASES block string defining variscite
    """
    return VARISCITE_PHASES_BLOCK


# =============================================================================
# BRUSHITE (CaHPO4·2H2O) PHASE DEFINITION
# =============================================================================

BRUSHITE_PHASES_BLOCK = """
# ==== BRUSHITE PHASE (Inline Definition) ====
# Brushite: CaHPO4·2H2O - Calcium hydrogen phosphate dihydrate
# Kinetically favored Ca-P phase at neutral to acidic pH
#
# NOTE: Brushite is typically already in minteq.v4.dat as "CaHPO4:2H2O"
# This block is provided for databases that lack it

PHASES
Brushite
    CaHPO4:2H2O = Ca+2 + HPO4-2 + 2H2O
    log_k     -6.59
    -delta_h  0.0 kcal
"""


def get_brushite_phases_block() -> str:
    """
    Get the PHREEQC PHASES block for brushite (CaHPO4·2H2O).

    Returns:
        PHREEQC PHASES block string defining brushite
    """
    return BRUSHITE_PHASES_BLOCK


# =============================================================================
# HAO SURFACE COMPLEXATION (Hydrous Aluminum Oxide)
# =============================================================================

HAO_SURFACE_BLOCK = """
# ==== HAO SURFACE COMPLEXATION (Inline Definition) ====
# Hydrous Aluminum Oxide surface sites for phosphate adsorption
# Analogous to HFO (Hydrous Ferric Oxide) but for aluminum coagulants
#
# Site densities and surface reactions derived from:
#   - Goldberg & Sposito (1984) - Al oxide surface chemistry
#   - Dzombak & Morel (1990) - Surface Complexation Modeling
#   - Karamalidis & Dzombak (2010) - Metal-phosphate complexation
#
# Strong sites (Hao_s): Low capacity, high affinity
# Weak sites (Hao_w): High capacity, lower affinity
#
# Typical site densities (mol sites / mol Al(OH)3):
#   Strong: 0.005 mol/mol
#   Weak: 0.2 mol/mol (40:1 weak:strong ratio, same as HFO)

SURFACE_MASTER_SPECIES
    Hao_s   Hao_sOH    -2.0    # Strong sites on HAO
    Hao_w   Hao_wOH    -2.0    # Weak sites on HAO

SURFACE_SPECIES
    # === Acid-base equilibria for HAO ===
    # pKa1 and pKa2 for Al oxide surfaces

    Hao_sOH = Hao_sOH
        log_k     0.0

    Hao_sOH + H+ = Hao_sOH2+
        log_k     7.29

    Hao_sOH = Hao_sO- + H+
        log_k     -8.93

    Hao_wOH = Hao_wOH
        log_k     0.0

    Hao_wOH + H+ = Hao_wOH2+
        log_k     7.29

    Hao_wOH = Hao_wO- + H+
        log_k     -8.93

    # === Phosphate adsorption on HAO ===
    # Bidentate and monodentate surface complexes
    # log_k values are approximate, derived from HFO analogs

    Hao_sOH + PO4-3 + 3H+ = Hao_sH2PO4 + H2O
        log_k     31.29

    Hao_sOH + PO4-3 + 2H+ = Hao_sHPO4- + H2O
        log_k     25.39

    Hao_sOH + PO4-3 + H+ = Hao_sPO4-2 + H2O
        log_k     17.72

    Hao_wOH + PO4-3 + 3H+ = Hao_wH2PO4 + H2O
        log_k     30.0

    Hao_wOH + PO4-3 + 2H+ = Hao_wHPO4- + H2O
        log_k     24.0
"""


def get_hao_surface_block() -> str:
    """
    Get the PHREEQC SURFACE_MASTER_SPECIES and SURFACE_SPECIES blocks for HAO.

    This defines hydrous aluminum oxide surface sites for phosphate adsorption,
    analogous to the HFO surface in minteq.v4.dat for iron coagulants.

    Returns:
        PHREEQC surface definition blocks for HAO
    """
    return HAO_SURFACE_BLOCK


# =============================================================================
# COMBINED BLOCKS FOR DIFFERENT USE CASES
# =============================================================================


def get_p_removal_inline_blocks(
    coagulant_type: str = "iron",
    include_struvite: bool = False,
    include_brushite: bool = False,
) -> str:
    """
    Get combined inline PHREEQC blocks for phosphorus removal modeling.

    Args:
        coagulant_type: "iron", "aluminum", "magnesium", or "calcium"
        include_struvite: Include struvite phase for Mg-based P recovery
        include_brushite: Include brushite phase for Ca-P precipitation

    Returns:
        Combined PHREEQC blocks to prepend to input string
    """
    blocks = []

    # Add phases based on coagulant type
    if coagulant_type == "aluminum":
        blocks.append(get_variscite_phases_block())
        blocks.append(get_hao_surface_block())
        logger.info("Added inline blocks for Al coagulant (Variscite + HAO surface)")

    elif coagulant_type == "magnesium" or include_struvite:
        blocks.append(get_struvite_phases_block())
        logger.info("Added inline block for struvite precipitation")

    elif coagulant_type == "calcium" or include_brushite:
        # Brushite is usually in minteq.v4.dat, but include for safety
        blocks.append(get_brushite_phases_block())
        logger.info("Added inline block for brushite precipitation")

    # For iron coagulant, standard database phases are sufficient
    elif coagulant_type == "iron":
        logger.debug("Iron coagulant uses standard database phases (Ferrihydrite, Strengite)")

    return "\n".join(blocks)


def build_hao_phase_linked_surface_block(
    phase_name: str = "Gibbsite",
    sites_per_mole_strong: float = 0.005,
    weak_to_strong_ratio: float = 40.0,
    specific_area_m2_per_mol: float = 53300.0,
    equilibrate_solution: int = 1,
    block_num: int = 1,
    no_edl: bool = False,
) -> str:
    """
    Build a phase-linked SURFACE block for HAO (hydrous aluminum oxide).

    Analogous to build_phase_linked_surface_block() in helpers.py but for
    aluminum coagulants. The surface sites scale with precipitated Al(OH)3.

    Args:
        phase_name: Equilibrium phase to link to (e.g., "Gibbsite", "Al(OH)3(a)")
        sites_per_mole_strong: Strong site density (mol sites / mol Al phase)
        weak_to_strong_ratio: Ratio of weak to strong sites (default 40:1)
        specific_area_m2_per_mol: Specific surface area (m²/mol phase)
        equilibrate_solution: Solution number to equilibrate with
        block_num: SURFACE block number
        no_edl: If True, disable electric double layer calculations

    Returns:
        PHREEQC SURFACE block string with phase-linked HAO sites
    """
    # Calculate weak site density
    sites_per_mole_weak = sites_per_mole_strong * weak_to_strong_ratio

    lines = [f"SURFACE {block_num}"]
    lines.append(f"    -equilibrate {equilibrate_solution}")

    # Strong sites - phase linked
    lines.append(
        f"    Hao_sOH  {phase_name}  equilibrium_phase  {sites_per_mole_strong}  {specific_area_m2_per_mol}"
    )

    # Weak sites - phase linked
    lines.append(
        f"    Hao_wOH  {phase_name}  equilibrium_phase  {sites_per_mole_weak}  {specific_area_m2_per_mol}"
    )

    if no_edl:
        lines.append("    -no_edl")

    return "\n".join(lines) + "\n"


# =============================================================================
# PHASE AVAILABILITY CHECK
# =============================================================================


def check_phases_in_database(
    database_path: str,
    phases: List[str],
) -> Dict[str, bool]:
    """
    Check which phases are available in a PHREEQC database.

    Args:
        database_path: Path to PHREEQC database file
        phases: List of phase names to check

    Returns:
        Dictionary mapping phase name to availability (True/False)
    """
    availability = {phase: False for phase in phases}

    try:
        with open(database_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Look for each phase name in the PHASES section
        for phase in phases:
            # Simple check: phase name appears followed by newline (start of definition)
            if f"\n{phase}\n" in content or f"\n{phase} " in content:
                availability[phase] = True

    except Exception as e:
        logger.warning(f"Could not check database {database_path}: {e}")

    return availability


def get_required_inline_blocks_for_database(
    database_path: str,
    coagulant_type: str = "iron",
    include_struvite: bool = False,
) -> str:
    """
    Determine which inline blocks are needed based on database content.

    Checks the database for required phases and returns only the inline
    blocks that are missing.

    Args:
        database_path: Path to PHREEQC database
        coagulant_type: "iron", "aluminum", "magnesium", or "calcium"
        include_struvite: Whether struvite modeling is required

    Returns:
        Combined inline blocks for missing phases/surfaces
    """
    blocks = []

    # Phases to check based on coagulant type
    phases_to_check = []

    if coagulant_type == "aluminum":
        phases_to_check = ["Variscite", "AlPO4:2H2O"]

    if include_struvite or coagulant_type == "magnesium":
        phases_to_check.extend(["Struvite", "MgNH4PO4:6H2O"])

    # Check database
    availability = check_phases_in_database(database_path, phases_to_check)

    # Add missing phases
    if coagulant_type == "aluminum":
        if not availability.get("Variscite") and not availability.get("AlPO4:2H2O"):
            blocks.append(get_variscite_phases_block())
            logger.info("Adding inline Variscite phase (not in database)")

        # HAO surface is never in standard databases
        blocks.append(get_hao_surface_block())
        logger.info("Adding inline HAO surface block for Al coagulant")

    if include_struvite or coagulant_type == "magnesium":
        if not availability.get("Struvite") and not availability.get("MgNH4PO4:6H2O"):
            blocks.append(get_struvite_phases_block())
            logger.info("Adding inline Struvite phase (not in database)")

    return "\n".join(blocks)
