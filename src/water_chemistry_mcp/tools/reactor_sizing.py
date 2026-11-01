"""Screening reactor-sizing for standalone physicochemical precipitation units.

Exposes the vetted mineral-kinetics layer (utils.kinetic_database) as an MCP tool so a design agent
can size the CONTACT TIME / retention time / reactor volume of a seeded precipitation reactor (lime
softening, struvite/pellet crystallizer, tertiary Ca-P) without hand-building PHREEQC KINETICS. This
is a SCREENING model (surface-controlled first-order approach to equilibrium); it is NOT a design-grade
crystallizer model (no crystal population balance, CSTR-vs-batch hydraulics, mass transfer, solids
recycle, aggregation). Confirm with a jar/pilot test and layer a design_safety_factor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError

from water_chemistry_mcp.utils.kinetic_database import KINETIC_DATABASE, get_kinetic_parameters, seeded_precipitation_contact_time


class SizePrecipitationReactorRequest(BaseModel):
    """Inputs for screening reactor sizing of a seeded precipitation."""

    mineral: str = Field(..., description="Mineral being precipitated (e.g. Calcite, Brucite, Struvite, "
                                          "Hydroxyapatite, Gypsum). Case-insensitive; formulas like CaCO3 accepted.")
    initial_si: float = Field(..., description="Saturation index of the mineral at the reactor inlet "
                                               "(after dosing). SI > 0 is supersaturated; <= 0 means nothing precipitates.")
    equilibrium_conc_mmol_per_L: float = Field(..., gt=0, description="Equilibrium (residual) concentration of the "
                                               "rate-limiting constituent, mmol/L (e.g. softened Ca ~0.5, struvite P ~0.1).")
    reactive_surface_area_m2_per_L: float = Field(1.0, gt=0, description="Reactive (seed) surface area per litre, m2/L. "
                                                  "Higher = faster (seeded/solids-recycle reactors have more area).")
    approach_fraction: float = Field(0.90, gt=0, lt=1.0, description="Target fraction of the way from the inlet "
                                     "supersaturation to equilibrium (0.90 = reach 90% of the achievable removal).")
    temperature_c: float = Field(25.0, ge=0, le=100, description="Operating temperature, C (Arrhenius-corrects the rate).")
    flow_m3_d: Optional[float] = Field(None, gt=0, description="Design flow, m3/d. If given, a reactor volume is returned.")
    design_safety_factor: float = Field(1.0, ge=1.0, le=5.0, description="Multiplier on the screening contact time / "
                                        "reactor volume for imperfect mixing, floc/seed variability and model uncertainty.")


async def size_precipitation_reactor(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Screening estimate of the contact time / retention time / reactor volume for a seeded
    precipitation reactor (lime softening, struvite crystallizer, tertiary Ca-P), from the vetted
    mineral-kinetics database. Returns the kinetic time constant, t90/t95/t99, the contact time for the
    requested approach-to-equilibrium, an optional reactor volume (if flow is given), a seeding-required
    flag (inlet SI below the mineral's nucleation threshold), and explicit screening caveats.
    """
    try:
        req = SizePrecipitationReactorRequest.model_validate(input_data)
    except ValidationError as e:
        return {"code": "INVALID_INPUT", "message": f"Invalid reactor-sizing input: {e}"}

    params = get_kinetic_parameters(req.mineral)
    if params is None:
        return {
            "code": "UNKNOWN_MINERAL",
            "message": f"No kinetic parameters for '{req.mineral}'. Known minerals: "
                       f"{sorted(KINETIC_DATABASE)}.",
        }

    k = seeded_precipitation_contact_time(
        mineral=req.mineral,
        initial_si=req.initial_si,
        reactive_surface_area_m2_per_L=req.reactive_surface_area_m2_per_L,
        equilibrium_conc_mmol_per_L=req.equilibrium_conc_mmol_per_L,
        approach_fraction=req.approach_fraction,
        temperature_c=req.temperature_c,
    )

    if not k.get("supersaturated"):
        return {
            "status": "not_supersaturated",
            "message": f"{req.mineral} is at or below saturation (SI={req.initial_si}); nothing precipitates. "
                       "Increase the dose / pH so the mineral is supersaturated before sizing a reactor.",
            **{key: k.get(key) for key in ("mineral", "nucleation_si_threshold", "seeding_required", "caveats")},
        }

    # Design contact time = screening contact time * safety factor; reactor volume V = Q * HRT.
    contact_time_min = k["contact_time_min"] * req.design_safety_factor
    result: Dict[str, Any] = {
        "status": "success",
        "mineral": req.mineral,
        "temperature_c": req.temperature_c,
        "rate_constant_25c_mol_m2_s": params.get("rate_constant"),
        "references": params.get("references"),
        "rate_constant_at_T_mol_m2_s": k["rate_constant_at_T"],
        "time_constant_min": k["time_constant_s"] / 60.0,
        "screening_contact_time_min": k["contact_time_min"],
        "design_safety_factor": req.design_safety_factor,
        "design_contact_time_min": contact_time_min,
        "approach_fraction": k["approach_fraction"],
        "t90_min": k["t90_min"],
        "t95_min": k["t95_min"],
        "t99_min": k["t99_min"],
        "seeding_required": k["seeding_required"],
        "nucleation_si_threshold": k["nucleation_si_threshold"],
        "model": k["model"],
        "caveats": k["caveats"],
    }
    # SI is used only to gate supersaturation (SI>0) and seeding (SI vs nucleation threshold); the
    # first-order kernel does NOT shorten the contact time for higher SI, so do not read a higher inlet
    # SI as a smaller reactor here.
    result["si_usage_note"] = ("initial_si gates supersaturation + the seeding_required flag only; the "
                               "first-order kernel does not shorten contact time for higher SI.")
    if req.flow_m3_d is not None:
        hrt_d = contact_time_min / 1440.0
        result["flow_m3_d"] = req.flow_m3_d
        result["required_hrt_min"] = contact_time_min
        # NOTE: named "screening_reactor_volume_m3" (not "reactor_volume_m3") to signal this is an
        # ideal-contact-volume screening estimate, NOT a final mixing-regime-specific design volume.
        result["screening_reactor_volume_m3"] = req.flow_m3_d * hrt_d
        result["hydraulic_model_assumption"] = (
            "ideal contact volume V = Q * HRT; NOT mixing-regime-specific -- no CSTR N-tanks-in-series, "
            "dead zones, short-circuiting, or plug-flow dispersion. Apply a hydraulic/pilot factor."
        )
    return result
