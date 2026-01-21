"""
Schemas for ferric phosphate precipitation modeling.

This module defines the Pydantic models for the calculate_ferric_dose_for_tp tool,
which models Fe-P precipitation with surface complexation and redox awareness.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator

from .schemas import SolutionOutput, WaterAnalysisInput

# --- Redox Mode Definitions ---

RedoxMode = Literal["aerobic", "anaerobic", "pe_from_orp", "fixed_pe", "fixed_fe2_fraction"]


ORPReference = Literal["SHE", "AgAgCl_3M", "AgAgCl_sat"]

# ORP reference corrections to SHE (mV to add to reading)
ORP_REFERENCE_CORRECTIONS = {
    "SHE": 0.0,
    "AgAgCl_3M": 210.0,  # Ag/AgCl (3M KCl) at 25°C
    "AgAgCl_sat": 197.0,  # Ag/AgCl (saturated KCl) at 25°C
}


class RedoxSpecification(BaseModel):
    """Specification for redox conditions in Fe-P modeling."""

    mode: RedoxMode = Field(
        "aerobic",
        description=(
            "Redox mode for simulation: "
            "'aerobic' (pe=+3.5, typical ORP ~200mV for aerated biological treatment; Fe(III) dominates), "
            "'anaerobic' (pe=-4 via Fix_pe, Fe(II) dominates), "
            "'pe_from_orp' (calculate pe from ORP measurement), "
            "'fixed_pe' (use specified pe value), "
            "'fixed_fe2_fraction' (experimental: specify Fe(II)/Fe ratio, uses Nernst approximation)."
        ),
    )
    fix_pe: bool = Field(
        True,
        description=(
            "Whether to constrain pe throughout simulation using pseudo-phase equilibrium. "
            "If True (default): pe is fixed using Fix_pe pseudo-phase (anaerobic) or O2(g) "
            "equilibrium (aerobic). This prevents pe drift when oxidizing/reducing agents "
            "are added and models systems with sufficient redox buffering capacity. "
            "If False: pe is set initially but allowed to drift based on solution equilibrium."
        ),
    )
    orp_mv: Optional[float] = Field(
        None,
        description="ORP measurement in mV. Required if mode='pe_from_orp'. Specify reference electrode via orp_reference.",
    )
    orp_reference: ORPReference = Field(
        "AgAgCl_3M",
        description="ORP reference electrode type. SHE, AgAgCl_3M (Ag/AgCl 3M KCl, +210mV), or AgAgCl_sat (+197mV).",
    )
    orp_temperature_c: float = Field(
        25.0,
        description="Temperature for ORP measurement (°C). Used for Nernst equation temperature correction.",
        ge=0,
        le=100,
    )
    pe_value: Optional[float] = Field(
        None,
        description="Fixed pe value. Required if mode='fixed_pe'. Typical range: -10 to +15.",
        ge=-15,
        le=20,
    )
    fe2_fraction: Optional[float] = Field(
        None,
        description="Fe(II)/Fe(total) fraction. Required if mode='fixed_fe2_fraction'. Range 0-1.",
        ge=0,
        le=1,
    )

    @validator("orp_mv", always=True)
    def validate_orp(cls, v, values):
        if values.get("mode") == "pe_from_orp" and v is None:
            raise ValueError("orp_mv is required when mode='pe_from_orp'")
        return v

    @validator("pe_value", always=True)
    def validate_pe(cls, v, values):
        if values.get("mode") == "fixed_pe" and v is None:
            raise ValueError("pe_value is required when mode='fixed_pe'")
        return v


# --- Surface Complexation Options ---


class SurfaceComplexationOptions(BaseModel):
    """Options for HFO surface complexation modeling."""

    enabled: bool = Field(
        True,
        description="Enable surface complexation modeling on HFO (hydrous ferric oxide).",
    )
    surface_name: str = Field(
        "Hfo",
        description="Base surface name for HFO sites (e.g., 'Hfo' for Hfo_sOH, Hfo_wOH).",
    )
    sites_per_mole_strong: float = Field(
        0.005,
        description="Strong site density (mol sites / mol Fe(OH)3). Default: 0.005 (Dzombak & Morel).",
        gt=0,
    )
    weak_to_strong_ratio: float = Field(
        40.0,
        description="Ratio of weak to strong sites. Default: 40 (Dzombak & Morel).",
        gt=0,
    )
    specific_area_m2_per_mol: float = Field(
        53300.0,
        description="Specific surface area in m2/mol Fe(OH)3. Default: 53300.",
        gt=0,
    )
    no_edl: bool = Field(
        False,
        description="Disable electric double layer calculations (faster, less accurate).",
    )


# --- Binary Search Options ---


class BinarySearchOptions(BaseModel):
    """Options for binary search dose optimization."""

    max_iterations: int = Field(
        30,
        description="Maximum binary search iterations.",
        ge=5,
        le=100,
    )
    tolerance_mg_l: float = Field(
        0.01,
        description="Convergence tolerance for P residual in mg/L.",
        gt=0,
    )
    initial_dose_multiplier: float = Field(
        3.0,
        description="Multiplier for stoichiometric estimate to set upper bound.",
        gt=1,
    )
    bracket_expansion_factor: float = Field(
        2.0,
        description="Factor to expand search bracket if initial bounds don't bracket solution.",
        gt=1,
    )


# --- pH Adjustment Options ---


class PhAdjustmentOptions(BaseModel):
    """Options for pH adjustment during coagulation.

    When enabled, a nested binary search finds the optimal base/acid dose
    to achieve the target pH at each coagulant dose iteration.
    """

    enabled: bool = Field(
        False,
        description="Enable pH adjustment optimization.",
    )
    target_ph: Optional[float] = Field(
        None,
        description="Target pH to achieve. Required if enabled=True.",
        ge=4.0,
        le=12.0,
    )
    reagent: str = Field(
        "NaOH",
        description="pH adjustment reagent: 'NaOH' (caustic soda), 'Ca(OH)2' (lime), 'HCl' (acid).",
    )
    max_dose_mmol: float = Field(
        20.0,
        description="Maximum pH adjustment reagent dose in mmol/L.",
        gt=0,
    )
    tolerance_ph: float = Field(
        0.1,
        description="Convergence tolerance for pH (pH units).",
        gt=0,
    )
    max_iterations: int = Field(
        15,
        description="Maximum iterations for pH binary search (inner loop).",
        ge=3,
        le=50,
    )

    @validator("target_ph", always=True)
    def validate_target_ph(cls, v, values):
        if values.get("enabled") and v is None:
            raise ValueError("target_ph is required when pH adjustment is enabled")
        return v


# --- Main Input Schema ---


class CalculateFerricDoseInput(BaseModel):
    """Input for ferric dose calculation to achieve target TP."""

    initial_solution: WaterAnalysisInput = Field(
        ...,
        description="The starting water composition with P concentration.",
    )
    target_residual_p_mg_l: float = Field(
        ...,
        description="Target residual total phosphorus concentration in mg/L as P.",
        ge=0,
        examples=[0.5, 1.0, 0.1, 0.05],
    )
    iron_source: str = Field(
        "FeCl3",
        description="Iron source formula: 'FeCl3' (ferric chloride), 'FeSO4' (ferrous sulfate), 'FeCl2' (ferrous chloride).",
    )
    redox: Optional[RedoxSpecification] = Field(
        None,
        description="Redox specification. Defaults to aerobic if not provided.",
    )
    surface_complexation: Optional[SurfaceComplexationOptions] = Field(
        None,
        description="Surface complexation options. Enabled by default for aerobic conditions.",
    )
    binary_search: Optional[BinarySearchOptions] = Field(
        None,
        description="Binary search optimization options.",
    )
    ph_adjustment: Optional[PhAdjustmentOptions] = Field(
        None,
        description="pH adjustment options for dual P+pH optimization. When enabled, nested binary search finds optimal base/acid dose.",
    )
    include_calcite: bool = Field(
        True,
        description="Include Calcite in equilibrium phases for pH buffering.",
    )
    database: Optional[str] = Field(
        "minteq.v4.dat",
        description="PHREEQC database file. minteq.v4.dat recommended for Fe-P modeling.",
    )
    # --- Sulfide sensitivity for anaerobic mode ---
    sulfide_sensitivity: Optional[bool] = Field(
        None,
        description=(
            "Enable sulfide sensitivity analysis for anaerobic mode. "
            "When True and in anaerobic mode without S(-2) specified, runs multiple simulations "
            "at [0, 20, 50, 100] mg/L S(-2) to show impact on Fe requirements. "
            "Set to False to explicitly acknowledge sulfide-free prediction. "
            "Required for anaerobic mode when S(-2) not in initial_solution."
        ),
    )
    # --- Optional tuning parameters ---
    p_inert_soluble_mg_l: float = Field(
        0.0,
        description=(
            "Non-reactive soluble P (mg/L) that won't precipitate. "
            "Accounts for organic P, colloidal P. Use when effluent shows a 'floor' P concentration."
        ),
        ge=0,
    )
    hfo_site_multiplier: float = Field(
        1.0,
        description=(
            "Scaling factor for HFO surface site density (0.5-2.0). "
            "Lower for aged/co-precipitated HFO, higher for fresh HFO with high mixing."
        ),
        ge=0.1,
        le=5.0,
    )
    organics_ligand_mmol_l: Optional[float] = Field(
        None,
        description=(
            "Proxy for organic interference via metal complexation (mmol/L). "
            "Models Fe complexation by dissolved organics. Typical: 0.01-0.05 (low), 0.1-0.5 (high/industrial)."
        ),
        ge=0,
    )
    max_dose_mg_l: float = Field(
        500.0,
        description=(
            "Maximum Fe dose to try in binary search (mg/L as Fe). "
            "Auto-scales for high-P applications: effective_max = max(this_value, 15 * initial_P * 1.8). "
            "For digesters (100-300 mg/L P), auto-scaling ensures sufficient dose range."
        ),
        ge=10,
    )

    @validator("target_residual_p_mg_l")
    def validate_target_p(cls, v, values):
        # Get initial P from solution if available
        initial_solution = values.get("initial_solution")
        if initial_solution and initial_solution.analysis:
            initial_p = initial_solution.analysis.get("P", initial_solution.analysis.get("P(5)", 0))
            if isinstance(initial_p, (int, float)) and v >= initial_p:
                raise ValueError(f"Target P ({v} mg/L) must be less than initial P ({initial_p} mg/L)")
        return v


# --- Partitioning Output ---


class PhosphatePartitioning(BaseModel):
    """Phosphate partitioning between phases."""

    dissolved_p_mmol: float = Field(..., description="Dissolved P in mmol/L.")
    dissolved_p_mg_l: float = Field(..., description="Dissolved P in mg/L as P.")
    adsorbed_p_mmol: Optional[float] = Field(None, description="P adsorbed on HFO surface in mmol/L.")
    precipitated_p_mmol: Optional[float] = Field(None, description="P in precipitated phases in mmol/L.")
    precipitated_phases: Optional[Dict[str, float]] = Field(
        None, description="Moles of each precipitated phase containing P."
    )
    total_p_removal_percent: Optional[float] = Field(None, description="Total P removal as percentage of initial P.")


# --- Mechanistic Partition Output (NEW) ---


class MechanisticPartition(BaseModel):
    """Detailed mechanistic partition showing WHERE P and Fe ended up.

    This allows verification of which mechanisms are driving P removal:
    - Adsorption (HFO surface complexation)
    - Precipitation (Strengite, Vivianite)
    - Mixed (both contribute significantly)
    """

    # Phosphorus partitioning (mmol/L)
    p_on_hfo_surfaces_mmol: float = Field(..., description="P adsorbed on HFO surfaces (mmol/L).")
    p_in_strengite_mmol: float = Field(..., description="P in Strengite precipitate (mmol/L).")
    p_in_vivianite_mmol: float = Field(..., description="P in Vivianite precipitate (mmol/L).")
    p_dissolved_mmol: float = Field(..., description="Residual dissolved P (mmol/L).")

    # Iron partitioning (mmol/L)
    fe_in_ferrihydrite_mmol: float = Field(..., description="Fe in Ferrihydrite/Fe(OH)3 (mmol/L).")
    fe_in_vivianite_mmol: float = Field(
        ..., description="Fe in Vivianite (mmol/L). Note: Vivianite = Fe3(PO4)2, so 3 Fe per formula."
    )
    fe_in_fes_mmol: float = Field(..., description="Fe in FeS (mackinawite/amorphous) (mmol/L).")
    fe_in_siderite_mmol: float = Field(..., description="Fe in Siderite FeCO3 (mmol/L).")
    fe_dissolved_mmol: float = Field(..., description="Residual dissolved Fe (mmol/L).")

    # Mechanism attribution
    p_removal_dominant_mechanism: str = Field(
        ...,
        description=(
            "Dominant P removal mechanism: 'adsorption' (HFO surface), "
            "'precipitation' (Strengite/Vivianite), or 'mixed' (both contribute)."
        ),
    )
    p_removal_by_adsorption_percent: float = Field(..., description="Percentage of removed P via adsorption.")
    p_removal_by_precipitation_percent: float = Field(..., description="Percentage of removed P via precipitation.")


class MarginalFePRatio(BaseModel):
    """Marginal Fe:P ratio showing incremental cost at current operating point.

    While average Fe:P shows overall efficiency, marginal Fe:P shows the
    incremental Fe needed per additional P removed - critical for evaluating
    the cost of pushing to lower P targets.
    """

    value_molar: float = Field(..., description="Marginal dFe/dP in molar ratio (mmol Fe per mmol P removed).")
    description: str = Field(
        default="Additional Fe per additional P removed at current target", description="What this metric represents."
    )
    interpretation: str = Field(
        ..., description="Interpretation guidance (e.g., 'High values (>5) indicate diminishing returns')."
    )


# --- Phosphate Residual Metrics (NEW) ---


class PhosphateResidualMetrics(BaseModel):
    """Explicit phosphate residual metrics for clear P accounting.

    Provides unambiguous metrics for phosphate residuals, distinguishing
    between total P (from PHREEQC), reactive P (what can precipitate/adsorb),
    inert P (user assumption), and orthophosphate speciation.
    """

    residual_p_total_mg_l_as_P: float = Field(
        ...,
        description=(
            "Total dissolved phosphorus from PHREEQC TOT('P') in mg/L as P. "
            "This is the thermodynamically calculated residual."
        ),
    )
    residual_p_reactive_mg_l_as_P: float = Field(
        ...,
        description=(
            "Reactive dissolved P = total_P - inert_P (mg/L as P). "
            "This is the P that was available for precipitation/adsorption."
        ),
    )
    p_inert_assumed_mg_l_as_P: float = Field(
        ...,
        description=(
            "User-specified non-reactive P (mg/L as P). "
            "Represents organic P, colloidal P, or other forms that won't precipitate."
        ),
    )
    residual_orthophosphate_mg_l_as_P: Optional[float] = Field(
        None,
        description=(
            "Sum of orthophosphate species (PO4-3 + HPO4-2 + H2PO4- + H3PO4) in mg/L as P. "
            "May differ slightly from total P due to complexed species."
        ),
    )


# --- Redox Diagnostics (NEW) ---


class RedoxDiagnostics(BaseModel):
    """Detailed redox diagnostics for thermodynamic modeling verification.

    Provides transparency into how redox conditions are being modeled,
    including the constraint method used and any drift from target values.
    """

    redox_constraint_type: str = Field(
        ...,
        description=(
            "Method used to constrain pe: 'fix_pe' (pseudo-phase constraint), "
            "'o2_equilibrium' (O2(g) equilibrium), or 'none' (no constraint)."
        ),
    )
    target_pe: float = Field(..., description="Target pe value specified for the simulation.")
    achieved_pe: float = Field(..., description="Actual pe value after equilibration from PHREEQC output.")
    pe_drift: Optional[float] = Field(
        None,
        description="Difference between achieved and target pe. >0.5 indicates potential modeling issues.",
    )
    target_orp_mV_vs_SHE: Optional[float] = Field(
        None,
        description="ORP corresponding to target pe in mV vs SHE (computed from pe).",
    )
    achieved_orp_mV_vs_SHE: Optional[float] = Field(
        None,
        description="ORP corresponding to achieved pe in mV vs SHE (computed from achieved pe).",
    )
    constraint_blocks_used: Optional[List[str]] = Field(
        None,
        description="PHREEQC blocks used for pe constraint, e.g., ['Fix_pe', 'O2(g)'].",
    )


# --- Sulfide Sensitivity Analysis Output ---


class SulfideSensitivityScenario(BaseModel):
    """Result for a single sulfide concentration scenario."""

    sulfide_mg_l: float = Field(..., description="Sulfide concentration for this scenario (mg/L as S).")
    fe_dose_required_mmol: float = Field(..., description="Optimal Fe dose required (mmol/L).")
    fe_dose_required_mg_l: float = Field(..., description="Optimal Fe dose required (mg/L as Fe).")
    fe_to_p_ratio: float = Field(..., description="Molar Fe:P ratio at optimal dose.")
    achieved_p_mg_l: float = Field(..., description="Achieved residual P (mg/L).")
    fes_precipitated_mmol: Optional[float] = Field(None, description="FeS precipitated (mmol/L) at this sulfide level.")


class SulfideSensitivityResult(BaseModel):
    """Results from sulfide sensitivity analysis for anaerobic mode.

    When sulfide_sensitivity=True and anaerobic mode is used without S(-2),
    this contains results for multiple sulfide scenarios to show the impact
    of sulfide competition on Fe requirements.
    """

    scenarios: List[SulfideSensitivityScenario] = Field(
        ...,
        description="Results for each sulfide concentration scenario.",
    )
    recommendation: str = Field(
        ...,
        description="Engineering recommendation based on sensitivity results.",
    )
    primary_scenario_sulfide_mg_l: float = Field(
        ...,
        description="Sulfide level used for the primary (main output) result.",
    )
    sulfide_impact_summary: str = Field(
        ...,
        description="Summary of how sulfide affects Fe requirements (e.g., 'Fe:P increases from 1.6 to 4.2 as sulfide increases from 0 to 100 mg/L').",
    )


class IronPartitioning(BaseModel):
    """Iron partitioning between phases."""

    dissolved_fe_mmol: float = Field(..., description="Dissolved Fe in mmol/L.")
    dissolved_fe_mg_l: float = Field(..., description="Dissolved Fe in mg/L.")
    precipitated_fe_mmol: Optional[float] = Field(None, description="Fe in precipitated phases in mmol/L.")
    precipitated_phases: Optional[Dict[str, float]] = Field(
        None, description="Moles of each precipitated phase containing Fe."
    )
    fe_utilization_percent: Optional[float] = Field(
        None, description="Percentage of added Fe that precipitated/adsorbed."
    )


class FerricDoseOptimizationSummary(BaseModel):
    """Summary of ferric dose optimization results."""

    optimal_fe_dose_mmol: float = Field(..., description="Optimal Fe dose in mmol/L.")
    optimal_fe_dose_mg_l: float = Field(..., description="Optimal Fe dose in mg/L as Fe.")
    optimal_product_dose_mg_l: float = Field(
        ..., description="Optimal dose in mg/L of the actual iron product (e.g., FeCl3, FeSO4)."
    )
    iron_source_used: str = Field(..., description="Iron source used (e.g., 'FeCl3', 'FeSO4').")
    initial_p_mg_l: float = Field(..., description="Initial P concentration in mg/L.")
    target_p_mg_l: float = Field(..., description="Target P concentration in mg/L.")
    achieved_p_mg_l: float = Field(..., description="Achieved P concentration in mg/L.")
    fe_to_p_molar_ratio: float = Field(..., description="Molar Fe:P ratio at optimal dose.")
    p_removal_efficiency_percent: float = Field(..., description="P removal efficiency as percentage.")
    iterations_taken: int = Field(..., description="Number of binary search iterations.")
    convergence_achieved: bool = Field(..., description="Whether convergence was achieved within tolerance.")
    convergence_status: str = Field(..., description="Convergence status message.")
    ph_adjustment_dose_mmol: Optional[float] = Field(
        None, description="pH adjustment reagent dose in mmol/L if pH control enabled."
    )
    ph_adjustment_dose_mg_l: Optional[float] = Field(
        None, description="pH adjustment reagent dose in mg/L if pH control enabled."
    )
    ph_adjustment_reagent: Optional[str] = Field(
        None, description="pH adjustment reagent used (e.g., 'NaOH', 'Ca(OH)2')."
    )
    target_ph: Optional[float] = Field(None, description="Target pH if pH control enabled.")
    achieved_ph: Optional[float] = Field(None, description="Final pH after dosing.")
    ph_convergence_achieved: Optional[bool] = Field(
        None, description="Whether pH convergence was achieved within tolerance."
    )
    alkalinity_consumed_mg_caco3_l: Optional[float] = Field(
        None, description="Alkalinity consumed by iron dosing in mg/L as CaCO3."
    )
    alkalinity_remaining_mg_caco3_l: Optional[float] = Field(
        None, description="Remaining alkalinity after treatment in mg/L as CaCO3."
    )
    redox_mode_used: str = Field(..., description="Redox mode that was applied.")
    pe_used: Optional[float] = Field(None, description="pe value used in simulation.")
    surface_complexation_enabled: bool = Field(..., description="Whether surface complexation was enabled.")
    optimization_path: Optional[List[Dict[str, Any]]] = Field(
        None, description="Binary search iteration history for debugging."
    )
    notes: Optional[List[str]] = Field(None, description="Additional notes and warnings.")


# --- Status Type ---

FerricDoseStatus = Literal["success", "infeasible", "input_error"]


# --- Infeasible Response ---


class InfeasibleResponse(BaseModel):
    """Response when target cannot be achieved."""

    status: Literal["infeasible"] = Field("infeasible", description="Infeasible status.")
    error_message: str = Field(..., description="Description of why target couldn't be achieved.")
    best_achieved: Optional[Dict[str, float]] = Field(
        None, description="Best achieved result before giving up (fe_dose_mg_l, residual_p_mg_l)."
    )
    suggestions: Optional[List[str]] = Field(None, description="Suggestions for resolving the infeasibility.")


# --- Main Output Schema ---


class CalculateFerricDoseOutput(SolutionOutput):
    """Output from ferric dose calculation."""

    status: FerricDoseStatus = Field(
        "success", description="Operation status: 'success', 'infeasible', or 'input_error'."
    )
    error_message: Optional[str] = Field(None, description="Error message if status is not 'success'.")
    suggestions: Optional[List[str]] = Field(
        None, description="Suggestions for resolving issues (if status is 'infeasible')."
    )
    optimization_summary: Optional[FerricDoseOptimizationSummary] = Field(
        None, description="Summary of dose optimization results."
    )
    phosphate_partitioning: Optional[PhosphatePartitioning] = Field(
        None, description="Phosphate distribution between dissolved, adsorbed, and precipitated phases."
    )
    iron_partitioning: Optional[IronPartitioning] = Field(
        None, description="Iron distribution between dissolved and precipitated phases."
    )
    mechanistic_partition: Optional[MechanisticPartition] = Field(
        None,
        description=(
            "Detailed mechanistic breakdown showing WHERE P and Fe went. "
            "Use to verify whether removal is adsorption-driven or precipitation-driven."
        ),
    )
    marginal_fe_to_p: Optional[MarginalFePRatio] = Field(
        None,
        description=(
            "Marginal Fe:P ratio (dFe/dP) at current operating point. "
            "Shows incremental cost of pushing to lower P targets."
        ),
    )
    phosphate_residual_metrics: Optional[PhosphateResidualMetrics] = Field(
        None,
        description=(
            "Explicit phosphate residual metrics for unambiguous P accounting. "
            "Distinguishes between total P, reactive P, inert P, and orthophosphate."
        ),
    )
    redox_diagnostics: Optional[RedoxDiagnostics] = Field(
        None,
        description=(
            "Detailed redox diagnostics showing pe constraint method, target vs achieved values, "
            "and any drift. Use to verify thermodynamic modeling assumptions."
        ),
    )
    sulfide_assumption: Optional[str] = Field(
        None,
        description=(
            "Sulfide modeling assumption: 'sulfide_free_limit' (no S(-2) specified, "
            "represents optimistic lower bound) or 'with_sulfide' (FeS competition modeled)."
        ),
    )
    sulfide_sensitivity_results: Optional[SulfideSensitivityResult] = Field(
        None,
        description=(
            "Results from sulfide sensitivity analysis for anaerobic mode. "
            "Present when sulfide_sensitivity=True and anaerobic mode without S(-2) specified."
        ),
    )
    precipitated_phases: Optional[Dict[str, float]] = Field(
        None, description="All precipitated phases and their amounts in mmol."
    )
    final_conditions: Optional[Dict[str, Any]] = Field(
        None, description="Final conditions including pH, pe, ionic strength."
    )
    database_used: Optional[str] = Field(None, description="PHREEQC database that was used.")
    warnings: Optional[List[str]] = Field(None, description="Non-fatal warnings from the simulation.")


# --- Utility Functions ---


def orp_to_pe(orp_mv: float, temperature_celsius: float = 25.0, reference: str = "SHE") -> float:
    """
    Convert ORP (mV vs reference) to pe.

    pe = E_h / (2.303 * R * T / F)

    At 25C: pe = E_h (mV) / 59.16

    Args:
        orp_mv: ORP in millivolts vs specified reference electrode
        temperature_celsius: Temperature in Celsius
        reference: Reference electrode type ("SHE", "AgAgCl_3M", "AgAgCl_sat")

    Returns:
        pe value
    """
    # Apply reference correction to convert to SHE
    correction = ORP_REFERENCE_CORRECTIONS.get(reference, 0.0)
    orp_vs_she = orp_mv + correction

    # Nernst factor: 2.303 * R * T / F
    # At 25C: 59.16 mV per pe unit
    temperature_kelvin = temperature_celsius + 273.15
    nernst_factor = 2.303 * 8.314 * temperature_kelvin / 96485 * 1000  # mV

    return orp_vs_she / nernst_factor


def pe_to_orp(pe: float, temperature_celsius: float = 25.0) -> float:
    """
    Convert pe to ORP (mV vs SHE).

    Args:
        pe: pe value
        temperature_celsius: Temperature in Celsius

    Returns:
        ORP in millivolts vs SHE
    """
    temperature_kelvin = temperature_celsius + 273.15
    nernst_factor = 2.303 * 8.314 * temperature_kelvin / 96485 * 1000  # mV

    return pe * nernst_factor


# Molecular weights for unit conversions
# Note: These are used for dose conversions. For more precise values,
# consider using the periodictable package in the future.
MOLECULAR_WEIGHTS = {
    # Elements
    "P": 30.97,  # Phosphorus
    "Fe": 55.85,  # Iron
    "Al": 26.98,  # Aluminum
    # Iron coagulants
    "FeCl3": 162.2,  # Ferric chloride
    "FeSO4": 151.9,  # Ferrous sulfate
    "FeCl2": 126.75,  # Ferrous chloride
    "Fe2(SO4)3": 399.9,  # Ferric sulfate
    # Aluminum coagulants
    "AlCl3": 133.34,  # Aluminum chloride
    "Al2(SO4)3": 342.15,  # Alum (aluminum sulfate)
    # pH adjustment reagents
    "NaOH": 40.0,  # Sodium hydroxide
    "Ca(OH)2": 74.09,  # Calcium hydroxide (lime)
    "HCl": 36.46,  # Hydrochloric acid
}


# Coagulant definitions: maps formula to metal type and atoms per formula
# Critical for correct dose calculations with multi-metal formulas like Fe2(SO4)3
COAGULANT_DEFINITIONS = {
    # Iron coagulants
    "FeCl3": {"metal": "Fe", "metal_atoms": 1, "oxidation_state": 3},
    "FeSO4": {"metal": "Fe", "metal_atoms": 1, "oxidation_state": 2},
    "FeCl2": {"metal": "Fe", "metal_atoms": 1, "oxidation_state": 2},
    "Fe2(SO4)3": {"metal": "Fe", "metal_atoms": 2, "oxidation_state": 3},
    # Aluminum coagulants
    "AlCl3": {"metal": "Al", "metal_atoms": 1, "oxidation_state": 3},
    "Al2(SO4)3": {"metal": "Al", "metal_atoms": 2, "oxidation_state": 3},
}


def get_coagulant_metal(formula: str) -> str:
    """Get the active metal element for a coagulant formula."""
    if formula not in COAGULANT_DEFINITIONS:
        raise ValueError(f"Unknown coagulant formula: {formula}")
    return COAGULANT_DEFINITIONS[formula]["metal"]


def get_metal_atoms_per_formula(formula: str) -> int:
    """Get the number of metal atoms per formula unit.

    Critical for correct dose calculations:
    - FeCl3: 1 Fe atom per formula → 1 mmol FeCl3 = 1 mmol Fe
    - Fe2(SO4)3: 2 Fe atoms per formula → 1 mmol Fe2(SO4)3 = 2 mmol Fe
    - Al2(SO4)3: 2 Al atoms per formula → 1 mmol Al2(SO4)3 = 2 mmol Al
    """
    if formula not in COAGULANT_DEFINITIONS:
        raise ValueError(f"Unknown coagulant formula: {formula}")
    return COAGULANT_DEFINITIONS[formula]["metal_atoms"]


def metal_dose_to_product_dose(metal_dose_mmol: float, formula: str) -> float:
    """Convert metal dose (mmol) to product formula dose (mmol).

    Example: 2 mmol Fe with Fe2(SO4)3 → 1 mmol Fe2(SO4)3
    """
    atoms = get_metal_atoms_per_formula(formula)
    return metal_dose_mmol / atoms


def product_dose_to_metal_dose(product_dose_mmol: float, formula: str) -> float:
    """Convert product formula dose (mmol) to metal dose (mmol).

    Example: 1 mmol Fe2(SO4)3 → 2 mmol Fe
    """
    atoms = get_metal_atoms_per_formula(formula)
    return product_dose_mmol * atoms


def is_iron_coagulant(formula: str) -> bool:
    """Check if the coagulant is iron-based."""
    return formula in COAGULANT_DEFINITIONS and COAGULANT_DEFINITIONS[formula]["metal"] == "Fe"


def is_aluminum_coagulant(formula: str) -> bool:
    """Check if the coagulant is aluminum-based."""
    return formula in COAGULANT_DEFINITIONS and COAGULANT_DEFINITIONS[formula]["metal"] == "Al"


def mg_l_to_mmol(mg_l: float, element: str) -> float:
    """Convert mg/L to mmol/L."""
    mw = MOLECULAR_WEIGHTS.get(element)
    if not mw:
        raise ValueError(f"Unknown molecular weight for {element}")
    return mg_l / mw


def mmol_to_mg_l(mmol: float, element: str) -> float:
    """Convert mmol/L to mg/L."""
    mw = MOLECULAR_WEIGHTS.get(element)
    if not mw:
        raise ValueError(f"Unknown molecular weight for {element}")
    return mmol * mw
