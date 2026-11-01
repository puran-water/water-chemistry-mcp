"The database's sha256 (`waterchem.answered_by`) says WHICH thermodynamic data answered; it cannot say WHICH problem was\nsolved: the legacy optimiser's retries had deleted EQUILIBRIUM_PHASES or rebuilt a reaction-only model and reported the\nsame database. The structure here is derived from the input the run was given (never written by the tool that asked):\nthe phases each EQUILIBRIUM_PHASES block lets form (with its target SI and any alternative reactant, so a fixed pe or a\nheld pH is part of it), the gas components, the surfaces and the phases they ride on, the exchangers, the REACTION\nformulas, the kinetic rates, MIX and REACTION_TEMPERATURE, the phases and master species the input defines inline (a\nheld redox state is one: Amm, Nit, Nitr, Sulf, Sulfat). Amounts are left out on purpose: a dose search's runs at\ndifferent doses solve one model and share one fingerprint; a run that lets a different set of phases form does not."

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .carbonate_species import _KEYWORDS

CHEMICAL_MODEL_RULING_ID = 'PUBLIC-RULE-8226e460e1d8'
_HOLD_RULING_ID = 'PUBLIC-RULE-d3e7617e1bb5'
#: the decoupled elements a held redox state is carried on (`engineering_utils.phreeqc_batch_redox`)
_HELD_ELEMENTS = frozenset({"Amm", "Nit", "Nitr", "Sulf", "Sulfat"})
#: blocks that open at column 0 beside PHREEQC's data keywords (KNOBS is solver settings: never part of the model)
_ALSO = frozenset({"KNOBS", "DATABASE"})


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _split(input_string: str) -> List[tuple]:
    blocks: List[tuple] = []
    for raw in input_string.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        token = line.split()[0].upper() if not line[:1].isspace() else None
        if token is not None and (token in _KEYWORDS or token in _ALSO or token.endswith("_MODIFY")
                                  or token.endswith("_RAW")):
            blocks.append((token, []))
            continue
        if blocks:
            blocks[-1][1].append(line)
    return blocks


def chemical_model(input_string: str, *, equilibrate_requested: bool = False,
                   reactions_as_requested: Optional[List[str]] = None,
                   requested_input: Optional[str] = None) -> Dict[str, Any]:
    'The structure of the model a PHREEQC input solves, and its ``fingerprint`` (sha256 of the structure, 16 hex).'
    model: Dict[str, Any] = {
        "equilibrium_phases": [], "gas_phase": [], "surfaces": [], "exchange": [], "reactions": [], "kinetics": [],
        "phases_defined": [], "master_species_defined": [], "mix": False, "reaction_temperature": False,
    }
    for keyword, lines in _split(input_string):
        indented = [line.strip() for line in lines if line[:1].isspace()]
        column0 = [line.split()[0] for line in lines if not line[:1].isspace()]
        entries = [line for line in indented if not line.startswith("-")]
        if keyword == "EQUILIBRIUM_PHASES":
            for line in entries:
                tokens = line.split()
                target = tokens[1] if len(tokens) > 1 and _is_number(tokens[1]) else "0"
                reactant = tokens[2] if len(tokens) > 2 and not _is_number(tokens[2]) else None
                model["equilibrium_phases"].append(
                    f"{tokens[0]} SI {float(target):g}" + (f" with {reactant}" if reactant else ""))
        elif keyword == "GAS_PHASE":
            model["gas_phase"] += [line.split()[0] for line in entries]
            model["gas_phase"] += [line.split()[0] for line in indented if line.startswith("-")
                                   and line.split()[0].lower() in {"-fixed_pressure", "-fixed_volume"}]
        elif keyword == "SURFACE":
            for line in entries:
                tokens = line.split()
                linked = [t for t in tokens[1:] if not _is_number(t)]
                numbers = [t for t in tokens[1:] if _is_number(t)]
                model["surfaces"].append(" ".join([tokens[0], *linked, *(f"{float(n):g}" for n in numbers)]))
            model["surfaces"] += [line.split()[0] for line in indented if line.startswith("-")]
        elif keyword == "EXCHANGE":
            model["exchange"] += [line.split()[0] for line in entries]
        elif keyword == "REACTION":
            model["reactions"] += [line.split()[0] for line in entries if not _is_number(line.split()[0])]
        elif keyword == "KINETICS":
            model["kinetics"] += column0  # the rate names open at column 0 inside the block
        elif keyword == "PHASES":
            model["phases_defined"] += column0
        elif keyword == "SOLUTION_MASTER_SPECIES":
            model["master_species_defined"] += [line.split()[0] for line in lines if line.split()]
        elif keyword == "MIX":
            model["mix"] = True
        elif keyword == "REACTION_TEMPERATURE":
            model["reaction_temperature"] = True
    if reactions_as_requested is not None:
        model["reactions"] = list(reactions_as_requested)
    for key, value in model.items():
        if isinstance(value, list):
            model[key] = sorted(set(value))
    held = sorted(set(model["master_species_defined"]) & _HELD_ELEMENTS)
    batch = any(model[key] for key in ("reactions", "equilibrium_phases", "gas_phase", "surfaces", "exchange",
                                       "kinetics", "mix", "reaction_temperature"))
    model["held_redox_states"] = held
    model["batch_redox"] = (
        "no batch step" if not batch else
        f"held as fed on redox-uncoupled elements: {', '.join(held)}" if held else
        "re-equilibrated by request (equilibrate_redox: true): every redox couple, ammonium, nitrate, sulfide and "
        "sulfate included, is equilibrated at one pe in this batch step; an N or S valence it moved is a "
        f"thermodynamic endpoint, not a process result ({_HOLD_RULING_ID})" if equilibrate_requested else
        "held: the batch step carries no N or S state that can change valence (none fed, or one valence in the "
        f"database), so nothing is re-equilibrated ({_HOLD_RULING_ID})" if requested_input is not None else
        "re-equilibrated: every redox couple, ammonium, nitrate, sulfide and sulfate included, is equilibrated at "
        "one pe in this batch step; no biology acts in an equilibrium step, so an N or S valence it moved is a "
        "thermodynamic endpoint, not a process result (review M20; the held-state route is the P screen's and the "
        "walk's, engineering_utils.phreeqc_batch_redox)"
    )
    model["fingerprint"] = hashlib.sha256(
        json.dumps(model, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return model


__all__ = ["CHEMICAL_MODEL_RULING_ID", "chemical_model"]
