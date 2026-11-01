"On the production engine (phreeqpython, ``USE_PHREEQC_SUBPROCESS=0``) a one-step answer is read from the solutions the\nrun STORED, the highest-numbered one (`tools.phreeqc.parser.parse_phreeqc_results`; VIPhreeqc lists them in ascending\norder). PHREEQC runs a batch step wherever a simulation carries EQUILIBRIUM_PHASES, EXCHANGE, GAS_PHASE, KINETICS, MIX,\nREACTION, REACTION_TEMPERATURE, REACTION_PRESSURE, SOLID_SOLUTIONS, SURFACE or USE (the manual's list), and a batch\nstep stores nothing unless the simulation SAVEs its solution. So a batch that saves nothing, or saves below a stored\ninput's number, was answered with an INPUT water's pH, water and composition while the punched columns were the\nbatch's: on main 9da188b33 the mixing tool returned its last input water as the mixture and a one-step kinetic run its\ninitial water (closure c18, c18b-chem/runs). Such a deck is refused by name, before it runs; a deck whose last batch\nSAVEs into the solution the answer reads is unchanged.\n\nTRANSPORT, ADVECTION and RUN_CELLS store their cells themselves and are not batch steps here. SOLUTION_SPREAD numbers\nits solutions from its own rows, so a batch deck that carries one is refused: which solution answers cannot be checked."

from __future__ import annotations

import re
from typing import List, Optional, Tuple

RULING_ID = 'PUBLIC-RULE-5164c01473a4'

#: PHREEQC's spellings of the keywords this check reads (keywords.cpp), on their canonical names
_SYNONYMS = {
    "END": "END",
    "SOLUTION": "SOLUTION", "SOLUTION_RAW": "SOLUTION_RAW",
    "SOLUTION_SPREAD": "SOLUTION_SPREAD", "SPREAD_SOLUTION": "SOLUTION_SPREAD",
    "SAVE": "SAVE", "USE": "USE", "COPY": "COPY",
    "REACTION": "REACTION", "REACTIONS": "REACTION",
    "EQUILIBRIUM_PHASES": "EQUILIBRIUM_PHASES", "EQUILIBRIUM_PHASE": "EQUILIBRIUM_PHASES",
    "EQUILIBRIA": "EQUILIBRIUM_PHASES", "EQUILIBRIUM": "EQUILIBRIUM_PHASES", "PURE_PHASES": "EQUILIBRIUM_PHASES",
    "PURE": "EQUILIBRIUM_PHASES",
    "MIX": "MIX", "GAS_PHASE": "GAS_PHASE", "SURFACE": "SURFACE", "EXCHANGE": "EXCHANGE", "KINETICS": "KINETICS",
    "SOLID_SOLUTIONS": "SOLID_SOLUTIONS", "SOLID_SOLUTION": "SOLID_SOLUTIONS",
    "REACTION_TEMPERATURE": "REACTION_TEMPERATURE", "REACTION_TEMPERATURES": "REACTION_TEMPERATURE",
    "REACTION_PRESSURE": "REACTION_PRESSURE", "REACTION_PRESSURES": "REACTION_PRESSURE",
}
#: a simulation carrying any of these runs a batch step (PHREEQC manual, "batch-reaction calculations"); a reactant's
#: _RAW definition is one too
BATCH_KEYWORDS = frozenset({"REACTION", "EQUILIBRIUM_PHASES", "MIX", "GAS_PHASE", "SURFACE", "EXCHANGE", "KINETICS",
                            "SOLID_SOLUTIONS", "REACTION_TEMPERATURE", "REACTION_PRESSURE", "USE"})
_RAW_REACTANTS = frozenset({f"{k}_RAW" for k in ("EQUILIBRIUM_PHASES", "EXCHANGE", "SURFACE", "KINETICS", "GAS_PHASE",
                                                   "SOLID_SOLUTIONS", "REACTION", "MIX", "REACTION_TEMPERATURE",
                                                   "REACTION_PRESSURE")})
_NUMBERS = re.compile(r"^(\d+)(?:-(\d+))?")


def _numbers(argument: str, default: int = 1) -> Tuple[int, ...]:
    """The solution numbers a keyword argument names: ``2``, ``2-4`` (both ends), none (PHREEQC's default 1)."""
    match = _NUMBERS.match(argument.strip())
    if not match:
        return (default,)
    low = int(match.group(1))
    return (low, int(match.group(2))) if match.group(2) else (low,)


def _simulations(input_string: str) -> List[List[Tuple[str, List[str]]]]:
    """The deck as its simulations (split at END), each a list of ``(canonical keyword, the tokens after it)``. A
    keyword is the first token of a line, indented or not, in any case (PHREEQC reads it so)."""
    simulations: List[List[Tuple[str, List[str]]]] = [[]]
    for raw in input_string.split("\n"):
        code = raw.split("#", 1)[0].strip()
        if not code:
            continue
        tokens = code.split()
        word = tokens[0].upper()
        keyword = _SYNONYMS.get(word) or (word if word in _RAW_REACTANTS else None)
        if keyword is None:
            continue
        if keyword == "END":
            simulations.append([])
            continue
        simulations[-1].append((keyword, tokens[1:]))
    return [s for s in simulations if s]


def unsaved_batch_reason(input_string: str) -> Optional[str]:
    """Why a deck's one-step answer would not be its last batch step's solution, or None when it would be (or when the
    deck runs no batch step)."""
    simulations = _simulations(input_string)
    batches = [i for i, sim in enumerate(simulations)
               if any(k in BATCH_KEYWORDS or k in _RAW_REACTANTS for k, _ in sim)]
    if not batches:
        return None
    if any(k == "SOLUTION_SPREAD" for sim in simulations for k, _ in sim):
        return (f"the deck runs a batch step and defines its solutions by SOLUTION_SPREAD, whose rows number them: "
                f"which stored solution the answer is read from cannot be checked, so the run is refused ({RULING_ID}, "
                f"C16B-1)")
    stored = []
    for sim in simulations:
        for keyword, args in sim:
            if keyword in ("SOLUTION", "SOLUTION_RAW"):
                stored.extend(_numbers(" ".join(args)))
            elif keyword == "SAVE" and args and args[0].lower().startswith("solution"):
                stored.extend(_numbers(" ".join(args[1:])))
            elif keyword == "COPY" and len(args) >= 3 and args[0].lower().startswith("solution"):
                stored.extend(_numbers(args[2]))
    last = simulations[batches[-1]]
    saved = [n for keyword, args in last if keyword == "SAVE" and args and args[0].lower().startswith("solution")
             for n in _numbers(" ".join(args[1:]))]
    if not saved:
        return (f"the deck's last batch step (simulation {batches[-1] + 1}) SAVEs no solution: phreeqpython stores "
                f"nothing for it, and the answer would be read from an input solution while the punched columns are "
                f"the batch's. Add `SAVE solution N` to that simulation, N at least the highest solution number the "
                f"deck stores ({RULING_ID}, C16B-1)")
    highest = max(stored)
    if max(saved) < highest:
        return (f"the deck's last batch step SAVEs solution {max(saved)}, but the deck stores solution {highest}: the "
                f"answer is read from the highest-numbered stored solution, an input, not the batch's. SAVE the batch "
                f"as solution {highest} or higher ({RULING_ID}, C16B-1)")
    return None


def refuse_unsaved_batch(input_string: str) -> None:
    """Raise `InputContractError` naming the missing SAVE (`unsaved_batch_reason`)."""
    reason = unsaved_batch_reason(input_string)
    if reason is not None:
        from water_chemistry_mcp.waterchem.errors import InputContractError

        raise InputContractError(f"Batch deck refused: {reason}")


__all__ = ["BATCH_KEYWORDS", "RULING_ID", "refuse_unsaved_batch", "unsaved_batch_reason"]
