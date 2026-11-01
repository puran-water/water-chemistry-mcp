"A mineral is taken from the PHASES the database file itself defines (PHREEQC matches a phase name without regard to\ncase, so the database's own spelling is written). A name the file does not define is answered only by a declared\nmember of the same phase (`SAME_PHASE`), each entry carrying the evidence that it is one phase, and the substitution is\nput on the answer (``phase_substitutions``). Anything else refuses, naming the mineral, the database, and the\nrepository databases that define it. Before this ruling three tables substituted silently: Strengite (an Fe(III)\nphosphate) by Vivianite (an Fe(II) phosphate) on phreeqc.dat; amorphous Al(OH)3(a) by crystalline Gibbsite on\nminteq.v4.dat; brushite by monetite; any phase by another of the same formula; and a phase found nowhere was dropped\nfrom the run with a log line.\n\nThe table lives in `engineering_utils.phreeqc_phases` (one table for this server and engineering_utils' callers). It is\nchecked against the shipped databases by the tests: a database that defines two members of one entry\ndefines them with one reaction and one log K, and every pair in `TWO_PHASES` is defined by some shipped database\nunder both names with different data, which is what proves it two phases."
from __future__ import annotations
import contextlib
import os
from contextvars import ContextVar
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from puran_mcp_support.engineering import phreeqc_phases as _phases
from .errors import InputContractError
from .input_key_rulings import FALLBACKS_D1_5_RULING_ID
PHASE_CHOICE_RULING_ID = FALLBACKS_D1_5_RULING_ID
_REPOSITORY_DATABASES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'databases', 'official')
SamePhase = _phases.SamePhase
SAME_PHASE = _phases.SAME_PHASE
TWO_PHASES = _phases.TWO_PHASES
same_phase_as = _phases.same_phase_as
_DEFINED: Dict[Tuple[str, float, int], Dict[str, str]] = {}
_SUBSTITUTIONS: ContextVar[Optional[List[Dict[str, str]]]] = ContextVar('waterchem_phase_substitutions', default=None)

class MissingPhaseError(InputContractError):
    """A mineral the database does not define, with no member of the same phase under another name there."""

def defined_phases(database_path: str) -> Dict[str, str]:
    """The phases a database file defines, keyed by lower-case name (PHREEQC's match), valued by the file's spelling."""
    from water_chemistry_mcp.utils.phreeqc_database_introspection import parse_phase_names
    stat = os.stat(database_path)
    key = (os.path.realpath(database_path), stat.st_mtime, stat.st_size)
    if key not in _DEFINED:
        with open(database_path, 'r', errors='ignore') as handle:
            _DEFINED[key] = {name.lower(): name for name in parse_phase_names(handle.read())}
    return _DEFINED[key]

def repository_databases_defining(name: str) -> List[str]:
    """The repository databases that define the phase under this name or a same-phase name."""
    entry = same_phase_as(name)
    names = {member.lower() for member in entry.names} if entry else {name.lower()}
    found = []
    for filename in sorted(os.listdir(_REPOSITORY_DATABASES)):
        if filename.endswith('.dat') and names & set(defined_phases(os.path.join(_REPOSITORY_DATABASES, filename))):
            found.append(filename)
    return found

def missing_phase_message(name: str, database_path: str) -> str:
    """The refusal for one mineral: what it is not, where it is defined, and the ruling."""
    database = os.path.basename(database_path)
    elsewhere = repository_databases_defining(name)
    where = f'the repository databases that define it: {', '.join(elsewhere)}; name one of them, or leave the mineral out' if elsewhere else 'no repository database defines it'
    return f'{name} is not a phase of {database}, and no phase there is the same phase under another name, so it is refused rather than replaced or dropped (D3 of {PHASE_CHOICE_RULING_ID}); {where}.'

def resolve_phases(database_path: str, requested: Iterable[str], *, provided: Iterable[str]=(), purpose: str='equilibrium phases') -> List[str]:
    """The phases to write for the minerals requested, in the database's own spelling, in order, without duplicates.

    A name the database defines is itself; a name it does not define is answered by a same-phase name it does
    (recorded on the answer); a name in ``provided`` (defined by the run's own inline PHASES) is itself. Every other
    name refuses (`MissingPhaseError`), all of them in one message.
    """
    phases = defined_phases(database_path)
    supplied = {name.lower(): name for name in provided}
    resolved: List[str] = []
    missing: List[str] = []
    for name in requested:
        lowered = name.lower()
        if lowered in supplied and lowered not in phases:
            chosen = supplied[lowered]
        else:
            chosen, entry = _phases.resolve_phase(name, phases)
            if chosen is None:
                missing.append(name)
                continue
            if entry is not None:
                record_substitution(name, chosen, database_path, entry)
        if chosen not in resolved:
            resolved.append(chosen)
    if missing:
        raise MissingPhaseError(' '.join((missing_phase_message(name, database_path) for name in missing)), call_stage='resolve_phases', details={'missing_phases': missing, 'database': os.path.basename(database_path), 'purpose': purpose, 'ruling': PHASE_CHOICE_RULING_ID})
    return resolved

def same_phase_alternatives(name: str, database_path: str) -> Dict[str, str]:
    """For a 'phase not found' error: the same-phase names the database defines (never another phase)."""
    entry = same_phase_as(name)
    if entry is None:
        return {}
    phases = defined_phases(database_path)
    return {phases[m.lower()]: entry.phase for m in entry.names if m.lower() != name.lower() and m.lower() in phases}

def record_substitution(requested: str, used: str, database_path: str, entry: SamePhase) -> None:
    substitutions = _SUBSTITUTIONS.get()
    if substitutions is not None:
        item = {'requested': requested, 'used': used, 'database': os.path.basename(database_path), 'phase': entry.phase, 'evidence': entry.evidence, 'ruling': PHASE_CHOICE_RULING_ID}
        if item not in substitutions:
            substitutions.append(item)

@contextlib.contextmanager
def collecting_phase_substitutions() -> Iterator[List[Dict[str, str]]]:
    """Collect the substitutions made inside the block; a nested collection also adds them to its caller's."""
    outer = _SUBSTITUTIONS.get()
    substitutions: List[Dict[str, str]] = []
    token = _SUBSTITUTIONS.set(substitutions)
    try:
        yield substitutions
    finally:
        _SUBSTITUTIONS.reset(token)
        if outer is not None:
            outer.extend((item for item in substitutions if item not in outer))

def inline_defined_phases(inline_text: Optional[str]) -> List[str]:
    """The phases a run's own inline PHASES blocks define."""
    if not inline_text:
        return []
    from water_chemistry_mcp.utils.phreeqc_database_introspection import parse_phase_names
    return list(parse_phase_names(inline_text))
__all__: Sequence[str] = ('MissingPhaseError', 'PHASE_CHOICE_RULING_ID', 'SAME_PHASE', 'SamePhase', 'TWO_PHASES', 'collecting_phase_substitutions', 'defined_phases', 'inline_defined_phases', 'missing_phase_message', 'repository_databases_defining', 'resolve_phases', 'same_phase_alternatives', 'same_phase_as')
