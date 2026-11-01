"""Contracts for phosphorus-removal treatment calculations."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import ConfigDict, Field, model_validator

from .common import StrictFiniteBaseModel, assert_finite_payload

#: Owner ruling 2026-10-15 ("Refuse, name the key"): the screen refuses an NH4 analysis key by name, since it does not
#: say whether its value is mg/L as NH4 or as N; callers pass ammonium nitrogen as N under N(-3). The key's spellings
#: (case, a charge sign) are one key. It is refused in EVERY unit: the molar exemption of

#: kept it).
NH4_KEY_RULING_ID = 'PUBLIC-RULE-a687712bdbd8'
_REFUSED_AMMONIUM_KEYS = frozenset({"nh4", "nh4+"})


#: (`waterchem.analysis`); re-exported here, where the rule was first built.
from ..input_key_rulings import COMPOUND_KEY_RULING_ID, INPUT_KEY_RULING_ID  # noqa: E402,F401
#: A post-digester liquor (digestate, centrate, filtrate) carries the sulfate that survived digestion, as measured: the
#: screen takes it in ``fixed_pe``, which holds sulfate as fed, never in ``anaerobic``, which reduces the feed's sulfate


POST_DIGESTER_FEED_SOURCES = frozenset({"digestate", "centrate", "filtrate", "post_digester_liquor"})
FEED_SOURCES = ("digester_feed", *sorted(POST_DIGESTER_FEED_SOURCES), "other")


def _resolve_analysis_keys(initial_solution: Any) -> Any:
    "The P screen's analysis with every basis settled before anything reads it."
    from water_chemistry_mcp.waterchem.analysis import settled_analysis

    if not isinstance(initial_solution, dict) or not isinstance(initial_solution.get("analysis"), dict):
        return initial_solution
    for key, value in initial_solution["analysis"].items():
        stated = (isinstance(value, dict) and bool(value.get("as"))) or (
            isinstance(value, str) and " as " in f" {value.strip().lower()} "
        )
        if str(key).strip().lower() in _REFUSED_AMMONIUM_KEYS and not stated:
            raise ValueError(
                f"analysis key {key!r} is refused ({NH4_KEY_RULING_ID}, {COMPOUND_KEY_RULING_ID}): it does not "
                "say whether the value is mg/L as NH4 or as N. Pass ammonium nitrogen as N under 'N(-3)' "
                "(mg N/L), or state the basis: {'value': x, 'as': 'NH4'}."
            )
    return settled_analysis(initial_solution)


class PhosphorusRemovalStrategyRequest(StrictFiniteBaseModel):
    """Strategy selector and dosing bounds for phosphorus removal."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["iron", "aluminum", "struvite", "calcium_phosphate"]
    reagent: Optional[str] = None
    max_dose_mmol: float = Field(50.0, gt=0)
    si_trigger: Optional[float] = None
    allowed_phases: Optional[List[str]] = None
    iron_sulfide_phase_case: Optional[
        Literal["mackinawite", "amorphous_fes"]
    ] = None

    @model_validator(mode="after")
    def validate_iron_sulfide_case(self) -> "PhosphorusRemovalStrategyRequest":
        if self.iron_sulfide_phase_case is not None and self.strategy != "iron":
            raise ValueError("iron_sulfide_phase_case is valid only for the iron strategy")
        return self


class ParameterContactObservation(StrictFiniteBaseModel):
    """Optional plant observation used only to detect capacity-parameter contact."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(..., min_length=1)
    delivered_fe_mg_l: float = Field(..., gt=0)
    influent_p_mg_l: float = Field(..., ge=0)
    residual_p_mg_l: float = Field(..., ge=0)
    p_measurement_basis: Literal[
        "dissolved_reactive_p", "orthophosphate_p", "total_p"
    ]
    observation_port: str = Field(..., min_length=1)
    relative_contact_tolerance: float = Field(0.10, ge=0, le=1)

    @model_validator(mode="after")
    def validate_nonnegative_removal(self) -> "ParameterContactObservation":
        if self.residual_p_mg_l > self.influent_p_mg_l:
            raise ValueError("parameter-contact observation must imply nonnegative P removal")
        return self


#: The anaerobic mode's donor (owner ruling 2026-10-17 on the gpt-astra review, M18: "P-screen anaerobic: explicit
#: finite-donor reactions now"); the method and every constant are `engineering_utils.finite_donor_redox`'s.
FINITE_DONOR_RULING_ID = 'PUBLIC-RULE-8226e460e1d8'


class ElectronDonorRequest(StrictFiniteBaseModel):
    """The electron donor the anaerobic mode reduces nitrate, ferric iron and sulfate with: finite, stated, its basis
    and source named. Nothing defaults: an unstated cell-synthesis fraction refuses (0 is allowed, and is then the
    donor's thermodynamic minimum demand, the most reduction a donor can buy)."""

    model_config = ConfigDict(extra="forbid")

    compound: Optional[str] = Field(
        None,
        description=(
            "A named donor of engineering_utils.finite_donor_redox.DONOR_COMPOUNDS (acetate, propionate, butyrate, "
            "valerate, methanol, ethanol, glucose, carbohydrate, hydrogen, domestic_wastewater_organics, protein), "
            "or the name of the donor whose 'formula' is given."
        ),
    )
    formula: Optional[str] = Field(None, description="The donor's neutral molecule CcHhOoNn (an acid as the acid).")
    available_cod_mg_l: float = Field(
        ..., ge=0, description="COD of the donor available to the reduction, mg O2/L of feed."
    )
    basis: Literal["measured", "assumed"] = Field(..., description="Whether the available COD was measured or assumed.")
    source: str = Field(..., min_length=1, description="Where the available COD comes from (an analysis, a balance).")
    cell_synthesis_fraction: Union[float, Dict[str, float]] = Field(
        ...,
        description=(
            "fs, the share of the donor's electrons each acceptor's organisms put into cells (C5H7O2N): one number "
            "for every acceptor, or one per acceptor ('nitrate_to_n2', 'ferric_to_ferrous', 'sulfate_to_sulfide'). "
            "0 <= fs < 1; it depends on the organism and the solids retention time (Rittmann & McCarty 2001)."
        ),
    )

    @model_validator(mode="after")
    def resolve_donor(self) -> "ElectronDonorRequest":
        from puran_mcp_support.engineering.finite_donor_redox import ACCEPTOR_SEQUENCE, donor_compound

        donor_compound(self.compound, self.formula)
        fractions = self.fractions()
        missing = [acceptor for acceptor in ACCEPTOR_SEQUENCE if acceptor not in fractions]
        unknown = sorted(set(fractions) - set(ACCEPTOR_SEQUENCE))
        if missing or unknown:
            raise ValueError(
                f"cell_synthesis_fraction must state every acceptor {list(ACCEPTOR_SEQUENCE)} (missing {missing}, "
                f"unknown {unknown}), or be one number for all ({FINITE_DONOR_RULING_ID})"
            )
        bad = {a: f for a, f in fractions.items() if not 0.0 <= f < 1.0}
        if bad:
            raise ValueError(f"cell_synthesis_fraction must be 0 <= fs < 1, got {bad}")
        return self

    def fractions(self) -> Dict[str, float]:
        from puran_mcp_support.engineering.finite_donor_redox import ACCEPTOR_SEQUENCE

        if isinstance(self.cell_synthesis_fraction, dict):
            return {str(k): float(v) for k, v in self.cell_synthesis_fraction.items()}
        return {acceptor: float(self.cell_synthesis_fraction) for acceptor in ACCEPTOR_SEQUENCE}


class PhosphorusRemovalRequest(StrictFiniteBaseModel):
    """Public input contract for equilibrium phosphorus-removal screening."""

    model_config = ConfigDict(extra="forbid")

    initial_solution: Dict[str, Any] = Field(
        ...,
        description=(
            "Feed water: 'analysis' keyed by element or redox state ('P' mg P/L, 'N(-3)' mg N/L). A key whose bare mass does not say whose mass it is must state its basis in mg/L or ug/L, {'value': x, 'as': 'PO4'} or 'x as PO4': a compound-named key ('PO4', 'SO4', 'silica', 'sulfide' ...; PUBLIC-RULE-bfdea5a72210), 'S(6)' / 'Si', read as SO4 / SiO2 ({'value': 96, 'as': 'SO4'}; PUBLIC-RULE-e2d8ea8feebf), and 'C(4)' / 'C', read as HCO3, CO3 or C by database ({'value': 61, 'as': 'HCO3'}). A bare value in mmol/L or mol/L is the analyte's moles (PUBLIC-RULE-23b4e8d2c29a); an 'NH4' key is refused by name in every unit (pass 'N(-3)'). 'HCO3'/'CO3' need a meaning in every unit: 'as alkalinity' or 'as species' (the free ion, ion pairs excluded, converted to C(4) at the feed pH)."
        ),
    )
    target_residual_p_mg_l: float = Field(..., ge=0)
    strategy: PhosphorusRemovalStrategyRequest
    redox: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Redox environment as {'mode': ...}. 'aerobic' (O2 equilibrium; ferric strengite + fresh-HFO adsorption), 'anaerobic' (Fix_pe; ferrous vivianite + FeS sulfide competition), 'pe_from_orp'/'fixed_pe' (set pe from a measured ORP or value), or 'full_redox' (carry BOTH the ferric and ferrous phase families across the anoxic transition at a user pe -- equilibrium screening of the whole ORP spectrum). Omit for the aerobic default. 'anaerobic' is the DIGESTER (its feed): it reduces the feed's nitrate, a ferric dose and the sulfate by the stated 'electron_donor', as far as it reaches. A post-digester liquor (digestate, centrate, filtrate) is screened in 'fixed_pe', which holds its measured sulfate as fed (PUBLIC-RULE-e2d8ea8feebf)."
        ),
    )
    feed_source: Optional[Literal["digester_feed", "digestate", "centrate", "filtrate", "post_digester_liquor", "other"]] = Field(
        None,
        description=(
            "Where the feed water comes from. A post-digester liquor ('digestate', 'centrate', 'filtrate', 'post_digester_liquor') is screened in redox mode 'fixed_pe' (sulfate held as measured); the 'anaerobic' mode is the digester on its feed and reduces the feed's sulfate by the stated donor, so on such a feed its result says so (PUBLIC-RULE-e2d8ea8feebf)."
        ),
    )
    electron_donor: Optional[ElectronDonorRequest] = Field(
        None,
        description=(
            "Required in the 'anaerobic' mode, refused in every other: the finite electron donor that reduces the "
            "feed's nitrate (to N2), a ferric dose's iron (to ferrous) and the sulfate (feed and reagent, to sulfide), "
            "in that order, each as far as the donor reaches, with its carbon, cells, COD and alkalinity booked "
            f"({FINITE_DONOR_RULING_ID}; engineering_utils.finite_donor_redox). The donor left is not oxidised "
            "(methanogenesis is outside the screen)."
        ),
    )
    include_background_sinks: bool = False
    sulfide_sensitivity: Optional[bool] = None
    activity_domain_outside: Literal["refuse", "screen"] = Field(
        "refuse",
        description=(
            "A feed above the ionic strength the house reads an ion-association activity model as qualified to "
            "(engineering_utils.house_standards.activity_models, 0.5 mol/kgw) is refused ('refuse', the default): "
            "the screen's databases are Davies / extended Debye-Hueckel and the house has no qualified phosphate "
            "electrolyte model. 'screen' answers anyway, the result stamped outside the domain "
            f"({FINITE_DONOR_RULING_ID}, review M17)."
        ),
    )
    p_inert_soluble_mg_l: float = Field(0.0, ge=0)
    parameter_contact_observation: Optional[ParameterContactObservation] = Field(
        None,
        description=(
            "Optional delivered-Fe and observed net-P data used to flag algebraic "
            "contact with the selected HFO capacity. It never identifies a mechanism."
        ),
    )
    oxide_surface_mode: Literal["fresh_in_situ", "preformed_aged"] = Field(
        "fresh_in_situ",
        description=(
            "Metal-oxide P-adsorbent state for iron/aluminium strategies. 'fresh_in_situ' (default): "
            "coagulant dosed directly into the water forms fresh hydroxide. 'preformed_aged': "
            "pre-formed/aged oxide in recycle polishing, aged floc, or media contactors. The modes "
            "select different declared surface-site datasets; neither is a transferable Fe:P curve."
        ),
    )
    hfo_site_multiplier: float = Field(
        1.0, ge=0.1, le=5.0,
        description="Sensitivity multiplier on the declared oxide-surface dataset. "
                    "For a deliberate fresh/aged choice use oxide_surface_mode.",
    )
    design_safety_factor: float = Field(
        1.0,
        ge=1.0,
        le=5.0,
        description=(
            "Legacy-named multiplier applied to a target-converged equilibrium screen. The multiplied "
            "value is returned only as screening_dose_with_factor and is never recommendation-eligible."
        ),
    )
    target_ph: Optional[float] = Field(
        None,
        ge=3.0,
        le=12.0,
        description=(
            "Operating pH to HOLD during dosing (a Fix_H+ pseudo-phase titrated with NaOH). When set, "
            "the acidifying coagulant no longer drags pH down, so the combined mineral-precipitation + "
            "HFO/HAO surface-complexation model is evaluated at this controlled pH — matching how a plant "
            "trims pH, and putting the result at parity with the fixed-pH mADM1/PCM reference. Omit to "
            "let pH float from the reagent."
        ),
    )
    oxide_formation_ph: Optional[float] = Field(
        None,
        ge=3.0,
        le=12.0,
        description=(
            "Local pH at which fresh HFO/HAO forms when bulk pH is not controlled. "
            "Required for a fresh_in_situ iron/aluminium screen when target_ph is omitted; "
            "initial feed pH is not silently substituted for post-dose formation pH."
        ),
    )
    database: Optional[str] = "minteq.v4.dat"

    @model_validator(mode="before")
    @classmethod
    def one_database(cls, data: Any) -> Any:
        'Public engineering calculation interface.'
        from water_chemistry_mcp.waterchem.database_choice import unify_request_database

        return unify_request_database(data, ('initial_solution',))
    max_iterations: int = Field(30, ge=5, le=100)
    tolerance_mg_l: float = Field(0.1, gt=0)

    @model_validator(mode="before")
    @classmethod
    def resolve_analysis_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "initial_solution" in data:
            for key in (data["initial_solution"] or {}).get("analysis") or {}:
                if str(key).strip().lower() in _REFUSED_AMMONIUM_KEYS - {"nh4"}:
                    # the parser does not know NH4+; refuse it by name rather than as an unknown analyte
                    raise ValueError(
                        f"analysis key {key!r} is refused ({NH4_KEY_RULING_ID}): it does not say whether the value "
                        "is mg/L as NH4 or as N. Pass ammonium nitrogen as N under 'N(-3)' (mg N/L)."
                    )
            data = {**data, "initial_solution": _resolve_analysis_keys(data["initial_solution"])}
        return data

    @model_validator(mode="after")
    def validate_initial_solution(self) -> "PhosphorusRemovalRequest":
        from water_chemistry_mcp.waterchem.analysis import canonicalize_water_analysis

        canonicalize_water_analysis(self.initial_solution)
        if self.parameter_contact_observation is not None and self.strategy.strategy != "iron":
            raise ValueError("parameter_contact_observation is valid only for the iron strategy")
        return self


class PhosphorusPhaseRole(StrictFiniteBaseModel):
    """A phase considered by the phosphorus-removal model and its role."""

    name: str
    family: str
    role: str
    # Optional because a phase may be advisory/inactive in a strategy.
    active: Optional[bool] = None
    # Optional because phase-specific partitioning is available only when extraction succeeds.
    p_removed_mg_l: Optional[float] = None
    # Optional because saturation indices are absent for inline or inactive phases.
    saturation_index: Optional[float] = None


class PhosphorusConvergenceFlags(StrictFiniteBaseModel):
    """Convergence summary independent of raw status text."""

    converged: bool = False
    iterations_used: int = 0
    tolerance_met: bool = False
    # Optional because input-error responses do not have a numeric tolerance context.
    tolerance_mg_l: Optional[float] = None
    # Optional because infeasible/input-error responses may not have a best residual.
    residual_error_mg_l: Optional[float] = None


class PhosphorusMassPartitionPoint(StrictFiniteBaseModel):
    """P mass partition at one dose-search iteration."""

    iteration: int
    dose_mmol: float
    p_solution_mg_l: float
    p_solid_total_mg_l: float
    # Optional because adsorption extraction only applies to surface-enabled strategies.
    p_adsorbed_mg_l: Optional[float] = None
    p_removed_by_phase_mg_l: Dict[str, float] = Field(default_factory=dict)


class PhosphorusSITrigger(StrictFiniteBaseModel):
    """SI-trigger status for metastable precipitation."""

    triggered: bool = False
    # Optional because no phase is named when no trigger fires.
    phase: Optional[str] = None
    # Optional because no saturation index is relevant when no trigger fires.
    saturation_index: Optional[float] = None
    # Optional because no threshold is relevant when no trigger fires.
    threshold: Optional[float] = None
    # Optional because no explanatory message is needed when no trigger fires.
    message: Optional[str] = None



#: unqualified equilibrium result from authorizing reactor size, guaranteed dose or PO clearance"). The screen carries no
#: kinetics, contact time, measured surface inventory or validated electrolyte model, so no result of it can be the basis
#: of any of the three; the contract refuses a result that claims otherwise.
SCREEN_AUTHORIZES = {"reactor_size": False, "guaranteed_dose": False, "po_clearance": False}
SCREEN_NOT_AUTHORIZED_BECAUSE = [
    "an equilibrium screen: no precipitation, nucleation or adsorption kinetics and no contact time, so no reactor size",
    "surface-site inventories are literature datasets, not measured on this water, and no ligand (DOC/humic) model, so "
    "no guaranteed dose",
    "no jar test, pilot or vendor guarantee stands behind it, so no purchase-order clearance",
]


def _default_qualification() -> Dict[str, Any]:
    return {"standing": "equilibrium_screen", "authorizes": dict(SCREEN_AUTHORIZES),
            "not_authorized_because": list(SCREEN_NOT_AUTHORIZED_BECAUSE), "ruling_id": FINITE_DONOR_RULING_ID}


class PhosphorusRemovalResult(StrictFiniteBaseModel):
    """Public output contract for equilibrium phosphorus-removal screening."""

    model_config = ConfigDict(extra="allow")

    status: Literal[
        "converged_target_met",
        "converged_target_not_met",
        "nonconverged",
        "infeasible",
        "solver_error",
        "input_error",
    ]
    # Optional because successful responses do not carry an error message.
    error_message: Optional[str] = None
    strategy_used: str
    reagent_used: str
    strategy_assumptions: Dict[str, Any] = Field(default_factory=dict)
    target_basis: str = "reactive_dissolved_p_as_P"
    target_variable: str = "dissolved_reactive_p"
    calculation_class: Literal["equilibrium_screen"] = "equilibrium_screen"
    recommendation_eligible: Literal[False] = False
    simulation_provenance: Dict[str, Any] = Field(default_factory=dict)
    assumption_ledger: Dict[str, Any] = Field(default_factory=dict)
    phases_involved: List[PhosphorusPhaseRole] = Field(default_factory=list)
    convergence: PhosphorusConvergenceFlags = Field(default_factory=PhosphorusConvergenceFlags)
    tolerance_met: bool = False
    mass_partition: List[PhosphorusMassPartitionPoint] = Field(default_factory=list)
    si_trigger: PhosphorusSITrigger = Field(default_factory=PhosphorusSITrigger)
    warnings: List[str] = Field(default_factory=list)
    qualification: Dict[str, Any] = Field(default_factory=_default_qualification)

    @model_validator(mode="after")
    def authorize_nothing(self) -> "PhosphorusRemovalResult":
        authorizes = (self.qualification or {}).get("authorizes")
        if authorizes != SCREEN_AUTHORIZES:
            raise ValueError(
                f"qualification.authorizes must be {SCREEN_AUTHORIZES}: an equilibrium screen authorizes no reactor "
                f"size, guaranteed dose or PO clearance ({FINITE_DONOR_RULING_ID}, review M17); got {authorizes!r}"
            )
        return self

    @model_validator(mode="after")
    def reject_non_finite_output(self) -> "PhosphorusRemovalResult":
        assert_finite_payload(self.model_dump(exclude_none=True), "phosphorus_removal_result")
        return self
