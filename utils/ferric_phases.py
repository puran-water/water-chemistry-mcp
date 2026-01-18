"""
Coagulant precipitation phase sets for phosphorus removal.

This module defines the equilibrium phases used for Fe-P and Al-P precipitation
modeling under aerobic and anaerobic conditions. Phase names are database-specific.

Database Phase Availability (verified via Codex review 2026-01-16):

IRON PHASES:
- minteq.v4.dat: Ferrihydrite, Strengite, Vivianite, FeS(ppt), Mackinawite, Siderite, Calcite
- phreeqc.dat: Fe(OH)3(a), Mackinawite (NO Strengite)
- wateq4f.dat: Fe(OH)3(a), Mackinawite, FeS(ppt)

ALUMINUM PHASES:
- minteq.v4.dat: Gibbsite, Al(OH)3(am) (NO AlPO4 / Variscite)
- phreeqc.dat: Al(OH)3(a), Gibbsite
- wateq4f.dat: Al(OH)3(a), Gibbsite

NOTE: Al-phosphate phases (AlPO4, Variscite) are NOT available in standard databases.
Al-P removal relies on adsorption to HAO (hydrous aluminum oxide) and co-precipitation.

Key precipitates:
- Fe(OH)3 / Ferrihydrite: Amorphous ferric hydroxide (HFO), adsorption substrate
- Strengite: FePO4·2H2O, direct Fe-P precipitation at low pH
- Vivianite: Fe3(PO4)2·8H2O, Fe(II)-phosphate under anaerobic conditions
- FeS(ppt) / Mackinawite: Iron sulfide under sulfidic conditions
- Siderite: FeCO3, iron carbonate under high alkalinity
- Al(OH)3(a) / Gibbsite: Amorphous aluminum hydroxide (HAO), adsorption substrate
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Database-specific phase name mappings
# Key: canonical name, Value: {database: actual_phase_name or None}
PHASE_NAME_BY_DATABASE: Dict[str, Dict[str, Optional[str]]] = {
    # Iron phases
    "ferric_hydroxide": {
        "minteq.v4.dat": "Ferrihydrite",
        "phreeqc.dat": "Fe(OH)3(a)",
        "wateq4f.dat": "Fe(OH)3(a)",
        "default": "Ferrihydrite",
    },
    "ferric_phosphate": {
        "minteq.v4.dat": "Strengite",
        "phreeqc.dat": None,  # Not available in phreeqc.dat
        "wateq4f.dat": None,  # Not available
        "default": "Strengite",
    },
    "ferrous_phosphate": {
        "minteq.v4.dat": "Vivianite",
        "phreeqc.dat": "Vivianite",
        "wateq4f.dat": "Vivianite",
        "default": "Vivianite",
    },
    "iron_sulfide": {
        "minteq.v4.dat": "FeS(ppt)",
        "phreeqc.dat": "Mackinawite",
        "wateq4f.dat": "FeS(ppt)",
        "default": "Mackinawite",
    },
    "iron_carbonate": {
        "minteq.v4.dat": "Siderite",
        "phreeqc.dat": "Siderite",
        "wateq4f.dat": "Siderite",
        "default": "Siderite",
    },
    # Aluminum phases
    "aluminum_hydroxide": {
        "minteq.v4.dat": "Gibbsite",
        "phreeqc.dat": "Al(OH)3(a)",
        "wateq4f.dat": "Gibbsite",
        "default": "Gibbsite",
    },
    "aluminum_phosphate": {
        # AlPO4 / Variscite NOT available in standard databases
        # Al-P removal relies on adsorption to HAO (hydrous aluminum oxide)
        "minteq.v4.dat": None,
        "phreeqc.dat": None,
        "wateq4f.dat": None,
        "default": None,
    },
    # Common phases
    "calcium_carbonate": {
        "minteq.v4.dat": "Calcite",
        "phreeqc.dat": "Calcite",
        "wateq4f.dat": "Calcite",
        "default": "Calcite",
    },
}


def get_phase_name(canonical_name: str, database: str = "minteq.v4.dat") -> Optional[str]:
    """
    Get the database-specific phase name for a canonical phase.

    Args:
        canonical_name: Canonical phase name (e.g., "ferric_hydroxide")
        database: Database filename (e.g., "minteq.v4.dat")

    Returns:
        Database-specific phase name, or None if not available
    """
    if canonical_name not in PHASE_NAME_BY_DATABASE:
        logger.warning(f"Unknown canonical phase name: {canonical_name}")
        return None

    mapping = PHASE_NAME_BY_DATABASE[canonical_name]

    # Extract just the filename if a full path was provided
    db_name = database.split("/")[-1].split("\\")[-1]

    return mapping.get(db_name, mapping.get("default"))


def get_aerobic_phases(
    database: str = "minteq.v4.dat",
    include_hfo: bool = True,
    include_strengite: bool = True,
    include_calcite: bool = True,
) -> List[Dict[str, any]]:
    """
    Get equilibrium phases for aerobic Fe-P precipitation.

    Aerobic conditions favor Fe(III) phases:
    - Ferric hydroxide (HFO): Primary adsorption substrate
    - Strengite: Direct FePO4·2H2O precipitation at lower pH

    Args:
        database: Database filename
        include_hfo: Include ferric hydroxide (HFO) phase
        include_strengite: Include Strengite (FePO4·2H2O) phase
        include_calcite: Include Calcite for pH buffering

    Returns:
        List of phase dicts for build_equilibrium_phases_block()
    """
    phases = []

    if include_hfo:
        hfo_name = get_phase_name("ferric_hydroxide", database)
        if hfo_name:
            phases.append({
                "name": hfo_name,
                "target_si": 0.0,
                "initial_moles": 0.0,  # Precipitation only
            })

    if include_strengite:
        strengite_name = get_phase_name("ferric_phosphate", database)
        if strengite_name:
            phases.append({
                "name": strengite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })
        else:
            logger.warning(
                f"Strengite not available in {database}. "
                "Fe-P removal will rely on adsorption to HFO only."
            )

    if include_calcite:
        calcite_name = get_phase_name("calcium_carbonate", database)
        if calcite_name:
            phases.append({
                "name": calcite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    return phases


def get_anaerobic_phases(
    database: str = "minteq.v4.dat",
    include_vivianite: bool = True,
    include_iron_sulfide: bool = True,
    include_siderite: bool = True,
    include_calcite: bool = True,
    sulfide_present: bool = False,
) -> List[Dict[str, any]]:
    """
    Get equilibrium phases for anaerobic Fe-P precipitation.

    Anaerobic conditions favor Fe(II) phases:
    - Vivianite: Fe3(PO4)2·8H2O, primary Fe(II)-P precipitate
    - Iron sulfide: FeS(ppt)/Mackinawite under sulfidic conditions
    - Siderite: FeCO3 under high alkalinity

    Args:
        database: Database filename
        include_vivianite: Include Vivianite (Fe3(PO4)2·8H2O) phase
        include_iron_sulfide: Include FeS/Mackinawite phase
        include_siderite: Include Siderite (FeCO3) phase
        include_calcite: Include Calcite for pH buffering
        sulfide_present: If True, prioritize FeS precipitation

    Returns:
        List of phase dicts for build_equilibrium_phases_block()
    """
    phases = []

    if include_vivianite:
        vivianite_name = get_phase_name("ferrous_phosphate", database)
        if vivianite_name:
            phases.append({
                "name": vivianite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    if include_iron_sulfide and sulfide_present:
        fes_name = get_phase_name("iron_sulfide", database)
        if fes_name:
            phases.append({
                "name": fes_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    if include_siderite:
        siderite_name = get_phase_name("iron_carbonate", database)
        if siderite_name:
            phases.append({
                "name": siderite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    if include_calcite:
        calcite_name = get_phase_name("calcium_carbonate", database)
        if calcite_name:
            phases.append({
                "name": calcite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    return phases


def get_aluminum_phases(
    database: str = "minteq.v4.dat",
    include_hao: bool = True,
    include_calcite: bool = True,
) -> List[Dict[str, any]]:
    """
    Get equilibrium phases for Al-P precipitation.

    Aluminum P removal relies primarily on adsorption to HAO (hydrous aluminum
    oxide) because AlPO4/Variscite phases are not available in standard databases.

    Args:
        database: Database filename
        include_hao: Include aluminum hydroxide (HAO) phase
        include_calcite: Include Calcite for pH buffering

    Returns:
        List of phase dicts for build_equilibrium_phases_block()
    """
    phases = []

    if include_hao:
        hao_name = get_phase_name("aluminum_hydroxide", database)
        if hao_name:
            phases.append({
                "name": hao_name,
                "target_si": 0.0,
                "initial_moles": 0.0,  # Precipitation only
            })

    if include_calcite:
        calcite_name = get_phase_name("calcium_carbonate", database)
        if calcite_name:
            phases.append({
                "name": calcite_name,
                "target_si": 0.0,
                "initial_moles": 0.0,
            })

    return phases


def get_hfo_surface_phase(database: str = "minteq.v4.dat") -> Optional[str]:
    """
    Get the HFO phase name for surface complexation modeling.

    This is the phase that HFO surface sites should be linked to
    in phase-linked surface blocks.

    Args:
        database: Database filename

    Returns:
        Phase name for HFO (e.g., "Ferrihydrite" or "Fe(OH)3(a)")
    """
    return get_phase_name("ferric_hydroxide", database)


def get_hao_surface_phase(database: str = "minteq.v4.dat") -> Optional[str]:
    """
    Get the HAO phase name for surface complexation modeling.

    This is the phase that HAO (hydrous aluminum oxide) surface sites
    should be linked to in phase-linked surface blocks.

    Args:
        database: Database filename

    Returns:
        Phase name for HAO (e.g., "Gibbsite" or "Al(OH)3(a)")
    """
    return get_phase_name("aluminum_hydroxide", database)


def get_phases_for_redox_mode(
    redox_mode: str,
    database: str = "minteq.v4.dat",
    sulfide_mg_l: float = 0.0,
) -> Tuple[List[Dict[str, any]], str]:
    """
    Get appropriate phases based on redox mode.

    Args:
        redox_mode: One of "aerobic", "anaerobic", "pe_from_orp", "fixed_pe", "fixed_fe2_fraction"
        database: Database filename
        sulfide_mg_l: Sulfide concentration for anaerobic conditions

    Returns:
        Tuple of (phases_list, hfo_phase_name)
    """
    sulfide_present = sulfide_mg_l > 0.1  # 0.1 mg/L threshold

    if redox_mode == "aerobic":
        phases = get_aerobic_phases(database)
        hfo_phase = get_hfo_surface_phase(database)
    elif redox_mode == "anaerobic":
        phases = get_anaerobic_phases(database, sulfide_present=sulfide_present)
        hfo_phase = None  # No HFO under anaerobic conditions
    else:
        # For pe_from_orp, fixed_pe, or fixed_fe2_fraction:
        # Include both aerobic and anaerobic phases, let PHREEQC decide
        aerobic = get_aerobic_phases(database, include_calcite=False)
        anaerobic = get_anaerobic_phases(
            database, sulfide_present=sulfide_present, include_calcite=False
        )

        # Combine phases, avoiding duplicates
        phase_names = set()
        phases = []
        for p in aerobic + anaerobic:
            if p["name"] not in phase_names:
                phases.append(p)
                phase_names.add(p["name"])

        # Add calcite once
        calcite = get_phase_name("calcium_carbonate", database)
        if calcite and calcite not in phase_names:
            phases.append({"name": calcite, "target_si": 0.0, "initial_moles": 0.0})

        hfo_phase = get_hfo_surface_phase(database)

    return phases, hfo_phase


# Stoichiometry constants for metal-P precipitation
# Used for initial dose estimation in binary search
STOICHIOMETRY = {
    # Iron phases
    "Strengite": {"metal_per_P": 1.0, "formula": "FePO4·2H2O", "metal": "Fe"},
    "Vivianite": {"metal_per_P": 1.5, "formula": "Fe3(PO4)2·8H2O", "metal": "Fe"},  # 3 Fe per 2 P
    "Fe_adsorption": {"metal_per_P": 2.0, "typical_range": (1.5, 3.0), "metal": "Fe"},  # Empirical HFO
    # Aluminum phases (adsorption only - no Al-P precipitates in standard databases)
    "Al_adsorption": {"metal_per_P": 2.5, "typical_range": (1.5, 4.0), "metal": "Al"},  # Empirical HAO
    # Note: Al:P ratios are typically higher than Fe:P because:
    # 1. No direct Al-P precipitate (Variscite not in databases)
    # 2. Al adsorption capacity varies more with pH
    # 3. Optimal pH range is narrower (5.5-7.0)
}


def estimate_initial_fe_dose(
    target_p_removal_mmol: float,
    redox_mode: str = "aerobic",
    safety_factor: float = 1.5,
) -> float:
    """
    Estimate initial Fe dose for binary search based on stoichiometry.

    Args:
        target_p_removal_mmol: P to be removed in mmol/L
        redox_mode: "aerobic" or "anaerobic"
        safety_factor: Multiplier for initial estimate (default 1.5x)

    Returns:
        Estimated Fe dose in mmol/L
    """
    if redox_mode == "anaerobic":
        # Vivianite: Fe3(PO4)2, so 1.5 Fe per P
        fe_per_p = STOICHIOMETRY["Vivianite"]["metal_per_P"]
    else:
        # Aerobic: Mix of Strengite (1:1) and adsorption (2:1)
        # Use weighted average assuming 50/50 split
        fe_per_p = (STOICHIOMETRY["Strengite"]["metal_per_P"] +
                    STOICHIOMETRY["Fe_adsorption"]["metal_per_P"]) / 2

    return target_p_removal_mmol * fe_per_p * safety_factor


def estimate_initial_al_dose(
    target_p_removal_mmol: float,
    safety_factor: float = 1.5,
) -> float:
    """
    Estimate initial Al dose for binary search based on stoichiometry.

    Al-P removal relies primarily on adsorption to HAO (hydrous aluminum oxide).
    No Al-P precipitates (Variscite/AlPO4) are available in standard PHREEQC databases.

    Args:
        target_p_removal_mmol: P to be removed in mmol/L
        safety_factor: Multiplier for initial estimate (default 1.5x)

    Returns:
        Estimated Al dose in mmol/L
    """
    # Al:P ratio for adsorption (empirical, higher than Fe due to no direct precipitate)
    al_per_p = STOICHIOMETRY["Al_adsorption"]["metal_per_P"]

    return target_p_removal_mmol * al_per_p * safety_factor


def estimate_initial_metal_dose(
    target_p_removal_mmol: float,
    metal_type: str,
    redox_mode: str = "aerobic",
    safety_factor: float = 1.5,
) -> float:
    """
    Unified function to estimate initial metal dose for binary search.

    Args:
        target_p_removal_mmol: P to be removed in mmol/L
        metal_type: "Fe" or "Al"
        redox_mode: "aerobic" or "anaerobic" (only affects Fe)
        safety_factor: Multiplier for initial estimate (default 1.5x)

    Returns:
        Estimated metal dose in mmol/L
    """
    if metal_type == "Al":
        return estimate_initial_al_dose(target_p_removal_mmol, safety_factor)
    else:
        return estimate_initial_fe_dose(target_p_removal_mmol, redox_mode, safety_factor)


def get_phases_for_coagulant(
    coagulant_formula: str,
    redox_mode: str = "aerobic",
    database: str = "minteq.v4.dat",
    sulfide_mg_l: float = 0.0,
) -> Tuple[List[Dict[str, any]], Optional[str], str]:
    """
    Get appropriate phases based on coagulant type (Fe or Al).

    Args:
        coagulant_formula: Coagulant formula (e.g., "FeCl3", "AlCl3", "Al2(SO4)3")
        redox_mode: One of "aerobic", "anaerobic", "pe_from_orp", "fixed_pe", "fixed_fe2_fraction"
        database: Database filename
        sulfide_mg_l: Sulfide concentration for anaerobic conditions (Fe only)

    Returns:
        Tuple of (phases_list, surface_phase_name, surface_type)
        surface_type is "Hfo" for Fe or "Hao" for Al
    """
    # Import here to avoid circular dependency
    from tools.schemas_ferric import get_coagulant_metal

    metal_type = get_coagulant_metal(coagulant_formula)

    if metal_type == "Al":
        # Aluminum coagulant - use Al phases (adsorption only, no Al-P precipitates)
        phases = get_aluminum_phases(database, include_hao=True, include_calcite=True)
        surface_phase = get_hao_surface_phase(database)
        surface_type = "Hao"

        # Log warning about Al-P limitations
        logger.info(
            "Al coagulant selected. Note: Al-P removal relies on adsorption to HAO. "
            "AlPO4/Variscite phases are not available in standard PHREEQC databases."
        )

        return phases, surface_phase, surface_type
    else:
        # Iron coagulant - use existing Fe logic
        phases, hfo_phase = get_phases_for_redox_mode(redox_mode, database, sulfide_mg_l)
        return phases, hfo_phase, "Hfo"
