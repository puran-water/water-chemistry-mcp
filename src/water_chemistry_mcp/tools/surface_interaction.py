"""Tool for simulating surface complexation interactions."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

from puran_mcp_support.schema import DZOMBAK_MOREL_HFO

from water_chemistry_mcp.waterchem.phreeqc_input import build_selected_output_block, build_surface_block

from water_chemistry_mcp.waterchem.analysis import build_solution_block_from_canonical
from water_chemistry_mcp.waterchem.contracts.common import redact_paths
from water_chemistry_mcp.waterchem.contracts.surface import SurfaceInteractionRequest, SurfaceInteractionResult
from water_chemistry_mcp.waterchem.engine import resolve_database
from water_chemistry_mcp.waterchem.errors import WaterChemistryError, public_error
from water_chemistry_mcp.waterchem.runner import PhreeqcJob, run_phreeqc_job
from water_chemistry_mcp.waterchem.batch_redox import holds_batch_redox

logger = logging.getLogger(__name__)


def _surface_user_punch(surface_def: Dict[str, Any]) -> str:
    """Return auditable sorbed/system P and metal totals for declared surfaces."""

    names = set()
    for site in surface_def.get("sites_info", []) or []:
        if not isinstance(site, dict) or not site.get("name"):
            continue
        names.add(str(site["name"]).split("_", 1)[0])
    for site in surface_def.get("sites", []) or []:
        name = site.get("name") if isinstance(site, dict) else site
        if name:
            names.add(str(name).split("_", 1)[0])
    if not names:
        return ""

    elements = ("P", "Fe", "Al")
    headings = [
        f"surf_{element}_{surface_name}"
        for surface_name in sorted(names)
        for element in elements
    ] + [f"sys_{element}" for element in elements]
    expressions = [
        f'SURF("{element}", "{surface_name}")'
        for surface_name in sorted(names)
        for element in elements
    ] + [f'SYS("{element}")' for element in elements]
    return (
        "USER_PUNCH 1\n"
        f"    -headings {' '.join(headings)}\n"
        f"    10 PUNCH {', '.join(expressions)}\n"
    )


@holds_batch_redox
async def simulate_surface_interaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Simulate surface complexation/adsorption through canonical PHREEQC execution."""

    logger.info("Running simulate_surface_interaction tool...")

    try:
        input_model = SurfaceInteractionRequest.model_validate(input_data)
    except Exception as exc:
        message = redact_paths(str(exc))
        logger.error(f"Input validation error: {message}")
        return public_error(
            "INVALID_INPUT",
            f"Input validation error: {message}",
            {"tool_name": "simulate_surface_interaction", "stage": "validate_input"},
        )

    try:
        database_path = resolve_database(input_model.database, category="surface")
        surface_def = input_model.surface_definition.model_dump(exclude_none=True)
        phreeqc_input = build_solution_block_from_canonical(input_model.initial_canonical())
        phreeqc_input += "\n" + build_surface_block(surface_def, block_num=1)
        phreeqc_input += "USE solution 1\n"
        phreeqc_input += "USE surface 1\n"
        phreeqc_input += "SAVE solution 2\n"
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            surface=True,
            user_punch=True,
        )
        phreeqc_input += _surface_user_punch(surface_def)
        phreeqc_input += "END\n"

        run_result = await run_phreeqc_job(
            PhreeqcJob(
                input_string=phreeqc_input,
                tool_name="simulate_surface_interaction",
                job_name="surface_interaction",
                database_path=database_path,
                database_category="surface",
                num_steps=1,
            )
        )

        results = run_result.data
        if isinstance(results, dict):
            results.setdefault("surface_equilibrated", True)
            results.setdefault("surface_sites", _extract_surface_info(surface_def))
            results["diagnostics"] = {
                **(run_result.diagnostics or {}),
                **(results.get("diagnostics") or {}),
                "engine": run_result.engine,
                "database": run_result.database,
            }

        output_model = SurfaceInteractionResult.model_validate(results)
        logger.info("simulate_surface_interaction tool finished successfully.")
        return output_model.model_dump(exclude_none=True)

    except WaterChemistryError as exc:
        public_payload = exc.to_public_dict()
        logger.error(f"Surface interaction tool failed: {public_payload.get('message')}")
        return public_payload
    except Exception as exc:
        logger.exception("Unexpected error in simulate_surface_interaction")
        return public_error(
            "UNEXPECTED_ERROR",
            f"Unexpected server error: {type(exc).__name__}: {redact_paths(str(exc))}",
            {"tool_name": "simulate_surface_interaction", "stage": "execute"},
        )


def _extract_surface_info(surface_def: Dict[str, Any]) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    if surface_def.get("sites_info"):
        info["sites"] = surface_def["sites_info"]
    elif surface_def.get("sites"):
        info["sites"] = surface_def["sites"]
    if surface_def.get("equilibrate_with_solution_number"):
        info["equilibrated_with_solution"] = surface_def["equilibrate_with_solution_number"]
    for name in (
        "surface_dataset_id",
        "surface_basis",
        "parameter_interpretation",
        "oxide_history_class",
        "site_ratio_policy",
        "surface_initialization_mode",
        "source",
        "legacy_default_values_invalidated",
    ):
        if name in surface_def:
            info[name] = surface_def[name]
    return info


@holds_batch_redox
async def simulate_hfo_adsorption(
    solution_data: Dict[str, Any],
    hfo_mass_g: float = 1.0,
    hfo_specific_area_m2_g: float = 600.0,
    weak_site_density_mol_m2: Optional[float] = None,
    strong_site_density_mol_m2: Optional[float] = None,
    database: Optional[str] = None,
) -> Dict[str, Any]:
    """Simulate adsorption on HFO with an explicit, dimensionally valid basis.

    When no caller-specific site densities are supplied, this uses the
    documented Dzombak--Morel inventory (0.200 weak + 0.005 strong mol sites
    per mol Fe/HFO host).  The historical defaults ``2e-4`` and ``5e-6``
    mol/m2 yielded about 53.4 times too many sites at 600 m2/g and are no
    longer accepted implicitly.
    """

    if not math.isfinite(float(hfo_mass_g)) or hfo_mass_g <= 0:
        raise ValueError("hfo_mass_g must be finite and positive")
    if not math.isfinite(float(hfo_specific_area_m2_g)) or hfo_specific_area_m2_g <= 0:
        raise ValueError("hfo_specific_area_m2_g must be finite and positive")
    if (weak_site_density_mol_m2 is None) != (strong_site_density_mol_m2 is None):
        raise ValueError(
            "weak and strong site densities must either both be omitted or both be supplied"
        )

    total_area = hfo_mass_g * hfo_specific_area_m2_g
    if weak_site_density_mol_m2 is None:
        site_moles = DZOMBAK_MOREL_HFO.site_moles_for_host_mass(hfo_mass_g)
        site_densities = DZOMBAK_MOREL_HFO.site_density_mol_m2(
            hfo_specific_area_m2_g
        )
        surface_metadata = {
            "surface_dataset_id": DZOMBAK_MOREL_HFO.dataset_id,
            "surface_basis": "mol_sites_per_mol_Fe_HFO_host",
            "parameter_interpretation": DZOMBAK_MOREL_HFO.parameter_interpretation,
            "oxide_history_class": DZOMBAK_MOREL_HFO.oxide_history_class,
            "site_ratio_policy": "preserve_weak_to_strong_40_to_1",
            "source": DZOMBAK_MOREL_HFO.source,
            "legacy_default_values_invalidated": True,
        }
    else:
        weak = float(weak_site_density_mol_m2)
        strong = float(strong_site_density_mol_m2)
        if any(not math.isfinite(value) or value <= 0 for value in (weak, strong)):
            raise ValueError("caller-supplied site densities must be finite and positive")
        site_densities = {"weak": weak, "strong": strong}
        site_moles = {
            "weak": weak * total_area,
            "strong": strong * total_area,
        }
        surface_metadata = {
            "surface_dataset_id": "caller_supplied_unvalidated",
            "surface_basis": "mol_sites_per_m2",
            "parameter_interpretation": "caller_supplied_unvalidated",
            "oxide_history_class": "caller_supplied_unresolved",
            "site_ratio_policy": "caller_supplied",
            "source": "caller supplied",
            "legacy_default_values_invalidated": True,
        }
    input_data = {
        "initial_solution": solution_data,
        "surface_definition": {
            "sites_info": [
                {
                    "name": "Hfo_w",
                    "moles": site_moles["weak"],
                    "specific_area_m2_g": hfo_specific_area_m2_g,
                    "mass_g": hfo_mass_g,
                    "site_density_mol_m2": site_densities["weak"],
                },
                {
                    "name": "Hfo_s",
                    "moles": site_moles["strong"],
                    "specific_area_m2_g": hfo_specific_area_m2_g,
                    "mass_g": hfo_mass_g,
                    "site_density_mol_m2": site_densities["strong"],
                },
            ],
            "equilibrate_with_solution_number": None,
            "surface_initialization_mode": "mass_balanced_batch",
            **surface_metadata,
        },
        "database": database,
    }
    return await simulate_surface_interaction(input_data)
