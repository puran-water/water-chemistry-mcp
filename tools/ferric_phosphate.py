"""
Ferric phosphate precipitation modeling tool.

.. deprecated::
    This tool is DEPRECATED. Use `calculate_phosphorus_removal_dose` instead.
    The unified tool supports multiple strategies (iron, aluminum, struvite, calcium_phosphate)
    and includes additional features:
    - Redox constraints (O2(g) equilibrium, Fix_pe) in PHREEQC deck
    - Mandatory sulfide sensitivity for anaerobic mode
    - Inert P accounting
    - SI trigger for metastability
    - Background sinks (struvite/Ca-P)

This tool calculates optimal ferric (or ferrous) dose to achieve a target
residual phosphorus concentration using:
- Thermodynamic equilibrium modeling via PHREEQC
- Surface complexation on hydrous ferric oxide (HFO)
- Redox-aware phase selection (aerobic vs anaerobic)
- Binary search optimization with bracketing

Key features:
- Database-aware phase naming (minteq.v4.dat vs phreeqc.dat)
- USER_PUNCH output for Fe/P partitioning
- Phase-linked surface sites for HFO adsorption
- Multiple redox modes (aerobic, anaerobic, pe_from_orp, fixed_pe)
"""

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.database_management import database_manager
from utils.exceptions import InputValidationError
from utils.ferric_phases import (
    estimate_initial_fe_dose,
    estimate_initial_metal_dose,
    get_hao_surface_phase,
    get_hfo_surface_phase,
    get_phases_for_coagulant,
    get_phases_for_redox_mode,
    validate_phase_redox_consistency,
)
from utils.helpers import (
    build_equilibrium_phases_block,
    build_equilibrium_phases_with_pe_constraint,
    build_fix_pe_phase,
    build_phase_linked_surface_block,
    build_reaction_block,
    build_selected_output_block,
    build_solution_block,
    build_user_punch_for_partitioning,
)

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas_ferric import (
    MOLECULAR_WEIGHTS,
    BinarySearchOptions,
    CalculateFerricDoseInput,
    CalculateFerricDoseOutput,
    FerricDoseOptimizationSummary,
    IronPartitioning,
    MarginalFePRatio,
    MechanisticPartition,
    PhAdjustmentOptions,
    PhosphatePartitioning,
    PhosphateResidualMetrics,
    RedoxDiagnostics,
    RedoxSpecification,
    SulfideSensitivityResult,
    SulfideSensitivityScenario,
    SurfaceComplexationOptions,
    get_coagulant_metal,
    get_metal_atoms_per_formula,
    is_aluminum_coagulant,
    is_iron_coagulant,
    metal_dose_to_product_dose,
    mg_l_to_mmol,
    mmol_to_mg_l,
    orp_to_pe,
    pe_to_orp,
)

logger = logging.getLogger(__name__)


# Default values for optional parameters
DEFAULT_REDOX = RedoxSpecification(mode="aerobic")
DEFAULT_SURFACE = SurfaceComplexationOptions()
DEFAULT_BINARY_SEARCH = BinarySearchOptions()
DEFAULT_PH_ADJUSTMENT = PhAdjustmentOptions(enabled=False)


async def calculate_ferric_dose_for_tp(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate optimal ferric dose to achieve target residual total phosphorus.

    Uses binary search optimization with PHREEQC thermodynamic modeling to find
    the Fe dose that achieves the target P concentration. Includes surface
    complexation on HFO and redox-aware phase selection.

    Args:
        input_data: Dictionary matching CalculateFerricDoseInput schema

    Returns:
        Dictionary matching CalculateFerricDoseOutput schema with:
        - Solution state (pH, pe, saturation indices, etc.)
        - optimization_summary with Fe dose and convergence info
        - phosphate_partitioning (dissolved, adsorbed, precipitated)
        - iron_partitioning (dissolved, precipitated)
    """
    logger.info("Running calculate_ferric_dose_for_tp tool...")

    # Step 1: Validate input
    try:
        input_model = CalculateFerricDoseInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}

    # Extract parameters
    initial_solution = input_model.initial_solution.dict()
    target_p_mg_l = input_model.target_residual_p_mg_l
    iron_source = input_model.iron_source
    database = input_model.database or "minteq.v4.dat"

    # Get optional parameters with defaults
    redox = input_model.redox or DEFAULT_REDOX
    surface_opts = input_model.surface_complexation or DEFAULT_SURFACE
    binary_opts = input_model.binary_search or DEFAULT_BINARY_SEARCH
    ph_opts = input_model.ph_adjustment or DEFAULT_PH_ADJUSTMENT
    sulfide_sensitivity = input_model.sulfide_sensitivity

    # --- Sulfide Sensitivity Enforcement for Anaerobic Mode ---
    # Check if anaerobic mode without S(-2) specified
    analysis = initial_solution.get("analysis", {})
    has_sulfide = "S(-2)" in analysis and analysis.get("S(-2)", 0) > 0
    is_anaerobic = redox.mode == "anaerobic"

    # Variables for sensitivity results (will be populated if running sensitivity analysis)
    sulfide_sensitivity_results = None

    if is_anaerobic and not has_sulfide:
        # Anaerobic mode without sulfide specified - require explicit handling
        if sulfide_sensitivity is None:
            # User hasn't specified how to handle missing sulfide
            return {
                "error": (
                    "Anaerobic mode requires sulfide specification for realistic Fe:P prediction. "
                    "Typical digesters have 20-100 mg/L S(-2). Choose one:\n"
                    "1. Add 'S(-2)' to initial_solution.analysis with measured sulfide concentration\n"
                    "2. Set 'sulfide_sensitivity': true to run sensitivity analysis at [0, 20, 50, 100] mg/L\n"
                    "3. Set 'sulfide_sensitivity': false to explicitly accept sulfide-free limit (Fe:P ≈ 1.5-1.7)"
                ),
                "status": "input_error",
                "suggestions": [
                    "Add S(-2) concentration from lab measurement to initial_solution.analysis",
                    "Enable sulfide_sensitivity for range analysis",
                    "Set sulfide_sensitivity=false to acknowledge sulfide-free optimistic estimate",
                ],
            }
        elif sulfide_sensitivity is True:
            # Run sensitivity analysis
            logger.info("Running sulfide sensitivity analysis for anaerobic mode without S(-2)")
            sulfide_sensitivity_results = await _run_sulfide_sensitivity_analysis(
                base_input_data=input_data,
                target_p_mg_l=target_p_mg_l,
                sulfide_levels=[0.0, 20.0, 50.0, 100.0],
            )
            # Continue with primary (0 mg/L sulfide-free) calculation for main output
        # If sulfide_sensitivity is False, proceed with warning (existing behavior)

    # Extract tuning parameters
    p_inert = input_model.p_inert_soluble_mg_l  # Non-reactive soluble P
    hfo_multiplier = input_model.hfo_site_multiplier  # HFO site scaling
    organics_ligand = input_model.organics_ligand_mmol_l  # Organic interference proxy

    # Apply HFO site multiplier to surface options
    if hfo_multiplier != 1.0 and surface_opts.enabled:
        surface_opts = SurfaceComplexationOptions(
            enabled=surface_opts.enabled,
            surface_name=surface_opts.surface_name,
            sites_per_mole_strong=surface_opts.sites_per_mole_strong * hfo_multiplier,
            weak_to_strong_ratio=surface_opts.weak_to_strong_ratio,
            specific_area_m2_per_mol=surface_opts.specific_area_m2_per_mol,
            no_edl=surface_opts.no_edl,
        )
        logger.info(
            f"Applied hfo_site_multiplier={hfo_multiplier}, scaled strong sites to {surface_opts.sites_per_mole_strong}"
        )

    # Step 2: Resolve database
    try:
        database_path = database_manager.resolve_and_validate_database(database, category="general")
    except Exception as e:
        logger.warning(f"Database resolution failed, using default: {e}")
        database_path = database_manager.resolve_and_validate_database("minteq.v4.dat", category="general")

    # Step 3: Determine pe based on redox mode
    pe_value = _determine_pe(redox, initial_solution.get("temperature_celsius", 25.0))
    initial_solution["pe"] = pe_value

    # Add organic ligand proxy if specified (using EDTA as representative chelating agent)
    # This accounts for organic matter interference in Fe-P precipitation
    if organics_ligand and organics_ligand > 0:
        if "analysis" not in initial_solution:
            initial_solution["analysis"] = {}
        # Add EDTA as proxy for organic ligands (1:1 Fe complexation)
        initial_solution["analysis"]["Edta"] = organics_ligand
        logger.info(f"Added EDTA proxy for organics_ligand_mmol_l={organics_ligand} mmol/L")

    # Step 4: Get initial P concentration
    initial_p_mg_l = _get_initial_p(initial_solution)
    if initial_p_mg_l <= 0:
        return {"error": "Could not determine initial P concentration from input"}

    if target_p_mg_l >= initial_p_mg_l:
        return {"error": f"Target P ({target_p_mg_l} mg/L) must be less than initial P ({initial_p_mg_l} mg/L)"}

    # Adjust effective target for inert (non-reactive) P
    # p_inert represents soluble P that won't precipitate (organic P, colloidal P)
    # Effective target for reactive P = target - inert
    effective_target_p = target_p_mg_l - p_inert
    if effective_target_p < 0:
        notes_list = [
            f"Warning: p_inert_soluble_mg_l ({p_inert}) exceeds target ({target_p_mg_l}). Target is infeasible."
        ]
        return {
            "error": f"Target P ({target_p_mg_l} mg/L) is less than non-reactive P ({p_inert} mg/L). Cannot achieve target.",
            "suggestions": [
                "Increase target_residual_p_mg_l",
                "Reduce p_inert_soluble_mg_l",
                "Pre-treat to remove organic/colloidal P",
            ],
        }
    elif p_inert > 0:
        logger.info(f"Adjusted target for p_inert={p_inert}: effective reactive P target = {effective_target_p} mg/L")

    # Initialize notes list for warnings and information
    notes = []

    # Get sulfide concentration for later use
    sulfide_mg_l = initial_solution.get("analysis", {}).get("S(-2)", 0)

    # Step 5: Get phases, surface phase, and pe constraint based on coagulant type
    # Determine metal type from coagulant formula
    metal_type = get_coagulant_metal(iron_source)

    # Get phases appropriate for this coagulant and redox mode
    # Now includes pe_constraint for thermodynamically correct redox modeling
    phases, surface_phase, surface_type, pe_constraint = get_phases_for_coagulant(
        coagulant_formula=iron_source,
        redox_mode=redox.mode,
        database=database_path,
        sulfide_mg_l=initial_solution.get("analysis", {}).get("S(-2)", 0),
        fix_pe=redox.fix_pe,  # Pass fix_pe setting from redox specification
    )

    # Update pe_constraint with calculated pe value for fixed_pe/pe_from_orp modes
    if pe_constraint and pe_constraint.get("method") == "fix_pe":
        # Use the calculated pe_value for the target
        pe_constraint["target_pe"] = pe_value
        logger.info(f"Using Fix_pe constraint with target pe={pe_value:.1f}")

    # Phase 2: Thermodynamic consistency validation
    # Check for Fe(III) phases at reducing conditions (pe < -2)
    redox_warnings = validate_phase_redox_consistency(phases, pe_value)
    for warning in redox_warnings:
        logger.warning(warning)
        notes.append(warning)

    # Phase 3: Sulfide competition warning for anaerobic mode
    if redox.mode == "anaerobic" and sulfide_mg_l > 0.1:
        sulfide_warning = (
            f"Sulfide ({sulfide_mg_l:.1f} mg/L) will compete with Vivianite for Fe. "
            f"FeS precipitation may increase Fe requirement. Consider higher Fe:P ratio."
        )
        logger.warning(sulfide_warning)
        notes.append(sulfide_warning)
        sulfide_assumption = "with_sulfide"
    elif redox.mode == "anaerobic" and sulfide_mg_l < 0.1:
        # NEW: Sulfide-free warning for anaerobic mode
        sulfide_free_warning = (
            "WARNING: No sulfide specified for anaerobic mode. "
            "Result represents SULFIDE-FREE LIMIT (Fe:P ≈ 1.5-1.7). "
            "Typical digesters have 20-100 mg/L S(-2) → expect Fe:P = 2.5-5+. "
            "Specify S(-2) in analysis for realistic digester modeling."
        )
        logger.warning(sulfide_free_warning)
        notes.append(sulfide_free_warning)
        sulfide_assumption = "sulfide_free_limit"
    else:
        # Aerobic or other modes
        sulfide_assumption = "not_applicable" if redox.mode == "aerobic" else "with_sulfide"

    # Handle surface complexation for different coagulant types
    # IMPORTANT LIMITATION: Standard PHREEQC databases (minteq.v4.dat, phreeqc.dat)
    # do NOT support Al-based P removal because:
    # 1. No AlPO4/Variscite phases are defined
    # 2. No HAO (hydrous aluminum oxide) surface complexation data exists
    # Al coagulants are NOT SUPPORTED until custom database support is added.
    if metal_type == "Al":
        error_msg = (
            "Aluminum coagulants (AlCl3, Al2(SO4)3) are not currently supported. "
            "Standard PHREEQC databases lack: (1) Al-phosphate phases (AlPO4/Variscite), "
            "and (2) HAO surface complexation data. "
            "Use Fe coagulants (FeCl3, FeSO4, Fe2(SO4)3) for P removal modeling."
        )
        logger.error(error_msg)
        return {
            "status": "input_error",
            "error": error_msg,
            "suggestions": [
                "Use FeCl3 or other Fe coagulants instead",
                "Fe coagulants work well with standard PHREEQC databases",
                "Al-P removal relies primarily on adsorption which isn't modeled in standard databases",
            ],
        }

    # Disable surface complexation for anaerobic mode (no HFO) or if no surface phase
    use_surface = surface_opts.enabled and surface_phase is not None

    # Step 6: Estimate initial metal dose bounds (Fe or Al)
    target_p_removal_mmol = (initial_p_mg_l - target_p_mg_l) / MOLECULAR_WEIGHTS["P"]
    initial_estimate = estimate_initial_metal_dose(
        target_p_removal_mmol,
        metal_type=metal_type,
        redox_mode=redox.mode,
        safety_factor=1.5,
    )

    # Set binary search bounds
    fe_min = 0.0
    fe_max = initial_estimate * binary_opts.initial_dose_multiplier

    # Auto-scale max_dose_mg_l based on initial P for high-P applications (digesters, sidestream)
    # Formula: effective_max = max(user_max, ratio * initial_P * MW_ratio)
    # Fe: 15:1 molar ratio covers worst-case high-sulfide digesters
    # Al: 18:1 molar ratio (higher due to adsorption-only mechanism)
    molar_ratio_multiplier = 18 if metal_type == "Al" else 15
    auto_scaled_max = max(
        input_model.max_dose_mg_l,
        molar_ratio_multiplier * initial_p_mg_l * (MOLECULAR_WEIGHTS[metal_type] / MOLECULAR_WEIGHTS["P"]),
    )

    if auto_scaled_max > input_model.max_dose_mg_l:
        logger.info(
            f"Auto-scaled max_dose from {input_model.max_dose_mg_l} to {auto_scaled_max:.1f} mg/L {metal_type} "
            f"(based on initial P={initial_p_mg_l} mg/L, {molar_ratio_multiplier}:1 molar {metal_type}:P)"
        )
        effective_max_dose = auto_scaled_max
    else:
        effective_max_dose = input_model.max_dose_mg_l

    # Enforce max_dose limit (convert to mmol for comparison)
    max_dose_mmol = mg_l_to_mmol(effective_max_dose, metal_type)
    if fe_max > max_dose_mmol:
        fe_max = max_dose_mmol
        logger.info(f"Capped fe_max to {effective_max_dose:.1f} mg/L ({max_dose_mmol:.3f} mmol/L)")

    logger.info(
        f"Starting binary search: target_P={target_p_mg_l} mg/L, "
        f"initial bounds=[{fe_min:.3f}, {fe_max:.3f}] mmol/L Fe"
    )

    # Step 7: Binary search with bracketing
    optimization_path = []

    try:
        result = await _binary_search_fe_dose(
            initial_solution=initial_solution,
            target_p_mg_l=effective_target_p,  # Use effective target (accounts for inert P)
            iron_source=iron_source,
            phases=phases,
            hfo_phase=surface_phase,  # Can be HFO or HAO depending on coagulant
            use_surface=use_surface,
            surface_opts=surface_opts,
            pe_constraint=pe_constraint,  # NEW: pe constraint for thermodynamically correct redox
            fe_min=fe_min,
            fe_max=fe_max,
            max_iterations=binary_opts.max_iterations,
            tolerance=binary_opts.tolerance_mg_l,
            expansion_factor=binary_opts.bracket_expansion_factor,
            database_path=database_path,
            optimization_path=optimization_path,
            notes=notes,
            ph_adjustment=ph_opts,
        )
    except Exception as e:
        logger.error(f"Binary search failed: {e}")
        return {
            "error": f"Optimization failed: {e}",
            "optimization_path": optimization_path,
        }

    # Step 8: Build output
    optimal_fe_mmol = result["optimal_fe_dose_mmol"]
    achieved_reactive_p_mg_l = result["achieved_p_mg_l"]  # Reactive P from PHREEQC
    final_state = result["final_state"]

    # Extract pH adjustment results if enabled
    ph_adj_dose_mmol = result.get("ph_adjustment_dose_mmol")
    ph_convergence_achieved = result.get("ph_convergence_achieved")

    # Total achieved P = reactive P + inert P
    achieved_p_mg_l = achieved_reactive_p_mg_l + p_inert

    # Calculate metrics
    fe_to_p_ratio = optimal_fe_mmol / (target_p_removal_mmol + 1e-9)
    p_removal_pct = ((initial_p_mg_l - achieved_p_mg_l) / initial_p_mg_l) * 100

    # Calculate product dose (e.g., FeCl3 mg/L or AlCl3 mg/L)
    # CRITICAL: Account for metal atoms per formula for multi-metal coagulants
    # e.g., 2 mmol Fe with Fe2(SO4)3 → 1 mmol Fe2(SO4)3 → 399.9 mg/L
    # e.g., 2 mmol Al with Al2(SO4)3 → 1 mmol Al2(SO4)3 → 342.15 mg/L
    metal_mg_l = mmol_to_mg_l(optimal_fe_mmol, metal_type)
    if iron_source in MOLECULAR_WEIGHTS:
        # Convert metal mmol to product mmol, then to mg/L
        product_mmol = metal_dose_to_product_dose(optimal_fe_mmol, iron_source)
        product_mg_l = mmol_to_mg_l(product_mmol, iron_source)
    else:
        product_mg_l = metal_mg_l  # Fallback if unknown product

    # Check convergence
    convergence_achieved = abs(achieved_p_mg_l - target_p_mg_l) <= binary_opts.tolerance_mg_l

    # Get solution pH and alkalinity
    solution_summary = final_state.get("solution_summary", {})
    achieved_ph = solution_summary.get("pH")

    # Get alkalinity info
    # Initial alkalinity from user input (mg/L as CaCO3)
    initial_alk = _get_alkalinity(initial_solution)

    # Final alkalinity from PHREEQC output
    # PHREEQC outputs alkalinity in eq/L; convert to mg/L as CaCO3 (multiply by 50,000)
    final_alk_eq_L = solution_summary.get("alkalinity_eq_L")
    if final_alk_eq_L is not None:
        final_alk = final_alk_eq_L * 50000  # Convert eq/L to mg/L as CaCO3
    else:
        # Fallback to other possible formats
        final_alk = solution_summary.get("alkalinity_mg_CaCO3", solution_summary.get("alkalinity"))

    if isinstance(final_alk, (int, float)) and isinstance(initial_alk, (int, float)):
        alk_consumed = initial_alk - final_alk
        alk_remaining = final_alk
    else:
        alk_consumed = None
        alk_remaining = None

    # Build optimization summary
    # Calculate pH adjustment dose in mg/L if applicable
    ph_adj_dose_mg_l = None
    if ph_adj_dose_mmol is not None and ph_opts.enabled:
        ph_adj_dose_mg_l = mmol_to_mg_l(ph_adj_dose_mmol, ph_opts.reagent)

    optimization_summary = FerricDoseOptimizationSummary(
        optimal_fe_dose_mmol=optimal_fe_mmol,
        optimal_fe_dose_mg_l=metal_mg_l,
        optimal_product_dose_mg_l=product_mg_l,
        iron_source_used=iron_source,
        initial_p_mg_l=initial_p_mg_l,
        target_p_mg_l=target_p_mg_l,
        achieved_p_mg_l=achieved_p_mg_l,
        fe_to_p_molar_ratio=fe_to_p_ratio,
        p_removal_efficiency_percent=p_removal_pct,
        iterations_taken=len(optimization_path),
        convergence_achieved=convergence_achieved,
        convergence_status=result["convergence_status"],
        ph_adjustment_dose_mmol=ph_adj_dose_mmol,
        ph_adjustment_dose_mg_l=ph_adj_dose_mg_l,
        ph_adjustment_reagent=ph_opts.reagent if ph_opts.enabled else None,
        target_ph=ph_opts.target_ph if ph_opts.enabled else None,
        achieved_ph=achieved_ph,
        ph_convergence_achieved=ph_convergence_achieved,
        alkalinity_consumed_mg_caco3_l=alk_consumed,
        alkalinity_remaining_mg_caco3_l=alk_remaining,
        redox_mode_used=redox.mode,
        pe_used=pe_value,
        surface_complexation_enabled=use_surface,
        optimization_path=optimization_path,
        notes=notes if notes else None,
    )

    # Build partitioning outputs
    p_partitioning = _build_phosphate_partitioning(final_state, initial_p_mg_l, surface_opts.surface_name)
    fe_partitioning = _build_iron_partitioning(
        final_state, optimal_fe_mmol, surface_opts.surface_name, metal_type=metal_type
    )

    # NEW: Build mechanistic partition (Phase 1)
    mechanistic_part = _build_mechanistic_partition(final_state, initial_p_mg_l, surface_opts.surface_name)

    # NEW: Calculate marginal Fe:P (Phase 2)
    marginal_fe_p = _calculate_marginal_fe_p(optimization_path)

    # NEW: Build phosphate residual metrics (explicit P accounting)
    p_residual_metrics = _build_phosphate_residual_metrics(
        state=final_state,
        p_inert_mg_l=p_inert,
        temperature_celsius=initial_solution.get("temperature_celsius", 25.0),
    )

    # NEW: Build redox diagnostics
    redox_diag = _build_redox_diagnostics(
        state=final_state,
        pe_constraint=pe_constraint,
        target_pe=pe_value,
        temperature_celsius=initial_solution.get("temperature_celsius", 25.0),
    )

    # Phase 4: Check for pe drift (pe_from_orp or fixed_pe modes)
    if pe_constraint and pe_constraint.get("method") == "fix_pe":
        target_pe = pe_constraint.get("target_pe", 0)
        final_pe = solution_summary.get("pe", target_pe)
        if abs(final_pe - target_pe) > 0.5:
            pe_drift_warning = (
                f"pe drift detected: target={target_pe:.1f}, final={final_pe:.1f}. "
                f"This may affect Fe speciation and phase stability."
            )
            logger.warning(pe_drift_warning)
            notes.append(pe_drift_warning)

    # Build final conditions
    final_conditions = {
        "ph": achieved_ph,
        "pe": solution_summary.get("pe", pe_value),
        "ionic_strength": solution_summary.get("ionic_strength"),
        "alkalinity_remaining_mg_caco3_l": alk_remaining,
    }

    # Build final output
    output = CalculateFerricDoseOutput(
        **final_state,
        status="success",
        optimization_summary=optimization_summary,
        phosphate_partitioning=p_partitioning,
        iron_partitioning=fe_partitioning,
        mechanistic_partition=mechanistic_part,
        marginal_fe_to_p=marginal_fe_p,
        phosphate_residual_metrics=p_residual_metrics,
        redox_diagnostics=redox_diag,
        sulfide_assumption=sulfide_assumption,
        sulfide_sensitivity_results=sulfide_sensitivity_results,
        precipitated_phases=final_state.get("equilibrium_phase_moles"),
        final_conditions=final_conditions,
        database_used=database,
        warnings=notes if notes else None,
    )

    logger.info(
        f"Optimization complete: Fe={optimal_fe_mmol:.3f} mmol/L, "
        f"P={achieved_p_mg_l:.3f} mg/L, {len(optimization_path)} iterations"
    )

    # Use exclude_none instead of exclude_defaults to keep status="success"
    return output.dict(exclude_none=True)


def _determine_pe(redox: RedoxSpecification, temperature: float) -> float:
    """Determine pe value based on redox mode.

    Fe:P ratio follows U-shape vs removal:
    - LOW removal: High ratio due to FIXED Fe OVERHEAD (min dose to supersaturate)
    - OPTIMAL: Lowest ratio when precipitation is efficient
    - HIGH removal: Higher ratio due to equilibrium limits (Le Chatelier)
    """
    if redox.mode == "aerobic":
        # pe=3.5 represents typical ORP (~200 mV vs SHE) in aerated wastewater
        # Note: O2/H2O equilibrium gives pe≈13.4 at DO=2mg/L, but ORP electrodes
        # don't reach equilibrium - measured ORP reflects mixed potential of multiple
        # redox couples. pe=3.5 matches typical field ORP while ensuring Fe(III) stability.
        return 3.5
    elif redox.mode == "anaerobic":
        return -4.0  # Typical anaerobic pe
    elif redox.mode == "pe_from_orp":
        # Use ORP reference and temperature from redox specification
        orp_temp = redox.orp_temperature_c if redox.orp_temperature_c is not None else temperature
        orp_ref = redox.orp_reference if redox.orp_reference else "SHE"
        return orp_to_pe(redox.orp_mv, orp_temp, orp_ref)
    elif redox.mode == "fixed_pe":
        return redox.pe_value
    elif redox.mode == "fixed_fe2_fraction":
        # Calculate pe from Fe2+/Fe3+ ratio using Nernst equation
        # pe = pe° + log([Fe3+]/[Fe2+]) where pe° ≈ 13.0 for Fe3+/Fe2+ couple at 25°C
        # If f = Fe2+/(Fe2+ + Fe3+), then [Fe3+]/[Fe2+] = (1-f)/f
        #
        # LIMITATION: PHREEQC will re-equilibrate redox species based on this pe.
        # The actual Fe2+/Fe3+ ratio in the final solution may differ from the
        # specified fraction due to reactions with other species. This mode sets
        # the INITIAL pe that corresponds to the target ratio; the equilibrated
        # ratio depends on solution composition.
        import math

        f = redox.fe2_fraction or 0.5
        f = max(0.001, min(0.999, f))  # Clamp to avoid log(0) or log(inf)
        fe3_fe2_ratio = (1.0 - f) / f
        pe_calculated = 13.0 + math.log10(fe3_fe2_ratio)
        logger.info(
            f"fixed_fe2_fraction mode: target fraction={f:.3f}, "
            f"calculated pe={pe_calculated:.2f} (Fe3/Fe2 ratio={(1-f)/f:.3f})"
        )
        return pe_calculated
    else:
        return 4.0  # Default


def _get_initial_p(solution_data: Dict[str, Any]) -> float:
    """Extract initial P concentration in mg/L from solution data."""
    analysis = solution_data.get("analysis", {})
    units = solution_data.get("units", "mg/L").lower()

    # Try various P keys
    p_keys = ["P", "P(5)", "p", "phosphorus", "phosphate", "PO4"]
    for key in p_keys:
        if key in analysis:
            val = analysis[key]
            if isinstance(val, (int, float)):
                if units in ("mg/l", "ppm"):
                    return float(val)
                elif units in ("mmol/l", "mmol"):
                    return float(val) * MOLECULAR_WEIGHTS["P"]
            break

    return 0.0


def _get_alkalinity(solution_data: Dict[str, Any]) -> Optional[float]:
    """Extract alkalinity in mg/L as CaCO3 from solution data."""
    analysis = solution_data.get("analysis", {})

    # Try various alkalinity keys
    alk_keys = ["Alkalinity", "alkalinity", "Alk", "alk"]
    for key in alk_keys:
        if key in analysis:
            val = analysis[key]
            if isinstance(val, (int, float)):
                return float(val)
            elif isinstance(val, str):
                # Parse "as CaCO3 100" format
                parts = val.split()
                try:
                    # Try to find a number in the string
                    for part in parts:
                        try:
                            return float(part)
                        except ValueError:
                            continue
                except Exception:
                    pass
            break

    return None


async def _binary_search_fe_dose(
    initial_solution: Dict[str, Any],
    target_p_mg_l: float,
    iron_source: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]],
    fe_min: float,
    fe_max: float,
    max_iterations: int,
    tolerance: float,
    expansion_factor: float,
    database_path: str,
    optimization_path: List[Dict[str, Any]],
    notes: List[str],
    ph_adjustment: Optional[PhAdjustmentOptions] = None,
) -> Dict[str, Any]:
    """
    Binary search to find optimal Fe dose, with optional nested pH adjustment.

    If ph_adjustment.enabled, uses nested binary search:
    - Outer loop: binary search for Fe dose to achieve target P
    - Inner loop: at each Fe dose, binary search for pH reagent dose to achieve target pH

    Args:
        pe_constraint: Dict with pe constraint specification for thermodynamically correct
                      redox modeling. See build_equilibrium_phases_with_pe_constraint().

    Returns dict with:
        - optimal_fe_dose_mmol: Optimal Fe dose
        - achieved_p_mg_l: Achieved P concentration
        - convergence_status: Convergence status message
        - final_state: Final PHREEQC simulation state
        - ph_adjustment_dose_mmol: Optimal pH reagent dose (if pH adjustment enabled)
        - ph_convergence_achieved: Whether pH target was achieved (if pH adjustment enabled)
    """
    use_ph_adjustment = ph_adjustment is not None and ph_adjustment.enabled

    # First, verify that bounds bracket the solution
    p_at_min = await _simulate_fe_dose(
        initial_solution,
        fe_min,
        iron_source,
        phases,
        hfo_phase,
        use_surface,
        surface_opts,
        pe_constraint,
        database_path,
    )
    p_at_max = await _simulate_fe_dose(
        initial_solution,
        fe_max,
        iron_source,
        phases,
        hfo_phase,
        use_surface,
        surface_opts,
        pe_constraint,
        database_path,
    )

    if p_at_min is None or p_at_max is None:
        raise PhreeqcError("Failed to evaluate initial bounds")

    optimization_path.append({"iteration": 0, "fe_mmol": fe_min, "p_mg_l": p_at_min, "type": "bound_check"})
    optimization_path.append({"iteration": 0, "fe_mmol": fe_max, "p_mg_l": p_at_max, "type": "bound_check"})

    # Expand bounds if needed
    expand_count = 0
    while p_at_max > target_p_mg_l and expand_count < 5:
        fe_max *= expansion_factor
        p_at_max = await _simulate_fe_dose(
            initial_solution,
            fe_max,
            iron_source,
            phases,
            hfo_phase,
            use_surface,
            surface_opts,
            pe_constraint,
            database_path,
        )
        expand_count += 1
        optimization_path.append({"iteration": 0, "fe_mmol": fe_max, "p_mg_l": p_at_max, "type": "bound_expansion"})
        if p_at_max is None:
            raise PhreeqcError("Bound expansion failed")

    if p_at_max > target_p_mg_l:
        notes.append(
            f"Warning: Could not bracket solution. Max Fe={fe_max:.2f} mmol/L "
            f"gives P={p_at_max:.3f} mg/L > target={target_p_mg_l} mg/L"
        )

    # Binary search
    best_fe = fe_max
    best_p = p_at_max
    best_state = None
    best_ph_dose = None
    best_ph_converged = None

    for iteration in range(1, max_iterations + 1):
        fe_mid = (fe_min + fe_max) / 2

        # If pH adjustment is enabled, find optimal pH reagent dose at this Fe dose
        if use_ph_adjustment:
            result = await _simulate_with_ph_adjustment(
                initial_solution=initial_solution,
                fe_dose_mmol=fe_mid,
                iron_source=iron_source,
                phases=phases,
                hfo_phase=hfo_phase,
                use_surface=use_surface,
                surface_opts=surface_opts,
                pe_constraint=pe_constraint,
                database_path=database_path,
                ph_adjustment=ph_adjustment,
            )
        else:
            result = await _simulate_fe_dose_with_state(
                initial_solution,
                fe_mid,
                iron_source,
                phases,
                hfo_phase,
                use_surface,
                surface_opts,
                pe_constraint,
                database_path,
            )

        if result is None:
            # Simulation failed, try narrowing from below
            fe_max = fe_mid
            optimization_path.append(
                {"iteration": iteration, "fe_mmol": fe_mid, "p_mg_l": None, "error": "simulation_failed"}
            )
            continue

        p_mid = result["p_mg_l"]
        state = result["state"]
        ph_dose = result.get("ph_adjustment_dose_mmol")
        ph_converged = result.get("ph_convergence_achieved")

        path_entry = {"iteration": iteration, "fe_mmol": fe_mid, "p_mg_l": p_mid}
        if use_ph_adjustment:
            path_entry["ph_reagent_mmol"] = ph_dose
            path_entry["achieved_ph"] = state.get("solution_summary", {}).get("pH")
        optimization_path.append(path_entry)

        # Update best if closer to target
        if abs(p_mid - target_p_mg_l) < abs(best_p - target_p_mg_l):
            best_fe = fe_mid
            best_p = p_mid
            best_state = state
            best_ph_dose = ph_dose
            best_ph_converged = ph_converged

        # Check convergence
        if abs(p_mid - target_p_mg_l) <= tolerance:
            result_dict = {
                "optimal_fe_dose_mmol": fe_mid,
                "achieved_p_mg_l": p_mid,
                "convergence_status": f"Converged in {iteration} iterations",
                "final_state": state,
            }
            if use_ph_adjustment:
                result_dict["ph_adjustment_dose_mmol"] = ph_dose
                result_dict["ph_convergence_achieved"] = ph_converged
            return result_dict

        # Update bounds
        if p_mid > target_p_mg_l:
            # Need more Fe
            fe_min = fe_mid
        else:
            # Too much Fe
            fe_max = fe_mid

        # Check for narrow bracket
        if (fe_max - fe_min) < 0.001:
            break

    # Return best found
    convergence_status = (
        f"Max iterations ({max_iterations}) reached. " f"Best: Fe={best_fe:.3f} mmol/L, P={best_p:.3f} mg/L"
    )

    # If we don't have a state, run one more simulation
    if best_state is None:
        if use_ph_adjustment:
            result = await _simulate_with_ph_adjustment(
                initial_solution=initial_solution,
                fe_dose_mmol=best_fe,
                iron_source=iron_source,
                phases=phases,
                hfo_phase=hfo_phase,
                use_surface=use_surface,
                surface_opts=surface_opts,
                pe_constraint=pe_constraint,
                database_path=database_path,
                ph_adjustment=ph_adjustment,
            )
        else:
            result = await _simulate_fe_dose_with_state(
                initial_solution,
                best_fe,
                iron_source,
                phases,
                hfo_phase,
                use_surface,
                surface_opts,
                pe_constraint,
                database_path,
            )
        if result:
            best_state = result["state"]
            best_ph_dose = result.get("ph_adjustment_dose_mmol")
            best_ph_converged = result.get("ph_convergence_achieved")
        else:
            best_state = {}

    result_dict = {
        "optimal_fe_dose_mmol": best_fe,
        "achieved_p_mg_l": best_p,
        "convergence_status": convergence_status,
        "final_state": best_state,
    }
    if use_ph_adjustment:
        result_dict["ph_adjustment_dose_mmol"] = best_ph_dose
        result_dict["ph_convergence_achieved"] = best_ph_converged

    return result_dict


async def _simulate_with_ph_adjustment(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]],
    database_path: str,
    ph_adjustment: PhAdjustmentOptions,
) -> Optional[Dict[str, Any]]:
    """
    Simulate Fe dose with nested binary search for pH adjustment.

    This function:
    1. Takes a fixed Fe dose
    2. Runs binary search to find the optimal pH reagent dose
    3. Returns the result with both P and pH data

    Args:
        initial_solution: Initial water chemistry
        fe_dose_mmol: Fixed Fe dose in mmol/L
        iron_source: Iron source formula
        phases: Equilibrium phases
        hfo_phase: HFO surface phase name
        use_surface: Whether to use surface complexation
        surface_opts: Surface complexation options
        pe_constraint: pe constraint specification
        database_path: PHREEQC database path
        ph_adjustment: pH adjustment options (must have enabled=True)

    Returns:
        Dict with p_mg_l, state, ph_adjustment_dose_mmol, ph_convergence_achieved
        or None on failure
    """
    target_ph = ph_adjustment.target_ph
    reagent = ph_adjustment.reagent
    tolerance_ph = ph_adjustment.tolerance_ph
    max_ph_iterations = ph_adjustment.max_iterations

    # Auto-scale max pH reagent dose based on Fe dose
    # FeCl3 hydrolysis generates ~3 mol H+ per mol Fe(III):
    #   Fe3+ + 3H2O -> Fe(OH)3 + 3H+
    # Need at least 3 mol NaOH (or 1.5 mol Ca(OH)2) per mol Fe to neutralize
    # Use 4:1 ratio for safety margin
    fe_neutralization_factor = 4.0 if reagent in ("NaOH", "HCl") else 2.0  # Ca(OH)2 has 2 OH-
    auto_scaled_max_ph_dose = max(ph_adjustment.max_dose_mmol, fe_dose_mmol * fe_neutralization_factor)
    if auto_scaled_max_ph_dose > ph_adjustment.max_dose_mmol:
        logger.info(
            f"Auto-scaled max pH dose from {ph_adjustment.max_dose_mmol:.1f} to "
            f"{auto_scaled_max_ph_dose:.1f} mmol/L {reagent} "
            f"(based on Fe dose={fe_dose_mmol:.1f} mmol/L, {fe_neutralization_factor}:1 ratio)"
        )
    max_ph_dose = auto_scaled_max_ph_dose

    # First, simulate without pH adjustment to get baseline pH
    baseline = await _simulate_fe_and_ph_dose(
        initial_solution,
        fe_dose_mmol,
        iron_source,
        0.0,
        reagent,
        phases,
        hfo_phase,
        use_surface,
        surface_opts,
        pe_constraint,
        database_path,
    )

    if baseline is None:
        return None

    baseline_ph = baseline.get("state", {}).get("solution_summary", {}).get("pH")
    if baseline_ph is None:
        logger.warning("Could not determine baseline pH")
        return baseline  # Return result without pH adjustment

    # Determine search direction: need to raise or lower pH?
    # NaOH/Ca(OH)2 raises pH, HCl lowers pH
    is_base = reagent in ("NaOH", "Ca(OH)2")

    # If pH is already at target (or on the wrong side for this reagent), return baseline
    if is_base and baseline_ph >= target_ph:
        # Already at/above target with a base - no dose needed
        baseline["ph_adjustment_dose_mmol"] = 0.0
        baseline["ph_convergence_achieved"] = abs(baseline_ph - target_ph) <= tolerance_ph
        return baseline
    elif not is_base and baseline_ph <= target_ph:
        # Already at/below target with an acid - no dose needed
        baseline["ph_adjustment_dose_mmol"] = 0.0
        baseline["ph_convergence_achieved"] = abs(baseline_ph - target_ph) <= tolerance_ph
        return baseline

    # Binary search for pH reagent dose
    ph_min = 0.0
    ph_max = max_ph_dose

    best_ph_dose = 0.0
    best_ph_diff = abs(baseline_ph - target_ph)
    best_result = baseline

    for ph_iter in range(1, max_ph_iterations + 1):
        ph_mid = (ph_min + ph_max) / 2

        result = await _simulate_fe_and_ph_dose(
            initial_solution,
            fe_dose_mmol,
            iron_source,
            ph_mid,
            reagent,
            phases,
            hfo_phase,
            use_surface,
            surface_opts,
            pe_constraint,
            database_path,
        )

        if result is None:
            # Simulation failed, try lower dose
            ph_max = ph_mid
            continue

        achieved_ph = result.get("state", {}).get("solution_summary", {}).get("pH")
        if achieved_ph is None:
            ph_max = ph_mid
            continue

        ph_diff = abs(achieved_ph - target_ph)

        # Update best if closer to target
        if ph_diff < best_ph_diff:
            best_ph_diff = ph_diff
            best_ph_dose = ph_mid
            best_result = result

        # Check convergence
        if ph_diff <= tolerance_ph:
            result["ph_adjustment_dose_mmol"] = ph_mid
            result["ph_convergence_achieved"] = True
            return result

        # Update bounds based on reagent type
        if is_base:
            # Base raises pH
            if achieved_ph < target_ph:
                ph_min = ph_mid  # Need more base
            else:
                ph_max = ph_mid  # Too much base
        else:
            # Acid lowers pH
            if achieved_ph > target_ph:
                ph_min = ph_mid  # Need more acid
            else:
                ph_max = ph_mid  # Too much acid

        # Check for narrow bracket
        if (ph_max - ph_min) < 0.01:
            break

    # Return best found
    best_result["ph_adjustment_dose_mmol"] = best_ph_dose
    best_result["ph_convergence_achieved"] = best_ph_diff <= tolerance_ph
    return best_result


async def _simulate_fe_and_ph_dose(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    ph_reagent_dose_mmol: float,
    ph_reagent: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]],
    database_path: str,
) -> Optional[Dict[str, Any]]:
    """Run simulation with both Fe dose and pH reagent dose."""
    try:
        # Build PHREEQC input with both reagents
        phreeqc_input = _build_phreeqc_input_with_ph_adjustment(
            initial_solution,
            fe_dose_mmol,
            iron_source,
            ph_reagent_dose_mmol,
            ph_reagent,
            phases,
            hfo_phase,
            use_surface,
            surface_opts,
            pe_constraint,
        )

        # Run simulation
        result = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        if not result or result.get("error"):
            return None

        # Extract residual P
        p_molal = result.get("element_totals_molality", {}).get("P", 0) or 0
        p_mg_l = p_molal * MOLECULAR_WEIGHTS["P"] * 1000  # mol/kgw to mg/L

        return {"p_mg_l": p_mg_l, "state": result}

    except Exception as e:
        logger.warning(f"Simulation failed for Fe={fe_dose_mmol:.3f}, pH_reagent={ph_reagent_dose_mmol:.3f}: {e}")
        return None


async def _simulate_fe_dose(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]],
    database_path: str,
) -> Optional[float]:
    """Run simulation and return residual P in mg/L, or None on failure."""
    result = await _simulate_fe_dose_with_state(
        initial_solution,
        fe_dose_mmol,
        iron_source,
        phases,
        hfo_phase,
        use_surface,
        surface_opts,
        pe_constraint,
        database_path,
    )
    return result["p_mg_l"] if result else None


async def _simulate_fe_dose_with_state(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]],
    database_path: str,
) -> Optional[Dict[str, Any]]:
    """Run simulation and return {p_mg_l, state} or None on failure."""
    try:
        # Build PHREEQC input
        phreeqc_input = _build_phreeqc_input(
            initial_solution, fe_dose_mmol, iron_source, phases, hfo_phase, use_surface, surface_opts, pe_constraint
        )

        # Run simulation
        result = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        if not result or result.get("error"):
            return None

        # Extract residual P
        p_molal = result.get("element_totals_molality", {}).get("P", 0) or 0
        p_mg_l = p_molal * MOLECULAR_WEIGHTS["P"] * 1000  # mol/kgw to mg/L

        return {"p_mg_l": p_mg_l, "state": result}

    except Exception as e:
        logger.warning(f"Simulation failed for Fe={fe_dose_mmol:.3f}: {e}")
        return None


def _build_phreeqc_input(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]] = None,
) -> str:
    """Build complete PHREEQC input string with pe constraint support.

    Args:
        pe_constraint: Dict specifying pe constraint method. If method is "fix_pe",
                      includes the Fix_pe pseudo-phase definition and adds it to
                      EQUILIBRIUM_PHASES. If method is "o2_equilibrium", adds O2(g)
                      to EQUILIBRIUM_PHASES.
    """
    blocks = []

    # If using Fix_pe method, add the pseudo-phase definition first
    # (PHASES block must come before EQUILIBRIUM_PHASES that references it)
    if pe_constraint and pe_constraint.get("method") == "fix_pe":
        blocks.append(build_fix_pe_phase())

    # Solution block
    blocks.append(build_solution_block(initial_solution, solution_num=1))

    # Reaction block (Fe addition)
    # CRITICAL: Convert metal dose to product dose for multi-metal formulas
    # e.g., 2 mmol Fe with Fe2(SO4)3 → 1 mmol Fe2(SO4)3 (because 2 Fe atoms per formula)
    if fe_dose_mmol > 0:
        product_dose_mmol = metal_dose_to_product_dose(fe_dose_mmol, iron_source)
        reactants = [{"formula": iron_source, "amount": product_dose_mmol, "units": "mmol"}]
        blocks.append(build_reaction_block(reactants, reaction_num=1))

    # Equilibrium phases block with pe constraint
    if phases or pe_constraint:
        blocks.append(
            build_equilibrium_phases_with_pe_constraint(
                phases=phases or [],
                pe_constraint=pe_constraint,
                block_num=1,
                allow_empty=not phases,  # Allow empty if only pe_constraint
            )
        )

    # Surface block (phase-linked to HFO)
    if use_surface and hfo_phase:
        blocks.append(
            build_phase_linked_surface_block(
                surface_name=surface_opts.surface_name,
                phase_name=hfo_phase,
                sites_per_mole=surface_opts.sites_per_mole_strong,
                weak_to_strong_ratio=surface_opts.weak_to_strong_ratio,
                specific_area_per_mole=surface_opts.specific_area_m2_per_mol,
                equilibrate_solution=1,
                block_num=1,
                no_edl=surface_opts.no_edl,
            )
        )

    # Selected output block
    blocks.append(
        build_selected_output_block(
            block_num=1,
            totals=True,
            molalities=True,
            phases=True,
            saturation_indices=True,
            surface=use_surface,
            user_punch=True,
        )
    )

    # USER_PUNCH for partitioning
    phase_names = [p["name"] for p in phases if "name" in p]
    blocks.append(
        build_user_punch_for_partitioning(
            phases=phase_names,
            surface_name=surface_opts.surface_name if use_surface else "Hfo",
            elements=["P", "Fe"],
            include_solution_totals=True,
            block_num=1,
        )
    )

    blocks.append("END\n")

    return "\n".join(blocks)


def _build_multi_reagent_reaction_block(
    reagent1_mmol: float,
    reagent1_formula: str,
    reagent2_mmol: float,
    reagent2_formula: str,
    reaction_num: int = 1,
) -> str:
    """
    Build a PHREEQC REACTION block for multiple reagents with correct proportions.

    PHREEQC REACTION syntax: coefficients are relative proportions, total amount is scaled.
    Example: To add 0.667 mmol FeCl3 and 2.0 mmol NaOH:
        REACTION 1
            FeCl3 0.25
            NaOH 0.75
            2.667 mmol in 1 steps
    Where 0.25 = 0.667/2.667 and 0.75 = 2.0/2.667

    Args:
        reagent1_mmol: Amount of first reagent in mmol
        reagent1_formula: Chemical formula of first reagent
        reagent2_mmol: Amount of second reagent in mmol
        reagent2_formula: Chemical formula of second reagent
        reaction_num: PHREEQC block number

    Returns:
        PHREEQC REACTION block string
    """
    lines = [f"REACTION {reaction_num}"]

    total_mmol = reagent1_mmol + reagent2_mmol

    if total_mmol <= 0:
        # No reactants - return empty reaction (shouldn't happen)
        return ""

    # Calculate proportional coefficients
    if reagent1_mmol > 0:
        coeff1 = reagent1_mmol / total_mmol
        lines.append(f"    {reagent1_formula} {coeff1:.6f}")

    if reagent2_mmol > 0:
        coeff2 = reagent2_mmol / total_mmol
        lines.append(f"    {reagent2_formula} {coeff2:.6f}")

    # Total amount
    lines.append(f"    {total_mmol:.6f} mmol in 1 steps")

    return "\n".join(lines)


def _build_phreeqc_input_with_ph_adjustment(
    initial_solution: Dict[str, Any],
    fe_dose_mmol: float,
    iron_source: str,
    ph_reagent_dose_mmol: float,
    ph_reagent: str,
    phases: List[Dict[str, Any]],
    hfo_phase: Optional[str],
    use_surface: bool,
    surface_opts: SurfaceComplexationOptions,
    pe_constraint: Optional[Dict[str, Any]] = None,
) -> str:
    """Build complete PHREEQC input string with both Fe and pH adjustment reagent."""
    blocks = []

    # If using Fix_pe method, add the pseudo-phase definition first
    if pe_constraint and pe_constraint.get("method") == "fix_pe":
        blocks.append(build_fix_pe_phase())

    # Solution block
    blocks.append(build_solution_block(initial_solution, solution_num=1))

    # Reaction block (Fe addition + pH adjustment reagent)
    # CRITICAL: When adding multiple reactants, we need to use proportional coefficients
    # because PHREEQC's REACTION block scales all reactants by total moles.
    # If we want X mmol Fe and Y mmol NaOH, we use coefficients X/(X+Y) and Y/(X+Y)
    # then specify total as (X+Y) mmol.
    product_dose_mmol = metal_dose_to_product_dose(fe_dose_mmol, iron_source) if fe_dose_mmol > 0 else 0

    if product_dose_mmol > 0 or ph_reagent_dose_mmol > 0:
        blocks.append(
            _build_multi_reagent_reaction_block(
                product_dose_mmol, iron_source, ph_reagent_dose_mmol, ph_reagent, reaction_num=1
            )
        )

    # Equilibrium phases block with pe constraint
    if phases or pe_constraint:
        blocks.append(
            build_equilibrium_phases_with_pe_constraint(
                phases=phases or [],
                pe_constraint=pe_constraint,
                block_num=1,
                allow_empty=not phases,
            )
        )

    # Surface block (phase-linked to HFO)
    if use_surface and hfo_phase:
        blocks.append(
            build_phase_linked_surface_block(
                surface_name=surface_opts.surface_name,
                phase_name=hfo_phase,
                sites_per_mole=surface_opts.sites_per_mole_strong,
                weak_to_strong_ratio=surface_opts.weak_to_strong_ratio,
                specific_area_per_mole=surface_opts.specific_area_m2_per_mol,
                equilibrate_solution=1,
                block_num=1,
                no_edl=surface_opts.no_edl,
            )
        )

    # Selected output block
    blocks.append(
        build_selected_output_block(
            block_num=1,
            totals=True,
            molalities=True,
            phases=True,
            saturation_indices=True,
            surface=use_surface,
            user_punch=True,
        )
    )

    # USER_PUNCH for partitioning
    phase_names = [p["name"] for p in phases if "name" in p]
    blocks.append(
        build_user_punch_for_partitioning(
            phases=phase_names,
            surface_name=surface_opts.surface_name if use_surface else "Hfo",
            elements=["P", "Fe"],
            include_solution_totals=True,
            block_num=1,
        )
    )

    blocks.append("END\n")

    return "\n".join(blocks)


def _build_phosphate_partitioning(
    state: Dict[str, Any],
    initial_p_mg_l: float,
    surface_name: str,
) -> PhosphatePartitioning:
    """Build phosphate partitioning output from simulation state."""
    # Dissolved P
    dissolved_p_molal = state.get("element_totals_molality", {}).get("P", 0) or 0
    dissolved_p_mmol = dissolved_p_molal * 1000  # mol/kgw to mmol/L (approx)
    dissolved_p_mg_l = dissolved_p_molal * MOLECULAR_WEIGHTS["P"] * 1000

    # Adsorbed P (from USER_PUNCH)
    surface_adsorbed = state.get("surface_adsorbed_moles", {})
    adsorbed_p_mmol = surface_adsorbed.get(f"P_{surface_name}", 0) * 1000

    # Precipitated P (from equilibrium phases)
    equi_phases = state.get("equilibrium_phase_moles", {})
    precipitated_phases = {}
    precipitated_p_mmol = 0.0

    # P content per phase (stoichiometry)
    p_per_phase = {
        "Strengite": 1.0,  # FePO4·2H2O
        "Vivianite": 2.0,  # Fe3(PO4)2·8H2O
    }

    for phase, moles in equi_phases.items():
        if phase in p_per_phase and moles > 0:
            p_in_phase = moles * p_per_phase[phase] * 1000  # to mmol
            precipitated_phases[phase] = p_in_phase
            precipitated_p_mmol += p_in_phase

    # Total P removal
    initial_p_mmol = initial_p_mg_l / MOLECULAR_WEIGHTS["P"]
    total_removed = initial_p_mmol - dissolved_p_mmol
    removal_pct = (total_removed / initial_p_mmol) * 100 if initial_p_mmol > 0 else 0

    return PhosphatePartitioning(
        dissolved_p_mmol=dissolved_p_mmol,
        dissolved_p_mg_l=dissolved_p_mg_l,
        adsorbed_p_mmol=adsorbed_p_mmol if adsorbed_p_mmol > 0 else None,
        precipitated_p_mmol=precipitated_p_mmol if precipitated_p_mmol > 0 else None,
        precipitated_phases=precipitated_phases if precipitated_phases else None,
        total_p_removal_percent=removal_pct,
    )


def _build_iron_partitioning(
    state: Dict[str, Any],
    added_metal_mmol: float,
    surface_name: str,
    metal_type: str = "Fe",
) -> IronPartitioning:
    """Build metal (Fe or Al) partitioning output from simulation state.

    Note: Returns IronPartitioning for backward compatibility, but handles
    both Fe and Al coagulants.
    """
    # Dissolved metal
    dissolved_metal_molal = state.get("element_totals_molality", {}).get(metal_type, 0) or 0
    dissolved_metal_mmol = dissolved_metal_molal * 1000
    dissolved_metal_mg_l = dissolved_metal_molal * MOLECULAR_WEIGHTS[metal_type] * 1000

    # Precipitated metal (from equilibrium phases)
    equi_phases = state.get("equilibrium_phase_moles", {})
    precipitated_phases = {}
    precipitated_metal_mmol = 0.0

    # Metal content per phase (stoichiometry) - combined Fe and Al phases
    metal_per_phase = {
        # Iron phases
        "Ferrihydrite": 1.0,
        "Fe(OH)3(a)": 1.0,
        "Strengite": 1.0,
        "Vivianite": 3.0,
        "FeS(ppt)": 1.0,
        "Mackinawite": 1.0,
        "Siderite": 1.0,
        # Aluminum phases
        "Gibbsite": 1.0,
        "Al(OH)3(a)": 1.0,
    }

    for phase, moles in equi_phases.items():
        if phase in metal_per_phase and moles > 0:
            metal_in_phase = moles * metal_per_phase[phase] * 1000
            precipitated_phases[phase] = metal_in_phase
            precipitated_metal_mmol += metal_in_phase

    # Metal utilization
    utilization_pct = (
        ((added_metal_mmol - dissolved_metal_mmol) / added_metal_mmol) * 100 if added_metal_mmol > 0 else 0
    )

    return IronPartitioning(
        dissolved_fe_mmol=dissolved_metal_mmol,
        dissolved_fe_mg_l=dissolved_metal_mg_l,
        precipitated_fe_mmol=precipitated_metal_mmol if precipitated_metal_mmol > 0 else None,
        precipitated_phases=precipitated_phases if precipitated_phases else None,
        fe_utilization_percent=utilization_pct,
    )


def _build_mechanistic_partition(
    state: Dict[str, Any],
    initial_p_mg_l: float,
    surface_name: str,
) -> MechanisticPartition:
    """Build detailed mechanistic partition showing WHERE P and Fe ended up.

    This provides transparency into which mechanisms are driving P removal:
    - Adsorption: P on HFO surfaces (Hfo_sOH, Hfo_wOH)
    - Precipitation: P in Strengite (FePO4·2H2O) or Vivianite (Fe3(PO4)2·8H2O)

    The dominant mechanism is determined by which pathway accounts for >60% of P removal.
    """
    # --- Phosphorus partitioning ---

    # Dissolved P
    dissolved_p_molal = state.get("element_totals_molality", {}).get("P", 0) or 0
    dissolved_p_mmol = dissolved_p_molal * 1000  # mol/kgw to mmol/L (approx)

    # Adsorbed P (from surface species or USER_PUNCH)
    surface_adsorbed = state.get("surface_adsorbed_moles", {})
    p_on_surfaces_mmol = surface_adsorbed.get(f"P_{surface_name}", 0) * 1000

    # Precipitated P (from equilibrium phases)
    equi_phases = state.get("equilibrium_phase_moles", {})

    # P stoichiometry per phase
    p_in_strengite_moles = equi_phases.get("Strengite", 0)  # 1 P per Strengite
    p_in_vivianite_moles = equi_phases.get("Vivianite", 0) * 2  # 2 P per Vivianite

    p_in_strengite_mmol = p_in_strengite_moles * 1000
    p_in_vivianite_mmol = p_in_vivianite_moles * 1000

    # --- Iron partitioning ---

    # Dissolved Fe
    dissolved_fe_molal = state.get("element_totals_molality", {}).get("Fe", 0) or 0
    dissolved_fe_mmol = dissolved_fe_molal * 1000

    # Fe in HFO/Ferrihydrite (includes Fe(OH)3(a) for other databases)
    fe_in_hfo_moles = equi_phases.get("Ferrihydrite", 0) + equi_phases.get(  # minteq.v4.dat
        "Fe(OH)3(a)", 0
    )  # phreeqc.dat, wateq4f.dat
    fe_in_hfo_mmol = fe_in_hfo_moles * 1000

    # Fe in Vivianite (3 Fe per formula unit)
    vivianite_moles = equi_phases.get("Vivianite", 0)
    fe_in_vivianite_mmol = vivianite_moles * 3 * 1000

    # Fe in FeS (1 Fe per formula)
    fe_in_fes_moles = equi_phases.get("FeS(ppt)", 0) + equi_phases.get("Mackinawite", 0)
    fe_in_fes_mmol = fe_in_fes_moles * 1000

    # Fe in Siderite (1 Fe per formula)
    fe_in_siderite_moles = equi_phases.get("Siderite", 0)
    fe_in_siderite_mmol = fe_in_siderite_moles * 1000

    # --- Mechanism attribution ---

    # Calculate total P removed
    initial_p_mmol = initial_p_mg_l / MOLECULAR_WEIGHTS["P"]
    total_p_removed = initial_p_mmol - dissolved_p_mmol

    if total_p_removed > 0.001:  # Avoid division by zero
        p_by_adsorption = p_on_surfaces_mmol
        p_by_precipitation = p_in_strengite_mmol + p_in_vivianite_mmol

        pct_by_adsorption = (p_by_adsorption / total_p_removed) * 100
        pct_by_precipitation = (p_by_precipitation / total_p_removed) * 100

        # Determine dominant mechanism
        if pct_by_adsorption > 60:
            dominant_mechanism = "adsorption"
        elif pct_by_precipitation > 60:
            dominant_mechanism = "precipitation"
        else:
            dominant_mechanism = "mixed"
    else:
        pct_by_adsorption = 0.0
        pct_by_precipitation = 0.0
        dominant_mechanism = "none"

    return MechanisticPartition(
        p_on_hfo_surfaces_mmol=p_on_surfaces_mmol,
        p_in_strengite_mmol=p_in_strengite_mmol,
        p_in_vivianite_mmol=p_in_vivianite_mmol,
        p_dissolved_mmol=dissolved_p_mmol,
        fe_in_ferrihydrite_mmol=fe_in_hfo_mmol,
        fe_in_vivianite_mmol=fe_in_vivianite_mmol,
        fe_in_fes_mmol=fe_in_fes_mmol,
        fe_in_siderite_mmol=fe_in_siderite_mmol,
        fe_dissolved_mmol=dissolved_fe_mmol,
        p_removal_dominant_mechanism=dominant_mechanism,
        p_removal_by_adsorption_percent=pct_by_adsorption,
        p_removal_by_precipitation_percent=pct_by_precipitation,
    )


def _calculate_marginal_fe_p(
    optimization_path: List[Dict[str, Any]],
) -> Optional[MarginalFePRatio]:
    """Calculate marginal Fe:P ratio from the last binary search iterations.

    Marginal Fe:P = dFe / dP (incremental Fe per incremental P removed)

    This is calculated from the last two valid iterations in the binary search path,
    showing the incremental cost of removing additional P at the current operating point.

    Unlike average Fe:P, marginal Fe:P captures diminishing returns - it explodes
    when pushing to ultra-low P targets (e.g., 0.1 mg/L).
    """
    # Filter to valid iterations with both fe_mmol and p_mg_l
    valid_iters = [
        entry
        for entry in optimization_path
        if entry.get("fe_mmol") is not None
        and entry.get("p_mg_l") is not None
        and entry.get("type") != "bound_check"
        and entry.get("type") != "bound_expansion"
    ]

    if len(valid_iters) < 2:
        return None

    # Get last two valid iterations
    iter_n = valid_iters[-1]
    iter_n1 = valid_iters[-2]

    fe_high = iter_n["fe_mmol"]
    fe_low = iter_n1["fe_mmol"]
    p_high = iter_n["p_mg_l"]
    p_low = iter_n1["p_mg_l"]

    # Need to order correctly: higher Fe should give lower P
    if fe_high < fe_low:
        fe_high, fe_low = fe_low, fe_high
        p_high, p_low = p_low, p_high

    # Calculate marginal ratio
    delta_fe = fe_high - fe_low
    delta_p = p_low - p_high  # P decreases as Fe increases

    if abs(delta_p) < 1e-6:  # Avoid division by very small numbers
        return None

    # Convert P from mg/L to mmol/L for molar ratio
    delta_p_mmol = delta_p / MOLECULAR_WEIGHTS["P"]

    marginal_ratio = delta_fe / delta_p_mmol if delta_p_mmol > 0 else float("inf")

    # Determine interpretation
    if marginal_ratio < 2.0:
        interpretation = "Excellent efficiency - well below stoichiometric limit"
    elif marginal_ratio < 5.0:
        interpretation = "Good efficiency - moderate incremental cost"
    elif marginal_ratio < 10.0:
        interpretation = "High values (>5) indicate diminishing returns"
    else:
        interpretation = "Very high marginal cost - consider if ultra-low P target is justified"

    return MarginalFePRatio(
        value_molar=marginal_ratio,
        interpretation=interpretation,
    )


def _build_phosphate_residual_metrics(
    state: Dict[str, Any],
    p_inert_mg_l: float,
    temperature_celsius: float = 25.0,
) -> PhosphateResidualMetrics:
    """Build explicit phosphate residual metrics from simulation state.

    Args:
        state: PHREEQC simulation result dictionary
        p_inert_mg_l: User-specified non-reactive P (mg/L)
        temperature_celsius: Temperature for unit conversions

    Returns:
        PhosphateResidualMetrics with clear P accounting
    """
    # Get total dissolved P from PHREEQC TOT("P")
    p_molal = state.get("element_totals_molality", {}).get("P", 0) or 0
    residual_p_total_mg_l = p_molal * MOLECULAR_WEIGHTS["P"] * 1000  # mol/kgw to mg/L

    # Total P reported = PHREEQC residual + user-specified inert P
    # Inert P is assumed to pass through unchanged (organic P, colloidal P)
    reported_total_p_mg_l = residual_p_total_mg_l + p_inert_mg_l

    # Reactive P = PHREEQC residual (excludes inert)
    # This is the P that actually participated in equilibrium
    residual_p_reactive_mg_l = residual_p_total_mg_l

    # Try to calculate orthophosphate sum from species molalities
    species_molalities = state.get("species_molalities", {})
    ortho_p_species = ["PO4-3", "HPO4-2", "H2PO4-", "H3PO4"]
    ortho_p_sum = 0.0
    ortho_p_available = False

    for species in ortho_p_species:
        if species in species_molalities:
            ortho_p_sum += species_molalities[species]
            ortho_p_available = True

    # Convert to mg/L as P
    residual_ortho_p_mg_l = ortho_p_sum * MOLECULAR_WEIGHTS["P"] * 1000 if ortho_p_available else None

    return PhosphateResidualMetrics(
        residual_p_total_mg_l_as_P=reported_total_p_mg_l,  # Total = reactive + inert
        residual_p_reactive_mg_l_as_P=residual_p_reactive_mg_l,  # Only PHREEQC residual
        p_inert_assumed_mg_l_as_P=p_inert_mg_l,
        residual_orthophosphate_mg_l_as_P=residual_ortho_p_mg_l,
    )


async def _run_sulfide_sensitivity_analysis(
    base_input_data: Dict[str, Any],
    target_p_mg_l: float,
    sulfide_levels: List[float] = None,
) -> SulfideSensitivityResult:
    """Run sulfide sensitivity analysis for anaerobic mode.

    Runs the ferric dose optimization at multiple sulfide concentrations
    to show the impact of sulfide competition on Fe requirements.

    Args:
        base_input_data: Base input data (will be modified with different S(-2) values)
        target_p_mg_l: Target P concentration (mg/L)
        sulfide_levels: Sulfide concentrations to test (default: [0, 20, 50, 100] mg/L)

    Returns:
        SulfideSensitivityResult with scenarios and recommendation
    """
    if sulfide_levels is None:
        sulfide_levels = [0.0, 20.0, 50.0, 100.0]

    scenarios = []
    logger.info(f"Running sulfide sensitivity analysis at levels: {sulfide_levels} mg/L S(-2)")

    for sulfide_mg_l in sulfide_levels:
        # Create modified input with this sulfide level
        modified_input = copy.deepcopy(base_input_data)
        if "initial_solution" not in modified_input:
            continue
        if "analysis" not in modified_input["initial_solution"]:
            modified_input["initial_solution"]["analysis"] = {}

        # Add sulfide to analysis
        if sulfide_mg_l > 0:
            modified_input["initial_solution"]["analysis"]["S(-2)"] = sulfide_mg_l
        else:
            # Remove any existing sulfide for 0 case
            modified_input["initial_solution"]["analysis"].pop("S(-2)", None)

        # Set sulfide_sensitivity to False to avoid recursion
        modified_input["sulfide_sensitivity"] = False

        try:
            # Run the optimization for this sulfide level
            result = await calculate_ferric_dose_for_tp(modified_input)

            if result.get("status") == "success" and result.get("optimization_summary"):
                opt_summary = result["optimization_summary"]
                fe_dose_mmol = opt_summary.get("optimal_fe_dose_mmol", 0)
                fe_dose_mg_l = opt_summary.get("optimal_fe_dose_mg_l", 0)
                fe_to_p = opt_summary.get("fe_to_p_molar_ratio", 0)
                achieved_p = opt_summary.get("achieved_residual_p_mg_l", target_p_mg_l)

                # Get FeS precipitation amount if available
                fes_mmol = None
                precipitated = result.get("precipitated_phases", {})
                for phase_name in ["FeS(ppt)", "Mackinawite", "FeS"]:
                    if phase_name in precipitated:
                        fes_mmol = precipitated[phase_name]
                        break

                scenarios.append(SulfideSensitivityScenario(
                    sulfide_mg_l=sulfide_mg_l,
                    fe_dose_required_mmol=fe_dose_mmol,
                    fe_dose_required_mg_l=fe_dose_mg_l,
                    fe_to_p_ratio=fe_to_p,
                    achieved_p_mg_l=achieved_p,
                    fes_precipitated_mmol=fes_mmol,
                ))
            else:
                logger.warning(f"Sensitivity run failed at S(-2)={sulfide_mg_l}: {result.get('error', 'unknown')}")

        except Exception as e:
            logger.error(f"Sensitivity analysis failed at S(-2)={sulfide_mg_l}: {e}")

    # Build summary and recommendation
    if len(scenarios) >= 2:
        min_fe_p = min(s.fe_to_p_ratio for s in scenarios)
        max_fe_p = max(s.fe_to_p_ratio for s in scenarios)
        min_sulfide = scenarios[0].sulfide_mg_l
        max_sulfide = scenarios[-1].sulfide_mg_l

        impact_summary = (
            f"Fe:P ratio increases from {min_fe_p:.1f} to {max_fe_p:.1f} "
            f"as sulfide increases from {min_sulfide:.0f} to {max_sulfide:.0f} mg/L S(-2)"
        )

        # Recommendation based on typical digester conditions
        if max_fe_p > 3.0:
            recommendation = (
                f"CAUTION: Fe:P exceeds 3.0 at higher sulfide levels. "
                f"Measure actual S(-2) concentration for accurate Fe dosing. "
                f"Consider pre-treatment to reduce sulfide if Fe cost is prohibitive."
            )
        else:
            recommendation = (
                f"Fe:P ratio ranges {min_fe_p:.1f}-{max_fe_p:.1f}. "
                f"Use S(-2)=50 mg/L scenario for conservative design. "
                f"Measure actual sulfide for operational optimization."
            )

        # Primary scenario: use 50 mg/L as representative mid-range
        primary_sulfide = 50.0 if 50.0 in sulfide_levels else sulfide_levels[len(sulfide_levels) // 2]
    else:
        impact_summary = "Insufficient scenarios completed for full analysis"
        recommendation = "Unable to provide recommendation - sensitivity analysis incomplete"
        primary_sulfide = 0.0

    return SulfideSensitivityResult(
        scenarios=scenarios,
        recommendation=recommendation,
        primary_scenario_sulfide_mg_l=primary_sulfide,
        sulfide_impact_summary=impact_summary,
    )


def _build_redox_diagnostics(
    state: Dict[str, Any],
    pe_constraint: Optional[Dict[str, Any]],
    target_pe: float,
    temperature_celsius: float = 25.0,
) -> RedoxDiagnostics:
    """Build redox diagnostics from simulation state.

    Args:
        state: PHREEQC simulation result dictionary
        pe_constraint: pe constraint specification used in simulation
        target_pe: Target pe value that was specified
        temperature_celsius: Temperature for ORP calculation

    Returns:
        RedoxDiagnostics with constraint info and drift analysis
    """
    # Get achieved pe from PHREEQC output
    solution_summary = state.get("solution_summary", {})
    achieved_pe = solution_summary.get("pe", target_pe)

    # Determine constraint type
    if pe_constraint:
        constraint_method = pe_constraint.get("method", "none")
    else:
        constraint_method = "none"

    # Calculate pe drift
    pe_drift = abs(achieved_pe - target_pe)

    # Convert pe values to ORP (mV vs SHE)
    target_orp = pe_to_orp(target_pe, temperature_celsius)
    achieved_orp = pe_to_orp(achieved_pe, temperature_celsius)

    # Determine constraint blocks used
    constraint_blocks = []
    if constraint_method == "fix_pe":
        constraint_blocks = ["Fix_pe", "O2(g)"]
    elif constraint_method == "o2_equilibrium":
        constraint_blocks = ["O2(g)"]

    return RedoxDiagnostics(
        redox_constraint_type=constraint_method,
        target_pe=target_pe,
        achieved_pe=achieved_pe,
        pe_drift=pe_drift if pe_drift > 0.01 else None,  # Only report if significant
        target_orp_mV_vs_SHE=target_orp,
        achieved_orp_mV_vs_SHE=achieved_orp,
        constraint_blocks_used=constraint_blocks if constraint_blocks else None,
    )
