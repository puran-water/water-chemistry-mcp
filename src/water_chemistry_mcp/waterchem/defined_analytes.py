"A SOLUTION analyte the run's database does not define is refused before the run, never dropped.\n\nA name is defined by the database's SOLUTION_MASTER_SPECIES or by the input's own (the phosphorus screen defines its\ndecoupled redox states inline). A database writes a redox state as ``C(4)`` or ``C(+4)``: one name."

from __future__ import annotations

import functools
import os
import re
from typing import FrozenSet, List, Optional

from .carbonate_species import _blocks
from .errors import InputContractError
from .input_key_rulings import DATABASE_CHOICE_RULING_ID

#: The SOLUTION identifiers that are settings, not analytes (an option line starts with '-').
_SETTINGS = frozenset({
    "temp", "temperature", "ph", "pe", "redox", "units", "unit", "density", "dens", "pressure", "press", "water",
    "isotope", "isotope_uncertainty", "uncertainty", "uncertainties", "title",
})
_SIGNED_VALENCE = re.compile(r"\(\+(\d+)\)$")


def normalized_name(name: str) -> str:
    """One spelling for a redox state: ``C(+4)`` (sit.dat, minteq.dat, wateq4f.dat, phreeqc.dat's Fe(+2)) is ``C(4)``."""
    return _SIGNED_VALENCE.sub(r"(\1)", name.strip())


def _master_species_rows(lines: List[str]) -> FrozenSet[str]:
    names = set()
    for raw in lines:
        tokens = raw.split("#", 1)[0].split()
        if tokens and not tokens[0].startswith("-"):
            names.add(normalized_name(tokens[0]))
    return frozenset(names)


def _master_names_in(text: str) -> FrozenSet[str]:
    """Every name a SOLUTION_MASTER_SPECIES block of ``text`` (a database or an input) defines."""
    lines = text.split("\n")
    names: set = set()
    for keyword, start, end in _blocks(lines):
        if keyword == "SOLUTION_MASTER_SPECIES":
            names |= _master_species_rows(lines[start + 1:end])
    return frozenset(names)


@functools.lru_cache(maxsize=32)
def _database_names(path: str, mtime_ns: int, size: int) -> FrozenSet[str]:
    with open(path, "r", errors="ignore") as handle:
        return _master_names_in(handle.read())


def database_names(database_path: str) -> FrozenSet[str]:
    """The names ``database_path`` defines (cached on the file's path, mtime and size)."""
    stat = os.stat(database_path)
    return _database_names(os.path.abspath(database_path), stat.st_mtime_ns, stat.st_size)


def solution_analyte_names(input_string: str) -> List[str]:
    """The analyte names the SOLUTION blocks of a PHREEQC input give (settings and option lines left out)."""
    lines = input_string.split("\n")
    names: List[str] = []
    for keyword, start, end in _blocks(lines):
        if keyword != "SOLUTION":
            continue
        for raw in lines[start + 1:end]:
            tokens = raw.split("#", 1)[0].split()
            if len(tokens) < 2 or tokens[0].startswith("-") or not tokens[0][:1].isalpha():
                continue
            if tokens[0].lower() in _SETTINGS or tokens[0] in names:
                continue
            names.append(tokens[0])
    return names


def undefined_solution_analytes(input_string: str, database_path: str) -> List[str]:
    """The SOLUTION analyte names neither ``database_path`` nor the input's own SOLUTION_MASTER_SPECIES defines."""
    defined = database_names(database_path) | _master_names_in(input_string)
    return [name for name in solution_analyte_names(input_string) if normalized_name(name) not in defined]


def refuse_undefined_solution_analytes(input_string: str, database_path: Optional[str]) -> None:
    'Public engineering calculation interface.'
    if not database_path or database_path == "INLINE" or not os.path.isfile(database_path):
        raise InputContractError(
            f'the SOLUTION analytes cannot be checked: no database file to read ({database_path!r}); an INLINE run is checked on the file its DATABASE line names (review M19, PUBLIC-RULE-8226e460e1d8)',
            call_stage="validate_solution_analytes",
            details={"database": str(database_path)},
        )
    undefined = undefined_solution_analytes(input_string, database_path)
    if not undefined:
        return
    database = os.path.basename(database_path)
    # the house's default redox states (Fe -> Fe(2), Mn -> Mn(2)) stay as written on every database (C4)
    from .analysis import PHREEQC_DEFAULT_SPECIES

    house_states = sorted(set(undefined) & set(PHREEQC_DEFAULT_SPECIES.values()))
    c4 = (
        f" The writer is unchanged (C4 of {DATABASE_CHOICE_RULING_ID}): {', '.join(map(repr, house_states))} is "
        "not rewritten to its element; name the database that defines it."
        if house_states else ""
    )
    raise InputContractError(
        f"{database} does not define {', '.join(repr(name) for name in undefined)}: PHREEQC would take "
        f"{'it' if len(undefined) == 1 else 'them'} as 0 without an error, so the run is refused by name (C3 of "
        f"{DATABASE_CHOICE_RULING_ID}). Run on a database that defines "
        f"{'it' if len(undefined) == 1 else 'them'} (a redox state needs its own SOLUTION_MASTER_SPECIES row; the "
        "house writes Fe as Fe(2), Mn as Mn(2), N as N(5), S as S(6)), or leave the analyte out knowingly." + c4,
        call_stage="validate_solution_analytes",
        details={"database": database, "undefined": undefined},
    )
