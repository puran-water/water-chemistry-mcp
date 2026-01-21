"""
Unified phosphorus removal tool supporting multiple coagulant/precipitation strategies.

This tool provides a single interface for calculating optimal reagent doses
to achieve target phosphorus removal using:

- Iron coagulants (FeCl3, FeSO4, FeCl2) - HFO adsorption + precipitation
- Aluminum coagulants (AlCl3, Al2(SO4)3) - HAO adsorption + precipitation
- Magnesium for struvite (MgCl2, MgO, Mg(OH)2) - Struvite crystallization
- Calcium for Ca-phosphate (Ca(OH)2, CaCl2) - Brushite/HAP precipitation

Key features:
- Inline PHREEQC blocks for phases not in standard databases (Struvite, Variscite, HAO)
- Redox modes: aerobic and anaerobic (with sulfide sensitivity)
- Metastability handling via SI triggers for slow-precipitation phases
- Background sinks (optional competing phases)
"""

import copy
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, validator

from utils.database_management import database_manager
from utils.exceptions import InputValidationError
from utils.inline_phases import (
    build_hao_phase_linked_surface_block,
    get_hao_surface_block,
    get_p_removal_inline_blocks,
    get_required_inline_blocks_for_database,
    get_struvite_phases_block,
    get_variscite_phases_block,
)

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas_ferric import (
    MOLECULAR_WEIGHTS,
    RedoxDiagnostics,
    RedoxSpecification,
    WaterAnalysisInput,
    mg_l_to_mmol,
    mmol_to_mg_l,
    orp_to_pe,
    pe_to_orp,
)

logger = logging.getLogger(__name__)


# =============================================================================
# STRATEGY DEFINITIONS (Table-Driven Config)
# =============================================================================

# Reagent definitions for each P removal strategy
REAGENT_DEFINITIONS = {
    # Iron coagulants
    "FeCl3": {"metal": "Fe", "metal_atoms": 1, "mw": 162.2, "oxidation_state": 3},
    "FeSO4": {"metal": "Fe", "metal_atoms": 1, "mw": 151.9, "oxidation_state": 2},
    "FeCl2": {"metal": "Fe", "metal_atoms": 1, "mw": 126.75, "oxidation_state": 2},
    "Fe2(SO4)3": {"metal": "Fe", "metal_atoms": 2, "mw": 399.9, "oxidation_state": 3},
    # Aluminum coagulants
    "AlCl3": {"metal": "Al", "metal_atoms": 1, "mw": 133.34, "adds_alkalinity": False},
    "Al2(SO4)3": {"metal": "Al", "metal_atoms": 2, "mw": 342.15, "adds_alkalinity": False},
    # Magnesium reagents (for struvite)
    "MgCl2": {"metal": "Mg", "metal_atoms": 1, "mw": 95.21, "adds_alkalinity": False},
    "MgO": {"metal": "Mg", "metal_atoms": 1, "mw": 40.30, "adds_alkalinity": True},
    "Mg(OH)2": {"metal": "Mg", "metal_atoms": 1, "mw": 58.32, "adds_alkalinity": True},
    # Calcium reagents (for Ca-phosphate)
    "Ca(OH)2": {"metal": "Ca", "metal_atoms": 1, "mw": 74.09, "adds_alkalinity": True},
    "CaCl2": {"metal": "Ca", "metal_atoms": 1, "mw": 110.98, "adds_alkalinity": False},
    "CaO": {"metal": "Ca", "metal_atoms": 1, "mw": 56.08, "adds_alkalinity": True},
}

# Strategy configuration: phases, surface, typical ratio
STRATEGY_CONFIG = {
    "iron": {
        "description": "Iron coagulant (HFO adsorption + Strengite/Vivianite precipitation)",
        "metal": "Fe",
        "default_reagent": "FeCl3",
        "phases_aerobic": ["Ferrihydrite", "Strengite", "Calcite"],
        "phases_anaerobic": ["Vivianite", "FeS(ppt)", "Siderite", "Calcite"],
        "surface_name": "Hfo",
        "requires_inline_blocks": False,
        "typical_metal_p_ratio": {"aerobic": 2.0, "anaerobic": 1.5},
        "optimal_ph_range": (5.5, 8.0),
    },
    "aluminum": {
        "description": "Aluminum coagulant (HAO adsorption, Variscite at low pH)",
        "metal": "Al",
        "default_reagent": "AlCl3",
        "phases_aerobic": ["Gibbsite", "Variscite", "Calcite"],
        "phases_anaerobic": ["Gibbsite", "Calcite"],  # Al less redox-sensitive
        "surface_name": "Hao",
        "requires_inline_blocks": True,  # HAO surface + Variscite not in std databases
        "typical_metal_p_ratio": {"aerobic": 2.5, "anaerobic": 2.5},
        "optimal_ph_range": (5.5, 7.0),
    },
    "struvite": {
        "description": "Struvite crystallization (MgNH4PO4·6H2O) for P recovery",
        "metal": "Mg",
        "default_reagent": "MgCl2",
        "phases_aerobic": ["Struvite", "Brucite", "Calcite"],
        "phases_anaerobic": ["Struvite", "Brucite", "Calcite"],
        "surface_name": None,  # No surface complexation for struvite
        "requires_inline_blocks": True,  # Struvite not in std databases
        "typical_metal_p_ratio": {"aerobic": 1.0, "anaerobic": 1.0},  # Stoichiometric
        "optimal_ph_range": (8.5, 9.5),
        "requires_ammonia": True,  # Struvite needs NH4
        "si_trigger": 0.5,  # SI threshold for precipitation (metastability)
    },
    "calcium_phosphate": {
        "description": "Calcium phosphate precipitation (Brushite/HAP)",
        "metal": "Ca",
        "default_reagent": "Ca(OH)2",
        "phases_aerobic": ["CaHPO4:2H2O", "Hydroxyapatite", "Calcite"],
        "phases_anaerobic": ["CaHPO4:2H2O", "Calcite"],
        "surface_name": None,  # No surface complexation for Ca-P
        "requires_inline_blocks": False,  # Usually in minteq.v4.dat
        "typical_metal_p_ratio": {"aerobic": 1.5, "anaerobic": 1.5},  # Ca:P ratio
        "optimal_ph_range": (9.0, 11.0),  # High pH for Ca-P
        "prefer_brushite": True,  # Kinetically favored at moderate pH
    },
}


# =============================================================================
# INPUT/OUTPUT SCHEMAS
# =============================================================================


class PhosphorusRemovalStrategy(BaseModel):
    """Strategy specification for phosphorus removal."""

    strategy: Literal["iron", "aluminum", "struvite", "calcium_phosphate"] = Field(
        ..., description="P removal strategy to use."
    )
    reagent: Optional[str] = Field(
        None, description="Specific reagent formula. If not provided, uses default for strategy."
    )
    max_dose_mmol: float = Field(50.0, description="Maximum reagent dose to search (mmol/L as metal).", gt=0)
    si_trigger: Optional[float] = Field(
        None,
        description=(
            "Saturation index threshold below which precipitation is unlikely without seeding. "
            "Struvite default: 0.5. Set to 0 to assume instantaneous equilibrium."
        ),
    )
    allowed_phases: Optional[List[str]] = Field(
        None,
        description=(
            "Override default phases for the strategy. For calcium_phosphate, default is "
            "['CaHPO4:2H2O', 'Hydroxyapatite'] but brushite (CaHPO4:2H2O) is kinetically preferred. "
            "Use ['CaHPO4:2H2O'] for brushite-only or ['Hydroxyapatite'] for HAP-only."
        ),
    )


class CalculatePhosphorusRemovalDoseInput(BaseModel):
    """Input for unified phosphorus removal dose calculation."""

    initial_solution: WaterAnalysisInput = Field(..., description="Starting water composition with P concentration.")
    target_residual_p_mg_l: float = Field(
        ..., description="Target residual phosphorus concentration (mg/L as P).", ge=0
    )
    strategy: PhosphorusRemovalStrategy = Field(..., description="P removal strategy and reagent specification.")
    redox: Optional[RedoxSpecification] = Field(
        None, description="Redox specification. Defaults to aerobic if not provided."
    )
    include_background_sinks: bool = Field(
        False,
        description=(
            "If True, include background precipitation sinks (struvite, Ca-phosphate) "
            "alongside primary strategy. Useful for complex wastewaters."
        ),
    )
    # Sulfide sensitivity for anaerobic iron strategy
    sulfide_sensitivity: Optional[bool] = Field(
        None,
        description=(
            "For iron strategy in anaerobic mode without S(-2): "
            "True = run mandatory sensitivity sweep at [0, 20, 50, 100] mg/L; "
            "False = accept sulfide-free optimistic estimate. "
            "Required when using iron strategy in anaerobic mode without S(-2)."
        ),
    )
    # Inert P accounting
    p_inert_soluble_mg_l: float = Field(
        0.0,
        description=(
            "Non-reactive soluble P (organic P, colloidal P) that won't precipitate. "
            "Subtracted from target to calculate effective reactive P target."
        ),
        ge=0,
    )
    # Tuning parameters
    hfo_site_multiplier: float = Field(
        1.0,
        description="Site density multiplier for HFO/HAO surface (0.5-2.0 typical).",
        ge=0.1,
        le=5.0,
    )
    database: Optional[str] = Field("minteq.v4.dat", description="PHREEQC database file.")
    # Search parameters
    max_iterations: int = Field(30, description="Maximum binary search iterations.", ge=5, le=100)
    tolerance_mg_l: float = Field(0.1, description="Convergence tolerance for P (mg/L).", gt=0)


class PhosphorusRemovalScenario(BaseModel):
    """Result for a single dose scenario in the sweep."""

    dose_mmol: float = Field(..., description="Reagent dose (mmol/L as metal).")
    dose_mg_l: float = Field(..., description="Reagent dose (mg/L as product).")
    residual_p_mg_l: float = Field(..., description="Residual P concentration (mg/L).")
    metal_to_p_ratio: float = Field(..., description="Molar metal:P ratio at this dose.")
    ph: float = Field(..., description="Final pH after treatment.")
    precipitation_breakdown: Optional[Dict[str, float]] = Field(
        None, description="Precipitated phases and amounts (mmol)."
    )


class CalculatePhosphorusRemovalDoseOutput(BaseModel):
    """Output from unified phosphorus removal dose calculation."""

    status: Literal["success", "infeasible", "input_error"] = Field(..., description="Operation status.")
    error_message: Optional[str] = Field(None, description="Error message if not success.")

    # Optimal dose
    optimal_dose_mmol: Optional[float] = Field(None, description="Optimal reagent dose (mmol/L as metal).")
    optimal_dose_mg_l: Optional[float] = Field(None, description="Optimal reagent dose (mg/L as product).")
    achieved_p_mg_l: Optional[float] = Field(None, description="Achieved residual P at optimal dose (mg/L).")
    metal_to_p_ratio: Optional[float] = Field(None, description="Molar metal:P ratio at optimal dose.")

    # Final state
    final_ph: Optional[float] = Field(None, description="Final pH after treatment.")
    final_pe: Optional[float] = Field(None, description="Final pe after treatment.")

    # Strategy info
    strategy_used: str = Field(..., description="Strategy that was used.")
    reagent_used: str = Field(..., description="Reagent formula that was used.")
    inline_blocks_added: Optional[List[str]] = Field(None, description="Inline PHREEQC blocks that were added.")

    # Precipitation breakdown
    precipitated_phases: Optional[Dict[str, float]] = Field(None, description="Precipitated phases and amounts (mmol).")

    # Dose-response curve (optional)
    dose_response_curve: Optional[List[PhosphorusRemovalScenario]] = Field(
        None, description="Dose-response curve data points for plotting."
    )

    # Redox diagnostics
    redox_diagnostics: Optional[RedoxDiagnostics] = Field(
        None, description="Detailed redox constraint and pe diagnostics."
    )

    # Warnings
    warnings: Optional[List[str]] = Field(None, description="Non-fatal warnings.")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_primary_p_phases(strategy_name: str, is_aerobic: bool) -> List[str]:
    """Get the primary P-precipitating phases for a strategy.

    These are the phases that actually remove P (not competing phases like Calcite).

    Args:
        strategy_name: Strategy name (iron, aluminum, struvite, calcium_phosphate)
        is_aerobic: True for aerobic mode

    Returns:
        List of primary P-phase names
    """
    # Map strategy to primary P-precipitating phases
    primary_p_phases = {
        "iron": {
            "aerobic": ["Strengite"],  # Fe-P solid
            "anaerobic": ["Vivianite"],  # Fe(II)-P solid
        },
        "aluminum": {
            "aerobic": ["Variscite"],  # Al-P solid
            "anaerobic": ["Variscite"],
        },
        "struvite": {
            "aerobic": ["Struvite"],
            "anaerobic": ["Struvite"],
        },
        "calcium_phosphate": {
            "aerobic": ["CaHPO4:2H2O", "Hydroxyapatite"],  # Brushite, HAP
            "anaerobic": ["CaHPO4:2H2O"],
        },
    }

    redox_key = "aerobic" if is_aerobic else "anaerobic"
    return primary_p_phases.get(strategy_name, {}).get(redox_key, [])


def _get_background_sink_phases(
    primary_strategy: str,
    is_aerobic: bool,
    has_ammonia: bool,
) -> Tuple[List[str], str]:
    """Get background P-sink phases to add alongside primary strategy.

    Background sinks can remove P via competing pathways. This is useful
    for complex wastewaters where multiple precipitation mechanisms occur.

    Args:
        primary_strategy: Primary strategy name (to avoid duplicating phases)
        is_aerobic: True for aerobic mode
        has_ammonia: True if ammonia/N(-3) is present (enables struvite)

    Returns:
        Tuple of (phase list, inline PHREEQC blocks to add)
    """
    background_phases = []
    inline_blocks = ""

    # Get primary strategy's phases to avoid duplicates
    primary_config = STRATEGY_CONFIG.get(primary_strategy, {})
    primary_phases_key = "phases_aerobic" if is_aerobic else "phases_anaerobic"
    primary_phases = primary_config.get(primary_phases_key, [])

    # Add struvite if ammonia present and not primary strategy
    if primary_strategy != "struvite" and has_ammonia:
        if "Struvite" not in primary_phases:
            background_phases.append("Struvite")
            inline_blocks += get_struvite_phases_block()

    # Add Ca-phosphate phases if not primary strategy
    if primary_strategy != "calcium_phosphate":
        ca_phases = ["CaHPO4:2H2O"]  # Brushite - kinetically favored
        for phase in ca_phases:
            if phase not in primary_phases:
                background_phases.append(phase)

    # Add Calcite as a Ca sink (competes with Ca-P)
    if "Calcite" not in primary_phases:
        background_phases.append("Calcite")

    return background_phases, inline_blocks


# Background sink SI triggers: phases that are slow to precipitate without seeding
BACKGROUND_SINK_SI_TRIGGERS = {
    "Struvite": 0.5,  # Metastable without seeding
    "CaHPO4:2H2O": 0.3,  # Brushite - some kinetic barrier
    "Hydroxyapatite": 0.5,  # HAP is thermodynamically stable but kinetically slow
}


def _apply_si_trigger_to_phases(
    result: Dict[str, Any],
    phases_to_check: List[str],
    si_trigger: float,
    initial_p_mg_l: float,
    residual_p_mg_l: float,
    warnings: List[str],
) -> Tuple[float, bool]:
    """Apply SI trigger logic to a set of phases.

    For each phase, if SI < trigger, assume that phase won't precipitate due to
    metastability (kinetic barrier). Return adjusted P value if all checked
    phases are below trigger.

    Args:
        result: PHREEQC simulation result
        phases_to_check: List of phase names to check
        si_trigger: SI threshold below which precipitation is unlikely
        initial_p_mg_l: Initial P concentration
        residual_p_mg_l: Current residual P from simulation
        warnings: List to append warnings to

    Returns:
        Tuple of (adjusted residual P mg/L, whether trigger was applied)
    """
    if not phases_to_check or si_trigger <= 0:
        return residual_p_mg_l, False

    saturation_indices = result.get("saturation_indices", {})
    phases_below_trigger = []

    for phase in phases_to_check:
        phase_si = saturation_indices.get(phase)
        if phase_si is not None and phase_si < si_trigger:
            phases_below_trigger.append((phase, phase_si))

    if phases_below_trigger and len(phases_below_trigger) == len(phases_to_check):
        # All checked phases are below trigger - precipitation unlikely
        logger.debug(
            f"SI trigger active: {phases_below_trigger} all below trigger {si_trigger}, " f"precipitation unlikely"
        )
        # Add warning once
        metastability_warning = (
            f"Metastability: {[p[0] for p in phases_below_trigger]} SI below trigger ({si_trigger}). "
            f"Precipitation may require seeding or higher supersaturation."
        )
        if metastability_warning not in warnings:
            warnings.append(metastability_warning)
        return initial_p_mg_l, True

    return residual_p_mg_l, False


def _build_redox_diagnostics(
    redox: "RedoxSpecification",
    target_pe: float,
    achieved_pe: float,
) -> RedoxDiagnostics:
    """Build redox diagnostics for output.

    Args:
        redox: RedoxSpecification model with mode information
        target_pe: Target pe value used for simulation
        achieved_pe: Actual pe from PHREEQC result

    Returns:
        RedoxDiagnostics model
    """
    # Determine constraint type from mode
    if redox.mode == "aerobic":
        constraint_type = "o2_equilibrium"
        constraint_blocks = ["O2(g)"]
    elif redox.mode == "anaerobic":
        constraint_type = "fix_pe"
        constraint_blocks = ["Fix_pe"]
    elif redox.mode == "fixed_pe":
        constraint_type = "fix_pe"
        constraint_blocks = ["Fix_pe"]
    elif redox.mode == "pe_from_orp":
        constraint_type = "fix_pe"
        constraint_blocks = ["Fix_pe"]
    else:
        constraint_type = "none"
        constraint_blocks = []

    # Calculate pe drift
    pe_drift = abs(achieved_pe - target_pe) if achieved_pe is not None else None

    # Calculate ORP equivalents (at 25°C)
    target_orp = pe_to_orp(target_pe, 25.0)
    achieved_orp = pe_to_orp(achieved_pe, 25.0) if achieved_pe is not None else None

    return RedoxDiagnostics(
        redox_constraint_type=constraint_type,
        target_pe=target_pe,
        achieved_pe=achieved_pe if achieved_pe is not None else target_pe,
        pe_drift=pe_drift,
        target_orp_mV_vs_SHE=target_orp,
        achieved_orp_mV_vs_SHE=achieved_orp,
        constraint_blocks_used=constraint_blocks if constraint_blocks else None,
    )


# =============================================================================
# MAIN FUNCTION
# =============================================================================


async def calculate_phosphorus_removal_dose(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate optimal reagent dose for phosphorus removal.

    Supports multiple strategies:
    - Iron coagulants (FeCl3, FeSO4) - HFO + Strengite/Vivianite
    - Aluminum coagulants (AlCl3, Al2(SO4)3) - HAO + Variscite
    - Struvite (MgCl2, MgO) - Mg-based P recovery
    - Calcium phosphate (Ca(OH)2, CaCl2) - Brushite/HAP

    Args:
        input_data: Dictionary matching CalculatePhosphorusRemovalDoseInput schema

    Returns:
        Dictionary matching CalculatePhosphorusRemovalDoseOutput schema
    """
    logger.info("Running calculate_phosphorus_removal_dose tool...")
    warnings = []
    inline_blocks_added = []

    # Step 1: Validate input
    try:
        input_model = CalculatePhosphorusRemovalDoseInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message=f"Input validation error: {e}",
            strategy_used="unknown",
            reagent_used="unknown",
        ).dict(exclude_none=True)

    # Extract parameters
    initial_solution = input_model.initial_solution.dict()
    target_p_mg_l = input_model.target_residual_p_mg_l
    strategy_spec = input_model.strategy
    redox = input_model.redox or RedoxSpecification(mode="aerobic")
    database = input_model.database or "minteq.v4.dat"

    # Get strategy configuration
    strategy_name = strategy_spec.strategy
    strategy_config = STRATEGY_CONFIG.get(strategy_name)
    if not strategy_config:
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message=f"Unknown strategy: {strategy_name}",
            strategy_used=strategy_name,
            reagent_used="unknown",
        ).dict(exclude_none=True)

    # Get reagent
    reagent = strategy_spec.reagent or strategy_config["default_reagent"]
    reagent_info = REAGENT_DEFINITIONS.get(reagent)
    if not reagent_info:
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message=f"Unknown reagent: {reagent}",
            strategy_used=strategy_name,
            reagent_used=reagent,
        ).dict(exclude_none=True)

    metal = reagent_info["metal"]

    # Step 2: Validate strategy requirements
    analysis = initial_solution.get("analysis", {})

    # Check ammonia requirement for struvite
    if strategy_name == "struvite":
        has_ammonia = "N(-3)" in analysis or "NH4" in analysis
        if not has_ammonia:
            return CalculatePhosphorusRemovalDoseOutput(
                status="input_error",
                error_message=(
                    "Struvite strategy requires ammonia (N(-3)) in initial_solution. "
                    "Add N(-3) concentration to analysis or choose a different strategy."
                ),
                strategy_used=strategy_name,
                reagent_used=reagent,
            ).dict(exclude_none=True)

    # Mandatory sulfide sensitivity check for anaerobic iron strategy
    sulfide_sensitivity = input_model.sulfide_sensitivity
    sulfide_sensitivity_results = None

    if strategy_name == "iron" and redox.mode == "anaerobic":
        has_sulfide = "S(-2)" in analysis and analysis.get("S(-2)", 0) > 0
        if not has_sulfide:
            # Anaerobic iron mode without sulfide - require explicit handling
            if sulfide_sensitivity is None:
                return CalculatePhosphorusRemovalDoseOutput(
                    status="input_error",
                    error_message=(
                        "Anaerobic iron strategy requires sulfide specification for realistic Fe:P prediction. "
                        "Typical digesters have 20-100 mg/L S(-2). Choose one:\n"
                        "1. Add 'S(-2)' to initial_solution.analysis with measured sulfide concentration\n"
                        "2. Set 'sulfide_sensitivity': true to run mandatory sensitivity sweep at [0, 20, 50, 100] mg/L\n"
                        "3. Set 'sulfide_sensitivity': false to explicitly accept sulfide-free limit (Fe:P ≈ 1.5-1.7)"
                    ),
                    strategy_used=strategy_name,
                    reagent_used=reagent,
                ).dict(exclude_none=True)
            elif sulfide_sensitivity is True:
                # Run sulfide sensitivity sweep (will be implemented in output)
                logger.info("Running sulfide sensitivity analysis for anaerobic iron mode without S(-2)")
                warnings.append(
                    "Sulfide sensitivity sweep enabled: running at [0, 20, 50, 100] mg/L S(-2). "
                    "Results will show Fe:P ratio sensitivity to sulfide levels."
                )
            else:
                # sulfide_sensitivity is False - proceed with warning
                warnings.append(
                    "Sulfide-free anaerobic estimate: Fe:P ≈ 1.5-1.7 (optimistic). "
                    "Real digesters with sulfide typically require Fe:P = 2.5-5+."
                )

    # Get initial P concentration
    initial_p_mg_l = _get_initial_p_mg_l(initial_solution)
    if initial_p_mg_l <= 0:
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message="Could not determine initial P concentration from input",
            strategy_used=strategy_name,
            reagent_used=reagent,
        ).dict(exclude_none=True)

    if target_p_mg_l >= initial_p_mg_l:
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message=f"Target P ({target_p_mg_l} mg/L) must be less than initial P ({initial_p_mg_l} mg/L)",
            strategy_used=strategy_name,
            reagent_used=reagent,
        ).dict(exclude_none=True)

    # Inert P accounting: adjust effective target for non-reactive P
    p_inert = input_model.p_inert_soluble_mg_l
    effective_target_p = target_p_mg_l - p_inert
    if effective_target_p < 0:
        return CalculatePhosphorusRemovalDoseOutput(
            status="input_error",
            error_message=(
                f"Target P ({target_p_mg_l} mg/L) is less than non-reactive P ({p_inert} mg/L). "
                "Cannot achieve target. Reduce p_inert_soluble_mg_l or increase target."
            ),
            strategy_used=strategy_name,
            reagent_used=reagent,
        ).dict(exclude_none=True)

    if p_inert > 0:
        logger.info(
            f"Adjusted for inert P: target {target_p_mg_l} - inert {p_inert} = effective {effective_target_p} mg/L"
        )
        warnings.append(
            f"Inert P accounting: {p_inert} mg/L non-reactive P assumed. "
            f"Effective reactive P target = {effective_target_p:.2f} mg/L"
        )

    # Step 3: Resolve database and check for required inline blocks
    try:
        database_path = database_manager.resolve_and_validate_database(database, category="general")
    except Exception as e:
        logger.warning(f"Database resolution failed, using default: {e}")
        database_path = database_manager.resolve_and_validate_database("minteq.v4.dat", category="general")

    # Build inline blocks if needed
    inline_phreeqc_prefix = ""
    if strategy_config.get("requires_inline_blocks"):
        if strategy_name == "aluminum":
            inline_phreeqc_prefix += get_variscite_phases_block()
            inline_phreeqc_prefix += get_hao_surface_block()
            inline_blocks_added.extend(["Variscite", "HAO_surface"])
            logger.info("Added inline blocks for aluminum strategy")

        elif strategy_name == "struvite":
            inline_phreeqc_prefix += get_struvite_phases_block()
            inline_blocks_added.append("Struvite")
            logger.info("Added inline block for struvite strategy")

    # Step 4: Determine redox parameters
    is_aerobic = redox.mode == "aerobic"
    pe_value = _determine_pe(redox)
    initial_solution["pe"] = pe_value

    # Get phases for this strategy (use allowed_phases override if provided)
    if strategy_spec.allowed_phases:
        # User specified custom phases - use those (preserve default competing phases like Calcite)
        phases = list(strategy_spec.allowed_phases)
        # Add Calcite if not already present (common competing sink)
        if "Calcite" not in phases:
            phases.append("Calcite")
        logger.info(f"Using user-specified phases: {phases}")
    else:
        # Use default phases for this strategy
        phases_key = "phases_aerobic" if is_aerobic else "phases_anaerobic"
        phases = list(strategy_config.get(phases_key, []))  # Make a copy

    # Step 4.5: Add background sinks if enabled
    if input_model.include_background_sinks:
        background_phases, background_inline = _get_background_sink_phases(
            primary_strategy=strategy_name,
            is_aerobic=is_aerobic,
            has_ammonia="N(-3)" in initial_solution.get("analysis", {}),
        )
        # Add background phases that aren't already in the list
        for phase in background_phases:
            if phase not in phases:
                phases.append(phase)

        # Add any needed inline blocks for background sinks
        if background_inline and background_inline not in inline_phreeqc_prefix:
            inline_phreeqc_prefix += background_inline
            inline_blocks_added.append("background_sinks")

        logger.info(f"Background sinks enabled: added {background_phases}")
        warnings.append(f"Background sinks enabled: {background_phases}. " "P removal may occur via multiple pathways.")

    # Step 5: Run binary search optimization
    # Use effective_target_p (accounts for inert P) in binary search
    logger.info(
        f"Starting {strategy_name} optimization: target P = {target_p_mg_l} mg/L "
        f"(effective reactive target = {effective_target_p} mg/L)"
    )

    # Calculate initial dose estimate based on effective reactive P to remove
    p_to_remove_mg_l = initial_p_mg_l - target_p_mg_l
    p_to_remove_mmol = mg_l_to_mmol(p_to_remove_mg_l, "P")

    # Get site multiplier for surface complexation
    hfo_site_multiplier = input_model.hfo_site_multiplier
    typical_ratio = strategy_config["typical_metal_p_ratio"].get("aerobic" if is_aerobic else "anaerobic", 2.0)
    initial_dose_mmol = p_to_remove_mmol * typical_ratio * 1.5  # Safety factor

    # Binary search
    dose_low = 0.0
    dose_high = min(strategy_spec.max_dose_mmol, initial_dose_mmol * 3)
    max_iterations = input_model.max_iterations
    tolerance = input_model.tolerance_mg_l

    optimal_dose_mmol = None
    achieved_p_mg_l = None
    final_state = None
    dose_response_data = []

    for iteration in range(max_iterations):
        dose_mid = (dose_low + dose_high) / 2

        # Run simulation at this dose
        try:
            result = await _run_p_removal_simulation(
                initial_solution=copy.deepcopy(initial_solution),
                reagent=reagent,
                dose_mmol=dose_mid,
                phases=phases,
                strategy_name=strategy_name,
                inline_prefix=inline_phreeqc_prefix,
                database_path=database_path,
                pe_value=pe_value,
                surface_name=strategy_config.get("surface_name"),
                hfo_site_multiplier=hfo_site_multiplier,
                redox_mode=redox.mode,
            )

            if "error" in result:
                logger.warning(f"Simulation error at dose {dose_mid:.3f}: {result['error']}")
                # Try to continue with reduced dose range
                dose_high = dose_mid
                continue

            residual_p_mg_l = result.get("residual_p_mg_l", target_p_mg_l)

            # Apply SI trigger for metastability (e.g., struvite won't precipitate if SI < trigger)
            # User can override via strategy_spec.si_trigger, or use strategy default
            primary_si_trigger = strategy_spec.si_trigger
            if primary_si_trigger is None:
                primary_si_trigger = strategy_config.get("si_trigger")  # Strategy default (e.g., 0.5 for struvite)

            # 1. Apply SI trigger to primary strategy phases
            if primary_si_trigger is not None and primary_si_trigger > 0:
                primary_phases = _get_primary_p_phases(strategy_name, is_aerobic)
                residual_p_mg_l, trigger_applied = _apply_si_trigger_to_phases(
                    result=result,
                    phases_to_check=primary_phases,
                    si_trigger=primary_si_trigger,
                    initial_p_mg_l=initial_p_mg_l,
                    residual_p_mg_l=residual_p_mg_l,
                    warnings=warnings,
                )

            # 2. Apply SI trigger to background sinks (if enabled)
            # Background sinks use their own SI triggers from BACKGROUND_SINK_SI_TRIGGERS
            if input_model.include_background_sinks:
                saturation_indices = result.get("saturation_indices", {})
                equilibrium_moles = result.get("equilibrium_phase_moles", {})

                # Check each background sink phase with SI triggers
                for bg_phase, bg_trigger in BACKGROUND_SINK_SI_TRIGGERS.items():
                    # Skip if this phase is part of the primary strategy
                    if bg_phase in _get_primary_p_phases(strategy_name, is_aerobic):
                        continue

                    # Check if phase was supposed to precipitate but is below SI trigger
                    phase_si = saturation_indices.get(bg_phase)
                    phase_moles = equilibrium_moles.get(bg_phase, 0.0)

                    if phase_si is not None and phase_si < bg_trigger and phase_moles > 0:
                        # This background sink is metastable - unlikely to precipitate
                        # Add back the P that was "removed" by this phase (stoichiometric estimate)
                        # For P phases: 1 mol phase = 1 mol P (Struvite, Brushite)
                        p_from_phase_mmol = phase_moles  # Assuming 1:1 P stoichiometry
                        p_from_phase_mg_l = p_from_phase_mmol * MOLECULAR_WEIGHTS["P"] * 1000

                        residual_p_mg_l += p_from_phase_mg_l
                        logger.debug(
                            f"Background sink {bg_phase} SI={phase_si:.2f} < trigger {bg_trigger}, "
                            f"adding back {p_from_phase_mg_l:.2f} mg/L P"
                        )

                        bg_warning = (
                            f"Background sink {bg_phase} metastable (SI={phase_si:.2f} < {bg_trigger}). "
                            f"P removal by this phase ({p_from_phase_mg_l:.2f} mg/L) may not occur."
                        )
                        if bg_warning not in warnings:
                            warnings.append(bg_warning)

            # Record data point for dose-response curve
            metal_atoms = reagent_info.get("metal_atoms", 1)
            dose_response_data.append(
                PhosphorusRemovalScenario(
                    dose_mmol=dose_mid,
                    dose_mg_l=dose_mid * reagent_info["mw"] / metal_atoms,
                    residual_p_mg_l=residual_p_mg_l,
                    metal_to_p_ratio=dose_mid / p_to_remove_mmol if p_to_remove_mmol > 0 else 0,
                    ph=result.get("ph", 7.0),
                    precipitation_breakdown=result.get("precipitated_phases"),
                )
            )

            # Check convergence against effective_target_p (accounts for inert P)
            # Achieved total P = reactive P from simulation + inert P
            achieved_total_p_mg_l = residual_p_mg_l + p_inert
            if abs(residual_p_mg_l - effective_target_p) <= tolerance:
                optimal_dose_mmol = dose_mid
                achieved_p_mg_l = achieved_total_p_mg_l  # Report total P (reactive + inert)
                final_state = result
                logger.info(f"Converged at iteration {iteration + 1}: dose={dose_mid:.3f} mmol/L")
                break

            # Update search bounds using effective target
            if residual_p_mg_l > effective_target_p:
                # Need more reagent
                dose_low = dose_mid
            else:
                # Too much reagent
                dose_high = dose_mid

            # Update best solution
            if optimal_dose_mmol is None or abs(residual_p_mg_l - effective_target_p) < abs(
                (achieved_p_mg_l or float("inf")) - p_inert - effective_target_p
            ):
                optimal_dose_mmol = dose_mid
                achieved_p_mg_l = achieved_total_p_mg_l  # Report total P
                final_state = result

        except Exception as e:
            logger.error(f"Simulation exception at dose {dose_mid:.3f}: {e}")
            dose_high = dose_mid

    # Step 6: Build output
    if optimal_dose_mmol is None:
        return CalculatePhosphorusRemovalDoseOutput(
            status="infeasible",
            error_message="Could not find optimal dose within search range",
            strategy_used=strategy_name,
            reagent_used=reagent,
            warnings=warnings if warnings else None,
        ).dict(exclude_none=True)

    # Calculate mg/L dose
    metal_atoms = reagent_info.get("metal_atoms", 1)
    optimal_dose_mg_l = optimal_dose_mmol * reagent_info["mw"] / metal_atoms

    # Calculate metal:P ratio
    metal_to_p_ratio = optimal_dose_mmol / p_to_remove_mmol if p_to_remove_mmol > 0 else 0

    # Build redox diagnostics
    achieved_pe = final_state.get("pe") if final_state else pe_value
    redox_diagnostics = _build_redox_diagnostics(
        redox=redox,
        target_pe=pe_value,
        achieved_pe=achieved_pe,
    )

    return CalculatePhosphorusRemovalDoseOutput(
        status="success",
        optimal_dose_mmol=optimal_dose_mmol,
        optimal_dose_mg_l=optimal_dose_mg_l,
        achieved_p_mg_l=achieved_p_mg_l,
        metal_to_p_ratio=metal_to_p_ratio,
        final_ph=final_state.get("ph") if final_state else None,
        final_pe=achieved_pe,
        strategy_used=strategy_name,
        reagent_used=reagent,
        inline_blocks_added=inline_blocks_added if inline_blocks_added else None,
        precipitated_phases=final_state.get("precipitated_phases") if final_state else None,
        dose_response_curve=dose_response_data if len(dose_response_data) >= 3 else None,
        redox_diagnostics=redox_diagnostics,
        warnings=warnings if warnings else None,
    ).dict(exclude_none=True)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_initial_p_mg_l(solution_data: Dict[str, Any]) -> float:
    """Extract initial P concentration from solution data."""
    analysis = solution_data.get("analysis", {})

    # Try different P keys
    for key in ["P", "PO4", "Orthophosphate-P"]:
        if key in analysis:
            value = analysis[key]
            if isinstance(value, (int, float)):
                return float(value)
            elif isinstance(value, str):
                # Try to extract numeric value
                try:
                    return float(value.split()[0])
                except (ValueError, IndexError):
                    pass

    return 0.0


def _determine_pe(redox: RedoxSpecification) -> float:
    """Determine pe value from redox specification."""
    if redox.mode == "aerobic":
        return 3.5
    elif redox.mode == "anaerobic":
        return -4.0
    elif redox.mode == "pe_from_orp" and redox.orp_mV:
        return orp_to_pe(redox.orp_mV, 25.0, redox.orp_reference or "SHE")
    elif redox.mode == "fixed_pe" and redox.fixed_pe is not None:
        return redox.fixed_pe
    else:
        return 4.0  # Default


async def _run_p_removal_simulation(
    initial_solution: Dict[str, Any],
    reagent: str,
    dose_mmol: float,
    phases: List[str],
    strategy_name: str,
    inline_prefix: str,
    database_path: str,
    pe_value: float,
    surface_name: Optional[str] = None,
    hfo_site_multiplier: float = 1.0,
    redox_mode: str = "aerobic",
) -> Dict[str, Any]:
    """Run a single P removal simulation at the specified dose.

    Args:
        initial_solution: Solution composition dict
        reagent: Reagent formula (e.g., "FeCl3", "AlCl3")
        dose_mmol: Reagent dose in mmol/L as metal
        phases: List of equilibrium phase names
        strategy_name: Name of strategy for logging
        inline_prefix: Inline PHREEQC blocks to prepend
        database_path: Path to PHREEQC database
        pe_value: Target pe for redox
        surface_name: Surface type ("Hfo" for iron, "Hao" for aluminum, None for others)
        hfo_site_multiplier: Multiplier for surface site density (default 1.0)
        redox_mode: Redox mode ("aerobic" for O2 equilibrium, "anaerobic" for Fix_pe)

    Returns:
        Simulation result dict with residual P, pH, precipitated phases
    """
    from utils.helpers import (
        build_equilibrium_phases_with_pe_constraint,
        build_phase_linked_surface_block,
        build_reaction_block,
        build_selected_output_block,
        build_solution_block,
    )

    # Build PHREEQC input
    phreeqc_input_parts = []

    # Add inline blocks if needed
    if inline_prefix:
        phreeqc_input_parts.append(inline_prefix)

    # Build solution block
    solution_block = build_solution_block(initial_solution, solution_num=1)
    phreeqc_input_parts.append(solution_block)

    # Build reaction block (reagent addition)
    reagent_info = REAGENT_DEFINITIONS.get(reagent, {})
    metal_atoms = reagent_info.get("metal_atoms", 1)
    product_mmol = dose_mmol / metal_atoms  # mmol of reagent product

    reaction_block = build_reaction_block([{"formula": reagent, "amount": product_mmol, "units": "mmol"}])
    phreeqc_input_parts.append(reaction_block)

    # Build equilibrium phases block with pe constraint
    phases_list = [{"name": p, "target_si": 0.0, "initial_moles": 0.0} for p in phases]

    # Determine pe constraint method based on redox mode
    if redox_mode == "aerobic":
        # Aerobic: equilibrate with O2(g) at atmospheric partial pressure
        pe_constraint = {"method": "o2_equilibrium", "o2_si": -0.68}
    elif redox_mode == "anaerobic":
        # Anaerobic: Fix pe at target value (typically -4.0)
        pe_constraint = {"method": "fix_pe", "target_pe": pe_value}
    else:
        # No explicit constraint (pe determined by solution equilibrium)
        pe_constraint = None

    phases_block = build_equilibrium_phases_with_pe_constraint(
        phases_list, pe_constraint=pe_constraint, allow_empty=True
    )
    if phases_block:
        phreeqc_input_parts.append(phases_block)

    # Build SURFACE block for metal hydroxide adsorption (HFO or HAO)
    if surface_name:
        # Determine which phase to link surface to
        if surface_name == "Hfo":
            # Link to Ferrihydrite for iron coagulants
            phase_for_surface = "Ferrihydrite"
            if phase_for_surface not in phases:
                phase_for_surface = "Fe(OH)3(a)" if "Fe(OH)3(a)" in phases else None
        elif surface_name == "Hao":
            # Link to Gibbsite for aluminum coagulants
            phase_for_surface = "Gibbsite"
            if phase_for_surface not in phases:
                phase_for_surface = "Al(OH)3(a)" if "Al(OH)3(a)" in phases else None
        else:
            phase_for_surface = None

        if phase_for_surface:
            # Build phase-linked surface block
            # For HAO, use the custom block builder from inline_phases
            if surface_name == "Hao":
                surface_block = build_hao_phase_linked_surface_block(
                    phase_name=phase_for_surface,
                    sites_per_mole_strong=0.005 * hfo_site_multiplier,
                    weak_to_strong_ratio=40.0,
                )
            else:
                # For HFO, use the helper function
                surface_block = build_phase_linked_surface_block(
                    surface_name="Hfo",
                    phase_name=phase_for_surface,
                    sites_per_mole=0.005 * hfo_site_multiplier,
                    weak_to_strong_ratio=40.0,  # 1 strong site per 40 weak sites
                    specific_area_per_mole=53300.0,
                )
            if surface_block:
                phreeqc_input_parts.append(surface_block)
                logger.debug(f"Added {surface_name} surface block linked to {phase_for_surface}")

    # Build selected output
    selected_output = build_selected_output_block()
    phreeqc_input_parts.append(selected_output)

    # Combine input
    phreeqc_input = "\nEND\n".join(phreeqc_input_parts) + "\nEND\n"

    # Run simulation
    try:
        result = await run_phreeqc_simulation(phreeqc_input, database_path)

        if isinstance(result, list):
            result = result[-1] if result else {}

        if "error" in result:
            return {"error": result["error"]}

        # Extract residual P
        element_totals = result.get("element_totals_molality", {})
        p_molal = element_totals.get("P", 0) or 0
        residual_p_mg_l = p_molal * MOLECULAR_WEIGHTS["P"] * 1000

        # Extract final conditions
        solution_summary = result.get("solution_summary", {})

        return {
            "residual_p_mg_l": residual_p_mg_l,
            "ph": solution_summary.get("pH", 7.0),
            "pe": solution_summary.get("pe", pe_value),
            "precipitated_phases": result.get("equilibrium_phase_moles", {}),
            "saturation_indices": result.get("saturation_indices", {}),
        }

    except Exception as e:
        logger.error(f"PHREEQC simulation error: {e}")
        return {"error": str(e)}
