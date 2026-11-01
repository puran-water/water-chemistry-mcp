"""Executable validation suite for the shared mADM1 Fe-S-P kernel."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Dict

from puran_mcp_support.chemistry import assess_validation_suite
from puran_mcp_support.schema import HFO_VALIDATION_CORPUS, MADM1_FECL3_FE_S_P_KERNEL, MADM1_MINERAL_EXTENSION_KERNEL, MINERAL_EXTENSION_DB_COUNTERPARTS, MINERAL_EXTENSION_25C_ANCHOR_COUNTERPARTS, ValidationCase, ValidationDataset, mao_yue_effective_total_sites, validate_reaction_conservation
from water_chemistry_mcp.tools.surface_interaction import simulate_hfo_adsorption
from water_chemistry_mcp.waterchem.contracts.common import assert_finite_payload, redact_paths
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox
from water_chemistry_mcp.waterchem.thermodynamic_audit import render_custom_phases, saturation_indices_from_activities


#: A3 equilibrium-reachability recipes: per registry phase, a seeded
#: SUPERSATURATED solution that must precipitate the phase from zero initial
#: mass (EQUILIBRIUM_PHASES "<name> 0 0") and close element mass. Reaching
#: equilibrium proves the rendered constants define a phase PHREEQC can
#: equilibrate against — the SI-identity benchmark alone cannot show that.
#: "elements" carries the per-formula-unit stoichiometric counts used for the
#: closure bookkeeping; "initial_mol" the seeded totals (mol per kg water).
SHARED_KERNEL_REACHABILITY_RECIPES = {
    # --- Fe-S-P kernel (prefix Shared_) ---
    "FeS_amorphous": {
        "kernel": "fesp",
        "solution": "    temp 25\n    pH 7\n    pe -4\n    Fe(2) 3e-3\n    S(-2) 3e-3\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Fe": 1, "S": 1},
        "initial_mol": {"Fe": 3.0e-3, "S": 3.0e-3},
    },
    "Mackinawite": {
        "kernel": "fesp",
        "solution": "    temp 25\n    pH 7\n    pe -4\n    Fe(2) 3e-3\n    S(-2) 3e-3\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Fe": 1, "S": 1},
        "initial_mol": {"Fe": 3.0e-3, "S": 3.0e-3},
    },
    "Vivianite": {
        "kernel": "fesp",
        "solution": "    temp 25\n    pH 7\n    pe -4\n    Fe(2) 3e-3\n    P 2e-3\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Fe": 3, "P": 2},
        "initial_mol": {"Fe": 3.0e-3, "P": 2.0e-3},
    },
    "Siderite": {
        "kernel": "fesp",
        "solution": "    temp 25\n    pH 7.5\n    pe -4\n    Fe(2) 3e-3\n    C(4) 1e-2\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Fe": 1, "C": 1},
        "initial_mol": {"Fe": 3.0e-3, "C": 1.0e-2},
    },
    # --- mineral-extension kernel (prefix SharedMin_) ---
    "Calcite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 9\n    Ca 5e-3\n    C(4) 1e-2\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Ca": 1, "C": 1},
        "initial_mol": {"Ca": 5.0e-3, "C": 1.0e-2},
    },
    "Aragonite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 9\n    Ca 5e-3\n    C(4) 1e-2\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Ca": 1, "C": 1},
        "initial_mol": {"Ca": 5.0e-3, "C": 1.0e-2},
    },
    "TCP_beta": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 9\n    Ca 5e-3\n    P 3e-3\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Ca": 3, "P": 2},
        "initial_mol": {"Ca": 5.0e-3, "P": 3.0e-3},
    },
    "Hydroxylapatite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 8\n    Ca 5e-3\n    P 3e-3\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Ca": 5, "P": 3},
        "initial_mol": {"Ca": 5.0e-3, "P": 3.0e-3},
    },
    "Brushite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 6.5\n    Ca 2e-2\n    P 5e-2\n    Na 5e-2\n    Cl 5e-2",
        "elements": {"Ca": 1, "P": 1},
        "initial_mol": {"Ca": 2.0e-2, "P": 5.0e-2},
    },
    "OCP": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 7\n    Ca 1e-2\n    P 1e-2\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Ca": 4, "P": 3},
        "initial_mol": {"Ca": 1.0e-2, "P": 1.0e-2},
    },
    "Struvite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 9\n    pe 0\n    Mg 1e-2\n    N(-3) 5e-2\n    P 1e-2\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Mg": 1, "N": 1, "P": 1},
        "initial_mol": {"Mg": 1.0e-2, "N": 5.0e-2, "P": 1.0e-2},
    },
    "Newberyite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 6.5\n    Mg 5e-2\n    P 5e-2\n    Na 5e-2\n    Cl 5e-2",
        "elements": {"Mg": 1, "P": 1},
        "initial_mol": {"Mg": 5.0e-2, "P": 5.0e-2},
    },
    "Magnesite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 9.5\n    Mg 1e-2\n    C(4) 2e-2\n    Na 1e-2\n    Cl 1e-2",
        "elements": {"Mg": 1, "C": 1},
        "initial_mol": {"Mg": 1.0e-2, "C": 2.0e-2},
    },
    "K_struvite": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 10\n    pe 0\n    K 3e-1\n    Mg 5e-2\n    P 5e-2\n    Cl 4e-1",
        "elements": {"K": 1, "Mg": 1, "P": 1},
        "initial_mol": {"K": 3.0e-1, "Mg": 5.0e-2, "P": 5.0e-2},
    },
    "AlPO4_am": {
        "kernel": "ext",
        "solution": "    temp 25\n    pH 5\n    Al 2e-3\n    P 2e-2\n    Na 2e-2\n    Cl 2e-2",
        "elements": {"Al": 1, "P": 1},
        "initial_mol": {"Al": 2.0e-3, "P": 2.0e-2},
    },
}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(result: Any, *, benchmark_id: str) -> Dict[str, Any]:
    data = getattr(result, "data", result)
    if not isinstance(data, dict):
        raise TypeError(f"{benchmark_id} returned a non-mapping result")
    return data


@holds_batch_redox
async def run_shared_kernel_validation_suite(
    database: str = "minteq.v4.dat",
) -> Dict[str, Any]:
    """Run analytical, PHREEQC, and published-experiment truth benchmarks.

    This action deliberately requires the full validation tier set when it
    summarizes results. Published cases include explicit model-form challenges,
    not just data used to set model parameters. Missing modeled observables and
    plant-holdout evidence remain failed gates and can never authorize a reagent
    setpoint.
    """

    kernel = MADM1_FECL3_FE_S_P_KERNEL
    phase_ids = ("FeS_amorphous", "Mackinawite", "Vivianite", "Siderite")
    try:
        database_path = resolve_database(database, category="general")
        database_hash = _sha256_file(database_path)
        components = {component.component_id: component for component in kernel.components}
        reaction_reports = tuple(
            validate_reaction_conservation(reaction, components)
            for reaction in kernel.reactions
        )
        maximum_reaction_residual = max(
            abs(value)
            for report in reaction_reports
            for value in report.residuals.values()
        )

        rendered, names = render_custom_phases(kernel, phase_ids)
        selected_phase_names = " ".join(names[phase_id] for phase_id in phase_ids)
        def phase_si_input(temperature_c: float) -> str:
            return f"""{rendered}
SOLUTION 1
    units mol/kgw
    temp {temperature_c:g}
    pH 7
    pe -4
    Fe(2) 1e-4
    S(-2) 1e-4
    P 1e-5
    C(4) 1e-3
    Na 1e-2
    Cl 1e-2
SELECTED_OUTPUT 1
    -reset false
    -high_precision true
    -pH true
    -pe true
    -mu true
    -si {selected_phase_names}
USER_PUNCH 1
    -headings a_Fe_II_aq a_HS_minus a_H_plus a_PO4 a_CO3 a_H2O
    10 PUNCH ACT("Fe+2"), ACT("HS-"), ACT("H+"), ACT("PO4-3"), ACT("CO3-2"), ACT("H2O")
END
"""
        si_input = phase_si_input(25.0)
        si_result = _require_mapping(
            await run_phreeqc_job(
                PhreeqcJob(
                    input_string=si_input,
                    tool_name="run_shared_kernel_validation_suite",
                    job_name="shared_kernel_phase_si_benchmark",
                    database_path=database_path,
                )
            ),
            benchmark_id="phase SI benchmark",
        )
        activities = dict(si_result.get("species_activities") or {})
        phreeqc_si_raw = dict(si_result.get("saturation_indices") or {})
        required_activities = {
            "Fe_II_aq",
            "HS_minus",
            "H_plus",
            "PO4",
            "CO3",
            "H2O",
        }
        missing_activities = sorted(required_activities - set(activities))
        missing_phase_si = sorted(
            phase_id for phase_id in phase_ids if names[phase_id] not in phreeqc_si_raw
        )
        if missing_activities or missing_phase_si:
            raise KeyError(
                "PHREEQC SI benchmark omitted selected output: "
                f"activities={missing_activities}, phases={missing_phase_si}"
            )
        shared_si = saturation_indices_from_activities(kernel, phase_ids, activities)
        phreeqc_si = {
            phase_id: float(phreeqc_si_raw[names[phase_id]])
            for phase_id in phase_ids
        }
        si_errors = {
            phase_id: abs(shared_si[phase_id] - phreeqc_si[phase_id])
            for phase_id in phase_ids
        }

        # Repeat away from the 25 C reference point. This catches the former
        # translator defect where shared phase enthalpies were silently omitted
        # from the rendered PHREEQC PHASES block.
        temperature_benchmark_c = 35.0
        si_temperature_result = _require_mapping(
            await run_phreeqc_job(
                PhreeqcJob(
                    input_string=phase_si_input(temperature_benchmark_c),
                    tool_name="run_shared_kernel_validation_suite",
                    job_name="shared_kernel_phase_temperature_benchmark",
                    database_path=database_path,
                )
            ),
            benchmark_id="phase temperature SI benchmark",
        )
        temperature_activities = dict(
            si_temperature_result.get("species_activities") or {}
        )
        temperature_phreeqc_si_raw = dict(
            si_temperature_result.get("saturation_indices") or {}
        )
        missing_temperature_activities = sorted(
            required_activities - set(temperature_activities)
        )
        missing_temperature_phase_si = sorted(
            phase_id
            for phase_id in phase_ids
            if names[phase_id] not in temperature_phreeqc_si_raw
        )
        if missing_temperature_activities or missing_temperature_phase_si:
            raise KeyError(
                "PHREEQC temperature benchmark omitted selected output: "
                f"activities={missing_temperature_activities}, "
                f"phases={missing_temperature_phase_si}"
            )
        shared_temperature_si = saturation_indices_from_activities(
            kernel,
            phase_ids,
            temperature_activities,
            temperature_k=temperature_benchmark_c + 273.15,
        )
        phreeqc_temperature_si = {
            phase_id: float(temperature_phreeqc_si_raw[names[phase_id]])
            for phase_id in phase_ids
        }
        temperature_si_errors = {
            phase_id: abs(
                shared_temperature_si[phase_id]
                - phreeqc_temperature_si[phase_id]
            )
            for phase_id in phase_ids
        }

        # ---- mineral-extension kernel: the Ca/Mg/K/Al phase set (11 phases) ----
        # Same identity mechanism: OUR constants rendered into PHREEQC, SI
        # compared on PHREEQC's own activities at 25 and 35 C. Independence is
        # NOT uniform: phases without a same-convention minteq.v4 counterpart
        # (Struvite, K_struvite, AlPO4_am; the P&B82 analytic carbonates) are
        # SELF-RENDERED — their check validates our implementation of the
        # chosen constants, not the constants themselves. The provenance map
        # in the payload says which is which; no fake independence.
        extension_kernel = MADM1_MINERAL_EXTENSION_KERNEL
        extension_phase_ids = tuple(
            phase.phase_id for phase in extension_kernel.phases
        )
        ext_rendered, ext_names = render_custom_phases(
            extension_kernel, extension_phase_ids, phase_name_prefix="SharedMin_"
        )
        ext_selected_names = " ".join(
            ext_names[phase_id] for phase_id in extension_phase_ids
        )

        def extension_si_input(temperature_c: float) -> str:
            return f"""{ext_rendered}
SOLUTION 1
    units mol/kgw
    temp {temperature_c:g}
    pH 7
    Ca 2e-3
    Mg 1e-3
    K 1e-3
    N(-3) 1e-3
    Al 1e-6
    P 1e-4
    C(4) 2e-3
    Na 5e-3
    Cl 5e-3
SELECTED_OUTPUT 1
    -reset false
    -high_precision true
    -pH true
    -mu true
    -si {ext_selected_names}
USER_PUNCH 1
    -headings a_Ca_ii a_Mg_ii a_K_i a_NH4 a_Al_iii a_H_plus a_PO4 a_CO3 a_H2O
    10 PUNCH ACT("Ca+2"), ACT("Mg+2"), ACT("K+"), ACT("NH4+"), ACT("Al+3"), ACT("H+"), ACT("PO4-3"), ACT("CO3-2"), ACT("H2O")
END
"""

        ext_required_activities = {
            "Ca_ii", "Mg_ii", "K_i", "NH4", "Al_iii", "H_plus", "PO4", "CO3", "H2O",
        }
        ext_si_errors: Dict[str, float] = {}
        ext_temperature_si_errors: Dict[str, float] = {}
        for temperature_c, error_sink in (
            (25.0, ext_si_errors),
            (temperature_benchmark_c, ext_temperature_si_errors),
        ):
            ext_result = _require_mapping(
                await run_phreeqc_job(
                    PhreeqcJob(
                        input_string=extension_si_input(temperature_c),
                        tool_name="run_shared_kernel_validation_suite",
                        job_name=(
                            "mineral_extension_phase_si_benchmark_"
                            f"{temperature_c:g}C"
                        ),
                        database_path=database_path,
                    )
                ),
                benchmark_id=f"mineral extension SI benchmark {temperature_c:g} C",
            )
            ext_activities = dict(ext_result.get("species_activities") or {})
            ext_phreeqc_si_raw = dict(ext_result.get("saturation_indices") or {})
            ext_missing_activities = sorted(
                ext_required_activities - set(ext_activities)
            )
            ext_missing_phases = sorted(
                phase_id
                for phase_id in extension_phase_ids
                if ext_names[phase_id] not in ext_phreeqc_si_raw
            )
            if ext_missing_activities or ext_missing_phases:
                raise KeyError(
                    "PHREEQC mineral-extension benchmark omitted selected "
                    f"output at {temperature_c:g} C: "
                    f"activities={ext_missing_activities}, "
                    f"phases={ext_missing_phases}"
                )
            ext_shared_si = saturation_indices_from_activities(
                extension_kernel,
                extension_phase_ids,
                ext_activities,
                temperature_k=temperature_c + 273.15,
            )
            for phase_id in extension_phase_ids:
                error_sink[phase_id] = abs(
                    ext_shared_si[phase_id]
                    - float(ext_phreeqc_si_raw[ext_names[phase_id]])
                )

        mineral_extension_provenance = {
            phase_id: {
                "database_counterpart": (
                    MINERAL_EXTENSION_DB_COUNTERPARTS.get(phase_id)
                    or MINERAL_EXTENSION_25C_ANCHOR_COUNTERPARTS.get(phase_id)
                ),
                "independence": (
                    "constants share a same-convention minteq.v4 counterpart"
                    if phase_id in MINERAL_EXTENSION_DB_COUNTERPARTS
                    else (
                        "counterpart at 25 C anchor ONLY — the P&B82 analytic "
                        "temperature law is SELF-RENDERED (minteq.v4 carries a "
                        "different-constant entry); this benchmark validates our "
                        "implementation of the chosen constants, not the "
                        "constants themselves"
                        if phase_id in MINERAL_EXTENSION_25C_ANCHOR_COUNTERPARTS
                        else (
                            "SELF-RENDERED constants — this benchmark validates "
                            "our implementation of the chosen constants, not the "
                            "constants themselves"
                        )
                    )
                ),
                "temperature_law": next(
                    (
                        "analytic_expression"
                        if phase.analytical_expression is not None
                        else (
                            "held_at_25C"
                            if phase.delta_h_j_mol is None
                            else (
                                "explicit_zero_delta_h"
                                if phase.delta_h_j_mol == 0.0
                                else "vanthoff_delta_h"
                            )
                        )
                    )
                    for phase in extension_kernel.phases
                    if phase.phase_id == phase_id
                ),
            }
            for phase_id in extension_phase_ids
        }

        vivianite_name = names["Vivianite"]
        initial_fe = 3.0e-3
        initial_p = 2.0e-3
        amount_input = f"""{rendered}
SOLUTION 1
    units mol/kgw
    temp 25
    pH 7
    pe -4
    Fe(2) {initial_fe}
    P {initial_p}
    Na 2e-2
    Cl 2e-2
EQUILIBRIUM_PHASES 1
    {vivianite_name} 0 0
SELECTED_OUTPUT 1
    -reset false
    -high_precision true
    -water true
USER_PUNCH 1
    -headings equi_{vivianite_name} tot_P tot_Fe
    10 PUNCH EQUI("{vivianite_name}"), TOT("P"), TOT("Fe")
END
"""
        amount_result = _require_mapping(
            await run_phreeqc_job(
                PhreeqcJob(
                    input_string=amount_input,
                    tool_name="run_shared_kernel_validation_suite",
                    job_name="shared_kernel_vivianite_amount_benchmark",
                    database_path=database_path,
                )
            ),
            benchmark_id="vivianite amount benchmark",
        )
        precipitated = float(
            dict(amount_result.get("equilibrium_phase_moles") or {})[vivianite_name]
        )
        water_mass = float(
            dict(amount_result.get("solution_summary") or {})["mass_kg_water"]
        )
        totals = dict(amount_result.get("element_totals_molality") or {})
        p_closure_error = abs(float(totals["P"]) * water_mass + 2 * precipitated - initial_p)
        fe_closure_error = abs(
            float(totals["Fe"]) * water_mass + 3 * precipitated - initial_fe
        )

        # ---- A3: all-15-phase equilibrium reachability (was Vivianite-only) ----
        # Each registry phase must be REACHABLE as an equilibrium: a seeded
        # supersaturated solution precipitates it from zero initial mass, lands
        # on SI ~ 0, and closes element mass against the dissolution
        # stoichiometry. Self-rendered phases (see the provenance map) prove
        # only that OUR rendered constants equilibrate self-consistently.
        equilibrium_reachability: Dict[str, Any] = {}
        for reach_phase_id, recipe in SHARED_KERNEL_REACHABILITY_RECIPES.items():
            if recipe["kernel"] == "fesp":
                reach_rendered, reach_names = rendered, names
            else:
                reach_rendered, reach_names = ext_rendered, ext_names
            reach_name = reach_names[reach_phase_id]
            closure_elements = recipe["elements"]
            tot_headings = " ".join(f"tot_{el}" for el in closure_elements)
            tot_punch = ", ".join(f'TOT("{el}")' for el in closure_elements)
            reach_input = f"""{reach_rendered}
SOLUTION 1
    units mol/kgw
{recipe["solution"]}
EQUILIBRIUM_PHASES 1
    {reach_name} 0 0
SELECTED_OUTPUT 1
    -reset false
    -high_precision true
    -water true
    -saturation_indices {reach_name}
USER_PUNCH 1
    -headings equi_{reach_name} {tot_headings}
    10 PUNCH EQUI("{reach_name}"), {tot_punch}
END
"""
            reach_result = _require_mapping(
                await run_phreeqc_job(
                    PhreeqcJob(
                        input_string=reach_input,
                        tool_name="run_shared_kernel_validation_suite",
                        job_name=(
                            "shared_kernel_equilibrium_reachability_"
                            f"{reach_phase_id}"
                        ),
                        database_path=database_path,
                    )
                ),
                benchmark_id=f"equilibrium reachability {reach_phase_id}",
            )
            reach_precip = float(
                dict(reach_result.get("equilibrium_phase_moles") or {})[reach_name]
            )
            reach_water = float(
                dict(reach_result.get("solution_summary") or {})["mass_kg_water"]
            )
            reach_totals = dict(reach_result.get("element_totals_molality") or {})
            reach_si = float(
                dict(reach_result.get("saturation_indices") or {})[reach_name]
            )
            closure_errors = {
                el: abs(
                    float(reach_totals[el]) * reach_water
                    + count * reach_precip
                    - recipe["initial_mol"][el]
                )
                for el, count in closure_elements.items()
            }
            equilibrium_reachability[reach_phase_id] = {
                "precipitated_mol": reach_precip,
                "final_si": reach_si,
                "closure_errors_mol": closure_errors,
                "max_closure_error_mol": max(closure_errors.values()),
                "self_rendered": (
                    recipe["kernel"] == "ext"
                    and reach_phase_id not in MINERAL_EXTENSION_DB_COUNTERPARTS
                ),
            }

        hfo_result = await simulate_hfo_adsorption(
            {
                "analysis": {"P": 10.0, "Na": 20.0, "Cl": 20.0},
                "units": "mmol/L",
                "ph": 7.0,
            },
            hfo_mass_g=0.1,
            database=database,
        )
        if not isinstance(hfo_result, dict) or "code" in hfo_result:
            raise RuntimeError(
                "HFO closed-batch benchmark failed: "
                + str((hfo_result or {}).get("message", "invalid result"))
            )
        site_moles = sum(
            float(row["moles"])
            for row in hfo_result["surface_sites"]["sites"]
        )
        expected_site_moles = (0.1 / 89.0) * 0.205
        site_inventory_error = abs(site_moles - expected_site_moles)
        dissolved_p_moles = (
            float(hfo_result["element_totals_molality"]["P"])
            * float(hfo_result["solution_summary"]["mass_kg_water"])
        )
        sorbed_p_moles = float(hfo_result["surface_adsorbed_moles"]["P_Hfo"])
        system_p_moles = float(hfo_result["system_element_moles"]["P"])
        hfo_p_closure_error = abs(
            dissolved_p_moles + sorbed_p_moles - system_p_moles
        )

        # Independent model-form challenge from Wang et al. (2013), DOI
        # 10.1021/es401301z / OpenAlex W2163458761. The paper reports 24 h
        # phosphate capacities at pH 4.5 and 0.01 M KCl of 1690 and 980
        # umol-P/g as ferrihydrite crystallite size increases from 1.6 to
        # 4.4 nm and BET area falls from 427 to 234 m2/g. We evaluate the
        # current equilibrium D-M surface under excess phosphate (5 mmol/L,
        # 1 g/L HFO). This is deliberately a morphology/time model-form test,
        # not a claim that the batch protocols are identical.
        ferrihydrite_capacity_predictions: dict[str, float] = {}
        for case_id, area_m2_g in (
            ("small_crystallite_427_m2_g", 427.0),
            ("large_crystallite_234_m2_g", 234.0),
        ):
            capacity_result = await simulate_hfo_adsorption(
                {
                    "analysis": {"P": 5.0, "K": 10.0, "Cl": 10.0},
                    "units": "mmol/L",
                    "ph": 4.5,
                },
                hfo_mass_g=1.0,
                hfo_specific_area_m2_g=area_m2_g,
                database=database,
            )
            if not isinstance(capacity_result, dict) or "code" in capacity_result:
                raise RuntimeError(
                    "published ferrihydrite-capacity benchmark failed: "
                    + str((capacity_result or {}).get("message", "invalid result"))
                )
            ferrihydrite_capacity_predictions[case_id] = (
                float(capacity_result["surface_adsorbed_moles"]["P_Hfo"])
                * 1.0e6
            )

        validation_corpus = {
            dataset.dataset_id: dataset for dataset in HFO_VALIDATION_CORPUS
        }

        datasets = (
            ValidationDataset(
                dataset_id="shared_kernel_exact_conservation",
                tier="analytical_limit",
                source="process-model-schema exact elemental/charge/redox matrix",
                matrix_scope="all registered mADM1 Fe-S-P reactions",
                observables=("maximum_reaction_conservation_residual",),
                intended_use="analytical software truth test",
                empirical=False,
            ),
            ValidationDataset(
                dataset_id="phreeqc_identical_definition_runtime",
                tier="independent_equilibrium",
                source=f"{database}:{database_hash}",
                matrix_scope="controlled 25 C and 35 C inorganic Fe-S-P-carbonate solutions",
                observables=(
                    "phase_saturation_index",
                    "vivianite_equilibrium_amount",
                    "closed_batch_HFO_P_balance",
                ),
                intended_use="independent thermodynamic implementation benchmark",
                empirical=False,
            ),
            validation_corpus["mao_yue_2016_in_situ_hfo"],
            validation_corpus["ferrihydrite_crystallite_size_2013"],
            validation_corpus["iron_sulfide_reoxidation_p_capture_2010"],
            validation_corpus["smp_competition_fresh_hfo_2016"],
        )
        cases = [
            ValidationCase(
                case_id="exact_reaction_conservation",
                dataset_id="shared_kernel_exact_conservation",
                observable_id="maximum_absolute_residual",
                observed_value=0.0,
                unit="coefficient_basis",
                absolute_tolerance=1e-12,
            )
        ]
        predictions = {"exact_reaction_conservation": maximum_reaction_residual}
        for phase_id in phase_ids:
            case_id = f"phreeqc_si_identity_{phase_id}"
            cases.append(
                ValidationCase(
                    case_id=case_id,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=f"absolute_log10_SI_error:{phase_id}",
                    observed_value=0.0,
                    unit="log10_saturation_index",
                    absolute_tolerance=1e-6,
                )
            )
            predictions[case_id] = si_errors[phase_id]
            temperature_case_id = f"phreeqc_si_temperature_identity_{phase_id}"
            cases.append(
                ValidationCase(
                    case_id=temperature_case_id,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=(
                        f"absolute_log10_SI_error_at_{temperature_benchmark_c:g}C:"
                        f"{phase_id}"
                    ),
                    observed_value=0.0,
                    unit="log10_saturation_index",
                    absolute_tolerance=1e-6,
                )
            )
            predictions[temperature_case_id] = temperature_si_errors[phase_id]
        for phase_id in extension_phase_ids:
            case_id = f"phreeqc_si_identity_mineral_extension_{phase_id}"
            cases.append(
                ValidationCase(
                    case_id=case_id,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=f"absolute_log10_SI_error:{phase_id}",
                    observed_value=0.0,
                    unit="log10_saturation_index",
                    absolute_tolerance=1e-6,
                )
            )
            predictions[case_id] = ext_si_errors[phase_id]
            temperature_case_id = (
                f"phreeqc_si_temperature_identity_mineral_extension_{phase_id}"
            )
            cases.append(
                ValidationCase(
                    case_id=temperature_case_id,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=(
                        f"absolute_log10_SI_error_at_{temperature_benchmark_c:g}C:"
                        f"{phase_id}"
                    ),
                    observed_value=0.0,
                    unit="log10_saturation_index",
                    absolute_tolerance=1e-6,
                )
            )
            predictions[temperature_case_id] = ext_temperature_si_errors[phase_id]
        for reach_phase_id, reach in equilibrium_reachability.items():
            eq_case = f"phreeqc_equilibrium_reachability_{reach_phase_id}"
            cases.append(
                ValidationCase(
                    case_id=eq_case,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=f"final_SI_at_equilibrium:{reach_phase_id}",
                    observed_value=0.0,
                    unit="log10_saturation_index",
                    absolute_tolerance=5e-3,
                )
            )
            predictions[eq_case] = reach["final_si"]
            cl_case = f"phreeqc_reachability_mass_closure_{reach_phase_id}"
            cases.append(
                ValidationCase(
                    case_id=cl_case,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=f"max_element_inventory_error:{reach_phase_id}",
                    observed_value=0.0,
                    unit="mol",
                    absolute_tolerance=5e-8,
                )
            )
            predictions[cl_case] = reach["max_closure_error_mol"]
            pr_case = f"phreeqc_reachability_precipitated_{reach_phase_id}"
            cases.append(
                ValidationCase(
                    case_id=pr_case,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=f"moles_precipitated_indicator:{reach_phase_id}",
                    observed_value=1.0,
                    unit="indicator",
                    absolute_tolerance=0.5,
                )
            )
            predictions[pr_case] = (
                1.0 if reach["precipitated_mol"] > 1.0e-10 else 0.0
            )
        for case_id, observable, prediction, tolerance in (
            ("vivianite_P_closure", "P_inventory_error", p_closure_error, 2e-8),
            ("vivianite_Fe_closure", "Fe_inventory_error", fe_closure_error, 2e-8),
            ("hfo_site_inventory", "site_inventory_error", site_inventory_error, 1e-12),
            ("hfo_closed_batch_P", "P_system_balance_error", hfo_p_closure_error, 3e-7),
        ):
            cases.append(
                ValidationCase(
                    case_id=case_id,
                    dataset_id="phreeqc_identical_definition_runtime",
                    observable_id=observable,
                    observed_value=0.0,
                    unit="mol",
                    absolute_tolerance=tolerance,
                )
            )
            predictions[case_id] = prediction

        # These three values are the source of the fresh-HFO effective-site
        # surrogate. They remain visible as fit/parameter-contact checks but
        # are explicitly excluded from independent validation coverage.
        for formation_ph, observed, uncertainty in (
            (6.0, 0.955, 0.170),
            (7.0, 1.46, 0.39),
            (8.0, 2.98, 0.78),
        ):
            case_id = f"mao_yue_effective_sites_pH_{formation_ph:g}"
            cases.append(
                ValidationCase(
                    case_id=case_id,
                    dataset_id="mao_yue_2016_in_situ_hfo",
                    observable_id="effective_site_capacity",
                    observed_value=observed,
                    unit="mol_effective_sites_per_mol_Fe",
                    absolute_tolerance=uncertainty,
                    parameter_ids_fitted_from_same_dataset=(
                        "hfo_mao_yue_in_situ_effective_v1",
                    ),
                    evidence_role="parameter_contact",
                )
            )
            predictions[case_id] = mao_yue_effective_total_sites(formation_ph)

        for suffix, observed in (
            ("small_crystallite_427_m2_g", 1690.0),
            ("large_crystallite_234_m2_g", 980.0),
        ):
            case_id = f"ferrihydrite_capacity_{suffix}"
            cases.append(
                ValidationCase(
                    case_id=case_id,
                    dataset_id="ferrihydrite_crystallite_size_2013",
                    observable_id="24h_phosphate_capacity_pH4.5_0.01M_KCl",
                    observed_value=observed,
                    unit="umol_P_per_g_ferrihydrite",
                    # The paper abstract does not report an uncertainty for
                    # these endpoint capacities. Ten percent is a declared
                    # screening acceptance band, not inferred measurement SD.
                    relative_tolerance=0.10,
                    evidence_role="model_form_challenge",
                )
            )
            predictions[case_id] = ferrihydrite_capacity_predictions[suffix]

        # The shared serial equations now represent FeS reoxidation and a
        # reduced conditional ligand/SMP competition surrogate. They still
        # cannot produce protocol-comparable independent predictions for these
        # observations without the papers' time-series boundary conditions,
        # material histories, and independently selected kinetic/ligand
        # parameters. Keep the predictions absent: availability of an equation
        # is not experimental validation and must not be converted into a fit to
        # the same endpoint being tested.
        cases.extend(
            (
                ValidationCase(
                    case_id="fes_reoxidation_phosphate_yield",
                    dataset_id="iron_sulfide_reoxidation_p_capture_2010",
                    observable_id="phosphate_removed_per_ferric_iron_dosed",
                    observed_value=0.44,
                    unit="mg_P_per_mg_Fe",
                    relative_tolerance=0.10,
                ),
                ValidationCase(
                    case_id="smp_hfo_DLM_surface_complexation_logK",
                    dataset_id="smp_competition_fresh_hfo_2016",
                    observable_id="DLM_SMP_HFO_surface_complexation_log10K",
                    observed_value=15.1,
                    unit="log10_equilibrium_constant",
                    absolute_tolerance=0.11,
                ),
            )
        )

        validation = assess_validation_suite(
            datasets=datasets,
            cases=cases,
            predictions=predictions,
        )
        result: Dict[str, Any] = {
            "status": "completed_diagnostic_validation",
            "kernel_id": kernel.kernel_id,
            "kernel_hash": kernel.sha256,
            "database": database,
            "database_hash": database_hash,
            "mineral_extension_kernel_id": extension_kernel.kernel_id,
            "mineral_extension_kernel_hash": extension_kernel.sha256,
            "benchmarks": {
                "reaction_conservation_maximum_residual": maximum_reaction_residual,
                "phase_si_absolute_errors": si_errors,
                "phase_temperature_benchmark_c": temperature_benchmark_c,
                "phase_temperature_si_absolute_errors": temperature_si_errors,
                "mineral_extension_phase_si_absolute_errors": ext_si_errors,
                "mineral_extension_phase_temperature_si_absolute_errors": (
                    ext_temperature_si_errors
                ),
                "mineral_extension_phase_provenance": mineral_extension_provenance,
                "vivianite_equilibrium_moles": precipitated,
                "equilibrium_reachability": equilibrium_reachability,
                "vivianite_P_closure_error_mol": p_closure_error,
                "vivianite_Fe_closure_error_mol": fe_closure_error,
                "hfo_site_inventory_error_mol": site_inventory_error,
                "hfo_closed_batch_P_closure_error_mol": hfo_p_closure_error,
                "published_ferrihydrite_capacity_model_form": {
                    "source_doi": "10.1021/es401301z",
                    "openalex_id": "W2163458761",
                    "model_protocol": (
                        "equilibrium D-M surface; pH 4.5; 0.01 M KCl; "
                        "5 mmol/L P; 1 g/L HFO"
                    ),
                    "predicted_umol_P_g": ferrihydrite_capacity_predictions,
                    "reported_24h_umol_P_g": {
                        "small_crystallite_427_m2_g": 1690.0,
                        "large_crystallite_234_m2_g": 980.0,
                    },
                    "comparison_class": "model_form_challenge_not_identical_protocol",
                },
                "published_protocol_prediction_gaps": {
                    "fes_reoxidation_phosphate_yield": {
                        "source_doi": "10.1016/j.watres.2010.03.023",
                        "openalex_id": "W2094295724",
                        "equations_available": True,
                        "prediction_available": False,
                        "reason": (
                            "independent protocol time series, FeS inventory/age, "
                            "oxidant exposure, and non-source-fit kinetic parameters "
                            "are not available in the repository"
                        ),
                    },
                    "smp_hfo_DLM_surface_complexation_logK": {
                        "source_doi": "10.2166/wst.2016.431",
                        "openalex_id": "W2519116963",
                        "equations_available": True,
                        "prediction_available": False,
                        "reason": (
                            "the current conditional ligand surrogate does not "
                            "implement the paper's molecule/site-specific DLM "
                            "reaction and no independent fitted parameter set is supplied"
                        ),
                    },
                },
            },
            "validation": validation,
            "recommendation_eligible": False,
            "recommendation_blockers": sorted(
                set(validation["recommendation_blockers"])
                | {
                    "thermodynamic_validation_does_not_validate_kinetics_or_plant_TP",
                    "site_calibration_and_temporal_holdout_validation_absent",
                }
            ),
        }
        assert_finite_payload(result)
        return result
    except WaterChemistryError as exc:
        return exc.to_public_dict()
    except Exception as exc:
        return public_error(
            "SHARED_KERNEL_VALIDATION_ERROR",
            f"Shared-kernel validation failed: {redact_paths(str(exc))}",
            {
                "tool_name": "run_shared_kernel_validation_suite",
                "stage": "validation",
            },
        )


__all__ = ["run_shared_kernel_validation_suite"]
