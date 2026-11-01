"""
Unified phosphorus removal tool supporting multiple coagulant/precipitation strategies.

This tool provides a single equilibrium-screening interface for evaluating
reagent doses against a dissolved reactive-phosphorus target using:

- Iron coagulants (FeCl3, FeSO4, FeCl2) - HFO adsorption + precipitation
- Aluminum coagulants (AlCl3, Al2(SO4)3) - HAO adsorption + precipitation
- Magnesium for struvite (MgCl2, MgO, Mg(OH)2) - Struvite crystallization
- Calcium for Ca-phosphate (Ca(OH)2, CaCl2) - metastable OCP/brushite (HAP = long-term floor)

Key features:
- Inline PHREEQC blocks for phases not in standard databases (Struvite, Variscite, HAO)
- Redox modes: aerobic and anaerobic (with sulfide sensitivity)
- Metastability handling via SI triggers for slow-precipitation phases
- Background sinks (optional competing phases)
"""

import copy
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import Field

from water_chemistry_mcp.utils.database_management import database_manager
from water_chemistry_mcp.utils.inline_phases import build_hao_phase_linked_surface_block, check_phases_in_database, get_hao_surface_block, get_struvite_phases_block, get_variscite_phases_block
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.phosphorus import INPUT_KEY_RULING_ID, POST_DIGESTER_FEED_SOURCES, ParameterContactObservation, PhosphorusRemovalRequest, PhosphorusRemovalResult
from puran_mcp_support.schema import AssumptionLedger, DZOMBAK_MOREL_HFO, assess_hfo_capacity_parameter_contact, mao_yue_effective_total_sites, mao_yue_fresh_hfo_dataset, split_total_sites_preserving_ratio
from water_chemistry_mcp.waterchem.errors import PurePhaseNotConvergedError, error_payload_message, is_error_payload, public_error

from .phreeqc import run_phreeqc_simulation
from .dose_search import (
    DoseEvaluationError,
    MissingSimulationOutputError,
    scan_and_bracket_dose,
)
from .schemas import BaseModel
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

# Strategy configuration: phases and surface definitions for equilibrium screens
STRATEGY_CONFIG = {
    "iron": {
        "description": "Iron coagulant (HFO adsorption + Strengite/Vivianite precipitation)",
        "metal": "Fe",
        "default_reagent": "FeCl3",
        "phases_aerobic": ["Ferrihydrite", "Strengite", "Calcite"],
        "phases_anaerobic": ["Vivianite", "FeS(ppt)", "Siderite", "Calcite"],  # FeS(ppt): the iron-sulfide ROLE
        "surface_name": "Hfo",
        "requires_inline_blocks": False,
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
        "optimal_ph_range": (8.5, 9.5),
        "requires_ammonia": True,  # Struvite needs NH4
        "si_trigger": 0.5,  # SI threshold for precipitation (metastability)
    },
    "calcium_phosphate": {
        # Ca-P precipitation follows the Ostwald rule of stages: amorphous calcium phosphate (ACP)
        # forms first, ripening through octacalcium phosphate (OCP) to hydroxylapatite (HAP) only
        # slowly (Ferguson & McCarty 1971; Musvoto & Ekama 2000; Barca 2012). HAP is the most
        # insoluble (thermodynamic) phase, so an HAP-equilibrium model predicts an unrealistically low
        # residual (~0.005 mgP/L) that real lime/Ca-P processes do NOT reach in a normal contact time
        # because HAP nucleation is strongly inhibited. We therefore control the near-term prediction
        # with the kinetically-realistic metastable OCP-type phase (Ca4H(PO4)3:3H2O) + brushite at the
        # low-pH end, which reproduces the ~0.1-1 mgP/L that is actually achievable; HAP is surfaced as
        # a long-term floor via a warning (it needs high pH, Ca excess, seeding, and long contact/aging).
        "description": "Calcium phosphate precipitation (metastable OCP/brushite near-term; HAP = long-term floor)",
        "metal": "Ca",
        "default_reagent": "Ca(OH)2",
        "phases_aerobic": ["Ca4H(PO4)3:3H2O", "CaHPO4:2H2O", "Calcite"],
        "phases_anaerobic": ["Ca4H(PO4)3:3H2O", "CaHPO4:2H2O", "Calcite"],
        "surface_name": None,  # No surface complexation for Ca-P
        "requires_inline_blocks": False,  # OCP (Ca4H(PO4)3:3H2O), brushite, Calcite all in minteq.v4.dat
        "optimal_ph_range": (9.0, 11.0),  # High pH for Ca-P
        "prefer_brushite": True,  # Kinetically favored at moderate pH
    },
}

# Database recommendations per strategy
STRATEGY_DATABASE_RECOMMENDATIONS = {
    "iron": {
        "recommended": "minteq.v4.dat",
        "avoid": ["phreeqc.dat"],
        "reason": "phreeqc.dat lacks Strengite (Fe-P precipitate for aerobic mode)",
    },
    "aluminum": {"recommended": "minteq.v4.dat"},
    "struvite": {"recommended": "minteq.v4.dat"},
    "calcium_phosphate": {"recommended": "minteq.v4.dat"},
}

# --- Fresh (in-situ formed) HFO active phosphate-site density, Mao & Yue (2016) ---------------------
# Freshly precipitated ("in-situ") HFO from dosing ferric into a phosphate solution exposes far more
# active P-binding sites than the aged/preformed 2-line ferrihydrite the standard Dzombak & Morel
# parameterisation (~0.2 mol weak sites/mol Fe) is fit to. Mao & Yue (2016, Sci. Rep. 6:35292)
# measured the active-site density Css of in-situ HFO as 0.955 / 1.46 / 2.98 mol sites per mol Fe at
# pH 6 / 7 / 8 (it RISES with pH as more HFO polymer forms) vs 0.45 for preformed HFO. Using the aged
# 0.2 value makes the tool demand ~4-5x too much ferric for a given effluent-P target, contradicting
# full-scale practice and Szabo et al. (2008), who report <0.1 mg/L at low Fe:P over a broad pH 5-7
# optimum. The rising site density with pH also offsets the falling per-site P affinity, reproducing
# that observed pH-insensitivity. We therefore default the (aerobic, freshly-dosed) HFO surface to the
# Mao & Yue in-situ site density; `hfo_site_multiplier` scales it (use ~0.3 for aged/preformed flocs).
def fresh_hfo_site_density(pH: float) -> float:
    """Return Mao--Yue effective *total* sites per mol Fe.

    This remains a compatibility alias; the value is a kinetic calibration
    surrogate and is split into weak/strong sites at the documented 40:1 ratio
    before being used by the equilibrium surface model.
    """

    return mao_yue_effective_total_sites(pH)


# --- Fresh (in-situ formed) HAO active phosphate-binding density (aluminium analog of the HFO calib) -
# Freshly precipitated amorphous aluminium hydroxide (from alum dosing) binds far more phosphate than
# aged/crystalline gibbsite the Dzombak-Morel-style ~0.2 mol/mol default assumes, so the aged value
# over-predicts the alum dose the same way it did for ferric. This is grounded on a DIRECT aluminium
# measurement: Lijklema (1980, Environ. Sci. Technol. 14(5):537) measured phosphate sorption on
# freshly-precipitated Al(OH)3 and found that when the hydroxide forms IN THE PRESENCE of phosphate
# (in-situ co-precipitation, as in coagulant dosing) the binding reaches ~1.0 mol P / mol Al at pH 7
# (a 1:1 Al-P complex), dropping to ~0.5 at pH 8, versus only ~0.2-0.35 (loading-dependent) for
# phosphate added to a pre-formed Al(OH)3 -- roughly a 3-5x fresh(in-situ)-vs-preformed difference.
# (Szabo et al. 2008's Al~=Fe chemical-P
# equivalence is consistent with this.) We therefore set the fresh in-situ HAO weak-site density to
# ~1.0 mol/mol Al. A per-mol-Al constant is appropriate because the Al amphoteric high-pH degradation
# (Lijklema's 1.0->0.5 from pH 7->8) is captured by the Gibbsite equilibrium-phase link (less Gibbsite
# precipitates at high pH -> fewer sites); variscite (AlPO4) covers the low-pH end. Reproduces Al:P
# ~2.1-2.85 for <1 mgP/L at pH 6.5-7 (Szabo optimum), degrading above pH 7.5.
_FRESH_HAO_CSS_PER_MOL_AL = 1.0  # mol P-binding sites per mol Al; Lijklema (1980) in-situ Al(OH)3 at pH 7


def fresh_hao_site_density(pH: float) -> float:
    """In-situ HAO P-binding site density (mol sites / mol Al). Constant per mol of precipitated
    Gibbsite; the pH-optimum shape comes from the Gibbsite solubility (handled by PHREEQC) + variscite.
    Grounded on Lijklema (1980) in-situ Al(OH)3 phosphate binding ~1.0 mol P/mol Al (see note above)."""
    return _FRESH_HAO_CSS_PER_MOL_AL


def _oxide_formation_ph_basis(
    *,
    target_ph: Optional[float],
    oxide_formation_ph: Optional[float],
) -> tuple[Optional[float], str]:
    """Resolve the pH used to select a fresh-oxide capacity dataset."""

    if oxide_formation_ph is not None:
        return float(oxide_formation_ph), "explicit_local_formation_ph"
    if target_ph is not None:
        return float(target_ph), "controlled_target_ph"
    return None, "unresolved"


def _resolve_oxide_surface_capacity(
    *,
    surface_name: str,
    oxide_surface_mode: str,
    target_ph: Optional[float],
    oxide_formation_ph: Optional[float],
    multiplier: float,
) -> Dict[str, Any]:
    """Return site inventories and provenance without changing site affinity ratio."""

    formation_ph, formation_ph_basis = _oxide_formation_ph_basis(
        target_ph=target_ph,
        oxide_formation_ph=oxide_formation_ph,
    )
    if surface_name == "Hfo":
        if oxide_surface_mode == "preformed_aged":
            dataset = DZOMBAK_MOREL_HFO
        else:
            if formation_ph is None:
                raise ValueError(
                    "fresh_in_situ HFO requires oxide_formation_ph when target_ph is not controlled"
                )
            dataset = mao_yue_fresh_hfo_dataset(
                formation_ph,
                formation_ph_basis=formation_ph_basis,
            )
        weak = dataset.weak_sites_mol_per_mol_host * multiplier
        strong = dataset.strong_sites_mol_per_mol_host * multiplier
        return {
            "surface_dataset_id": dataset.dataset_id,
            "surface_parameter_interpretation": dataset.parameter_interpretation,
            "oxide_history_class": dataset.oxide_history_class,
            "site_ratio_policy": "preserve_weak_to_strong_40_to_1",
            "weak_sites_mol_per_mol_host": weak,
            "strong_sites_mol_per_mol_host": strong,
            "total_sites_mol_per_mol_host": weak + strong,
            "weak_to_strong_ratio": weak / strong,
            "formation_ph": dataset.formation_ph,
            "formation_ph_basis": dataset.formation_ph_basis or "not_applicable_preformed",
            "surface_source": dataset.source,
            "site_multiplier": multiplier,
            "surface_parameter_domain": (
                None
                if oxide_surface_mode == "preformed_aged"
                else {"formation_ph_min": 6.0, "formation_ph_max": 8.0}
            ),
            "surface_parameter_in_domain": (
                None
                if oxide_surface_mode == "preformed_aged"
                else 6.0 <= float(formation_ph) <= 8.0
            ),
            "extrapolation_status": (
                "not_applicable"
                if oxide_surface_mode == "preformed_aged"
                else (
                    "within_declared_domain"
                    if 6.0 <= float(formation_ph) <= 8.0
                    else "outside_declared_domain"
                )
            ),
            "extrapolation_reasons": (
                []
                if oxide_surface_mode == "preformed_aged"
                or 6.0 <= float(formation_ph) <= 8.0
                else [
                    "fresh_HFO_formation_pH_outside_Mao_Yue_6_to_8_domain; "
                    "effective capacity is clamped to the nearest endpoint"
                ]
            ),
        }

    if surface_name == "Hao":
        if oxide_surface_mode == "fresh_in_situ" and formation_ph is None:
            raise ValueError(
                "fresh_in_situ HAO requires oxide_formation_ph when target_ph is not controlled"
            )
        # HAO is an explicitly separate calibration surrogate.  We retain the
        # 40:1 site split only as the declared current model policy; it is not
        # presented as the Dzombak--Morel HFO dataset.
        total = 0.305 if oxide_surface_mode == "preformed_aged" else fresh_hao_site_density(float(formation_ph))
        weak, strong = split_total_sites_preserving_ratio(total)
        weak *= multiplier
        strong *= multiplier
        return {
            "surface_dataset_id": (
                "hao_preformed_effective_v1"
                if oxide_surface_mode == "preformed_aged"
                else "hao_lijklema_in_situ_effective_v1"
            ),
            "surface_parameter_interpretation": "kinetic_calibration_surrogate",
            "oxide_history_class": oxide_surface_mode,
            "site_ratio_policy": "declared_weak_to_strong_40_to_1_unvalidated_for_HAO",
            "weak_sites_mol_per_mol_host": weak,
            "strong_sites_mol_per_mol_host": strong,
            "total_sites_mol_per_mol_host": weak + strong,
            "weak_to_strong_ratio": weak / strong,
            "formation_ph": formation_ph if oxide_surface_mode == "fresh_in_situ" else None,
            "formation_ph_basis": (
                formation_ph_basis
                if oxide_surface_mode == "fresh_in_situ"
                else "not_applicable_preformed"
            ),
            "surface_source": (
                "declared preformed HAO screening surrogate"
                if oxide_surface_mode == "preformed_aged"
                else "Lijklema (1980) effective in-situ Al(OH)3 capacity"
            ),
            "site_multiplier": multiplier,
            "surface_parameter_domain": None,
            "surface_parameter_in_domain": None,
            "extrapolation_status": "not_assessed",
            "extrapolation_reasons": [
                "HAO effective-capacity screening surrogate has no enforced plant matrix domain"
            ],
        }
    raise ValueError(f"unsupported oxide surface {surface_name!r}")


P_PHASE_STOICHIOMETRY = {
    "Strengite": 1.0,
    "Vivianite": 2.0,
    "Variscite": 1.0,
    "Struvite": 1.0,
    "CaHPO4:2H2O": 1.0,
    "Hydroxyapatite": 3.0,
    "Hydroxylapatite": 3.0,  # minteq.v4.dat spelling of hydroxyapatite (Ca5(PO4)3OH -> 3 P)
    "Ca4H(PO4)3:3H2O": 3.0,  # OCP-type octacalcium-phosphate precursor -> 3 P per formula
    "Ca3(PO4)2(beta)": 2.0,  # beta-tricalcium phosphate -> 2 P per formula
}


def validate_strategy_database_compatibility(
    strategy_name: str,
    phases: List[str],
    database_path: str,
    inline_phreeqc_prefix: str,
    is_aerobic: bool,
    phases_dropped_during_remap: Optional[List[str]] = None,
) -> List[str]:
    """
    Validate required phases exist in database or inline blocks.

    Returns structured warnings (not exceptions) to preserve existing
    status="input_error"/"infeasible" return semantics.

    A warning prefixed with "FATAL:" indicates the simulation cannot proceed.

    Args:
        phases_dropped_during_remap: Phases that were silently dropped because
            the database has no mapping for them (e.g. Strengite in phreeqc.dat).
            These are checked separately since they never made it into ``phases``.
    """
    import os

    db_name = os.path.basename(database_path)
    warnings: List[str] = []

    # Check which phases are actually in the database
    db_availability = check_phases_in_database(database_path, phases)

    # For phases NOT in db, check if they appear in the inline prefix
    missing_from_both = []
    for phase, in_db in db_availability.items():
        if not in_db:
            # Check inline text (e.g. "Struvite" in inline_phreeqc_prefix)
            if phase not in inline_phreeqc_prefix:
                missing_from_both.append(phase)

    # Also include phases that were dropped during iron phase remapping
    # (e.g. Strengite when phreeqc.dat maps ferric_phosphate → None)
    if phases_dropped_during_remap:
        missing_from_both.extend(phases_dropped_during_remap)

    if not missing_from_both:
        return warnings

    # Strategy-specific logic
    if strategy_name == "iron":
        # Iron needs at least a hydroxide phase (HFO substrate)
        hydroxide_phases = {"Ferrihydrite", "Fe(OH)3(a)"}
        has_hydroxide = any(p not in missing_from_both for p in phases if p in hydroxide_phases)
        if not has_hydroxide and hydroxide_phases & set(missing_from_both):
            warnings.append(
                f"FATAL: Iron hydroxide phase (Ferrihydrite/Fe(OH)3(a)) not found in "
                f"database '{db_name}' or inline blocks. Iron coagulation cannot proceed."
            )
            return warnings

        # Check Fe-P phase availability
        fe_p_phases = {"Strengite", "Vivianite"}
        missing_fep = fe_p_phases & set(missing_from_both)
        if missing_fep:
            rec = STRATEGY_DATABASE_RECOMMENDATIONS.get("iron", {})
            if is_aerobic and "Strengite" in missing_fep:
                warnings.append(
                    f"Database '{db_name}' lacks Strengite (direct Fe-P precipitate). "
                    f"P removal will rely solely on HFO surface adsorption, which may "
                    f"require higher Fe:P ratios. Recommended database: "
                    f"{rec.get('recommended', 'minteq.v4.dat')}."
                )
            if not is_aerobic and "Vivianite" in missing_fep:
                warnings.append(
                    f"FATAL: Database '{db_name}' lacks Vivianite (Fe(II)-P precipitate). "
                    f"Anaerobic Fe-P removal cannot proceed without a ferrous phosphate phase."
                )

        # Explicit sulfide phase warning for anaerobic
        sulfide_phases = {"FeS(ppt)", "Mackinawite"}
        if not is_aerobic and sulfide_phases & set(missing_from_both):
            warnings.append(
                f"Database '{db_name}' lacks iron sulfide phase (FeS(ppt)/Mackinawite). "
                f"Sulfide competition for Fe will not be modeled."
            )

        # Non-critical missing phases
        non_critical = set(missing_from_both) - hydroxide_phases - fe_p_phases - sulfide_phases
        if non_critical:
            warnings.append(f"Phases not found in '{db_name}': {sorted(non_critical)}. Simulation proceeds without them.")

    elif strategy_name == "aluminum":
        if "Variscite" in missing_from_both:
            warnings.append(
                f"Variscite not found in database '{db_name}' or inline blocks. "
                f"Al-P removal relies on HAO surface adsorption only."
            )

    elif strategy_name == "struvite":
        if "Struvite" in missing_from_both:
            warnings.append(
                f"FATAL: Struvite phase not found in database '{db_name}' or inline "
                f"blocks. Struvite crystallization cannot be modeled."
            )

    elif strategy_name == "calcium_phosphate":
        ca_p_phases = {"Ca4H(PO4)3:3H2O", "CaHPO4:2H2O"}
        missing_cap = ca_p_phases & set(missing_from_both)
        if missing_cap == ca_p_phases:
            warnings.append(
                f"FATAL: No Ca-P phases (Ca4H(PO4)3:3H2O, CaHPO4:2H2O) found in "
                f"database '{db_name}' or inline blocks."
            )
        elif missing_cap:
            warnings.append(f"Ca-P phase(s) missing from '{db_name}': {sorted(missing_cap)}.")

    return warnings


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
            "Override default phases for the strategy. For calcium_phosphate the default controls the "
            "near-term result with the metastable OCP-type phase ['Ca4H(PO4)3:3H2O', 'CaHPO4:2H2O'] "
            "(Ostwald sequence). Use ['Hydroxylapatite'] to force the long-term HAP thermodynamic floor "
            "(nucleation-inhibited; not normal-contact-time performance)."
        ),
    )
    iron_sulfide_phase_case: Optional[
        Literal["mackinawite", "amorphous_fes"]
    ] = Field(
        None,
        description=(
            "Explicit anaerobic iron-sulfide polymorph sensitivity. Use separate "
            "mackinawite and amorphous_fes calls; neither case is a plant state."
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
    parameter_contact_observation: Optional[ParameterContactObservation] = Field(
        None,
        description=(
            "Optional plant observation used only to detect contact with the selected "
            "HFO capacity parameter; it cannot identify oxide history or mechanism."
        ),
    )
    # Tuning parameters
    oxide_surface_mode: Literal["fresh_in_situ", "preformed_aged"] = Field(
        "fresh_in_situ",
        description=(
            "Whether the metal-oxide P-adsorbent is FRESH in-situ hydroxide (coagulant dosed directly "
            "into the water) or PRE-FORMED/aged oxide (e.g. recycle-stream polishing, aged floc, "
            "or oxide-media contactors). The modes select different declared surface-site datasets; "
            "they are screening assumptions, not transferable Fe:P design curves."
        ),
    )
    hfo_site_multiplier: float = Field(
        1.0,
        description="Sensitivity multiplier applied on top of the declared oxide-surface dataset. "
                    "For a deliberate fresh/aged choice use oxide_surface_mode instead.",
        ge=0.1,
        le=5.0,
    )
    target_ph: Optional[float] = Field(
        None,
        description=(
            "Operating pH to HOLD during dosing via a Fix_H+ pseudo-phase (titrated with NaOH). When "
            "set, the acidifying coagulant no longer drags pH down, so the combined precipitation + "
            "HFO/HAO adsorption chemistry is evaluated at this controlled pH (plant pH-trim) — at parity "
            "with the fixed-pH mADM1/PCM reference. Omit to let pH float from the reagent."
        ),
        ge=3.0,
        le=12.0,
    )
    oxide_formation_ph: Optional[float] = Field(
        None,
        description=(
            "Local fresh-oxide formation pH. Required for fresh_in_situ iron/aluminium "
            "when target_ph is not supplied."
        ),
        ge=3.0,
        le=12.0,
    )
    design_safety_factor: float = Field(
        1.0,
        description=(
            "Legacy-named multiplier on a target-converged equilibrium screen. The multiplied "
            "quantity is diagnostic only and is never a recommended design or operating dose."
        ),
        ge=1.0,
        le=5.0,
    )
    database: Optional[str] = Field("minteq.v4.dat", description="PHREEQC database file.")
    # Search parameters
    max_iterations: int = Field(
        30,
        description="Maximum refinement iterations after the initial dose-response scan.",
        ge=5,
        le=100,
    )
    tolerance_mg_l: float = Field(0.1, description="Convergence tolerance for P (mg/L).", gt=0)
    electron_donor: Optional[Dict[str, Any]] = Field(
        None,
        description="The anaerobic mode's stated finite donor (waterchem.contracts.phosphorus.ElectronDonorRequest).",
    )
    activity_domain_outside: Literal["refuse", "screen"] = Field(
        "refuse", description="Refuse (default) or screen a feed beyond the house activity domain (review M17)."
    )


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


class SulfideSensitivityScenario(BaseModel):
    """Result for a single sulfide concentration in the sensitivity sweep."""

    sulfide_mg_l: float = Field(..., description="Sulfide concentration for this scenario (mg/L as S).")
    status: Literal[
        "converged_target_met",
        "converged_target_not_met",
        "nonconverged",
        "infeasible",
        "solver_error",
    ] = Field(..., description="Fail-closed status of this scenario simulation.")
    optimal_dose_mmol: Optional[float] = Field(
        None, description="Optimal dose at this sulfide level (mmol/L as metal)."
    )
    optimal_dose_mg_l: Optional[float] = Field(
        None, description="Optimal dose at this sulfide level (mg/L as product)."
    )
    achieved_p_mg_l: Optional[float] = Field(None, description="Achieved residual P at this sulfide level (mg/L).")
    metal_to_p_ratio: Optional[float] = Field(None, description="Metal:P ratio at this sulfide level.")
    final_ph: Optional[float] = Field(None, description="Final pH at this sulfide level.")
    fe_consumed_by_sulfide_pct: Optional[float] = Field(
        None,
        description=(
            "Estimated percentage of Fe consumed by sulfide (FeS precipitation) "
            "rather than P removal. Higher values indicate more Fe waste."
        ),
    )
    error_message: Optional[str] = Field(None, description="Error message if simulation failed.")


class CalculatePhosphorusRemovalDoseOutput(BaseModel):
    """Output from unified phosphorus removal dose calculation."""

    status: Literal[
        "converged_target_met",
        "converged_target_not_met",
        "nonconverged",
        "infeasible",
        "solver_error",
        "input_error",
    ] = Field(
        ...,
        description=(
            "Explicit numerical/target status. Only converged_target_met means "
            "the screened target was reached within tolerance."
        ),
    )
    error_message: Optional[str] = Field(None, description="Error message if not success.")

    # Convergence tracking (NEW - Issue 3)
    converged: Optional[bool] = Field(
        None,
        description=(
            "Whether a resolved target-crossing branch converged within tolerance. "
            "False results are not dose recommendations."
        ),
    )
    target_met: Optional[bool] = Field(
        None,
        description=(
            "Whether target P concentration was achieved (achieved_p_mg_l <= target_residual_p_mg_l + tolerance). "
            "Check this to confirm the dose actually meets your target."
        ),
    )
    residual_error_mg_l: Optional[float] = Field(
        None,
        description="Absolute error from target (|achieved_p - target_p|) in mg/L.",
    )
    iterations_used: Optional[int] = Field(
        None,
        description="Number of dose evaluations/refinement iterations performed.",
    )
    dose_search_bounds: Optional[Dict[str, float]] = Field(
        None,
        description="Final search bounds {dose_low_mmol, dose_high_mmol}. Useful for debugging.",
    )
    bracket_resolved: bool = Field(False, description="Whether a physical target-crossing bracket was resolved.")
    dose_response_monotonic: Optional[bool] = Field(
        None, description="Whether the initial scan was monotone non-increasing within tolerance."
    )
    monotonicity_resolved: bool = Field(
        False, description="False when failed scan points leave monotonicity unresolved."
    )
    dose_response_branches: Optional[List[Dict[str, Any]]] = Field(
        None, description="Piecewise target-crossing branches discovered by the initial scan."
    )
    solver_errors: Optional[List[Dict[str, Any]]] = Field(
        None, description="Classified solver/parser failures. These never change physical dose bounds."
    )

    # Optimal dose
    optimal_dose_mmol: Optional[float] = Field(None, description="Optimal reagent dose (mmol/L as metal).")
    optimal_dose_mg_l: Optional[float] = Field(None, description="Optimal reagent dose (mg/L as product).")
    achieved_p_mg_l: Optional[float] = Field(None, description="Achieved residual P at optimal dose (mg/L).")
    metal_to_p_ratio: Optional[float] = Field(None, description="Molar metal:P ratio at optimal dose.")
    best_evaluated_dose_mmol: Optional[float] = Field(
        None, description="Closest evaluated point when no target-converged dose exists; diagnostic only."
    )
    best_evaluated_residual_p_mg_l: Optional[float] = Field(
        None, description="Residual at the diagnostic best-evaluated point."
    )
    mass_balance_passed: Optional[bool] = Field(
        None, description="Independent mass-balance gate; None means it was not established."
    )
    p_mass_balance_error_mg_l: Optional[float] = Field(
        None, description="Absolute reactive-P partition closure error at the selected screen point."
    )

    # Final state
    final_ph: Optional[float] = Field(None, description="Final pH after treatment.")
    final_pe: Optional[float] = Field(None, description="Final pe after treatment.")

    # Strategy info
    strategy_used: str = Field(..., description="Strategy that was used.")
    reagent_used: str = Field(..., description="Reagent formula that was used.")
    inline_blocks_added: Optional[List[str]] = Field(None, description="Inline PHREEQC blocks that were added.")

    # Precipitation breakdown
    precipitated_phases: Optional[Dict[str, float]] = Field(None, description="Precipitated phases and amounts (mmol).")

    # Partitioning outputs (Issue 5/11)
    phase_moles_mmol_per_L: Optional[Dict[str, float]] = Field(
        None,
        description=(
            "Normalized phase moles (mmol/L) for mass balance. " "Derived from USER_PUNCH EQUI() output on 1 kgw basis."
        ),
    )
    p_removed_by_phase_mg_L: Optional[Dict[str, float]] = Field(
        None,
        description=(
            "P removed by each precipitated phase (mg/L as P). " "Calculated from phase moles and P stoichiometry."
        ),
    )
    p_adsorbed_mg_L: Optional[float] = Field(
        None,
        description=(
            "P adsorbed on HFO/HAO surface (mg/L). "
            "Only populated for iron/aluminum strategies with surface complexation."
        ),
    )
    p_dissolved_mg_L: Optional[float] = Field(
        None,
        description="Dissolved P remaining in solution (mg/L). Should equal achieved_p_mg_l.",
    )

    # Dose-response curve (optional)
    dose_response_curve: Optional[List[PhosphorusRemovalScenario]] = Field(
        None, description="Dose-response curve data points for plotting."
    )

    # Redox diagnostics
    redox_diagnostics: Optional[RedoxDiagnostics] = Field(
        None, description="Detailed redox constraint and pe diagnostics."
    )

    # Sulfide sensitivity sweep results (Issue 1/9)
    sulfide_sensitivity_results: Optional[List[SulfideSensitivityScenario]] = Field(
        None,
        description=(
            "Results from sulfide sensitivity sweep at [0, 20, 50, 100] mg/L S(-2). "
            "Only populated when sulfide_sensitivity=true for anaerobic iron strategy."
        ),
    )
    design_scenario_sulfide_mg_l: Optional[float] = Field(
        None,
        description=(
            "Measured sulfide level used for a primary result. Assumed sensitivity cases do not "
            "populate this field or nominate any scenario as the plant state."
        ),
    )
    screening_dose_with_factor_mmol: Optional[float] = Field(
        None, description="Diagnostic equilibrium-screen dose multiplied by design_safety_factor; not a recommendation."
    )
    screening_dose_with_factor_mg_l: Optional[float] = Field(
        None, description="Product-basis diagnostic screen dose with factor; not a recommendation."
    )

    calculation_class: Literal["equilibrium_screen"] = "equilibrium_screen"
    recommendation_eligible: Literal[False] = False

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
            "aerobic": ["Ca4H(PO4)3:3H2O", "CaHPO4:2H2O"],  # metastable OCP-type + brushite (near-term)
            "anaerobic": ["Ca4H(PO4)3:3H2O", "CaHPO4:2H2O"],
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
        result: PHREEQC result of the DOSED solution with the checked phases withheld from the
            equilibrium (their supersaturation). Never the equilibrium run that includes them: a
            phase that precipitated there sits at SI = 0 and would always read as metastable.
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


def _si_trigger_detail(
    result: Dict[str, Any],
    phases_to_check: List[str],
    threshold: Optional[float],
    triggered: bool,
) -> Dict[str, Any]:
    """Build public SI-trigger diagnostics for metastability decisions."""

    if not threshold or threshold <= 0:
        return {"triggered": False}
    saturation_indices = result.get("saturation_indices", {})
    phase_details = [
        (phase, saturation_indices.get(phase))
        for phase in phases_to_check
        if saturation_indices.get(phase) is not None
    ]
    if not phase_details:
        return {"triggered": False, "threshold": threshold}
    phase, si_value = min(phase_details, key=lambda item: item[1])
    return {
        "triggered": triggered,
        "phase": phase,
        "saturation_index": si_value,
        "threshold": threshold,
        "message": (
            f"{phase} dosed-solution SI {si_value:.3g} is below trigger {threshold:g}; "
            "precipitation may need seeding."
            if triggered
            else f"No primary P phase was below SI trigger {threshold:g}."
        ),
    }


def _phase_role(phase: str, strategy_name: str) -> Tuple[str, str]:
    """Return a phase family and public role label."""

    if phase == "Struvite":
        return "struvite", "P recovery precipitate"
    if phase in {"Strengite", "Vivianite"}:
        return "Fe-P", "primary iron phosphate precipitate"
    if phase in {"Ferrihydrite", "Fe(OH)3(a)", "Goethite", "Fe(OH)3"}:
        return "iron_hydroxide", "surface adsorbent / iron sink"
    if phase == "Variscite":
        return "Al-P", "primary aluminum phosphate precipitate"
    if phase in {"Gibbsite", "Al(OH)3(a)"}:
        return "aluminum_hydroxide", "surface adsorbent / aluminum sink"
    if phase in {"Ca4H(PO4)3:3H2O", "Ca3(PO4)2(beta)", "CaHPO4:2H2O", "Hydroxyapatite", "Hydroxylapatite"}:
        return "calcium_phosphate", "primary calcium phosphate precipitate"
    if phase == "Calcite":
        return "carbonate", "competing calcium carbonate sink"
    if "FeS" in phase or "Siderite" in phase:
        return "iron_competition", "competing anaerobic iron sink"
    return strategy_name, "supporting equilibrium phase"


def _phase_roles(
    phases: List[str],
    strategy_name: str,
    final_state: Optional[Dict[str, Any]],
    partitioning_data: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build phase-role output with optional activity and P-removal data."""

    saturation_indices = (final_state or {}).get("saturation_indices", {})
    precipitated = (final_state or {}).get("precipitated_phases", {})
    p_removed = (partitioning_data or {}).get("p_removed_by_phase_mg_L") or {}
    roles = []
    for phase in phases:
        family, role = _phase_role(phase, strategy_name)
        removed = p_removed.get(phase)
        roles.append(
            {
                "name": phase,
                "family": family,
                "role": role,
                "active": bool((precipitated.get(phase) or 0) > 0 or (removed or 0) > 0),
                "p_removed_mg_l": removed,
                "saturation_index": saturation_indices.get(phase),
            }
        )
    return roles


def _p_removed_by_phase_from_result(
    phases: List[str], result: Dict[str, Any], feed_litres_per_system: float = 1.0
) -> Dict[str, float]:
    """Estimate P in solid phases (mg per litre of feed) from the system's equilibrium phase moles."""

    phase_moles = result.get("precipitated_phases", {}) or result.get("equilibrium_phase_moles", {})
    removed = {}
    for phase in phases:
        moles = phase_moles.get(phase, 0.0) or 0.0
        stoich = P_PHASE_STOICHIOMETRY.get(phase, 0.0)
        if moles > 0 and stoich > 0:
            removed[phase] = moles / feed_litres_per_system * 1000.0 * stoich * MOLECULAR_WEIGHTS["P"]
    return removed


def _mass_partition_point(
    *,
    iteration: int,
    dose_mmol: float,
    initial_p_mg_l: float,
    p_solution_mg_l: float,
    phases: List[str],
    result: Dict[str, Any],
    feed_litres_per_system: float = 1.0,
) -> Dict[str, Any]:
    """Build public P mass partition for one search iteration."""

    p_removed_by_phase = _p_removed_by_phase_from_result(phases, result, feed_litres_per_system)
    p_solid_total = max(initial_p_mg_l - p_solution_mg_l, 0.0)
    return {
        "iteration": iteration,
        "dose_mmol": dose_mmol,
        "p_solution_mg_l": p_solution_mg_l,
        "p_solid_total_mg_l": p_solid_total,
        "p_adsorbed_mg_l": None,
        "p_removed_by_phase_mg_l": p_removed_by_phase,
    }


def _batch_redox_warning(
    strategy_name: str,
    redox_mode: str,
    pe_value: float,
    held_states: Tuple[str, ...],
    finite_donor: Optional[Dict[str, Any]] = None,
) -> str:
    """The warning a result carries on what its dose equilibria hold, per mode."""
    if finite_donor is not None:
        donor = finite_donor["compound"]
        return (
            f"Ammonium, nitrate, sulfate and sulfide are held on redox-uncoupled elements (Amm, Nit, Sulfat, Sulf) at "
            f"the fixed pe {pe_value:g}, so the equilibrium moves no N or S valence state. They move only by the "
            f"stated donor ({donor.name}, {donor.formula_text}, {finite_donor['available_cod_mg_l']:g} mg COD/L, "
            f"{finite_donor['basis']}: {finite_donor['source']}), which each dose run's REACTION carries: the feed's "
            "nitrate to N2, the ferric iron to ferrous, then the sulfate (feed and reagent) to sulfide, each as far as "
            "the donor reaches, its carbon to the water, its cells out, its COD and alkalinity booked on the result "
            f"({FINITE_DONOR_RULING_ID}). Each dose run proves the fixed pe supplied no electron the donor did not, "
            "and is checked for any other N or S move. The donor left is NOT oxidised: methanogenesis is outside the "
            "screen."
        )
    if "S(+6)" in held_states:
        return (
            f"Ammonium, nitrate, sulfate and sulfide are held as fed at the fixed pe {pe_value:g}: the dose equilibria carry them on redox-uncoupled elements (Amm, Nit, Sulfat, Sulf; a sulfate reagent's sulfate on Sulfat, phases and surface species re-defined on them), so only the metals' redox equilibrates, and each dose run is checked for it (PUBLIC-RULE-1876369d9d6f; full_redox held the same way, PUBLIC-RULE-bfdea5a72210). The fixed pe supplies the electrons the metals take with no donor behind them; the result says how many (fixed_pe_boundary)."
        )
    return (
        f'Ammonium, nitrate and sulfide are held as fed: the dose equilibria carry them on redox-uncoupled elements (Amm, Nit, Sulf; struvite re-defined on AmmH+), so the {('floating-pe' if _pe_constraint(strategy_name, redox_mode, pe_value) is None else 'O2(g)')} equilibrium moves no N or S valence state, and each dose run is checked for it (PUBLIC-RULE-75274692f42e). PHREEQC totals report them under Amm, Nit and Sulf.'
    )


def _strategy_assumptions(
    input_model: "CalculatePhosphorusRemovalDoseInput",
    strategy_name: str,
    reagent: str,
    database: str,
    redox: "RedoxSpecification",
    target_basis: str,
) -> Dict[str, Any]:
    """Build explicit public assumptions for the selected P-removal strategy."""

    assumptions = {
        "strategy": strategy_name,
        "description": STRATEGY_CONFIG.get(strategy_name, {}).get("description"),
        "reagent": reagent,
        "target_residual_p_mg_l": input_model.target_residual_p_mg_l,
        "target_basis": target_basis,
        "p_inert_soluble_mg_l": input_model.p_inert_soluble_mg_l,
        "max_dose_mmol": input_model.strategy.max_dose_mmol,
        "tolerance_mg_l": input_model.tolerance_mg_l,
        "redox_mode": redox.mode,
        "database": redact_paths(database),
        "include_background_sinks": input_model.include_background_sinks,
        "iron_sulfide_phase_case": (
            input_model.strategy.iron_sulfide_phase_case or "database_default"
        ),
        "oxide_surface_mode": input_model.oxide_surface_mode,
        "oxide_formation_ph": input_model.oxide_formation_ph,
        "batch_redox": _batch_redox_label(strategy_name, redox.mode),
    }
    donor = _donor_spec(input_model) if _uses_finite_donor(strategy_name, redox.mode) else None
    if donor is not None:
        from puran_mcp_support.engineering.finite_donor_redox import ACCEPTOR_SEQUENCE, SOURCES

        compound = donor["compound"]
        assumptions["finite_donor"] = {
            "status": "STATED",
            "donor": compound.name,
            "formula": compound.formula_text,
            "electrons_per_mol": compound.electrons_per_mol,
            "available_cod_mg_l": donor["available_cod_mg_l"],
            "basis": donor["basis"],
            "source": donor["source"],
            "cell_synthesis_fraction": donor["cell_synthesis_fraction"],
            "acceptor_sequence": list(ACCEPTOR_SEQUENCE),
            "ruling_id": FINITE_DONOR_RULING_ID,
            "sources": dict(SOURCES),
        }
    surface_name = STRATEGY_CONFIG.get(strategy_name, {}).get("surface_name")
    if surface_name in {"Hfo", "Hao"} and redox.mode in {"aerobic", "full_redox"}:
        assumptions.update(
            _resolve_oxide_surface_capacity(
                surface_name=surface_name,
                oxide_surface_mode=input_model.oxide_surface_mode,
                target_ph=input_model.target_ph,
                oxide_formation_ph=input_model.oxide_formation_ph,
                multiplier=input_model.hfo_site_multiplier,
            )
        )
        assumptions["mechanism_identified"] = False
        assumptions["surface_capacity_is_mechanistic_validation"] = False
        assumptions["oxide_history_id"] = (
            f"screening_scenario:{assumptions['oxide_history_class']}:"
            f"{assumptions['surface_dataset_id']}"
        )
        assumptions["effective_active_fe_fraction"] = None
        assumptions["active_metal_fraction_basis"] = "not_identified_by_equilibrium_screen"
        assumptions["parameter_contact"] = {
            "status": "not_assessed",
            "parameter_contact": None,
            "mechanism_identified": False,
        }
        observation = input_model.parameter_contact_observation
        if surface_name == "Hfo" and observation is not None:
            assessment = assess_hfo_capacity_parameter_contact(
                delivered_fe_mg_l=observation.delivered_fe_mg_l,
                influent_p_mg_l=observation.influent_p_mg_l,
                residual_p_mg_l=observation.residual_p_mg_l,
                assumed_capacity_mol_p_per_mol_fe=assumptions[
                    "total_sites_mol_per_mol_host"
                ],
                relative_contact_tolerance=observation.relative_contact_tolerance,
            )
            assumptions["parameter_contact"] = {
                "status": "assessed",
                "evidence_id": observation.evidence_id,
                "p_measurement_basis": observation.p_measurement_basis,
                "observation_port": observation.observation_port,
                **assessment.public_dict(),
                "interpretation": (
                    "Numerical contact with the selected capacity parameter is not "
                    "independent evidence of oxide history or mechanism."
                ),
            }
        assumptions["ligand_model_id"] = "none_unvalidated"
        assumptions["smp_or_doc_basis"] = "not_supplied"
    return assumptions


def _assumption_ledger(
    *,
    input_model: "CalculatePhosphorusRemovalDoseInput",
    strategy_name: str,
    database: str,
    redox: "RedoxSpecification",
    phases: List[str],
    strategy_assumptions: Dict[str, Any],
    provenance: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the cross-engine interpretation ledger for an equilibrium screen."""

    analysis = input_model.initial_solution.analysis
    if "S(-2)" in analysis:
        sulfide_source = "initial_solution_analytical_S(-2)"
    elif strategy_name == "iron" and redox.mode == "anaerobic":
        sulfide_source = "unmeasured_assumed_sensitivity_sweep"
    else:
        sulfide_source = "not_applicable_or_not_supplied"
    phase_set = ",".join(sorted(phases)) or "none"
    surface_dataset_id = strategy_assumptions.get("surface_dataset_id")
    oxide_history_class = strategy_assumptions.get("oxide_history_class", "unresolved")
    oxide_history_id = strategy_assumptions.get(
        "oxide_history_id", f"screening_scenario:{oxide_history_class}"
    )
    return AssumptionLedger(
        target_variable="dissolved_reactive_p",
        thermodynamic_dataset_id=database,
        database_hash=str(provenance["database_hash"]),
        phase_definition_set_id=f"explicit_screen_phase_set:{phase_set}",
        activity_model_id="phreeqc_database_defined",
        redox_method=f"phreeqc_boundary:{redox.mode}",
        sulfide_source=sulfide_source,
        iron_sulfide_phase_case=strategy_assumptions.get(
            "iron_sulfide_phase_case", "not_applicable"
        ),
        chemistry_treatment="single_step_equilibrium_screen",
        solids_inventory_id=None,
        mbr_retention_profile_id=None,
        calibration_domain_id=None,
        ligand_model_id=strategy_assumptions.get("ligand_model_id", "none_unvalidated"),
        model_commit=str(provenance["model_commit"]),
        input_hash=str(provenance["input_hash"]),
        surface_dataset_id=surface_dataset_id,
        surface_parameter_interpretation=strategy_assumptions.get(
            "surface_parameter_interpretation"
        ),
        oxide_history_class=oxide_history_class,
        oxide_history_id=oxide_history_id,
        oxide_formation_ph=strategy_assumptions.get("formation_ph"),
        oxide_formation_ph_basis=strategy_assumptions.get(
            "formation_ph_basis", "not_applicable"
        ),
        active_metal_fraction_basis=strategy_assumptions.get(
            "active_metal_fraction_basis", "not_identified"
        ),
        effective_active_fe_fraction=None,
        parameter_contact=strategy_assumptions.get("parameter_contact", {}).get(
            "parameter_contact"
        ),
        parameter_contact_evidence_id=strategy_assumptions.get(
            "parameter_contact", {}
        ).get("evidence_id"),
        mechanism_identified=False,
        smp_or_doc_basis=strategy_assumptions.get("smp_or_doc_basis", "not_supplied"),
        extrapolation_status=strategy_assumptions.get(
            "extrapolation_status", "not_assessed"
        ),
        extrapolation_reasons=tuple(
            strategy_assumptions.get("extrapolation_reasons", ())
        ),
    ).public_dict()


def _simulation_provenance(
    *,
    input_model: Optional["CalculatePhosphorusRemovalDoseInput"],
    database_path: Optional[str],
    phases: List[str],
) -> Dict[str, Any]:
    """Return non-empirical provenance without exposing local database paths."""

    database_hash = "unavailable"
    if database_path:
        try:
            database_hash = hashlib.sha256(Path(database_path).read_bytes()).hexdigest()
        except OSError:
            database_hash = "unavailable"
    input_hash = "unavailable"
    if input_model is not None:
        canonical_input = json.dumps(
            input_model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        input_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    phase_manifest = json.dumps(sorted(phases), separators=(",", ":"))
    return {
        "data_class": "simulated",
        "not_empirical": True,
        "site_calibrated": False,
        "model_commit": (
            os.environ.get("PURANOS_MODEL_COMMIT")
            or os.environ.get("GIT_COMMIT_SHA")
            or "unknown"
        ),
        "database_hash": database_hash,
        "input_hash": input_hash,
        "phase_set_hash": hashlib.sha256(phase_manifest.encode("utf-8")).hexdigest(),
    }


def _house_reagent_id(reagent: str) -> Optional[str]:
    """The house reagent catalogue's grade of ``reagent`` (by its active compound), or None: read from
    engineering_utils.house_standards.reagent_catalog.REAGENT_GRADES, never a list kept here."""
    from puran_mcp_support.engineering.house_standards.reagent_catalog import REAGENT_GRADES

    for grade in REAGENT_GRADES.values():
        if grade.active_compound == reagent:
            return grade.reagent_id
    return None


def _stamp_reagent_catalogue(payload: Dict[str, Any], reagent: str) -> Dict[str, Any]:
    'A reagent the catalogue has no grade of (FeSO4, FeCl2, ...) stays available for screening, but the result says so:\n    sizing reads the reagent catalogue only, and the house coagulant is its COAGULANT_IDENTITY.'
    from puran_mcp_support.engineering.house_standards.reagent_catalog import COAGULANT_IDENTITY

    house_id = _house_reagent_id(reagent)
    payload["not_a_house_reagent"] = house_id is None
    payload["house_reagent_id"] = house_id
    if house_id is None:
        warning = (
            f"{reagent} is not a house reagent: the house reagent catalogue "
            "(engineering_utils.house_standards.reagent_catalog) has no grade of it, and the house coagulant "
            f"is {COAGULANT_IDENTITY}. This result is a screening comparison only; sizing reads the reagent "
            f"catalogue ({NONCATALOGUE_REAGENT_RULING_ID})."
        )
        warnings = list(payload.get("warnings") or [])
        if warning not in warnings:
            warnings.append(warning)
        payload["warnings"] = warnings
    return payload


def _activity_record(
    database_path: Optional[str], feed_ionic_strength: Optional[float], selected_ionic_strength: Optional[float]
) -> Dict[str, Any]:
    """The activity model the screen ran and whether the waters it answered on are inside its qualified ionic strength
    (`engineering_utils.house_standards.activity_models`, review M17)."""
    from puran_mcp_support.engineering.house_standards.activity_models import ION_ASSOCIATION_QUALIFIED_IONIC_STRENGTH_MOL_KGW as LIMIT, ION_ASSOCIATION_QUALIFIED_SOURCE

    readings = [value for value in (feed_ionic_strength, selected_ionic_strength) if value is not None]
    return {
        "database": os.path.basename(database_path) if database_path else None,
        "model": "ion association (Davies / extended Debye-Hueckel)",
        "qualified_to_mol_kgw": LIMIT,
        "source": ION_ASSOCIATION_QUALIFIED_SOURCE,
        "feed_ionic_strength_mol_kgw": feed_ionic_strength,
        "selected_dose_ionic_strength_mol_kgw": selected_ionic_strength,
        "in_domain": bool(readings) and all(value <= LIMIT for value in readings),
    }


def _qualification(
    *,
    input_model: Optional["CalculatePhosphorusRemovalDoseInput"],
    strategy_name: str,
    database_path: Optional[str],
    redox: "RedoxSpecification",
    phases: List[str],
    final_state: Optional[Dict[str, Any]],
    strategy_assumptions: Dict[str, Any],
    feed_ionic_strength: Optional[float],
) -> Dict[str, Any]:
    'Public engineering calculation interface.'
    from water_chemistry_mcp.waterchem.contracts.phosphorus import _default_qualification
    from water_chemistry_mcp.waterchem.defined_analytes import database_names

    record = _default_qualification()
    final_state = final_state or {}
    record["activity_model"] = _activity_record(
        database_path, feed_ionic_strength, final_state.get("ionic_strength_mol_kgw"))
    methane = "not applicable (no fixed pe)"
    if _pe_constraint(strategy_name, redox.mode, 0.0) and _pe_constraint(strategy_name, redox.mode, 0.0).get(
            "method") == "fix_pe" and database_path:
        methane = (
            f"not in this chemistry: {os.path.basename(database_path)} defines no C(-4), so no CH4 can form from "
            "inorganic carbon at the fixed pe; a database that defines it refuses (P2-2). The database's choice, not a "
            "validated reaction balance, keeps methane out; a stated donor's remainder is not oxidised either."
            if "C(-4)" not in database_names(database_path) else
            "the database defines C(-4); the run's redox check refuses any CH4 it makes (P2-2)"
        )
    record["phases"] = {
        "considered": list(phases),
        "basis": "a phase not in this list cannot form in this answer; each is the database's own or a declared "
                 "same phase (phase_substitutions)",
        "redox_held_as_fed": list(_held_redox_states(strategy_name, redox.mode)),
        "methanogenesis": methane,
    }
    surface_name = STRATEGY_CONFIG.get(strategy_name, {}).get("surface_name")
    host = _surface_host_phase(surface_name, phases)
    if surface_name and host:
        analysis = (input_model.initial_solution.analysis if input_model else {}) or {}
        temperature = float(getattr(input_model.initial_solution, "temperature_celsius", 25.0) or 25.0) \
            if input_model else 25.0
        outside = []
        if abs(temperature - 25.0) > 1e-9:
            outside.append(f"temperature: the surface constants are log K at 25 C with no temperature term (the "
                           f"database writes delta_h 0 or none); this water is at {temperature:g} C")
        sulfide = analysis.get("S(-2)")
        if sulfide not in (None, 0, 0.0, "0"):
            outside.append("sulfide: the oxide surface's constants are for sulfide-free water; reductive dissolution "
                           "and FeS coating of the oxide are not modelled")
        outside.append("organic ligand: no DOC/humic ligand model (ligand_model_id none_unvalidated); organic "
                       "competition for sites and metal complexation are not in the answer")
        if record["activity_model"]["in_domain"] is False:
            outside.append("ionic strength: the diffuse-layer constants are read beyond the activity domain")
        record["surface"] = {
            "model": f"{surface_name} on {host}, diffuse layer (Dzombak & Morel 1990 constants)",
            "site_inventory_basis": "assumed",
            "site_inventory_dataset": strategy_assumptions.get("surface_dataset_id"),
            "measured_site_inventory": None,
            "parameter_temperature_c": 25.0,
            "run_temperature_c": temperature,
            "outside_parameter_basis": outside,
        }
    if final_state.get("fixed_pe_boundary"):
        record["redox_boundary"] = final_state["fixed_pe_boundary"]
    return record


async def _feed_ionic_strength(initial_solution: Dict[str, Any], inline_prefix: str, database_path: str) -> float:
    """The feed's ionic strength, mol/kgw, on the run's database (the activity-domain gate, review M17)."""
    from water_chemistry_mcp.waterchem.phreeqc_input import build_solution_block

    text = "\n".join(part for part in (
        inline_prefix, build_solution_block(initial_solution, solution_num=1),
        "SELECTED_OUTPUT 1\n    -reset false\n    -mu true\n    -water true") if part) + "\nEND\n"
    result = await run_phreeqc_simulation(text, database_path)
    if isinstance(result, list):
        result = result[-1] if result else {}
    if is_error_payload(result):
        raise MissingSimulationOutputError(f"feed speciation failed: {error_payload_message(result)}")
    value = _run_ionic_strength(result)
    if value is None:
        raise MissingSimulationOutputError("feed speciation omitted the ionic strength; the activity domain cannot be read")
    return value


def _run_ionic_strength(result: Dict[str, Any]) -> Optional[float]:
    selected = result.get("selected_output_data") or {}
    summary = result.get("solution_summary") or {}
    for value in (selected.get("mu"), summary.get("ionic_strength_molal"), summary.get("ionic_strength")):
        if value is not None:
            return float(value)
    return None


def _finalize_phosphorus_result(
    payload: Dict[str, Any],
    *,
    input_model: Optional["CalculatePhosphorusRemovalDoseInput"] = None,
    strategy_name: str,
    reagent: str,
    database: str = "minteq.v4.dat",
    database_path: Optional[str] = None,
    redox: Optional["RedoxSpecification"] = None,
    phases: Optional[List[str]] = None,
    final_state: Optional[Dict[str, Any]] = None,
    partitioning_data: Optional[Dict[str, Any]] = None,
    mass_partition: Optional[List[Dict[str, Any]]] = None,
    si_trigger: Optional[Dict[str, Any]] = None,
    feed_ionic_strength: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate and enrich P-removal output through the phosphorus contract."""

    public_payload = dict(payload)
    warnings = public_payload.get("warnings") or []
    target_basis = "reactive_dissolved_p_as_P"
    if input_model and input_model.p_inert_soluble_mg_l > 0:
        target_basis = "reactive_dissolved_p_plus_fixed_inert_offset_as_P"

    if input_model:
        public_payload.setdefault(
            "strategy_assumptions",
            _strategy_assumptions(input_model, strategy_name, reagent, database, redox or RedoxSpecification(mode="aerobic"), target_basis),
        )
    else:
        public_payload.setdefault("strategy_assumptions", {"strategy": strategy_name, "reagent": reagent})
    public_payload.setdefault("target_basis", target_basis)
    public_payload.setdefault("target_variable", "dissolved_reactive_p")
    public_payload.setdefault("calculation_class", "equilibrium_screen")
    public_payload["recommendation_eligible"] = False
    # An equilibrium screen is never an autonomous plant recommendation. Drop
    # legacy recommendation fields even if an older internal call populated
    # them, so downstream agents cannot confuse a safety-factor scenario with
    # an operational setpoint.
    public_payload.pop("recommended_design_dose_mmol", None)
    public_payload.pop("recommended_design_dose_mg_l", None)
    public_payload.pop("recommended_design_basis", None)
    public_payload.setdefault("phases_involved", _phase_roles(phases or [], strategy_name, final_state, partitioning_data))
    provenance = _simulation_provenance(
        input_model=input_model,
        database_path=database_path,
        phases=phases or [],
    )
    public_payload.setdefault("simulation_provenance", provenance)
    if input_model:
        public_payload.setdefault(
            "assumption_ledger",
            _assumption_ledger(
                input_model=input_model,
                strategy_name=strategy_name,
                database=database,
                redox=redox or RedoxSpecification(mode="aerobic"),
                phases=phases or [],
                strategy_assumptions=public_payload["strategy_assumptions"],
                provenance=provenance,
            ),
        )

    tolerance_met = bool(public_payload.get("target_met") or False)
    public_payload.setdefault("tolerance_met", tolerance_met)
    public_payload.setdefault(
        "convergence",
        {
            "converged": bool(public_payload.get("converged") or False),
            "iterations_used": int(public_payload.get("iterations_used") or 0),
            "tolerance_met": tolerance_met,
            "tolerance_mg_l": input_model.tolerance_mg_l if input_model else None,
            "residual_error_mg_l": public_payload.get("residual_error_mg_l"),
        },
    )
    public_payload.setdefault("mass_partition", mass_partition or [])
    public_payload.setdefault("si_trigger", si_trigger or {"triggered": False})
    public_payload["warnings"] = warnings
    _stamp_reagent_catalogue(public_payload, reagent)
    public_payload["qualification"] = _qualification(
        input_model=input_model,
        strategy_name=strategy_name,
        database_path=database_path,
        redox=redox or RedoxSpecification(mode="aerobic"),
        phases=phases or [],
        final_state=final_state,
        strategy_assumptions=public_payload.get("strategy_assumptions") or {},
        feed_ionic_strength=feed_ionic_strength,
    )

    return PhosphorusRemovalResult.model_validate(public_payload).model_dump(exclude_none=True)


def _phosphorus_public_error(
    message: str,
    *,
    code: str = "INVALID_INPUT",
    strategy_name: str = "unknown",
    reagent: str = "unknown",
    stage: str = "validate_input",
) -> Dict[str, Any]:
    error = public_error(
        code,
        message,
        {
            "tool_name": "screen_equilibrium_phosphorus_removal",
            "stage": stage,
            "strategy": strategy_name,
            "reagent": reagent,
        },
    )
    status = {
        "DATABASE_RESOLUTION_ERROR": "solver_error",
        "PHREEQC_EXECUTION_ERROR": "solver_error",
        "INFEASIBLE_TARGET": "infeasible",
    }.get(code, "input_error")
    payload = {
        **error,
        "status": status,
        "error_message": error["message"],
        "strategy_used": strategy_name,
        "reagent_used": reagent,
        "calculation_class": "equilibrium_screen",
        "recommendation_eligible": False,
        "converged": False,
        "target_met": False,
        "mass_balance_passed": False,
        "bracket_resolved": False,
    }
    if reagent != "unknown":
        _stamp_reagent_catalogue(payload, reagent)
    return PhosphorusRemovalResult.model_validate(payload).model_dump(exclude_none=True)


def _build_redox_diagnostics(
    redox: "RedoxSpecification",
    target_pe: float,
    achieved_pe: float,
) -> RedoxDiagnostics:
    """Build redox diagnostics for output.

    Args:
        redox: RedoxSpecification model with mode information
        target_pe: Target pe value used for simulation (may be nominal for O2 equilibrium)
        achieved_pe: Actual pe from PHREEQC result

    Returns:
        RedoxDiagnostics model

    Notes:
        For aerobic mode (O2 equilibrium), target_pe is NOT a constraint - the pe is
        determined dynamically by O2(g) equilibrium. We report achieved_pe but pe_drift
        is not meaningful since there's no fixed target.

        For anaerobic/fixed_pe/pe_from_orp modes, pe is constrained via Fix_pe pseudo-phase
        and pe_drift shows how well the constraint was maintained.
    """
    # Determine constraint type, control variable, and pO2 from mode (Issue 4 fix)
    if redox.mode == "aerobic":
        # Aerobic: O2 equilibrium controls pe dynamically
        # target_pe is just a nominal value, not an actual constraint
        constraint_type = "o2_equilibrium"
        control_variable = "pO2"  # pO2 is constrained, pe floats
        target_pO2 = 0.21  # Atmospheric O2 partial pressure
        constraint_blocks = ["O2(g)"]
        # For O2 equilibrium, pe_drift is not meaningful - pe floats with O2
        pe_drift = None
        # Report nominal target_pe but clarify it's not a constraint
        effective_target_pe = None  # No fixed target for O2 equilibrium
    elif redox.mode in ("anaerobic", "fixed_pe", "pe_from_orp"):
        # Fixed pe constraint via pseudo-phase
        constraint_type = "fix_pe"
        control_variable = "pe"  # pe is constrained
        target_pO2 = None  # Not applicable
        constraint_blocks = ["Fix_pe"]
        effective_target_pe = target_pe
        pe_drift = abs(achieved_pe - target_pe) if achieved_pe is not None else None
    else:
        # No explicit constraint (e.g., fixed_fe2_fraction experimental mode)
        constraint_type = "none"
        control_variable = None
        target_pO2 = None
        constraint_blocks = []
        effective_target_pe = None
        pe_drift = None

    # Calculate ORP equivalents (at 25°C)
    # For aerobic mode, only report achieved ORP (target is not meaningful)
    if effective_target_pe is not None:
        target_orp = pe_to_orp(effective_target_pe, 25.0)
    else:
        target_orp = None

    achieved_orp = pe_to_orp(achieved_pe, 25.0) if achieved_pe is not None else None

    return RedoxDiagnostics(
        redox_constraint_type=constraint_type,
        redox_control_variable=control_variable,  # Issue 4 fix: clarify what's constrained
        target_pO2_atm=target_pO2,  # Issue 4 fix: report pO2 for O2 equilibrium mode
        target_pe=effective_target_pe,  # None for O2 equilibrium mode
        achieved_pe=achieved_pe if achieved_pe is not None else target_pe,
        pe_drift=pe_drift,
        target_orp_mV_vs_SHE=target_orp,
        achieved_orp_mV_vs_SHE=achieved_orp,
        constraint_blocks_used=constraint_blocks if constraint_blocks else None,
    )


# =============================================================================
# SULFIDE SENSITIVITY SWEEP (Issue 1/9)
# =============================================================================


async def _run_sulfide_sensitivity_sweep(
    input_model: "CalculatePhosphorusRemovalDoseInput",
    reagent_info: Dict[str, Any],
    strategy_config: Dict[str, Any],
    database_path: str,
    inline_phreeqc_prefix: str,
    warnings: List[str],
) -> Tuple[
    List[SulfideSensitivityScenario],  # sweep results
    str,  # diagnostic-envelope explanation
]:
    """
    Run sulfide sensitivity sweep at [0, 20, 50, 100] mg/L S(-2).

    For anaerobic iron dosing without explicit sulfide input, this sweep shows
    how Fe:P ratio varies with digester sulfide levels. FeS precipitation competes
    with P removal, so higher sulfide = higher Fe dose required.

    Returns four diagnostic scenarios. No assumed sulfide point is promoted to
    a plant state and no maximum is promoted to a design dose.
    """
    SULFIDE_LEVELS = [0, 20, 50, 100]  # mg/L as S
    # These are explicitly assumed sensitivity cases, not a measured plant
    # sulfur inventory and not a design-scenario selector.

    results: List[SulfideSensitivityScenario] = []
    max_dose = 0.0
    max_dose_sulfide = 0.0

    strategy_spec = input_model.strategy
    reagent = strategy_spec.reagent or strategy_config["default_reagent"]
    metal_atoms = reagent_info.get("metal_atoms", 1)
    redox = input_model.redox or RedoxSpecification(mode="anaerobic")
    tolerance = input_model.tolerance_mg_l
    max_iterations = input_model.max_iterations
    target_p_mg_l = input_model.target_residual_p_mg_l
    p_inert = input_model.p_inert_soluble_mg_l
    effective_target_p = target_p_mg_l - p_inert

    # Calculate P to remove (in mmol) for metal:P ratio
    base_solution = input_model.initial_solution.model_dump()
    initial_p_mg_l = _get_initial_p_mg_l(base_solution)
    reactive_initial_p_mg_l = initial_p_mg_l - p_inert
    base_solution = _with_reactive_p_mg_l(base_solution, reactive_initial_p_mg_l)
    p_to_remove_mg_l = reactive_initial_p_mg_l - effective_target_p
    p_to_remove_mmol = p_to_remove_mg_l / MOLECULAR_WEIGHTS["P"]

    # Resolve pe for anaerobic mode
    pe_value = _determine_pe(redox)

    # Get phases for iron strategy (anaerobic)
    phases = list(strategy_config.get("phases_anaerobic", strategy_config.get("phases", [])))
    
    from puran_mcp_support.engineering.phreeqc_phases import HOUSE_IRON_SULFIDE_PHASE

    if not {"FeS(ppt)", "FeS", HOUSE_IRON_SULFIDE_PHASE}.intersection(phases):
        phases.append(HOUSE_IRON_SULFIDE_PHASE)
    # Each case holds the request's mode's valence states as the main screen does (ammonium in every
    
    # reduces nitrate, ferric iron and sulfate by the request's stated donor (FINITE_DONOR_RULING_ID).
    held_states = _held_redox_states("iron", redox.mode)
    finite_donor = _donor_spec(input_model) if _uses_finite_donor("iron", redox.mode) else None

    for sulfide_mg_l in SULFIDE_LEVELS:
        logger.info(f"Running sulfide sensitivity scenario: S(-2) = {sulfide_mg_l} mg/L")

        try:
            # Create modified solution with sulfide
            modified_solution = copy.deepcopy(base_solution)
            analysis = modified_solution.get("analysis", {})
            analysis["S(-2)"] = sulfide_mg_l
            modified_solution["analysis"] = analysis
            modified_solution["pe"] = pe_value
            redox_hold = await _decouple_batch_redox(
                modified_solution,
                inline_phreeqc_prefix,
                database_path,
                phases,
                strategy_config.get("surface_name"),
                held_states,
                reagent,
                finite_donor=finite_donor,
                water_redox=_water_redox_checked("iron", redox.mode),
            )
            modified_solution = redox_hold["solution"]
            case_prefix = redox_hold["prefix"]
            feed_litres = await _feed_litres_per_system(modified_solution, case_prefix, database_path)
            if finite_donor is not None:
                refusal = _donor_covers_the_dose_range(redox_hold, reagent, strategy_spec.max_dose_mmol, feed_litres)
                if refusal:
                    raise DoseEvaluationError(f"sulfide={sulfide_mg_l} mg/L: {refusal}")

            async def evaluate_sulfide_dose(dose_mmol: float) -> Dict[str, Any]:
                if dose_mmol == 0:
                    return {
                        "residual_p_mg_l": reactive_initial_p_mg_l,
                        "ph": modified_solution.get("ph", 7.0),
                        "pe": pe_value,
                        "precipitated_phases": {},
                        "saturation_indices": {},
                    }
                result = await _run_p_removal_simulation(
                    initial_solution=copy.deepcopy(modified_solution),
                    reagent=reagent,
                    dose_mmol=dose_mmol,
                    phases=phases,
                    strategy_name="iron",
                    inline_prefix=case_prefix,
                    database_path=database_path,
                    pe_value=pe_value,
                    surface_name=strategy_config.get("surface_name"),
                    hfo_site_multiplier=input_model.hfo_site_multiplier,
                    redox_mode="anaerobic",
                    oxide_surface_mode=input_model.oxide_surface_mode,
                    target_ph=input_model.target_ph,
                    oxide_formation_ph=input_model.oxide_formation_ph,
                    feed_litres_per_system=feed_litres,
                    redox_hold=redox_hold,
                )
                if is_error_payload(result):
                    raise DoseEvaluationError(
                        f"sulfide={sulfide_mg_l} mg/L: {error_payload_message(result)}"
                    )
                if "residual_p_mg_l" not in result:
                    raise MissingSimulationOutputError(
                        f"sulfide={sulfide_mg_l} mg/L, dose={dose_mmol:g} mmol/L "
                        "omitted residual_p_mg_l"
                    )
                return result

            search = await scan_and_bracket_dose(
                evaluate_sulfide_dose,
                target_residual_p_mg_l=effective_target_p,
                max_dose_mmol=strategy_spec.max_dose_mmol,
                tolerance_mg_l=tolerance,
                max_iterations=max_iterations,
                scan_points=9,
                retry_count=1,
            )
            converged = search.status == "converged_target_met"
            selected = search.selected if converged else None
            best_dose = selected.dose_mmol if selected else None
            best_achieved_p = selected.residual_p_mg_l + p_inert if selected else None
            best_result = dict(selected.payload) if selected else None
            best_ph = best_result.get("ph") if best_result else None

            # Calculate metal:P ratio
            metal_p_ratio = best_dose / p_to_remove_mmol if best_dose and p_to_remove_mmol > 0 else None

            # Estimate Fe consumed by sulfide (FeS precipitated vs total Fe)
            fe_sulfide_pct = None
            if best_result and sulfide_mg_l > 0:
                # System moles of the FeS phase (one Fe each) against the dose in mmol per litre
                # of feed. The subprocess engine fills precipitated_phases, not the USER_PUNCH
                # equilibrium_phase_moles, which read 0 here before 2026-10-15.
                eq_phases = (
                    best_result.get("precipitated_phases")
                    or best_result.get("equilibrium_phase_moles")
                    or {}
                )
                fes_moles = (
                    eq_phases.get("Mackinawite", 0)
                    or eq_phases.get("FeS(ppt)", 0)
                    or eq_phases.get("FeS", 0)
                )
                if best_dose and best_dose > 0:
                    fes_mmol_per_litre = fes_moles * 1000.0 / feed_litres
                    fe_sulfide_pct = fes_mmol_per_litre / best_dose * 100

            scenario = SulfideSensitivityScenario(
                sulfide_mg_l=sulfide_mg_l,
                status=search.status,
                optimal_dose_mmol=best_dose,
                optimal_dose_mg_l=best_dose * reagent_info["mw"] / metal_atoms if best_dose else None,
                achieved_p_mg_l=best_achieved_p,
                metal_to_p_ratio=metal_p_ratio,
                final_ph=best_ph,
                fe_consumed_by_sulfide_pct=fe_sulfide_pct,
            )
            results.append(scenario)

            # Track the maximum only as a diagnostic envelope; it is not a
            # recommended dose because the sulfur cases are assumptions.
            if best_dose and best_dose > max_dose:
                max_dose = best_dose
                max_dose_sulfide = sulfide_mg_l

        except Exception as e:
            message = redact_paths(str(e))
            logger.error(f"Sulfide sweep error at {sulfide_mg_l} mg/L: {message}")
            results.append(
                SulfideSensitivityScenario(
                    sulfide_mg_l=sulfide_mg_l,
                    status="solver_error",
                    error_message=message,
                )
            )

    # Build design basis explanation
    design_basis = (
        f"Diagnostic sensitivity envelope only: maximum target-converged screen "
        f"was {max_dose:.2f} mmol/L at the assumed {max_dose_sulfide} mg/L S(-2) "
        "case. A measured sulfur inventory is required to select a plant scenario."
    )

    # Add sweep summary to warnings
    if results:
        sweep_summary = "Sulfide sensitivity sweep completed: "
        sweep_summary += ", ".join(
            [
                f"{r.sulfide_mg_l} mg/L → {r.metal_to_p_ratio:.1f}:1 Fe:P"
                for r in results
                if r.metal_to_p_ratio is not None
            ]
        )
        warnings.append(sweep_summary)

    return results, design_basis


# =============================================================================
# MAIN FUNCTION
# =============================================================================


async def screen_equilibrium_phosphorus_removal(input_data: Dict[str, Any]) -> Dict[str, Any]:
    'Screen equilibrium phosphorus response over a reagent-dose interval.\n\n    Supports multiple strategies:\n    - Iron coagulants (FeCl3, FeSO4) - HFO + Strengite/Vivianite\n    - Aluminum coagulants (AlCl3, Al2(SO4)3) - HAO + Variscite\n    - Struvite (MgCl2, MgO) - Mg-based P recovery\n    - Calcium phosphate (Ca(OH)2, CaCl2) - Brushite/HAP\n\n    Args:\n        input_data: Dictionary matching CalculatePhosphorusRemovalDoseInput schema\n\n    Returns:\n        Dictionary matching CalculatePhosphorusRemovalDoseOutput schema'
    logger.info("Running screen_equilibrium_phosphorus_removal tool...")
    warnings = []
    inline_blocks_added = []

    # Step 1: Validate input
    try:
        public_request = PhosphorusRemovalRequest.model_validate(input_data)
        input_model = CalculatePhosphorusRemovalDoseInput.model_validate(public_request.model_dump(exclude_none=True))
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"Input validation error: {message}")
        return _phosphorus_public_error(f"Input validation error: {message}")

    # Extract parameters
    initial_solution = input_model.initial_solution.model_dump()
    target_p_mg_l = input_model.target_residual_p_mg_l
    strategy_spec = input_model.strategy
    redox = input_model.redox or RedoxSpecification(mode="aerobic")
    database = input_model.database or "minteq.v4.dat"
    if redox.mode == "anaerobic" and public_request.feed_source in POST_DIGESTER_FEED_SOURCES:
        warnings.append(_post_digester_anaerobic_warning(public_request.feed_source))
    if (
        input_model.strategy.iron_sulfide_phase_case is not None
        and redox.mode == "aerobic"
    ):
        return _phosphorus_public_error(
            "iron_sulfide_phase_case is an anaerobic/full-redox sensitivity only",
            strategy_name=input_model.strategy.strategy,
            reagent=input_model.strategy.reagent or "FeCl3",
        )

    # Get strategy configuration (deep-copy to avoid mutating global config)
    import copy

    strategy_name = strategy_spec.strategy
    strategy_config = copy.deepcopy(STRATEGY_CONFIG.get(strategy_name))
    if not strategy_config:
        return _phosphorus_public_error(f"Unknown strategy: {strategy_name}", strategy_name=strategy_name)

    # Get reagent
    reagent = strategy_spec.reagent or strategy_config["default_reagent"]
    reagent_info = REAGENT_DEFINITIONS.get(reagent)
    if not reagent_info:
        return _phosphorus_public_error(f"Unknown reagent: {reagent}", strategy_name=strategy_name, reagent=reagent)

    metal = reagent_info["metal"]

    # The anaerobic mode reduces only by a stated finite donor; no other mode reduces, so none takes one
    # (FINITE_DONOR_RULING_ID, review M18).
    if _uses_finite_donor(strategy_name, redox.mode) and input_model.electron_donor is None:
        return _phosphorus_public_error(
            "The 'anaerobic' mode reduces the feed's nitrate, a ferric dose and the sulfate only by a stated finite "
            f"electron donor ({FINITE_DONOR_RULING_ID}, review M18): give 'electron_donor' {{'compound' (or "
            "'formula'), 'available_cod_mg_l', 'basis', 'source', 'cell_synthesis_fraction'}. A post-digester "
            "liquor is screened in 'fixed_pe', which holds its sulfate and nitrate as measured and takes no donor.",
            strategy_name=strategy_name,
            reagent=reagent,
        )
    if not _uses_finite_donor(strategy_name, redox.mode) and input_model.electron_donor is not None:
        return _phosphorus_public_error(
            f"'electron_donor' is the anaerobic mode's only ({FINITE_DONOR_RULING_ID}): redox mode {redox.mode!r} "
            f"for the {strategy_name} strategy holds nitrate and sulfate as fed and reduces nothing by a donor.",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    # Step 2: Validate strategy requirements
    analysis = initial_solution.get("analysis", {})

    # Check ammonia requirement for struvite
    if strategy_name == "struvite":
        has_ammonia = "N(-3)" in analysis
        if not has_ammonia:
            return _phosphorus_public_error(
                "Struvite strategy requires ammonia (N(-3)) in initial_solution. "
                "Add N(-3) concentration to analysis or choose a different strategy.",
                strategy_name=strategy_name,
                reagent=reagent,
            )

        # Issue 7: Ca competition warning for struvite
        ca_mg_l = _get_element_mg_l(initial_solution, "Ca", ["Ca", "Calcium"])
        if ca_mg_l is not None and ca_mg_l > 50:
            warnings.append(
                f"High Ca concentration ({ca_mg_l:.1f} mg/L) may compete with struvite formation. "
                "Ca-phosphate phases (brushite, hydroxyapatite) may co-precipitate, reducing struvite yield. "
                "Consider lowering pH or removing Ca first."
            )

        # Issue 7: NH4 limitation warning for struvite
        # Struvite is MgNH4PO4·6H2O - 1:1:1 stoichiometry
        # Check if NH4 is potentially limiting compared to P
        nh4_mg_l = _get_element_mg_l(initial_solution, "N", ["N(-3)"])
        p_mg_l = _get_initial_p_mg_l(initial_solution)
        if nh4_mg_l is not None and p_mg_l > 0:
            # Convert to mmol for stoichiometric comparison
            nh4_mmol = nh4_mg_l / MOLECULAR_WEIGHTS["N"]
            p_mmol = p_mg_l / MOLECULAR_WEIGHTS["P"]
            if nh4_mmol < p_mmol * 0.8:  # NH4 is < 80% of stoichiometric requirement
                warnings.append(
                    f"Ammonia may be limiting for struvite: N(-3) = {nh4_mg_l:.1f} mg/L ({nh4_mmol:.2f} mmol/L), "
                    f"P = {p_mg_l:.1f} mg/L ({p_mmol:.2f} mmol/L). Struvite requires 1:1 N:P molar ratio. "
                    "Maximum P removal may be limited by available ammonia."
                )

    # Issue 7: Alkalinity validation for calcium_phosphate strategy
    if strategy_name == "calcium_phosphate":
        alk_mg_caco3 = _get_alkalinity_mg_caco3(initial_solution)
        if alk_mg_caco3 is not None and alk_mg_caco3 < 50:
            warnings.append(
                f"Low alkalinity ({alk_mg_caco3:.1f} mg/L as CaCO3) detected. "
                "Ca(OH)2 dosing will significantly raise pH (to 9-11) and consume alkalinity. "
                "Consider pH monitoring and potential re-carbonation after treatment."
            )
        elif alk_mg_caco3 is None:
            warnings.append(
                "Alkalinity not specified. Ca(OH)2 strategy requires understanding of "
                "carbonate system for accurate pH prediction. Consider adding 'Alkalinity' to analysis."
            )

        # Check initial pH for Ca-P strategy
        initial_ph = initial_solution.get("ph") or initial_solution.get("pH")
        if initial_ph is not None and initial_ph < 7.0:
            warnings.append(
                f"Initial pH ({initial_ph}) is below optimal range for Ca-P precipitation (9-11). "
                "Significant lime dose may be required to achieve precipitation conditions."
            )

    # Mandatory sulfide sensitivity check for anaerobic iron strategy
    sulfide_sensitivity = input_model.sulfide_sensitivity
    sulfide_sensitivity_results = None

    if strategy_name == "iron" and redox.mode == "anaerobic":
        has_sulfide = "S(-2)" in analysis and analysis.get("S(-2)", 0) > 0
        if not has_sulfide:
            # Anaerobic iron mode without sulfide - require explicit handling
            if sulfide_sensitivity is None:
                return _phosphorus_public_error(
                    "Anaerobic iron screening requires a measured sulfide input or an explicitly "
                    "assumed sensitivity treatment. Choose one:\n"
                    "1. Add 'S(-2)' to initial_solution.analysis with measured sulfide concentration\n"
                    "2. Set 'sulfide_sensitivity': true to run mandatory sensitivity sweep at [0, 20, 50, 100] mg/L\n"
                    "3. Set 'sulfide_sensitivity': false to explicitly inspect the sulfide-free boundary case",
                    strategy_name=strategy_name,
                    reagent=reagent,
                )
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
                    "Sulfide-free anaerobic boundary selected. This is an optimistic "
                    "equilibrium screen and is not transferable to a sulfidic plant state."
                )

    # Get initial P concentration
    initial_p_mg_l = _get_initial_p_mg_l(initial_solution)
    if initial_p_mg_l <= 0:
        return _phosphorus_public_error(
            "Could not determine initial P concentration from input",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    if target_p_mg_l >= initial_p_mg_l:
        return _phosphorus_public_error(
            f"Target P ({target_p_mg_l} mg/L) must be less than initial P ({initial_p_mg_l} mg/L)",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    # Inert P accounting: adjust effective target for non-reactive P
    p_inert = input_model.p_inert_soluble_mg_l

    # Edge case validation: p_inert > initial_p makes no physical sense
    if p_inert > initial_p_mg_l:
        return _phosphorus_public_error(
            f"Non-reactive P ({p_inert} mg/L) exceeds initial P ({initial_p_mg_l} mg/L). "
            "This is physically impossible. Check p_inert_soluble_mg_l value.",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    effective_target_p = target_p_mg_l - p_inert
    if effective_target_p < 0:
        return _phosphorus_public_error(
            f"Target P ({target_p_mg_l} mg/L) is less than non-reactive P ({p_inert} mg/L). "
            "Cannot achieve target. Reduce p_inert_soluble_mg_l or increase target.",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    if p_inert > 0:
        logger.info(
            f"Adjusted for inert P: target {target_p_mg_l} - inert {p_inert} = effective {effective_target_p} mg/L"
        )
        warnings.append(
            f"Inert P accounting: {p_inert} mg/L non-reactive P assumed. "
            f"Effective reactive P target = {effective_target_p:.2f} mg/L"
        )
        # Equilibrate only the reactive fraction. The legacy path equilibrated
        # all reported P and then added the inert scalar again in the output.
        initial_solution = _with_reactive_p_mg_l(
            initial_solution, initial_p_mg_l - p_inert
        )

    # Step 3: Resolve database and check for required inline blocks
    try:
        database_path = database_manager.resolve_and_validate_database(database, category="general")
    except Exception as e:
        message = redact_paths(str(e))
        logger.warning(f"Database resolution failed: {message}")
        return _phosphorus_public_error(
            f"Database resolution failed: {message}",
            code="DATABASE_RESOLUTION_ERROR",
            strategy_name=strategy_name,
            reagent=reagent,
            stage="resolve_database",
        )

    if (
        strategy_name in {"iron", "aluminum"}
        and redox.mode in {"aerobic", "full_redox"}
        and input_model.oxide_surface_mode == "fresh_in_situ"
        and input_model.target_ph is None
        and input_model.oxide_formation_ph is None
    ):
        return _phosphorus_public_error(
            "fresh_in_situ oxide screening requires oxide_formation_ph when target_ph is not "
            "controlled; initial feed pH is not a valid implicit substitute for the local "
            "post-dose oxide-formation pH",
            strategy_name=strategy_name,
            reagent=reagent,
        )

    if strategy_name in {"iron", "aluminum"} and redox.mode in {"aerobic", "full_redox"}:
        surface_label = "HFO" if strategy_name == "iron" else "HAO"
        if input_model.oxide_surface_mode == "fresh_in_situ":
            warnings.append(
                f"The fresh {surface_label} capacity is a kinetic calibration surrogate mapped "
                "into an equilibrium surface model; it is not independent mechanistic validation."
            )
        else:
            warnings.append(
                f"The preformed/aged {surface_label} case is a capacity-bounding scenario; "
                "agreement with a plant net P/metal yield does not identify oxide history."
            )

    # The iron strategy's FeS(ppt) slot is a role (`ferric_phases.get_phase_name("iron_sulfide")`, the house's FeS case
    # per database). Every other phase keeps its name and is resolved against the database once, at Step 4.6b: the
    
    # The typed role table had dropped Strengite on wateq4f.dat, which defines it, and on phreeqc.dat, which does not.
    selected_iron_sulfide_phase: Optional[str] = None
    if strategy_name == "iron" and database_path:
        import os as _os

        from water_chemistry_mcp.utils.ferric_phases import get_iron_sulfide_phase_case, get_phase_name

        db_basename = _os.path.basename(database_path)
        for phases_key in ("phases_aerobic", "phases_anaerobic"):
            if phases_key in strategy_config:
                remapped = []
                for phase in strategy_config[phases_key]:
                    if phase == "FeS(ppt)":
                        phase = get_phase_name("iron_sulfide", db_basename) or phase
                    remapped.append(phase)
                strategy_config[phases_key] = remapped
        if strategy_spec.iron_sulfide_phase_case:
            selected_fes = get_iron_sulfide_phase_case(
                strategy_spec.iron_sulfide_phase_case,
                db_basename,
            )
            selected_iron_sulfide_phase = selected_fes
            strategy_config["phases_anaerobic"] = [
                phase
                for phase in strategy_config.get("phases_anaerobic", [])
                if phase not in {"FeS(ppt)", "Mackinawite"}
            ] + [selected_fes]
            warnings.append(
                "Explicit iron-sulfide phase sensitivity selected: "
                f"{strategy_spec.iron_sulfide_phase_case} ({selected_fes}). "
                "Compare it with a separate run of the other polymorph case."
            )
        logger.info(f"Remapped iron phases for {db_basename}: aerobic={strategy_config.get('phases_aerobic')}")

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
    is_full_redox = redox.mode == "full_redox"
    pe_value = _determine_pe(redox)
    initial_solution["pe"] = pe_value

    # Get phases for this strategy (use allowed_phases override if provided)
    if strategy_spec.allowed_phases:
        # User specified custom phases
        phases = list(strategy_spec.allowed_phases)
        if (
            selected_iron_sulfide_phase is not None
            and selected_iron_sulfide_phase not in phases
        ):
            return _phosphorus_public_error(
                "strategy.allowed_phases conflicts with iron_sulfide_phase_case; "
                f"include {selected_iron_sulfide_phase!r} or omit one override",
                strategy_name=strategy_name,
                reagent=reagent,
            )
        # Each name is resolved against the database at Step 4.6b, with the strategy's own phases: a phase the database
        
        # remapped by role and skipped when the role table had no name, or dropped with a log line.
        # Add Calcite if not already present (common competing sink)
        if "Calcite" not in phases:
            phases.append("Calcite")
        logger.info(f"Using user-specified phases: {phases}")
    elif is_full_redox:
        # Intermediate/anoxic ORP: carry BOTH the Fe(III) family (Ferrihydrite/Strengite + HFO) and
        # the Fe(II) family (Vivianite/FeS/Siderite) in one run, at the user-set pe. PHREEQC then
        # selects whichever phases are stable across the ferric->ferrous transition (and the
        # sulfide-competition collapse), so the anoxic zone is served without picking a single endpoint.
        phases = []
        for k in ("phases_aerobic", "phases_anaerobic"):
            for p in strategy_config.get(k, []):
                if p not in phases:
                    phases.append(p)
        logger.info(f"full_redox: carrying both Fe(III)+Fe(II) phase families at pe={pe_value}: {phases}")
        warnings.append(
            f"full_redox is an EQUILIBRIUM SCREENING model for the anoxic transition at pe={pe_value:g} "
            f"(default 0 = ferric/ferrous midpoint). It assumes phase equilibrium + available "
            f"nucleation; real systems are kinetically limited and spatially heterogeneous. Calibrate "
            f"pe against a measured ORP and confirm with bench/jar tests before using as a design dose."
        )
    else:
        # Use default phases for this strategy
        phases_key = "phases_aerobic" if is_aerobic else "phases_anaerobic"
        phases = list(strategy_config.get(phases_key, []))  # Make a copy

    if strategy_name == "iron" and not is_aerobic and "Mackinawite" in phases:
        warnings.append(
            "The iron sulfide is Mackinawite, the house polymorph on every database (E4 of PUBLIC-RULE-1ddf4c4cc59b). Re-run with iron_sulfide_phase_case 'amorphous_fes' for the amorphous FeS(ppt) sensitivity; the two phases are not interchangeable."
        )

    if strategy_name == "calcium_phosphate" and "Ca4H(PO4)3:3H2O" in phases:
        # Only when the metastable OCP-type phase is actually the controlling phase (the default). If a
        # user explicitly overrides to HAP-only via allowed_phases, this "OCP controls" note is skipped.
        warnings.append(
            "Ca-P residual is controlled here by the kinetically-realistic metastable OCP-type phase "
            "(Ca4H(PO4)3:3H2O) + brushite, giving the ~0.1-1 mgP/L achievable in normal contact time. "
            "The thermodynamic hydroxylapatite (HAP) floor is much lower (~0.005 mgP/L) but is nucleation-"
            "inhibited and only approached with high pH, Ca excess, seeding and long contact/aging "
            "(Ostwald sequence ACP->OCP->HAP); do not design to the HAP floor without pilot confirmation."
        )

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

    # Step 4.6b: every phase of the run is one the database defines (in its spelling), the same phase under another
    # name (recorded on the answer), or one this run's inline blocks define; any other refuses, naming it and the
    
    if database_path:
        from water_chemistry_mcp.waterchem.phase_choice import MissingPhaseError, inline_defined_phases, resolve_phases

        try:
            phases = resolve_phases(
                database_path, phases, provided=inline_defined_phases(inline_phreeqc_prefix),
                purpose=f"{strategy_name} phosphorus screen",
            )
        except MissingPhaseError as refused:
            return _phosphorus_public_error(
                refused.message, strategy_name=strategy_name, reagent=reagent, stage="resolve_phases"
            )

    # Step 4.7: Validate strategy-database compatibility
    validation_warnings = validate_strategy_database_compatibility(
        strategy_name, phases, database_path, inline_phreeqc_prefix, is_aerobic,
    )
    warnings.extend(validation_warnings)

    # Check for fatal incompatibility
    fatal = [w for w in validation_warnings if w.startswith("FATAL:")]
    if fatal:
        return _stamp_reagent_catalogue(
            CalculatePhosphorusRemovalDoseOutput(
                status="infeasible",
                error_message=fatal[0],
                strategy_used=strategy_name,
                reagent_used=reagent,
                warnings=warnings,
            ).model_dump(exclude_none=True),
            reagent,
        )

    # Step 4.65: the activity domain (review M17): an ion-association database beyond its qualified ionic strength is
    # refused unless the caller asks for a screening answer stamped outside the domain.
    from puran_mcp_support.engineering.house_standards.activity_models import ACTIVITY_DOMAIN_RULING_ID, ION_ASSOCIATION_QUALIFIED_IONIC_STRENGTH_MOL_KGW

    try:
        feed_ionic_strength = await _feed_ionic_strength(initial_solution, inline_phreeqc_prefix, database_path)
    except Exception as e:
        return _phosphorus_public_error(
            f"Could not read the feed's ionic strength: {redact_paths(str(e))}",
            code="PHREEQC_EXECUTION_ERROR", strategy_name=strategy_name, reagent=reagent, stage="activity_domain",
        )
    if feed_ionic_strength > ION_ASSOCIATION_QUALIFIED_IONIC_STRENGTH_MOL_KGW:
        outside = (
            f"the feed's ionic strength is {feed_ionic_strength:.3g} mol/kgw, above the {ION_ASSOCIATION_QUALIFIED_IONIC_STRENGTH_MOL_KGW:g} "
            f"mol/kgw the house reads an ion-association activity model (Davies / extended Debye-Hueckel; "
            f"{os.path.basename(database_path)}) as qualified to, and the house has no qualified phosphate electrolyte "
            f"model (pitzer.dat defines no P) ({ACTIVITY_DOMAIN_RULING_ID}, review M17)"
        )
        if input_model.activity_domain_outside == "refuse":
            return _phosphorus_public_error(
                f"Refused: {outside}. Pass activity_domain_outside 'screen' for a screening answer stamped outside "
                "the activity domain.",
                strategy_name=strategy_name, reagent=reagent, stage="activity_domain",
            )
        warnings.append(f"OUTSIDE THE ACTIVITY DOMAIN, screened at the caller's request: {outside}.")

    # Step 4.6: Handle sulfide sensitivity sweep for anaerobic iron (Issue 1/9)
    # If sulfide_sensitivity=True and no explicit S(-2), run the sweep and return results
    if sulfide_sensitivity is True and strategy_name == "iron" and not is_aerobic:
        logger.info("Running sulfide sensitivity sweep for anaerobic iron mode")

        # Get initial P for use in sweep
        initial_p_mg_l = _get_initial_p_mg_l(initial_solution)

        sweep_results, sensitivity_basis = await _run_sulfide_sensitivity_sweep(
            input_model=input_model,
            reagent_info=reagent_info,
            strategy_config=strategy_config,
            database_path=database_path,
            inline_phreeqc_prefix=inline_phreeqc_prefix,
            warnings=warnings,
        )
        warnings.append(sensitivity_basis)

        # No assumed sweep point is selected as the primary plant state.
        achieved_pe = pe_value
        redox_diagnostics = _build_redox_diagnostics(
            redox=redox,
            target_pe=pe_value,
            achieved_pe=achieved_pe,
        )

        # No assumed sulfide case is promoted to a plant design basis. Return
        # the diagnostic envelope and fail closed until a measured sulfur
        # inventory/design scenario is supplied.
        output = CalculatePhosphorusRemovalDoseOutput(
            status="nonconverged",
            error_message=(
                "Sulfide sensitivity cases are diagnostic assumptions; no measured "
                "plant sulfur scenario was available"
            ),
            converged=False,
            target_met=False,
            iterations_used=len(sweep_results) * input_model.max_iterations,  # Approx iterations
            dose_search_bounds=None,  # Not applicable for sweep
            final_pe=achieved_pe,
            strategy_used=strategy_name,
            reagent_used=reagent,
            inline_blocks_added=inline_blocks_added if inline_blocks_added else None,
            dose_response_curve=None,  # Not applicable for sweep
            redox_diagnostics=redox_diagnostics,
            sulfide_sensitivity_results=sweep_results,
            design_scenario_sulfide_mg_l=None,
            warnings=warnings if warnings else None,
        ).model_dump(exclude_none=True)
        return _finalize_phosphorus_result(
            output,
            feed_ionic_strength=feed_ionic_strength,
            input_model=input_model,
            strategy_name=strategy_name,
            reagent=reagent,
            database=database,
            database_path=database_path,
            redox=redox,
            phases=phases,
            final_state=None,
            mass_partition=[],
            si_trigger={"triggered": False},
        )

    # Step 5: Scan the response, resolve branches, then refine valid brackets.
    # Use effective_target_p (accounts for inert P) in the target comparison.
    logger.info(
        f"Starting {strategy_name} optimization: target P = {target_p_mg_l} mg/L "
        f"(effective reactive target = {effective_target_p} mg/L)"
    )

    # Calculate initial dose estimate based on effective reactive P to remove
    p_to_remove_mg_l = initial_p_mg_l - target_p_mg_l
    p_to_remove_mmol = mg_l_to_mmol(p_to_remove_mg_l, "P")

    # Get site multiplier for surface complexation
    hfo_site_multiplier = input_model.hfo_site_multiplier
    oxide_surface_mode = input_model.oxide_surface_mode
    target_ph = input_model.target_ph
    oxide_formation_ph = input_model.oxide_formation_ph
    design_safety_factor = input_model.design_safety_factor
    tolerance = input_model.tolerance_mg_l
    si_trigger_summary = {"triggered": False}
    # Every dose run holds its mode's valence states as fed (_held_redox_states; ammonium in every
    # mode) and, in the anaerobic mode, carries the stated donor's reduction: every later run, the
    # feed basis included, reads the decoupled feed and prefix.
    held_states = _held_redox_states(strategy_name, redox.mode)
    finite_donor = _donor_spec(input_model) if _uses_finite_donor(strategy_name, redox.mode) else None
    try:
        redox_hold: Dict[str, Any] = await _decouple_batch_redox(
            initial_solution,
            inline_phreeqc_prefix,
            database_path,
            phases,
            strategy_config.get("surface_name"),
            held_states,
            reagent,
            finite_donor=finite_donor,
            water_redox=_water_redox_checked(strategy_name, redox.mode),
        )
    except Exception as e:
        return _phosphorus_public_error(
            f"Could not hold {', '.join(held_states)} as fed in the dose equilibria: "
            f"{redact_paths(str(e))}",
            code="INVALID_INPUT" if isinstance(e, ValueError) else "PHREEQC_EXECUTION_ERROR",
            strategy_name=strategy_name,
            reagent=reagent,
            stage="decouple_batch_redox",
        )
    initial_solution = redox_hold["solution"]
    inline_phreeqc_prefix = redox_hold["prefix"]
    warnings.append(_batch_redox_warning(strategy_name, redox.mode, pe_value, held_states, finite_donor))
    redox_refusals: List[str] = []
    try:
        feed_litres = await _feed_litres_per_system(
            initial_solution, inline_phreeqc_prefix, database_path
        )
    except Exception as e:
        return _phosphorus_public_error(
            f"Could not fix the per-litre-of-feed basis: {redact_paths(str(e))}",
            strategy_name=strategy_name,
            reagent=reagent,
        )
    if finite_donor is not None:
        refusal = _donor_covers_the_dose_range(redox_hold, reagent, strategy_spec.max_dose_mmol, feed_litres)
        if refusal:
            return _phosphorus_public_error(refusal, strategy_name=strategy_name, reagent=reagent,
                                            stage="finite_donor")

    async def evaluate_dose(dose_mmol: float) -> Dict[str, Any]:
        """Evaluate one dose without inferring physical direction from failure."""

        nonlocal si_trigger_summary
        if dose_mmol == 0:
            return {
                "residual_p_mg_l": initial_p_mg_l - p_inert,
                "ph": initial_solution.get("ph", 7.0),
                "pe": pe_value,
                "precipitated_phases": {},
                "saturation_indices": {},
            }
        async def simulate(dose_phases: List[str], report_si_phases: Optional[List[str]] = None) -> Dict[str, Any]:
            try:
                simulated = await _simulate_dose(dose_phases, report_si_phases)
            except BatchRedoxMovedError as exc:
                redox_refusals.append(("check_batch_redox", str(exc)))
                raise
            except PurePhaseNotConvergedError as exc:  # C15-3: the run answered at the wrong pe or pH
                redox_refusals.append(("phreeqc_convergence", str(exc)))
                raise
            if is_error_payload(simulated):
                raise DoseEvaluationError(error_payload_message(simulated))
            if "residual_p_mg_l" not in simulated:
                raise MissingSimulationOutputError(
                    f"PHREEQC result at {dose_mmol:g} mmol/L omitted residual_p_mg_l"
                )
            return simulated

        async def _simulate_dose(
            dose_phases: List[str], report_si_phases: Optional[List[str]]
        ) -> Dict[str, Any]:
            return await _run_p_removal_simulation(
                initial_solution=copy.deepcopy(initial_solution),
                reagent=reagent,
                dose_mmol=dose_mmol,
                phases=dose_phases,
                strategy_name=strategy_name,
                inline_prefix=inline_phreeqc_prefix,
                database_path=database_path,
                pe_value=pe_value,
                surface_name=strategy_config.get("surface_name"),
                hfo_site_multiplier=hfo_site_multiplier,
                redox_mode=redox.mode,
                oxide_surface_mode=oxide_surface_mode,
                target_ph=target_ph,
                oxide_formation_ph=oxide_formation_ph,
                report_si_phases=report_si_phases,
                feed_litres_per_system=feed_litres,
                redox_hold=redox_hold,
            )

        result = await simulate(phases)

        # Metastability gates: a phase nucleates only when the dosed solution is supersaturated past
        # its trigger. That supersaturation is read from the dosed solution with the gated phases
        # WITHHELD: in the equilibrium run a phase that precipitated sits at SI = 0 by construction,
        # so judging that SI reverted every precipitation to the feed P (ZLR gap G5, 2026-10-15).
        primary_si_trigger = strategy_spec.si_trigger
        if primary_si_trigger is None:
            primary_si_trigger = strategy_config.get("si_trigger")
        primary_phases: List[str] = []
        gates: Dict[str, float] = {}
        if primary_si_trigger is not None and primary_si_trigger > 0:
            primary_phases = _get_primary_p_phases(strategy_name, is_aerobic)
            gates.update({phase: primary_si_trigger for phase in primary_phases})
        if input_model.include_background_sinks:
            strategy_phases = _get_primary_p_phases(strategy_name, is_aerobic)
            for bg_phase, bg_trigger in BACKGROUND_SINK_SI_TRIGGERS.items():
                if bg_phase in phases and bg_phase not in strategy_phases:
                    gates[bg_phase] = bg_trigger

        if gates:
            supersaturation = await simulate(
                [phase for phase in phases if phase not in gates], report_si_phases=list(gates)
            )
            supersaturation_si = supersaturation.get("saturation_indices", {}) or {}
            unjudged = [phase for phase in gates if supersaturation_si.get(phase) is None]
            if unjudged:
                raise MissingSimulationOutputError(
                    f"PHREEQC result at {dose_mmol:g} mmol/L reported no saturation index for the "
                    f"metastability-gated phase(s) {unjudged}; the gate cannot be judged"
                )
            metastable: List[str] = []
            if primary_phases:
                _, primary_metastable = _apply_si_trigger_to_phases(
                    result=supersaturation,
                    phases_to_check=primary_phases,
                    si_trigger=primary_si_trigger,
                    initial_p_mg_l=initial_p_mg_l,
                    residual_p_mg_l=float(result["residual_p_mg_l"]),
                    warnings=warnings,
                )
                detail = _si_trigger_detail(
                    supersaturation, primary_phases, primary_si_trigger, primary_metastable
                )
                if primary_metastable or not si_trigger_summary.get("phase"):
                    si_trigger_summary = detail
                if primary_metastable:
                    metastable.extend(primary_phases)
            for bg_phase, bg_trigger in gates.items():
                if bg_phase in primary_phases:
                    continue
                phase_si = supersaturation_si[bg_phase]
                # Undersaturated (SI <= 0) does not form either way; only a supersaturated phase
                # short of its trigger is metastable.
                if 0.0 < phase_si < bg_trigger:
                    metastable.append(bg_phase)
                    bg_warning = (
                        f"Background sink {bg_phase} metastable (dosed-solution SI={phase_si:.2f} "
                        f"< {bg_trigger}); it was withheld from the equilibrium."
                    )
                    if bg_warning not in warnings:
                        warnings.append(bg_warning)
                    si_trigger_summary = {
                        "triggered": True,
                        "phase": bg_phase,
                        "saturation_index": phase_si,
                        "threshold": bg_trigger,
                        "message": bg_warning,
                    }
            # A metastable phase does not form: the reported state is the equilibrium without it,
            # so the residual P, pH and phase list describe one solution.
            if metastable:
                if set(metastable) == set(gates):
                    result = supersaturation
                else:
                    result = await simulate([phase for phase in phases if phase not in metastable])

        processed = dict(result)
        processed["residual_p_mg_l"] = float(result["residual_p_mg_l"])
        return processed

    search = await scan_and_bracket_dose(
        evaluate_dose,
        target_residual_p_mg_l=effective_target_p,
        max_dose_mmol=strategy_spec.max_dose_mmol,
        tolerance_mg_l=tolerance,
        max_iterations=input_model.max_iterations,
        scan_points=9,
        retry_count=1,
    )
    if redox_refusals:
        # A redox state that moved, or a fixed pe or pH PHREEQC did not converge (C15-3 of
        
        # converge: the whole screen refuses, naming it, whatever the other doses returned.
        stage, message = redox_refusals[0]
        return _phosphorus_public_error(
            redact_paths(message),
            code="PHREEQC_EXECUTION_ERROR",
            strategy_name=strategy_name,
            reagent=reagent,
            stage=stage,
        )

    metal_atoms = reagent_info.get("metal_atoms", 1)
    dose_response_data = [
        PhosphorusRemovalScenario(
            dose_mmol=point.dose_mmol,
            dose_mg_l=point.dose_mmol * reagent_info["mw"] / metal_atoms,
            residual_p_mg_l=point.residual_p_mg_l + p_inert,
            metal_to_p_ratio=(
                point.dose_mmol / p_to_remove_mmol if p_to_remove_mmol > 0 else 0
            ),
            ph=point.payload.get("ph", 7.0),
            precipitation_breakdown=point.payload.get("precipitated_phases"),
        )
        for point in search.evaluations
    ]
    mass_partition_history = [
        _mass_partition_point(
            iteration=index,
            dose_mmol=point.dose_mmol,
            initial_p_mg_l=initial_p_mg_l,
            p_solution_mg_l=point.residual_p_mg_l + p_inert,
            phases=phases,
            result=dict(point.payload),
            feed_litres_per_system=feed_litres,
        )
        for index, point in enumerate(search.evaluations, start=1)
    ]

    selected = search.selected
    diagnostic_final_state = dict(selected.payload) if selected is not None else None
    converged = search.status == "converged_target_met"
    target_met = converged
    optimal_dose_mmol = selected.dose_mmol if converged and selected is not None else None
    optimal_dose_mg_l = (
        optimal_dose_mmol * reagent_info["mw"] / metal_atoms
        if optimal_dose_mmol is not None
        else None
    )
    achieved_p_mg_l = (
        selected.residual_p_mg_l + p_inert if converged and selected is not None else None
    )
    residual_error_mg_l = (
        abs(selected.residual_p_mg_l - effective_target_p)
        if selected is not None
        else None
    )
    metal_to_p_ratio = (
        optimal_dose_mmol / p_to_remove_mmol
        if optimal_dose_mmol is not None and p_to_remove_mmol > 0
        else None
    )
    if search.status != "converged_target_met":
        warnings.append(
            f"Equilibrium dose screen ended with status={search.status}; no "
            "target-converged dose or design recommendation is returned."
        )
    if not search.monotonic_nonincreasing:
        warnings.append(
            "The scanned dose response was not globally monotone; target crossings "
            "were treated as separate local branches."
        )

    achieved_pe = diagnostic_final_state.get("pe") if diagnostic_final_state else pe_value
    redox_diagnostics = _build_redox_diagnostics(
        redox=redox,
        target_pe=pe_value,
        achieved_pe=achieved_pe,
    )

    # Run final simulation with partitioning extraction (Issue 5)
    # This extracts detailed P partitioning data at the optimal dose
    partitioning_data = {}
    if optimal_dose_mmol is not None:
        try:
            final_partitioning_result = await _run_p_removal_simulation(
                initial_solution=copy.deepcopy(initial_solution),
                reagent=reagent,
                dose_mmol=optimal_dose_mmol,
                phases=phases,
                strategy_name=strategy_name,
                inline_prefix=inline_phreeqc_prefix,
                database_path=database_path,
                pe_value=pe_value,
                surface_name=strategy_config.get("surface_name"),
                hfo_site_multiplier=hfo_site_multiplier,
                redox_mode=redox.mode,
                oxide_surface_mode=oxide_surface_mode,
                extract_partitioning=True,
                target_ph=target_ph,
                oxide_formation_ph=oxide_formation_ph,
                feed_litres_per_system=feed_litres,
                redox_hold=redox_hold,
            )
            if not is_error_payload(final_partitioning_result):
                partitioning_data = {
                    "phase_moles_mmol_per_L": final_partitioning_result.get("phase_moles_mmol_per_L"),
                    "p_removed_by_phase_mg_L": final_partitioning_result.get("p_removed_by_phase_mg_L"),
                    "p_adsorbed_mg_L": final_partitioning_result.get("p_adsorbed_mg_L"),
                    "p_dissolved_mg_L": final_partitioning_result.get("p_dissolved_mg_L"),
                }
                if mass_partition_history:
                    best_point = min(
                        mass_partition_history,
                        key=lambda point: abs(point["dose_mmol"] - optimal_dose_mmol),
                    )
                    if partitioning_data.get("p_adsorbed_mg_L") is not None:
                        best_point["p_adsorbed_mg_l"] = partitioning_data["p_adsorbed_mg_L"]
                    if partitioning_data.get("p_removed_by_phase_mg_L"):
                        best_point["p_removed_by_phase_mg_l"] = partitioning_data["p_removed_by_phase_mg_L"]
        except (BatchRedoxMovedError, PurePhaseNotConvergedError) as e:
            return _phosphorus_public_error(
                redact_paths(str(e)),
                code="PHREEQC_EXECUTION_ERROR",
                strategy_name=strategy_name,
                reagent=reagent,
                stage="check_batch_redox" if isinstance(e, BatchRedoxMovedError) else "phreeqc_convergence",
            )
        except Exception as e:
            logger.warning(f"Failed to extract partitioning data: {redact_paths(str(e))}")

    reactive_initial_p_mg_l = initial_p_mg_l - p_inert
    p_mass_balance_passed, p_mass_balance_error_mg_l = _p_mass_balance_check(
        initial_reactive_p_mg_l=reactive_initial_p_mg_l,
        partitioning_data=partitioning_data,
        tolerance_mg_l=max(tolerance, 0.01 * max(reactive_initial_p_mg_l, 1e-12)),
    )
    if converged and not p_mass_balance_passed:
        warnings.append(
            "The equilibrium target was reached, but independent reactive-P "
            "partition closure was not established; the screen remains diagnostic."
        )

    # Preserve a user-requested factor only as a diagnostic screen. It is not a
    # recommendation because this kernel has no dynamic TP/solids/electron inventory.
    screening_factor_dose_mmol = screening_factor_dose_mg_l = None
    if design_safety_factor and design_safety_factor != 1.0 and optimal_dose_mmol is not None:
        screening_factor_dose_mmol = optimal_dose_mmol * design_safety_factor
        screening_factor_dose_mg_l = optimal_dose_mg_l * design_safety_factor
        warnings.append(
            "design_safety_factor was applied only to a diagnostic equilibrium "
            "screen; it does not create a recommendation-eligible setpoint."
        )

    output = CalculatePhosphorusRemovalDoseOutput(
        status=search.status,
        error_message=(
            None
            if converged
            else "No target-converged, fully bracketed equilibrium dose was established"
        ),
        converged=converged,
        target_met=target_met,
        residual_error_mg_l=residual_error_mg_l,
        iterations_used=len(search.evaluations) + len(search.errors),
        dose_search_bounds=(
            {
                "dose_low_mmol": search.final_bounds[0],
                "dose_high_mmol": search.final_bounds[1],
            }
            if search.final_bounds is not None
            else None
        ),
        bracket_resolved=search.bracket_resolved,
        dose_response_monotonic=search.monotonic_nonincreasing,
        monotonicity_resolved=search.monotonicity_resolved,
        dose_response_branches=[branch.public_dict() for branch in search.branches] or None,
        solver_errors=[
            {**error, "message": redact_paths(error["message"])} for error in search.errors
        ] or None,
        optimal_dose_mmol=optimal_dose_mmol,
        optimal_dose_mg_l=optimal_dose_mg_l,
        best_evaluated_dose_mmol=(selected.dose_mmol if not converged and selected else None),
        best_evaluated_residual_p_mg_l=(
            selected.residual_p_mg_l + p_inert if not converged and selected else None
        ),
        mass_balance_passed=p_mass_balance_passed,
        p_mass_balance_error_mg_l=p_mass_balance_error_mg_l,
        screening_dose_with_factor_mmol=screening_factor_dose_mmol,
        screening_dose_with_factor_mg_l=screening_factor_dose_mg_l,
        achieved_p_mg_l=achieved_p_mg_l,
        metal_to_p_ratio=metal_to_p_ratio,
        final_ph=diagnostic_final_state.get("ph") if diagnostic_final_state else None,
        final_pe=achieved_pe,
        strategy_used=strategy_name,
        reagent_used=reagent,
        inline_blocks_added=inline_blocks_added if inline_blocks_added else None,
        precipitated_phases=(
            diagnostic_final_state.get("precipitated_phases")
            if diagnostic_final_state
            else None
        ),
        phase_moles_mmol_per_L=partitioning_data.get("phase_moles_mmol_per_L"),
        p_removed_by_phase_mg_L=partitioning_data.get("p_removed_by_phase_mg_L"),
        p_adsorbed_mg_L=partitioning_data.get("p_adsorbed_mg_L"),
        p_dissolved_mg_L=partitioning_data.get("p_dissolved_mg_L"),
        dose_response_curve=dose_response_data if len(dose_response_data) >= 3 else None,
        redox_diagnostics=redox_diagnostics,
        warnings=warnings if warnings else None,
    ).model_dump(exclude_none=True)
    for key in ("donor_reduction", "fixed_pe_boundary"):
        if diagnostic_final_state and diagnostic_final_state.get(key):
            output[key] = diagnostic_final_state[key]
    return _finalize_phosphorus_result(
        output,
        feed_ionic_strength=feed_ionic_strength,
        input_model=input_model,
        strategy_name=strategy_name,
        reagent=reagent,
        database=database,
        database_path=database_path,
        redox=redox,
        phases=phases,
        final_state=diagnostic_final_state,
        partitioning_data=partitioning_data,
        mass_partition=mass_partition_history,
        si_trigger=si_trigger_summary,
    )


async def calculate_phosphorus_removal_dose(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Deprecated Python alias for the equilibrium screening tool.

    The MCP server intentionally exposes only
    :func:`screen_equilibrium_phosphorus_removal`; this alias remains for one
    compatibility cycle for direct library callers.
    """

    return await screen_equilibrium_phosphorus_removal(input_data)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_initial_p_mg_l(solution_data: Dict[str, Any]) -> float:
    """Extract initial P concentration from solution data and convert to mg/L.

    Handles unit conversion based on solution_data["units"]:
    - "mg/L" or "mg/l": No conversion (default assumption)
    - "mmol/L" or "mmol/l": Convert mmol/L to mg/L using P molecular weight

    Args:
        solution_data: Solution dict with "analysis" and optionally "units"

    Returns:
        P concentration in mg/L as P
    """
    analysis = solution_data.get("analysis", {})
    units = solution_data.get("units", "mg/L").lower()

    # Try different P keys
    p_value = None
    for key in ["P", "PO4", "Orthophosphate-P"]:
        if key in analysis:
            value = analysis[key]
            if isinstance(value, (int, float)):
                p_value = float(value)
                break
            elif isinstance(value, str):
                # Try to extract numeric value
                try:
                    p_value = float(value.split()[0])
                    break
                except (ValueError, IndexError):
                    pass

    if p_value is None:
        return 0.0

    # Convert based on units (Issue 6 fix)
    if "mmol" in units:
        # Convert mmol/L to mg/L: mg/L = mmol/L * MW
        p_value = mmol_to_mg_l(p_value, "P")
        logger.debug(f"Converted P from mmol/L to {p_value:.2f} mg/L")

    return p_value


def _with_reactive_p_mg_l(
    solution_data: Dict[str, Any], reactive_p_mg_l: float
) -> Dict[str, Any]:
    """Return a copy whose PHREEQC P input contains only reactive dissolved P."""

    if reactive_p_mg_l < 0 or not math.isfinite(float(reactive_p_mg_l)):
        raise ValueError("reactive phosphorus must be finite and nonnegative")
    result = copy.deepcopy(solution_data)
    analysis = result.setdefault("analysis", {})
    key = next((name for name in ("P", "PO4", "Orthophosphate-P") if name in analysis), "P")
    units = str(result.get("units", "mg/L")).lower()
    analysis[key] = (
        reactive_p_mg_l / MOLECULAR_WEIGHTS["P"]
        if "mmol" in units
        else reactive_p_mg_l
    )
    return result


def _p_mass_balance_check(
    *,
    initial_reactive_p_mg_l: float,
    partitioning_data: Dict[str, Any],
    tolerance_mg_l: float,
) -> tuple[bool, Optional[float]]:
    """Close dissolved + mineral + surface P at a selected PHREEQC point."""

    dissolved = partitioning_data.get("p_dissolved_mg_L")
    if dissolved is None:
        return False, None
    mineral = sum((partitioning_data.get("p_removed_by_phase_mg_L") or {}).values())
    adsorbed = partitioning_data.get("p_adsorbed_mg_L") or 0.0
    error = abs(float(dissolved) + float(mineral) + float(adsorbed) - initial_reactive_p_mg_l)
    return error <= tolerance_mg_l, error


def _get_element_mg_l(solution_data: Dict[str, Any], element: str, keys: List[str]) -> Optional[float]:
    """Extract element concentration from solution data and convert to mg/L.

    Args:
        solution_data: Solution dict with "analysis" and optionally "units"
        element: Element symbol for MW lookup (e.g., "Ca", "S")
        keys: List of keys to try in analysis (e.g., ["Ca", "Calcium"])

    Returns:
        Concentration in mg/L, or None if not found
    """
    analysis = solution_data.get("analysis", {})
    units = solution_data.get("units", "mg/L").lower()

    value = None
    for key in keys:
        if key in analysis:
            raw_value = analysis[key]
            if isinstance(raw_value, (int, float)):
                value = float(raw_value)
                break
            elif isinstance(raw_value, str):
                try:
                    value = float(raw_value.split()[0])
                    break
                except (ValueError, IndexError):
                    pass

    if value is None:
        return None

    # Convert based on units
    if "mmol" in units:
        value = mmol_to_mg_l(value, element)

    return value


def _get_alkalinity_mg_caco3(solution_data: Dict[str, Any]) -> Optional[float]:
    """Extract alkalinity from solution data and convert to mg/L as CaCO3.

    Handles common alkalinity formats:
    - "Alkalinity": 100 (assumes mg/L as CaCO3 if units=mg/L)
    - "Alkalinity": "as CaCO3 100" (explicit)
    - "C(4)": 2.0 (mmol/L, convert to CaCO3)

    Args:
        solution_data: Solution dict with "analysis" and optionally "units"

    Returns:
        Alkalinity in mg/L as CaCO3, or None if not found
    """
    analysis = solution_data.get("analysis", {})
    units = solution_data.get("units", "mg/L").lower()

    # Try Alkalinity key first
    if "Alkalinity" in analysis:
        raw_value = analysis["Alkalinity"]
        if isinstance(raw_value, str):
            # Handle "as CaCO3 100" format
            if "CaCO3" in raw_value:
                try:
                    # Extract numeric value after "CaCO3"
                    parts = raw_value.split()
                    for i, part in enumerate(parts):
                        if part == "CaCO3" and i + 1 < len(parts):
                            return float(parts[i + 1])
                        try:
                            return float(part)
                        except ValueError:
                            continue
                except (ValueError, IndexError):
                    pass
            else:
                try:
                    return float(raw_value.split()[0])
                except (ValueError, IndexError):
                    pass
        elif isinstance(raw_value, (int, float)):
            # Assume mg/L as CaCO3 if units=mg/L
            if "mmol" in units:
                # 1 mmol/L alkalinity = 50 mg/L as CaCO3 (equiv. weight)
                return float(raw_value) * 50.0
            return float(raw_value)

    # Try C(4) (total inorganic carbon) - convert to alkalinity estimate
    if "C(4)" in analysis:
        raw_value = analysis["C(4)"]
        if isinstance(raw_value, (int, float)):
            c4_value = float(raw_value)
            if "mmol" in units:
                # C(4) in mmol/L, alkalinity ≈ C(4) as CaCO3 (simplified)
                # 1 mmol/L C(4) ≈ 50 mg/L as CaCO3
                return c4_value * 50.0
            # If mg/L, convert from mg/L C to mg/L CaCO3
            # mg/L CaCO3 = mg/L C * (50/12)
            return c4_value * (50.0 / 12.0)

    return None


def _determine_pe(redox: RedoxSpecification) -> float:
    """Determine pe value from redox specification.

    Args:
        redox: RedoxSpecification with mode and parameters

    Returns:
        pe value for the simulation

    Field mappings from RedoxSpecification:
        - mode="aerobic" -> pe=3.5 (default for O2 equilibrium)
        - mode="anaerobic" -> pe=-4.0 (fixed screening boundary; not electron-balanced)
        - mode="pe_from_orp" -> convert ORP to pe using redox.orp_mv
        - mode="fixed_pe" -> use redox.pe_value directly
    """
    if redox.mode == "aerobic":
        return 3.5
    elif redox.mode == "anaerobic":
        return -4.0
    elif redox.mode == "pe_from_orp" and redox.orp_mv is not None:
        # Use orp_mv (lowercase) - correct field name from RedoxSpecification
        temp = redox.orp_temperature_c if redox.orp_temperature_c else 25.0
        return orp_to_pe(redox.orp_mv, temp, redox.orp_reference or "SHE")
    elif redox.mode == "fixed_pe" and redox.pe_value is not None:
        # Use pe_value (not fixed_pe) - correct field name from RedoxSpecification
        return redox.pe_value
    elif redox.mode == "full_redox":
        # Full-spectrum ORP: the user sets the pe/ORP of interest (the anoxic transition of interest).
        # Default to pe=0 (the ferric<->ferrous transition midpoint) when neither is given.
        if redox.pe_value is not None:
            return redox.pe_value
        if redox.orp_mv is not None:
            temp = redox.orp_temperature_c if redox.orp_temperature_c else 25.0
            return orp_to_pe(redox.orp_mv, temp, redox.orp_reference or "SHE")
        return 0.0
    else:
        return 4.0  # Default


def _pe_constraint(strategy_name: str, redox_mode: str, pe_value: float) -> Optional[Dict[str, Any]]:
    """The redox constraint a dose run's equilibrium carries (None: pe floats from the solution)."""
    if strategy_name == "struvite":
        # Struvite (MgNH4PO4) has no redox-active constituent, and its precipitation is pH-driven
        # (needs pH ~8-9, set by the Mg reagent / digester-supernatant alkalinity). Forcing an O2(g)
        # equilibrium acidifies an ammonia-rich liquor to ~pH 1.8 and prevents precipitation, so do
        # NOT impose a redox constraint here; let pH float from the reagent + alkalinity.
        return None
    if redox_mode == "aerobic":
        # Aerobic: equilibrate with O2(g) at atmospheric partial pressure
        return {"method": "o2_equilibrium", "o2_si": -0.68}
    if redox_mode in ("anaerobic", "fixed_pe", "pe_from_orp", "full_redox"):
        # Anaerobic/fixed_pe/pe_from_orp/full_redox: Fix pe at target value using pseudo-phase
        # - anaerobic: fixed screening boundary pe=-4.0
        # - fixed_pe / full_redox: user-specified pe_value (full_redox spans the ferric<->ferrous
        #   transition, so the user sets the anoxic pe of interest)
        # - pe_from_orp: pe calculated from ORP measurement
        return {"method": "fix_pe", "target_pe": pe_value}
    # No explicit constraint (pe determined by solution equilibrium)
    # This includes fixed_fe2_fraction mode which is experimental
    return None


#: Owner rulings of 2026-10-15 on this screen; the owner's words are in

FIXED_PE_PER_MODE_RULING_ID = 'PUBLIC-RULE-1876369d9d6f'
NONCATALOGUE_REAGENT_RULING_ID = 'PUBLIC-RULE-9f1fbc904545'
#: Owner ruling of 2026-10-16 on the lane P2 findings: full_redox holds sulfate and nitrate as fed (P2-1); the fixed-pe
#: refusal on phreeqc.dat stays and the screen runs on minteq.v4.dat (P2-2); the anaerobic sulfate endpoint is
#: stoichiometric (P2-3, superseded 2026-10-17 by the stated finite donor, FINITE_DONOR_RULING_ID); a compound-named
#: analysis key needs its basis stated (P2-4, waterchem.contracts.phosphorus).
P2_FOLLOWUPS_RULING_ID = 'PUBLIC-RULE-bfdea5a72210'
#: Owner ruling 2026-10-17 on the gpt-astra review (M18 / gap G07): "P-screen anaerobic: explicit finite-donor reactions
#: now". The anaerobic mode reduces the feed's nitrate, a ferric dose (and any ferric iron fed) and the sulfate (feed and
#: reagent) only by the caller's stated finite electron donor, in that order and as far as it reaches, every product
#: booked (`engineering_utils.finite_donor_redox`). It replaced two artificial endpoint phases (Nit_to_N2, Sulfat_to_H2S)
#: that took ALL the nitrate and ALL the sulfate with no donor, no carbon product and no COD.
FINITE_DONOR_RULING_ID = 'PUBLIC-RULE-8226e460e1d8'
#: Owner ruling of 2026-10-17 on lane c15's follow-ups: C15-2 a held floating-pe dose run's dissolved O2/H2 checked
#: against the feed's (`_water_redox_checked`); C15-3 a dose run PHREEQC did not converge (its Fix_pe or Fix_H+ add
#: formula) refuses the screen, never a dose the search steps past (`tools.phreeqc.simulation`).
WATER_REDOX_RULING_ID = 'PUBLIC-RULE-d6cee394f32b'
#: Held as fed where no pe is fixed (unchanged since 2026-10-15): sulfate stays coupled, with no reductant to meet it.
_NO_FIXED_PE_HELD = ("N(-3)", "N(+5)", "S(-2)")
#: Held as fed at every fixed pe, so only the metals' redox equilibrates: fixed_pe, pe_from_orp and full_redox; and the
#: anaerobic mode, whose nitrate and sulfate then move only by its donor's REACTION.
_FIXED_PE_HELD = ("N(-3)", "N(+5)", "S(-2)", "S(+6)")
#: The decoupled element of each species the donor's reaction moves (`engineering_utils.phreeqc_batch_redox`; the
#: ammonium element stands for the NH3 unit, NH4+ being AmmH+).
_DONOR_ELEMENT_NAMES = {"nitrate": "Nit", "sulfate": "Sulfat", "sulfide": "Sulf", "ammonia": "Amm"}


def _held_redox_states(strategy_name: str, redox_mode: str) -> Tuple[str, ...]:
    'Public engineering calculation interface.'
    constraint = _pe_constraint(strategy_name, redox_mode, 0.0)
    if constraint is None or constraint.get("method") == "o2_equilibrium":
        return _NO_FIXED_PE_HELD
    return _FIXED_PE_HELD


def _uses_finite_donor(strategy_name: str, redox_mode: str) -> bool:
    """Whether a mode reduces by the stated donor: the anaerobic mode where it fixes the pe (struvite fixes none)."""
    constraint = _pe_constraint(strategy_name, redox_mode, 0.0)
    return redox_mode == "anaerobic" and constraint is not None and constraint.get("method") == "fix_pe"


def _batch_redox_label(strategy_name: str, redox_mode: str) -> str:
    """The assumption ledger's ``batch_redox`` entry for a mode."""
    if _uses_finite_donor(strategy_name, redox_mode):
        return "ammonium_held_as_fed_nitrate_ferric_sulfate_reduced_by_the_stated_finite_donor"
    if "S(+6)" in _held_redox_states(strategy_name, redox_mode):
        return "ammonium_nitrate_sulfate_sulfide_held_as_fed"
    return "ammonium_nitrate_sulfide_held_as_fed"


def _water_redox_checked(strategy_name: str, redox_mode: str) -> bool:
    'Public engineering calculation interface.'
    return _pe_constraint(strategy_name, redox_mode, 0.0) is None


def _post_digester_anaerobic_warning(feed_source: str) -> str:
    'Public engineering calculation interface.'
    return (
        f"POST-DIGESTER FEED IN THE ANAEROBIC MODE ({INPUT_KEY_RULING_ID}): this feed is declared '{feed_source}', a "
        "post-digester liquor, but the 'anaerobic' mode is the digester screened on its FEED: it has reduced this "
        "liquor's sulfate (and a sulfate reagent's) as far as the stated donor reaches, although the liquor's sulfate "
        "already survived digestion. So the sulfide competing for iron, and the iron dose, are overstated by the "
        "sulfate reduced. Screen a post-digester liquor in redox mode 'fixed_pe' (its pe), which holds its sulfate and "
        "sulfide as measured."
    )


def _donor_spec(input_model: "CalculatePhosphorusRemovalDoseInput") -> Optional[Dict[str, Any]]:
    """The request's donor resolved: the compound (`engineering_utils.finite_donor_redox.donor_compound`), its COD,
    basis, source and cell-synthesis fractions; None when the request states none."""
    from puran_mcp_support.engineering.finite_donor_redox import donor_compound

    from water_chemistry_mcp.waterchem.contracts.phosphorus import ElectronDonorRequest

    if input_model.electron_donor is None:
        return None
    request = ElectronDonorRequest.model_validate(input_model.electron_donor)
    return {
        "compound": donor_compound(request.compound, request.formula),
        "available_cod_mg_l": request.available_cod_mg_l,
        "basis": request.basis,
        "source": request.source,
        "cell_synthesis_fraction": request.fractions(),
    }


def _finite_donor_reduction(
    redox_hold: Dict[str, Any], added: Dict[str, float], ferric_mol: float, feed_litres: float
):
    """The stated donor allocated to one dose run, in moles of the system: the feed's nitrate, the ferric iron (fed and
    dosed) and the sulfate (fed and dosed) in `engineering_utils.finite_donor_redox.ACCEPTOR_SEQUENCE`. Nitrate and
    ferric iron must be reduced completely (pe -4 holds neither), else `DonorInsufficientError`; sulfate goes as far as
    the donor reaches."""
    from puran_mcp_support.engineering.finite_donor_redox import donor_electron_mol, reduce_with_finite_donor
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLED_NITRATE, DECOUPLED_SULFATE

    donor = redox_hold["finite_donor"]
    return reduce_with_finite_donor(
        donor["compound"],
        donor_electron_mol(donor["available_cod_mg_l"], feed_litres),
        {
            "nitrate_to_n2": redox_hold["before"].get(DECOUPLED_NITRATE, 0.0),
            "ferric_to_ferrous": redox_hold.get("feed_ferric_mol", 0.0) + ferric_mol,
            "sulfate_to_sulfide": redox_hold["before"].get(DECOUPLED_SULFATE, 0.0) + added.get(DECOUPLED_SULFATE, 0.0),
        },
        donor["cell_synthesis_fraction"],
        complete=("nitrate_to_n2", "ferric_to_ferrous"),
    )


class BatchRedoxMovedError(ValueError):
    """A dose run's equilibrium moved a redox state beyond what a phase took (`check_batch_redox`)."""


def _decoupled_analysis_keys(states: Tuple[str, ...]) -> Dict[str, str]:
    """Analysis keys carried decoupled for the held ``states``: the shared map, plus the solution builder's bare "N"
    (read as nitrate) and bare "S" (read as sulfate; waterchem.phreeqc_input.build_solution_block ELEMENT_MAPPING)."""
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLED_NITRATE, DECOUPLED_SULFATE, decoupled_analysis_keys

    keys = decoupled_analysis_keys(states)
    if DECOUPLED_NITRATE in keys.values():
        keys["N"] = DECOUPLED_NITRATE
    if DECOUPLED_SULFATE in keys.values():
        keys["S"] = DECOUPLED_SULFATE
    return keys


def _held_reagent_formula(reagent: str, states: Tuple[str, ...]) -> str:
    """The reagent's formula as the REACTION adds it: a held sulfate's sulfur on its decoupled element (FeSO4 ->
    FeSulfatO4), so the dose's own sulfate is held with the feed's. Refuses a reagent carrying a held state it cannot
    place (every sulfur of a screened reagent is in SO4; none carries nitrogen)."""
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLABLE_STATES, formula_element_counts

    counts = formula_element_counts(reagent)
    if counts.get("N") and any(state.startswith("N(") for state in states):
        raise ValueError(f"reagent {reagent} carries nitrogen, which the screen holds decoupled: its valence state "
                         "is not known, so it cannot be added on a decoupled element")
    if not counts.get("S") or "S(+6)" not in states:
        return reagent
    if reagent.count("SO4") != len(re.findall(r"S(?![a-z])", reagent)):
        raise ValueError(f"reagent {reagent} carries sulfur outside SO4: it cannot be added on the held sulfate")
    return DECOUPLABLE_STATES["S(+6)"][2](reagent)


async def _decouple_batch_redox(
    initial_solution: Dict[str, Any],
    inline_prefix: str,
    database_path: str,
    phases: List[str],
    surface_name: Optional[str],
    states: Tuple[str, ...],
    reagent: Optional[str] = None,
    *,
    finite_donor: Optional[Dict[str, Any]] = None,
    water_redox: bool = False,
) -> Dict[str, Any]:
    "The held states go in on redox-uncoupled elements derived from the database's own definitions; every phase of the\n    run that holds one is re-defined on them under its own name (struvite on AmmH+), and every surface species that\n    binds one is carried onto its element (HFO sulfate onto SulfatO4-2), so a phase or a surface still takes what it\n    held. A held sulfate's reagent is added on its element too (`_held_reagent_formula`). With a ``finite_donor``\n    (`_donor_spec`; the anaerobic mode) every dose run's REACTION also carries the donor's reduction of the nitrate, the\n    ferric iron and the sulfate (`_finite_donor_reduction`), which needs all four states held; the feed's ferric iron is\n    read here for it. The feed's moles of every redox state are read once from the decoupled feed; each dose run is then\n    checked against them (`_check_dose_redox`). Refuses where the database cannot be decoupled. The batch has no\n    exchanger, so a database's exchange species (phreeqc.dat's NH4X) bind nothing and are left alone."
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLED_SULFATE, batch_redox_elements, batch_redox_states, decoupled_phase_definitions, decoupled_redox_definitions, decoupled_surface_definitions
    from water_chemistry_mcp.waterchem.defined_analytes import database_names
    from water_chemistry_mcp.waterchem.phreeqc_input import build_solution_block

    if finite_donor is not None and not set(_FIXED_PE_HELD) <= set(states):
        raise ValueError(f"the stated donor reduces nitrate and sulfate to sulfide, which needs {list(_FIXED_PE_HELD)} "
                         f"held; held: {sorted(states)}")
    database_text = Path(database_path).read_text(encoding="latin-1")
    definitions = decoupled_redox_definitions(database_text, states)
    texts = [database_text, inline_prefix]
    has_surface = bool(_surface_host_phase(surface_name, phases))
    surface_block = decoupled_surface_definitions(database_text, texts, states) if has_surface else ""
    phase_block, formulas = decoupled_phase_definitions(database_text, texts, phases, states)
    solution = copy.deepcopy(initial_solution)
    analysis = dict(solution.get("analysis", {}) or {})
    for coupled, decoupled in _decoupled_analysis_keys(states).items():
        if coupled in analysis:
            if decoupled in analysis:
                raise ValueError(
                    f"the analysis gives both {coupled} and {decoupled}: one inventory, one key"
                )
            analysis[decoupled] = analysis.pop(coupled)
    solution["analysis"] = analysis
    prefix = "\n".join(part for part in (inline_prefix, definitions, phase_block, surface_block) if part)
    punched = batch_redox_states(database_text, states)
    defined = database_names(database_path)
    ferric = ("Fe(3)",) if finite_donor is not None and "Fe(3)" in defined else ()
    hydrogen = ("H(0)",) if "H(0)" in defined else ()
    water_states: Tuple[str, ...] = ()
    if water_redox:
        if not {"O(0)", "H(0)"} <= set(defined):
            raise ValueError(f"{Path(database_path).name} defines no O(0)/H(0): a floating-pe dose run's dissolved O2 "
                             f"and H2 cannot be checked against the feed ({WATER_REDOX_RULING_ID}, C15-2)")
        water_states = ("O(0)", "H(0)")
    feed_input = "\n".join(
        (
            prefix,
            build_solution_block(solution, solution_num=1),
            "SELECTED_OUTPUT 1\n    -reset false\n    -water true\n    -high_precision true\n    -totals "
            + " ".join(dict.fromkeys([*punched, *ferric, *hydrogen, *water_states])),
        )
    ) + "\nEND\n"
    result = await run_phreeqc_simulation(feed_input, database_path)
    if isinstance(result, list):
        result = result[-1] if result else {}
    if is_error_payload(result):
        raise MissingSimulationOutputError(
            f"decoupled feed speciation failed: {error_payload_message(result)}"
        )
    before = _redox_state_moles(result, punched)
    elements = batch_redox_elements(states)
    if finite_donor is not None:
        # nitrate, sulfate and sulfide move only by the donor's REACTION, booked as added; a surface may take sulfate
        unchecked: Tuple[str, ...] = (DECOUPLED_SULFATE,) if has_surface else ()
    elif "S(+6)" in states:
        unchecked = (DECOUPLED_SULFATE,)  # no redox reaction left to it; it leaves onto a surface, arrives with a dose
    else:
        unchecked = ("S(6)",)  # leaves onto the oxide surface, arrives with a sulfate reagent; coupled S(-2) checked
    return {
        "solution": solution,
        "prefix": prefix,
        "states": punched,
        "held": states,
        "elements": elements,
        "unchecked": unchecked,
        "before": {state: before.get(state, 0.0) for names in elements.values() for state in names},
        "formulas": formulas,
        "finite_donor": finite_donor,
        "feed_ferric_mol": _redox_state_moles(result, ferric)["Fe(3)"] if ferric else 0.0,
        "feed_hydrogen_mol": _redox_state_moles(result, hydrogen)["H(0)"] if hydrogen else None,
        "reagent_formula": _held_reagent_formula(reagent, states) if reagent else None,
        "water_redox_before": _redox_state_moles(result, water_states) if water_states else None,
    }


def _surface_host_phase(surface_name: Optional[str], phases: List[str]) -> Optional[str]:
    """The equilibrium phase a dose run links its oxide surface to, or None (no surface in the batch): the first iron
    hydroxide of the run's phases for HFO (names already remapped to the database's), Gibbsite or Al(OH)3(a) for HAO."""
    if surface_name == "Hfo":
        return next((p for p in ("Ferrihydrite", "Fe(OH)3(a)", "Goethite", "Fe(OH)3") if p in phases), None)
    if surface_name == "Hao":
        return next((p for p in ("Gibbsite", "Al(OH)3(a)") if p in phases), None)
    return None


def _redox_state_moles(result: Dict[str, Any], states) -> Dict[str, float]:
    """Moles of each punched redox state in a PHREEQC result: the totals are per kg of water."""
    selected = result.get("selected_output_data") or {}
    mass_water_kg = (result.get("solution_summary") or {}).get("mass_kg_water")
    missing = [state for state in states if selected.get(state) is None]
    if mass_water_kg is None or missing:
        raise MissingSimulationOutputError(
            f"PHREEQC result omitted the mass of water or the redox-state totals {missing}; "
            "the batch's redox states cannot be checked"
        )
    return {state: float(selected[state]) * float(mass_water_kg) for state in states}


def _check_dose_redox(
    result: Dict[str, Any], redox_hold: Dict[str, Any], phases: List[str], step: str,
    added: Optional[Dict[str, float]] = None,
) -> None:
    """Refuse a dose run whose equilibrium moved a redox state beyond what its phases took and its REACTION added.

    The held states may leave only into the re-defined phases that hold them, and arrive or leave only with the
    REACTION (``added``, moles by decoupled element, negative where it removes: a sulfate reagent's sulfate; the stated
    donor's nitrate removed to N2, sulfate moved to sulfide, ammonium released by the donor and taken into cells); the
    coupled states they would re-equilibrate into start at zero and must stay there. What moves legitimately is read
    only to scale its element's tolerance (`_decouple_batch_redox` ``unchecked``): a coupled sulfate onto the oxide
    surface, and a held sulfate onto a surface. A refusal naming methane (C(-4), DIC reduced at the fixed pe on
    phreeqc.dat or wateq4f.dat) says to screen on minteq.v4.dat, which defines no CH4 (P2-2).
    """
    from puran_mcp_support.engineering.phreeqc_batch_redox import BATCH_REDOX_ABSOLUTE_TOLERANCE_MOL, BATCH_REDOX_RELATIVE_TOLERANCE, DECOUPLABLE_STATES, check_batch_redox, check_water_redox, formula_element_counts

    after = _redox_state_moles(result, redox_hold["states"])
    selected = result.get("selected_output_data") or {}
    taking = set(phases)
    taken: Dict[str, float] = {}
    for phase, formula in redox_hold["formulas"].items():
        if phase not in taking:
            continue
        moles = selected.get(phase)
        if moles is None:
            raise MissingSimulationOutputError(
                f"PHREEQC result omitted the moles of {phase}; what it took cannot be checked"
            )
        counts = formula_element_counts(formula)
        for element, *_rest in DECOUPLABLE_STATES.values():
            if counts.get(element):
                taken[element] = taken.get(element, 0.0) + float(moles) * counts[element]
    for element, moles in (added or {}).items():
        taken[element] = taken.get(element, 0.0) - moles

    def tolerance(element: str) -> float:
        scale = sum(abs(redox_hold["before"].get(state, 0.0)) for state in redox_hold["elements"].get(element, ()))
        return BATCH_REDOX_RELATIVE_TOLERANCE * scale + BATCH_REDOX_ABSOLUTE_TOLERANCE_MOL

    try:
        check_batch_redox(
            redox_hold["before"],
            {state: after.get(state, 0.0) for state in redox_hold["before"]},
            step=step,
            taken=taken,
            unchecked=redox_hold["unchecked"],
            elements=redox_hold["elements"],
        )
    except ValueError as exc:
        message = str(exc)
        methane = after.get("C(-4)", 0.0) - redox_hold["before"].get("C(-4)", 0.0)
        if "C(-4)" in redox_hold["before"] and methane > tolerance("C"):
            message += (
                f" This database reduces dissolved inorganic carbon to methane at the fixed pe ({methane:.6g} mol "
                f"C(-4)): screen the fixed-pe modes on minteq.v4.dat, which defines no CH4 "
                f"({P2_FOLLOWUPS_RULING_ID}, P2-2)."
            )
        raise BatchRedoxMovedError(message) from exc
    water_before = redox_hold.get("water_redox_before")
    if water_before is not None:
        # C15-2: dissolved O2 and H2 against the feed's, the tolerance on the held inventory (`check_water_redox`)
        held_mol = sum(abs(redox_hold["before"].get(DECOUPLABLE_STATES[state][0], 0.0)) for state in redox_hold["held"])
        try:
            check_water_redox(water_before, _redox_state_moles(result, tuple(water_before)), step=step,
                              held_mol=held_mol,
                              water_kg=float((result.get("solution_summary") or {}).get("mass_kg_water") or 0.0))
        except ValueError as exc:
            raise BatchRedoxMovedError(str(exc)) from exc


#: The fixed pe is held by a Fix_pe pseudo-phase titrated with O2(g) (`waterchem.phreeqc_input`): each mole of O2 it
#: takes OUT of the water hands the water 4 electrons, so the electrons the boundary supplied are 4 x d_Fix_pe.
FIX_PE_ELECTRONS_PER_REACTANT_MOL = 4.0
#: The donor closes a dose run's electrons when the boundary supplied no more than this share of the donor's electron
#: flow, plus this many moles (PHREEQC converged d_Fix_pe to 1e-10 - 3e-10 mol on every closed probe; a 1 mmol FeCl3
#: dose with no donor read 1.000 mmol: closure/c03-pscreen-donor/runs/probe_ferric_boundary.txt).
BOUNDARY_ELECTRON_RELATIVE_TOLERANCE = 1e-4
BOUNDARY_ELECTRON_ABSOLUTE_TOLERANCE_MOL = 1e-8


class RedoxBoundaryElectronsError(BatchRedoxMovedError):
    """The fixed-pe boundary supplied electrons the stated donor's reaction did not account for."""


def _boundary_electrons_mol(result: Dict[str, Any]) -> float:
    """Electrons the Fix_pe boundary handed the water in one run, moles (negative: it took them)."""
    selected = result.get("selected_output_data") or {}
    delta = selected.get("d_Fix_pe")
    if delta is None:
        raise MissingSimulationOutputError("PHREEQC result omitted d_Fix_pe; the fixed pe's electrons cannot be read")
    return FIX_PE_ELECTRONS_PER_REACTANT_MOL * float(delta)


def _dose_reaction(
    redox_hold: Optional[Dict[str, Any]],
    reaction_formula: str,
    product_mmol: float,
    *,
    ferric_mmol: float = 0.0,
    feed_litres: float = 1.0,
):
    """What one dose run's REACTION adds, in mmol of the system: the reagent and, with a stated donor
    (`_finite_donor_reduction`), the donor's molecule and the acceptors' element changes; the moles that adds of each
    decoupled element (negative where it removes), for the redox check; and the donor's allocation (None without one).
    ``ferric_mmol``: the ferric iron the dose adds to the system."""
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLABLE_STATES, formula_element_counts

    reactants = [(reaction_formula, product_mmol)] if product_mmol > 0 else []
    if redox_hold is None:
        return reactants, {}, None
    counts = formula_element_counts(reaction_formula)
    added = {
        element: product_mmol / 1000.0 * counts[element]
        for element, *_rest in DECOUPLABLE_STATES.values() if counts.get(element) and product_mmol > 0
    }
    if not redox_hold.get("finite_donor"):
        return reactants, added, None
    reduction = _finite_donor_reduction(redox_hold, added, ferric_mmol / 1000.0, feed_litres)
    elements = set(_DONOR_ELEMENT_NAMES.values())
    for formula, mol in reduction.element_changes(_DONOR_ELEMENT_NAMES):
        reactants.append((formula, mol * 1000.0))
        for element, n in formula_element_counts(formula).items():
            if element in elements:
                added[element] = added.get(element, 0.0) + mol * n
    return reactants, added, reduction


def _exact_reaction_block(reactants: List[Tuple[str, float]]) -> str:
    """A REACTION adding each reactant's mmol exactly (negative removes): the first at coefficient 1.0, the others at
    their ratio to it written to full precision (`build_reaction_block` writes 6 figures, which would leave a moved
    element off by up to 5e-6 of it)."""
    first_mmol = reactants[0][1]
    lines = ["REACTION 1", f"    {reactants[0][0]} 1.0"]
    lines += [f"    {formula} {mmol / first_mmol!r}" for formula, mmol in reactants[1:]]
    lines.append(f"    {first_mmol!r} mmol in 1 steps")
    return "\n".join(lines) + "\n"


async def _feed_litres_per_system(
    initial_solution: Dict[str, Any], inline_prefix: str, database_path: str
) -> float:
    """Return the litres of feed one PHREEQC batch system represents.

    PHREEQC reads a mg/L feed into a system holding the solution's kilograms of water, and reports
    dissolved totals per kg of WATER and phase amounts per SYSTEM. On a digestate centrate (about
    1.4 % solutes) treating either as "per litre" overstates residual P by 1.4 % and leaves the
    dissolved + mineral P 10.7 mg/L above the feed's 769 (ZLR gap G5, 2026-10-15). The factor is
    PHREEQC's own conversion of the feed's P: system P moles over the feed's P in mol/L.
    """
    from water_chemistry_mcp.waterchem.phreeqc_input import build_solution_block

    p_mg_l = _get_initial_p_mg_l(initial_solution)
    if p_mg_l <= 0:
        raise ValueError("the feed carries no reactive P to fix the per-litre basis")
    phreeqc_input = "\n".join(
        part
        for part in (
            inline_prefix,
            build_solution_block(initial_solution, solution_num=1),
            "SELECTED_OUTPUT 1\n    -reset false\n    -water true\n    -totals P",
        )
        if part
    ) + "\nEND\n"
    result = await run_phreeqc_simulation(phreeqc_input, database_path)
    if isinstance(result, list):
        result = result[-1] if result else {}
    if is_error_payload(result):
        raise MissingSimulationOutputError(f"feed speciation failed: {error_payload_message(result)}")
    p_molal = (result.get("element_totals_molality") or {}).get("P")
    if p_molal is None:
        p_molal = (result.get("selected_output_data") or {}).get("P")
    mass_water_kg = (result.get("solution_summary") or {}).get("mass_kg_water")
    if p_molal is None or mass_water_kg is None:
        raise MissingSimulationOutputError(
            "feed speciation omitted the P total or the mass of water; the per-litre basis cannot be fixed"
        )
    litres = float(p_molal) * float(mass_water_kg) / (p_mg_l / MOLECULAR_WEIGHTS["P"] / 1000.0)
    # A solution of density 1 kg/L holding 250 g/L of solutes is 1.33 L per kg of water. Outside
    # 0.5-2 L the screen and PHREEQC read the feed's P on different bases (a "PO4" entry read as P).
    if not 0.5 < litres < 2.0:
        raise ValueError(
            f"PHREEQC read the feed's P as {litres:.3f} L of feed per system; the screen's P basis "
            f"({p_mg_l:g} mg/L as P) and the database's disagree"
        )
    return litres


def _system_to_feed_litre_factor(
    result: Dict[str, Any], feed_litres_per_system: Optional[float]
) -> Dict[str, float]:
    """Multipliers from a PHREEQC result to per-litre-of-feed amounts.

    ``dissolved`` turns a molality (mol/kg water) into mol per litre of feed; ``system`` turns a
    system amount (equilibrium-phase or surface moles) into mol per litre of feed. Without a feed
    basis both are 1: the dilute-water shortcut of 1 kg of water = 1 L.
    """
    if feed_litres_per_system is None:
        return {"dissolved": 1.0, "system": 1.0}
    mass_water_kg = (result.get("solution_summary") or {}).get("mass_kg_water")
    if mass_water_kg is None:
        raise MissingSimulationOutputError(
            "PHREEQC result omitted the mass of water; dissolved P cannot be put per litre of feed"
        )
    return {
        "dissolved": float(mass_water_kg) / feed_litres_per_system,
        "system": 1.0 / feed_litres_per_system,
    }


def _donor_covers_the_dose_range(
    redox_hold: Dict[str, Any], reagent: str, max_dose_mmol: float, feed_litres: float
) -> Optional[str]:
    """Why the stated donor cannot serve the whole dose range, or None. Nitrate and ferric iron must be reduced
    completely at every dose (pe -4 holds neither), so the donor must cover the feed's nitrate and ferric iron and the
    ferric iron of the largest dose; the dose interval is never narrowed silently to fit it."""
    from puran_mcp_support.engineering.finite_donor_redox import DonorInsufficientError

    reagent_info = REAGENT_DEFINITIONS.get(reagent, {})
    ferric = reagent_info.get("oxidation_state") == 3
    try:
        _finite_donor_reduction(redox_hold, {}, 0.0, feed_litres)
    except DonorInsufficientError as exc:
        return f"The stated donor cannot hold the anaerobic mode's pe even with no dose: {exc}"
    if not ferric:
        return None
    try:
        _finite_donor_reduction(redox_hold, {}, max_dose_mmol / 1000.0 * feed_litres, feed_litres)
    except DonorInsufficientError as exc:
        donor = redox_hold["finite_donor"]
        fs = donor["cell_synthesis_fraction"]
        spare = (
            donor["available_cod_mg_l"] / 8000.0
            - redox_hold["before"].get("Nit", 0.0) / feed_litres * 5.0 / (1.0 - fs["nitrate_to_n2"])
            - redox_hold.get("feed_ferric_mol", 0.0) / feed_litres / (1.0 - fs["ferric_to_ferrous"])
        )
        largest = max(spare * (1.0 - fs["ferric_to_ferrous"]) * 1000.0, 0.0)
        return (
            f"The stated donor cannot reduce the ferric iron of the dose range's top ({max_dose_mmol:g} mmol/L "
            f"{reagent}): {exc}. The largest ferric dose it reduces with the feed's nitrate is {largest:.6g} mmol/L; "
            "set strategy.max_dose_mmol at or below it, or state more donor."
        )
    return None


def _fixed_pe_boundary_record(
    boundary_mol: float, hydrogen_mol: float, reduction, per_litre: float, pe_value: float
) -> Dict[str, Any]:
    """What a fixed-pe dose run's boundary did, per litre of feed: the electrons it supplied, the share that went to the
    dissolved H2 a pe this low holds at the run's pH (2H+ + 2e- = H2(aq), one electron per H(0); measured, 2.35e-6 mol
    at FeCl2 25 mmol/L, pH 5.37: closure/c03-pscreen-donor/runs/probe_feCl2_boundary.txt), and whether a stated donor
    accounts for the rest (the anaerobic mode) or nothing does (fixed_pe, pe_from_orp, full_redox: a pe is a redox
    CONSTRAINT, not a measured donor flux; review M17). The H2 is the fixed pe's, not the donor's: a digester's comes
    from fermentation, and its COD is reported, not charged to the donor."""
    return {
        "target_pe": pe_value,
        "electrons_supplied_mmol_per_l": boundary_mol * per_litre * 1000.0,
        "electrons_to_dissolved_h2_mmol_per_l": hydrogen_mol * per_litre * 1000.0,
        "dissolved_h2_cod_mg_l": hydrogen_mol * per_litre * 8000.0,
        "closed_by_donor": reduction is not None,
        "basis": (
            "4 x d_Fix_pe: the O2(g) the Fix_pe pseudo-phase took out of (or put into) the water to hold the pe"
        ),
        "meaning": (
            "the donor's reaction accounts for the electrons; the boundary supplied none beyond tolerance"
            if reduction is not None else
            "electrons the fixed pe supplied with no donor behind them (a ferric dose reduced to ferrous, one per Fe): "
            "an equilibrium constraint, not a measured electron-donor flux"
        ),
    }


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
    oxide_surface_mode: str = "fresh_in_situ",
    extract_partitioning: bool = False,
    target_ph: Optional[float] = None,
    oxide_formation_ph: Optional[float] = None,
    report_si_phases: Optional[List[str]] = None,
    feed_litres_per_system: Optional[float] = None,
    redox_hold: Optional[Dict[str, Any]] = None,
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
        extract_partitioning: Whether to add USER_PUNCH for P partitioning data
        target_ph: Optional controlled bulk reaction pH
        oxide_formation_ph: Explicit local pH used to select a fresh-oxide capacity
        report_si_phases: Phases whose saturation index must be punched even when they are not
            in the equilibrium assemblage (the default SELECTED_OUTPUT list omits the Ca-P phases)
        feed_litres_per_system: Litres of feed the PHREEQC system represents
            (:func:`_feed_litres_per_system`). Given, the dose is added per litre of feed and the
            residual and partition are reported per litre of feed; None keeps the dilute-water
            shortcut of 1 kg of water = 1 L, which a concentrated feed (a digestate centrate)
            does not satisfy.
        redox_hold: :func:`_decouple_batch_redox` of this feed: ``initial_solution`` and
            ``inline_prefix`` must be its decoupled ones. The run adds the reagent on the held
            elements (with a stated donor, its reduction too: `_dose_reaction`), punches every redox
            state and the phases that hold a decoupled one, and refuses (:class:`BatchRedoxMovedError`)
            where its equilibrium moved a redox state beyond what those phases took and the REACTION
            added, or (:class:`RedoxBoundaryElectronsError`) where the fixed pe supplied electrons the
            donor did not.

    Returns:
        Simulation result dict with residual P, pH, precipitated phases,
        and optionally partitioning data (phase_moles_mmol_per_L, p_adsorbed_mg_L, etc.)
    """
    from water_chemistry_mcp.waterchem.phreeqc_input import build_equilibrium_phases_with_pe_constraint, build_fix_pe_phase, build_fix_ph_phase, build_phase_linked_surface_block, build_reaction_block, build_selected_output_block, build_solution_block, build_user_punch_for_partitioning

    # Build PHREEQC input
    phreeqc_input_parts = []
    surface_model_metadata: Optional[Dict[str, Any]] = None

    # Add inline blocks if needed
    if inline_prefix:
        phreeqc_input_parts.append(inline_prefix)

    # Build solution block
    solution_block = build_solution_block(initial_solution, solution_num=1)
    phreeqc_input_parts.append(solution_block)

    # Build reaction block (reagent addition)
    reagent_info = REAGENT_DEFINITIONS.get(reagent, {})
    metal_atoms = reagent_info.get("metal_atoms", 1)
    product_mmol = dose_mmol / metal_atoms  # mmol of reagent product per litre of feed
    if feed_litres_per_system is not None:
        product_mmol *= feed_litres_per_system  # the REACTION adds to the whole system

    reaction_formula = (redox_hold or {}).get("reagent_formula") or reagent
    # the ferric iron the dose adds to the system: a stated donor reduces it (pe -4 holds no Fe(III))
    ferric_mmol = product_mmol * metal_atoms if reagent_info.get("oxidation_state") == 3 else 0.0
    reactants, added, reduction = _dose_reaction(
        redox_hold, reaction_formula, product_mmol, ferric_mmol=ferric_mmol,
        feed_litres=feed_litres_per_system if feed_litres_per_system is not None else 1.0,
    )
    if len(reactants) > 1:
        phreeqc_input_parts.append(_exact_reaction_block(reactants))
    elif reactants:
        reaction_block = build_reaction_block(
            [{"formula": formula, "amount": mmol, "units": "mmol"} for formula, mmol in reactants]
        )
        phreeqc_input_parts.append(reaction_block)

    # Build equilibrium phases block with pe constraint.
    phases_list = [{"name": p, "target_si": 0.0, "initial_moles": 0.0} for p in phases]

    pe_constraint = _pe_constraint(strategy_name, redox_mode, pe_value)
    fixed_pe = bool(pe_constraint and pe_constraint.get("method") == "fix_pe")

    # The Fix_pe method references a pseudo-phase that must be DEFINED (a PHASES block) before the
    # EQUILIBRIUM_PHASES block uses it, or PHREEQC errors "Phase not found in database, Fix_pe"
    # (breaking anaerobic iron/Vivianite and anaerobic struvite through the public path).
    if pe_constraint and pe_constraint.get("method") == "fix_pe":
        phreeqc_input_parts.append(build_fix_pe_phase())

    # Hold pH at a target operating value (Fix_H+ pseudo-phase titrated with NaOH). Without this the
    # acidifying coagulant (FeCl3/AlCl3) drops pH and the answer depends on where pH lands; holding it
    # lets the combined precipitation + HFO model be evaluated at a controlled pH (plant pH-trim), the
    # condition that puts the tool at parity with the fixed-pH mADM1/PCM reference.
    ph_constraint = None
    if target_ph is not None:
        phreeqc_input_parts.append(build_fix_ph_phase())
        ph_constraint = {"target_ph": target_ph, "reagent": "NaOH", "reagent_moles": 10.0}

    phases_block = build_equilibrium_phases_with_pe_constraint(
        phases_list, pe_constraint=pe_constraint, ph_constraint=ph_constraint, allow_empty=True
    )
    if phases_block:
        phreeqc_input_parts.append(phases_block)

    # Build SURFACE block for metal hydroxide adsorption (HFO or HAO)
    if surface_name:
        phase_for_surface = _surface_host_phase(surface_name, phases)
        if phase_for_surface:
            # Build phase-linked surface block
            # For HAO, use the custom block builder from inline_phases
            if surface_name == "Hao":
                # Freshly-dosed alum forms in-situ amorphous Al(OH)3 whose active P-site density is far
                # higher than the aged ~0.2 mol/mol default (same bug the HFO fix addressed). Set the
                # weak-site density from the fresh-HAO Css (anchored on Szabo 2008 Al~=Fe). The Al
                # amphoteric high-pH degradation is handled by the Gibbsite equilibrium-phase link.
                if redox_mode in ("aerobic", "full_redox"):
                    surface_model_metadata = _resolve_oxide_surface_capacity(
                        surface_name="Hao",
                        oxide_surface_mode=oxide_surface_mode,
                        target_ph=target_ph,
                        oxide_formation_ph=oxide_formation_ph,
                        multiplier=hfo_site_multiplier,
                    )
                else:
                    surface_model_metadata = _resolve_oxide_surface_capacity(
                        surface_name="Hao",
                        oxide_surface_mode="preformed_aged",
                        target_ph=None,
                        oxide_formation_ph=None,
                        multiplier=hfo_site_multiplier,
                    )
                surface_block = build_hao_phase_linked_surface_block(
                    phase_name=phase_for_surface,
                    sites_per_mole_strong=surface_model_metadata["strong_sites_mol_per_mol_host"],
                    weak_to_strong_ratio=surface_model_metadata["weak_to_strong_ratio"],
                )
            else:
                # For HFO: freshly-dosed ferric forms IN-SITU HFO, whose active P-site density is far
                # higher than the aged Dzombak-Morel default (~0.2 mol/mol Fe). Set the weak-site
                # density from the Mao & Yue (2016) in-situ Css(pH) so the required ferric dose matches
                # full-scale practice (Fe:P ~1.5-2.5 for <1 mgP/L) instead of over-predicting ~4-5x.
                # Aged/preformed flocs (e.g. recycle-stream polishing) use the lower preformed density.
                if redox_mode in ("aerobic", "full_redox"):
                    surface_model_metadata = _resolve_oxide_surface_capacity(
                        surface_name="Hfo",
                        oxide_surface_mode=oxide_surface_mode,
                        target_ph=target_ph,
                        oxide_formation_ph=oxide_formation_ph,
                        multiplier=hfo_site_multiplier,
                    )
                else:
                    surface_model_metadata = _resolve_oxide_surface_capacity(
                        surface_name="Hfo",
                        oxide_surface_mode="preformed_aged",
                        target_ph=None,
                        oxide_formation_ph=None,
                        multiplier=hfo_site_multiplier,
                    )
                surface_block = build_phase_linked_surface_block(
                    surface_name="Hfo",
                    phase_name=phase_for_surface,
                    sites_per_mole=surface_model_metadata["strong_sites_mol_per_mol_host"],
                    weak_to_strong_ratio=surface_model_metadata["weak_to_strong_ratio"],
                    specific_area_per_mole=53300.0,
                )
            if surface_block:
                phreeqc_input_parts.append(surface_block)
                logger.debug(f"Added {surface_name} surface block linked to {phase_for_surface}")

    # Save the reacted solution so parse_phreeqc_results can read it
    phreeqc_input_parts.append("SAVE solution 2")

    # Build selected output with USER_PUNCH for partitioning (Issue 5)
    selected_output = build_selected_output_block(user_punch=extract_partitioning)
    # Request phosphorus explicitly. The generic builder's broad ``-tot true``
    # form is not a guarantee that every backend emits a P column, especially
    # when the final dissolved amount is numerically zero. This explicit column
    # is the fail-closed residual-P source used below.
    totals = "P" if redox_hold is None else " ".join(["P", *redox_hold["states"]])
    if fixed_pe and redox_hold is not None and redox_hold.get("feed_hydrogen_mol") is not None:
        totals += " H(0)"  # the dissolved H2 the fixed pe makes from water (`_fixed_pe_boundary_record`)
    if redox_hold is not None and redox_hold.get("water_redox_before") is not None:
        totals += " " + " ".join(s for s in redox_hold["water_redox_before"] if s not in totals.split())  # C15-2
    selected_output = selected_output.rstrip("\n") + f"\n    -totals {totals}\n"
    if redox_hold is not None:
        # The redox check differences moles of a few mmol: at the default 5 significant figures a 12.5 mmol/L FeSO4
        # dose's sulfate (0.0135 mol) rounds by 5e-7 mol, five times the check's tolerance on a 1 mmol feed.
        selected_output = selected_output.rstrip("\n") + "\n    -high_precision true\n"
    # Emit per-mineral precipitated moles as selected-output columns (header = phase name). The
    # phreeqpython backend does NOT capture the USER_PUNCH EQUI() columns, so without this the
    # per-phase P attribution (p_removed_by_phase_mg_L / precipitated_phases) comes back empty even
    # though total solid P is known. `-equilibrium_phases` IS captured by phreeqpython.
    punched_phases = [p for p in phases if p] if extract_partitioning else []
    if redox_hold is not None:
        # what a phase holding a decoupled state took is read from its own column
        punched_phases += [p for p in redox_hold["formulas"] if p in phases and p not in punched_phases]
    if fixed_pe:
        # the electrons the fixed pe handed the water (`_boundary_electrons_mol`)
        punched_phases.append("Fix_pe")
    if punched_phases:
        eq_names = " ".join(punched_phases)
        selected_output = selected_output.rstrip("\n") + f"\n    -equilibrium_phases {eq_names}\n"
    if report_si_phases:
        si_names = " ".join(p for p in report_si_phases if p)
        if si_names:
            selected_output = selected_output.rstrip("\n") + f"\n    -saturation_indices {si_names}\n"
    phreeqc_input_parts.append(selected_output)

    # Add USER_PUNCH block for partitioning extraction (Issue 5)
    # This extracts phase moles, surface-adsorbed P, and dissolved P
    if extract_partitioning:
        # Determine which element to track based on strategy
        reagent_info = REAGENT_DEFINITIONS.get(reagent, {})
        metal = reagent_info.get("metal", "Fe")
        elements_to_track = ["P", metal]

        # Build USER_PUNCH block - always include phases, only surface if surface_name is set
        user_punch = build_user_punch_for_partitioning(
            phases=phases,
            surface_name=surface_name if surface_name else "Hfo",  # Default to Hfo for parsing
            elements=elements_to_track,
            include_solution_totals=True,
        )
        phreeqc_input_parts.append(user_punch)

    # Combine input — all blocks in a single simulation step
    phreeqc_input = "\n".join(phreeqc_input_parts) + "\nEND\n"

    # Run simulation
    try:
        result = await run_phreeqc_simulation(phreeqc_input, database_path)

        if isinstance(result, list):
            result = result[-1] if result else {}

        if is_error_payload(result):
            return result
        if redox_hold is not None:
            _check_dose_redox(
                result,
                redox_hold,
                phases,
                step=(
                    f"the phosphorus screen's {redox_mode} dose run ({strategy_name}, {reagent} "
                    f"{dose_mmol:g} mmol/L)"
                ),
                added=added,
            )

        # Extract residual P
        element_totals = result.get("element_totals_molality", {})
        selected_totals = result.get("selected_output_data", {}) or {}
        if "P" in element_totals:
            p_molal = element_totals["P"]
        elif "P" in selected_totals:
            p_molal = selected_totals["P"]
        else:
            raise MissingSimulationOutputError(
                "PHREEQC parser omitted both element_totals_molality['P'] and "
                "the explicit SELECTED_OUTPUT P total; "
                "residual phosphorus cannot be inferred"
            )
        # Extract final conditions
        solution_summary = result.get("solution_summary", {})
        system_per_litre = _system_to_feed_litre_factor(result, feed_litres_per_system)
        residual_p_mg_l = p_molal * system_per_litre["dissolved"] * MOLECULAR_WEIGHTS["P"] * 1000

        # Base result
        precipitated = dict(
            result.get("equilibrium_phase_moles", {})
            or {
                p: (result.get("selected_output_data", {}) or {}).get(p, 0.0)
                for p in phases
                if ((result.get("selected_output_data", {}) or {}).get(p, 0.0) or 0.0) > 0
            }
        )
        for pseudo_phase in ("Fix_pe", "Fix_H+"):
            precipitated.pop(pseudo_phase, None)
        sim_result = {
            "residual_p_mg_l": residual_p_mg_l,
            "ionic_strength_mol_kgw": _run_ionic_strength(result),
            "ph": solution_summary.get("pH", 7.0),
            "pe": solution_summary.get("pe", pe_value),
            "precipitated_phases": precipitated,
            "saturation_indices": result.get("saturation_indices", {}),
            "surface_model": surface_model_metadata,
        }
        if fixed_pe:
            boundary_mol = _boundary_electrons_mol(result)
            hydrogen_mol = 0.0
            if redox_hold is not None and redox_hold.get("feed_hydrogen_mol") is not None:
                hydrogen_mol = _redox_state_moles(result, ["H(0)"])["H(0)"] - redox_hold["feed_hydrogen_mol"]
            sim_result["fixed_pe_boundary"] = _fixed_pe_boundary_record(
                boundary_mol, hydrogen_mol, reduction, system_per_litre["system"], pe_value
            )
        if reduction is not None:
            flow = reduction.donor_consumed_electron_mol
            unclosed = boundary_mol - hydrogen_mol
            if abs(unclosed) > BOUNDARY_ELECTRON_RELATIVE_TOLERANCE * flow + BOUNDARY_ELECTRON_ABSOLUTE_TOLERANCE_MOL:
                raise RedoxBoundaryElectronsError(
                    f"the phosphorus screen's {redox_mode} dose run ({strategy_name}, {reagent} {dose_mmol:g} mmol/L): "
                    f"the fixed pe {pe_value:g} supplied {boundary_mol:.6g} mol of electrons, {hydrogen_mol:.6g} of "
                    f"them to dissolved H2, and {unclosed:.6g} mol that the stated donor's reaction did not account "
                    f"for ({flow:.6g} mol of donor electrons): a redox couple outside the donor's acceptors (nitrate, "
                    f"ferric iron, sulfate) moved at the fixed pe, so the electrons do not close ({FINITE_DONOR_RULING_ID})"
                )
            donor = redox_hold["finite_donor"]
            ledger = reduction.ledger(system_per_litre["system"], available_cod_mg_l=donor["available_cod_mg_l"])
            ledger["donor"].update({"basis": donor["basis"], "source": donor["source"]})
            sim_result["donor_reduction"] = ledger

        # Extract partitioning data if requested (Issue 5)
        if extract_partitioning:
            sim_result.update(
                _extract_partitioning_data(
                    result=result,
                    phases=phases,
                    surface_name=surface_name,
                    strategy_name=strategy_name,
                    system_per_litre=system_per_litre,
                )
            )

        return sim_result

    except (BatchRedoxMovedError, PurePhaseNotConvergedError):
        raise
    except Exception as e:
        message = redact_paths(str(e))
        logger.error(f"PHREEQC simulation error: {message}")
        return public_error(
            "PHREEQC_EXECUTION_ERROR",
            message,
            {"tool_name": "screen_equilibrium_phosphorus_removal", "stage": "run_phreeqc_simulation"},
        )


def _extract_partitioning_data(
    result: Dict[str, Any],
    phases: List[str],
    surface_name: Optional[str],
    strategy_name: str,
    system_per_litre: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Extract P partitioning data from PHREEQC simulation result.

    Parses equilibrium phase moles and USER_PUNCH output to calculate:
    - Phase moles in mmol/L (normalized from 1 kgw basis)
    - P removed by each phase (using stoichiometry)
    - P adsorbed on surface (for iron/aluminum strategies)
    - Dissolved P remaining

    Args:
        result: Raw PHREEQC simulation result dict
        phases: List of equilibrium phase names
        surface_name: Surface type ("Hfo", "Hao") or None
        strategy_name: Strategy name for logging
        system_per_litre: :func:`_system_to_feed_litre_factor` of this result (None: 1 kg = 1 L)

    Returns:
        Dict with partitioning fields:
        - phase_moles_mmol_per_L: {phase: moles}
        - p_removed_by_phase_mg_L: {phase: mg_P}
        - p_adsorbed_mg_L: float or None
        - p_dissolved_mg_L: float
    """
    from water_chemistry_mcp.waterchem.phreeqc_input import _sanitize_phase_name_for_basic

    partitioning = {}
    per_litre = system_per_litre or {"dissolved": 1.0, "system": 1.0}

    # 1. Extract phase moles (system mol -> mmol per litre of feed). Prefer the
    # subprocess parser's equilibrium_phase_moles; fall back to the `-equilibrium_phases` selected-
    # output columns (header = phase name) captured by the phreeqpython backend.
    equilibrium_moles = dict(result.get("equilibrium_phase_moles", {}) or {})
    selected = result.get("selected_output_data", {}) or {}
    phase_moles_mmol_per_L = {}
    p_removed_by_phase_mg_L = {}

    # P stoichiometry per mole of phase (most P phases are 1:1)
    P_STOICHIOMETRY = {
        "Strengite": 1.0,  # FePO4·2H2O
        "Vivianite": 2.0,  # Fe3(PO4)2·8H2O → 2 P per formula unit
        "Variscite": 1.0,  # AlPO4·2H2O
        "Struvite": 1.0,  # MgNH4PO4·6H2O
        "Hydroxylapatite": 3.0,  # Ca5(PO4)3OH (minteq.v4.dat spelling) -> 3 P per formula unit
        "Hydroxyapatite": 3.0,  # same, alternate spelling
        "Ca4H(PO4)3:3H2O": 3.0,  # OCP-type octacalcium phosphate -> 3 P
        "Ca3(PO4)2(beta)": 2.0,  # beta-TCP -> 2 P
        "CaHPO4:2H2O": 1.0,  # Brushite
        "Hydroxyapatite": 3.0,  # Ca5(PO4)3OH → 3 P per formula unit (but often written as 1/3)
    }

    for phase in phases:
        # Try the parser map (original + sanitized name) then the -equilibrium_phases column (phase name)
        safe_phase = _sanitize_phase_name_for_basic(phase)
        moles = (
            equilibrium_moles.get(phase, 0.0)
            or equilibrium_moles.get(safe_phase, 0.0)
            or selected.get(phase, 0.0)
            or 0.0
        )
        if moles and moles > 0:
            mmol = moles * 1000 * per_litre["system"]
            phase_moles_mmol_per_L[phase] = mmol

            # Calculate P removed by this phase using stoichiometry
            p_stoich = P_STOICHIOMETRY.get(phase, 0.0)
            if p_stoich > 0:
                p_mmol_from_phase = mmol * p_stoich
                p_mg_l_from_phase = p_mmol_from_phase * MOLECULAR_WEIGHTS["P"]
                p_removed_by_phase_mg_L[phase] = p_mg_l_from_phase

    partitioning["phase_moles_mmol_per_L"] = phase_moles_mmol_per_L if phase_moles_mmol_per_L else None
    partitioning["p_removed_by_phase_mg_L"] = p_removed_by_phase_mg_L if p_removed_by_phase_mg_L else None

    # 2. Extract surface-adsorbed P (only for iron/aluminum strategies with surfaces)
    p_adsorbed_mg_L = None
    if surface_name in ("Hfo", "Hao"):
        # Try to get surface P from USER_PUNCH output or species list
        # USER_PUNCH stores: surf_P_<surface_name>
        user_punch_data = result.get("user_punch", {})

        # Try sanitized surface name lookup
        surf_p_key = f"surf_P_{surface_name}"
        if surf_p_key in user_punch_data:
            p_surf_mol = user_punch_data.get(surf_p_key, 0.0)
            if p_surf_mol and p_surf_mol > 0:
                p_adsorbed_mg_L = p_surf_mol * per_litre["system"] * MOLECULAR_WEIGHTS["P"] * 1000

        # Fallback 1: Check surface_adsorbed_moles from subprocess parser (Issue 5 fix)
        # Subprocess parser stores: P_Hfo, P_Hao (after stripping surf_ prefix)
        if p_adsorbed_mg_L is None:
            surface_adsorbed = result.get("surface_adsorbed_moles", {})
            surf_p_key_alt = f"P_{surface_name}"  # e.g., P_Hfo
            p_surf_mol = surface_adsorbed.get(surf_p_key_alt, 0.0)
            if p_surf_mol and p_surf_mol > 0:
                p_adsorbed_mg_L = p_surf_mol * per_litre["system"] * MOLECULAR_WEIGHTS["P"] * 1000

        # Fallback 2: Sum P species on surface from species_molality/species_molalities
        # Note: phreeqpython mode uses "species_molality", subprocess mode uses "species_molalities"
        if p_adsorbed_mg_L is None:
            species_molality = result.get("species_molality", {}) or result.get("species_molalities", {})
            surface_p_moles = 0.0

            # Common HFO phosphate surface species
            hfo_p_species = [
                "Hfo_wH2PO4",
                "Hfo_wHPO4-",
                "Hfo_wPO4-2",
                "Hfo_sH2PO4",
                "Hfo_sHPO4-",
                "Hfo_sPO4-2",
            ]
            # Common HAO phosphate surface species
            hao_p_species = [
                "Hao_wH2PO4",
                "Hao_wHPO4-",
                "Hao_wPO4-2",
                "Hao_sH2PO4",
                "Hao_sHPO4-",
                "Hao_sPO4-2",
            ]

            p_species_to_check = hfo_p_species if surface_name == "Hfo" else hao_p_species

            for species in p_species_to_check:
                mol = species_molality.get(species, 0.0)
                if mol and mol > 0:
                    surface_p_moles += mol

            if surface_p_moles > 0:
                p_adsorbed_mg_L = surface_p_moles * per_litre["dissolved"] * MOLECULAR_WEIGHTS["P"] * 1000

    partitioning["p_adsorbed_mg_L"] = p_adsorbed_mg_L

    # 3. Extract dissolved P (from element_totals_molality)
    element_totals = result.get("element_totals_molality", {})
    if "P" in element_totals:
        p_molal = element_totals["P"]
    elif "P" in selected:
        p_molal = selected["P"]
    else:
        raise MissingSimulationOutputError(
            "partition extraction omitted the explicit dissolved-P total"
        )
    partitioning["p_dissolved_mg_L"] = p_molal * per_litre["dissolved"] * MOLECULAR_WEIGHTS["P"] * 1000

    logger.debug(
        f"Partitioning for {strategy_name}: phases={phase_moles_mmol_per_L}, "
        f"p_removed={p_removed_by_phase_mg_L}, adsorbed={p_adsorbed_mg_L}"
    )

    return partitioning
