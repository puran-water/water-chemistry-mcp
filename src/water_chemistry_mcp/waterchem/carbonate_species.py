"A carbonate SPECIES amount resolved to total inorganic carbon on the run's own database."

from __future__ import annotations

import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Tuple

from .analysis import INPUT_KEY_RULING_ID, SPECIES_MARKER, WATERCHEM_KEYS_RULING_ID

#: The marked line, as `waterchem.analysis` writes it: ``<element> <amount> as <formula> <marker><species>``.
_MARKED_LINE = re.compile(
    r"^(?P<indent>\s+)(?P<element>\S+)\s+(?P<value>[-+0-9.eE]+)\s+as\s+(?P<formula>\S+)\s+"
    + re.escape(SPECIES_MARKER)
    + r"(?P<species>\S+)\s*$"
)
#: Data blocks that DEFINE thermodynamics (kept ahead of a trial SOLUTION, which may name a species they define);
#: every other keyword block (reactions, phases to equilibrate, output) is left out of a trial.
_DEFINITION_BLOCKS = frozenset({
    "SOLUTION_MASTER_SPECIES", "SOLUTION_SPECIES", "PHASES", "SURFACE_MASTER_SPECIES", "SURFACE_SPECIES",
    "EXCHANGE_MASTER_SPECIES", "EXCHANGE_SPECIES", "LLNL_AQUEOUS_MODEL_PARAMETERS", "PITZER", "SIT",
    "NAMED_EXPRESSIONS", "CALCULATE_VALUES", "ISOTOPES", "ISOTOPE_RATIOS", "ISOTOPE_ALPHAS", "DATABASE",
    "INCLUDE$", "KNOBS",
})
MAX_ITERATIONS = 40
#: The total is taken when an iteration moves it by no more than this share, and the stated free-ion amount must be
#: met to `CLOSURE_TOLERANCE` at that total.
RELATIVE_TOLERANCE = 1e-10
CLOSURE_TOLERANCE = 1e-8
_HEADINGS = ("ws_species", "ws_total", "ws_lk_co3", "ws_lk_hco3", "ws_lk_co2", "ws_lk_h2co3")
_MISSING_LK = -999.0

Runner = Callable[[str, str], Awaitable[Any]]


#: PHREEQC's keywords. A fixed set, never "an unindented line": a data block writes its master-species rows, reactions
#: and phase names at column 0 (``Sulfat  SulfatO4-2 ...``, ``Fix_pe``).
_KEYWORDS = frozenset({
    *_DEFINITION_BLOCKS, "SOLUTION", "SOLUTION_SPREAD", "EQUILIBRIUM_PHASES", "EXCHANGE", "SURFACE", "GAS_PHASE",
    "REACTION", "REACTION_TEMPERATURE", "REACTION_PRESSURE", "KINETICS", "RATES", "MIX", "USE", "SAVE",
    "SELECTED_OUTPUT", "USER_PUNCH", "USER_PRINT", "USER_GRAPH", "PRINT", "TITLE", "END", "INVERSE_MODELING",
    "ADVECTION", "TRANSPORT", "COPY", "DELETE", "RUN_CELLS", "INCREMENTAL_REACTIONS", "SOLID_SOLUTIONS", "DUMP",
})


def _keyword(line: str) -> str | None:
    """The PHREEQC keyword a line opens (``SOLUTION 1`` -> SOLUTION), or None for any other line."""
    if not line or line[:1].isspace() or line.lstrip().startswith("#"):
        return None
    token = line.split()[0].upper()
    if token in _KEYWORDS or token.endswith("_MODIFY") or token.endswith("_RAW"):
        return token
    return None


def _blocks(lines: List[str]) -> List[Tuple[str, int, int]]:
    """(keyword, first line, end) for every keyword block of a PHREEQC input."""
    blocks: List[Tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        keyword = _keyword(line)
        if keyword is None:
            continue
        if blocks:
            name, start, _ = blocks[-1]
            blocks[-1] = (name, start, index)
        blocks.append((keyword, index, len(lines)))
    return blocks


def _setting(block: List[str], name: str) -> List[str] | None:
    """The tokens of a SOLUTION block's ``name`` line (pH, temp, units), or None."""
    for line in block[1:]:
        tokens = line.split("#", 1)[0].split()
        if tokens and tokens[0].lower().lstrip("-") == name:
            return tokens
    return None


def _punch(species: str) -> str:
    """The trial's punch: the FREE species alone (MOL of the species, no ion pair; `WATERCHEM_KEYS_RULING_ID`, B3)."""
    return "\n".join([
        "SELECTED_OUTPUT 1",
        "    -reset false",
        "    -high_precision true",
        "USER_PUNCH 1",
        "    -headings " + " ".join(_HEADINGS),
        f'    10 PUNCH MOL("{species}"), TOT("C(4)"), LK_SPECIES("CO3-2"), LK_SPECIES("HCO3-"), LK_SPECIES("CO2"), '
        'LK_SPECIES("H2CO3")',
    ])


def _selected(result: Any) -> Dict[str, float]:
    if isinstance(result, list):
        result = result[-1] if result else {}
    if not isinstance(result, dict) or result.get("error"):
        message = result.get("error") if isinstance(result, dict) else type(result).__name__
        raise ValueError(f"the carbonate-species trial run failed: {message}")
    selected = result.get("selected_output_data") or {}
    missing = [name for name in _HEADINGS[:2] if selected.get(name) is None]
    if missing:
        raise ValueError(f"the carbonate-species trial run punched no {missing}")
    return {name: float(selected[name]) for name in _HEADINGS if selected.get(name) is not None}


def _constants(selected: Dict[str, float]) -> Dict[str, float | None]:
    """log K1 (CO2 + H2O = HCO3- + H+) and log K2 (HCO3- = CO3-2 + H+) at the trial's temperature, from the database's
    own formation constants (LK_SPECIES, relative to whichever master species it writes C(4) as)."""
    lk_co3, lk_hco3 = selected.get("ws_lk_co3"), selected.get("ws_lk_hco3")
    neutral = next(
        (selected[name] for name in ("ws_lk_co2", "ws_lk_h2co3") if selected.get(name, _MISSING_LK) > _MISSING_LK),
        None,
    )
    log_k2 = lk_co3 - lk_hco3 if lk_co3 is not None and lk_hco3 is not None else None
    log_k1 = lk_hco3 - neutral if lk_hco3 is not None and neutral is not None else None
    return {"log_k1": log_k1, "log_k2": log_k2}


async def resolve_species_basis(input_string: str, database_path: str | None, run: Runner) -> Tuple[str, List[dict]]:
    """``input_string`` with every marked carbonate-species line replaced by its C(4) total, and one record per line.

    For each SOLUTION block carrying a marked line, a trial input (the definition blocks before it, the block with the
    line at a trial total, a punch of the free species and TOT("C(4)")) is run on ``database_path`` with ``run`` (the
    same engine the real run uses), and the total is iterated: T <- amount x TOT/MOL(species), until it moves by less
    than `RELATIVE_TOLERANCE`. The species amount and the total are written on the same formula and units, so the
    unit conversion cancels; what changes between iterations is the species' share, through ionic strength. Refuses a
    block with no pH, a pH balanced on charge, two marked lines, a share that does not close, or a run that fails."""
    lines = input_string.split("\n")
    records: List[dict] = []
    blocks = _blocks(lines)
    for keyword, start, end in blocks:
        if keyword != "SOLUTION":
            continue
        block = lines[start:end]
        marked = [(start + offset, _MARKED_LINE.match(line)) for offset, line in enumerate(block)]
        marked = [(index, match) for index, match in marked if match]
        if not marked:
            continue
        if len(marked) > 1:
            raise ValueError(f"{block[0].strip()} carries {len(marked)} carbonate species amounts: one C(4), one key")
        index, match = marked[0]
        ph = _setting(block, "ph")
        if ph is None or len(ph) < 2:
            raise ValueError(f"{block[0].strip()}: a carbonate species amount needs the input's pH ({INPUT_KEY_RULING_ID})")
        if any(token.lower() == "charge" for token in ph[2:]):
            raise ValueError(
                f"{block[0].strip()}: the pH is balanced on charge, so it is no input to convert a carbonate species "
                f"amount at ({INPUT_KEY_RULING_ID})"
            )
        temperature = _setting(block, "temp") or _setting(block, "temperature")
        units = _setting(block, "units")
        definitions = [
            line for name, b_start, b_end in blocks if b_end <= start and name in _DEFINITION_BLOCKS
            for line in lines[b_start:b_end]
        ]
        amount = float(match.group("value"))
        species, formula = match.group("species"), match.group("formula")
        if amount <= 0.0:
            raise ValueError(f"a {species} species amount must be positive (got {amount})")

        async def trial(total: float) -> Dict[str, float]:
            trial_block = list(block)
            trial_block[index - start] = f"{match.group('indent')}{match.group('element'):<14}{total!r} as {formula}"
            text = "\n".join([*definitions, *trial_block, _punch(species), "END", ""])
            return _selected(await run(text, database_path))

        total, iterations, selected = amount, 0, {}
        for iterations in range(1, MAX_ITERATIONS + 1):
            selected = await trial(total)
            if selected["ws_species"] <= 0.0:
                raise ValueError(f"the database holds no free {species} in this solution at its pH")
            new_total = amount * selected["ws_total"] / selected["ws_species"]
            moved = abs(new_total - total) / new_total
            total = new_total
            if moved <= RELATIVE_TOLERANCE:
                break
        else:
            raise ValueError(f"the {species} species amount did not settle to a C(4) total in {MAX_ITERATIONS} runs")
        selected = await trial(total)
        share = selected["ws_species"] / selected["ws_total"]
        if abs(total * share - amount) > CLOSURE_TOLERANCE * amount:
            raise ValueError(
                f"the {species} species amount does not close at the resolved C(4) total: {total * share!r} for {amount!r}"
            )
        lines[index] = f"{match.group('indent')}{match.group('element'):<14}{total!r} as {formula}"
        records.append({
            "solution": block[0].strip(),
            "species": species,
            "species_amount": amount,
            "units": units[1] if units and len(units) > 1 else None,
            "as": formula,
            "ph": float(ph[1]),
            "temperature_c": float(temperature[1]) if temperature and len(temperature) > 1 else None,
            "database": os.path.basename(database_path) if database_path else None,
            "c4_total": total,
            "species_share_of_c4": share,
            **_constants(selected),
            "iterations": iterations,
            "basis": (
                f"the free {species} ion only, ion pairs excluded, as this database speciates the solution at its pH "
                "and temperature (its own K1/K2 and activity model)"
            ),
            "ruling_id": INPUT_KEY_RULING_ID,
            "free_ion_ruling_id": WATERCHEM_KEYS_RULING_ID,
        })
    return "\n".join(lines), records
