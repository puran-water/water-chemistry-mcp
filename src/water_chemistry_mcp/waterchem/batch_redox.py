"A PHREEQC batch step (REACTION, EQUILIBRIUM_PHASES, GAS_PHASE, SURFACE, EXCHANGE, KINETICS, MIX, REACTION_TEMPERATURE)\nre-equilibrates every redox couple at one pe, where an initial SOLUTION keeps each valence state it was given.\nMeasured by lane c03 (closure/c03-pscreen-donor/runs/probe_general_batch_redox.txt): 2 mmol/L NaOH on a digestate water\nreduced all its sulfate to sulfide on phreeqc.dat (the pH FELL from 7.5 to 6.79 on adding a base, and 4.5 mmol of its\nDIC became methane) and oxidised all its sulfide on minteq.v4.dat. No biology acts in an equilibrium step.\n\nSo every PHREEQC run (`tools.phreeqc.simulation.run_phreeqc_simulation`, the one function every tool reaches) that is a\nbatch step and carries N or S is held (`hold_batch_redox`): the five states go in on redox-uncoupled elements derived\nfrom the database's own definitions (`engineering_utils.phreeqc_batch_redox`, as the walk and the phosphorus screen hold\ntheirs), every phase, surface species and reagent that names one is carried onto them, the SELECTED_OUTPUT and BASIC\nread them too, and the answer is mapped back to the coupled names (`HeldBatch.fold`). Each run is checked: a coupled\nstate no feed carried (N(-3), N(0), N(3), N(5), S(-2), S(6), and methane, C(-4)) must still be absent after the step,\nelse the run refuses by name (`check_batch_redox`); the held elements carry one valence each, so they cannot move.\n\n``equilibrate_redox: true`` on a tool's request is the named opt-in (`holds_batch_redox`): the batch then\nre-equilibrates every couple, as before, and the answer says so (``batch_redox.policy``). An input that already holds\nits own states (the phosphorus screen's per-mode holding) is left as it is."

from __future__ import annotations

import contextlib
import functools
import math
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .errors import InputContractError, PhreeqcExecutionError, public_error

BATCH_REDOX_HOLD_RULING_ID = 'PUBLIC-RULE-d3e7617e1bb5'
EQUILIBRATE_REDOX_KEY = "equilibrate_redox"
#: the five states a batch holds, in `engineering_utils.phreeqc_batch_redox.DECOUPLABLE_STATES` spelling
HELD_STATES = ("N(-3)", "N(+5)", "N(+3)", "S(-2)", "S(+6)")
POLICY_HELD = "held"
#: what every batch tool's description says (its MCP tool description is its docstring)
HOLD_NOTE = (
    "Batch redox (PUBLIC-RULE-d3e7617e1bb5): a batch step holds ammonium, nitrate, nitrite, sulfide and sulfate as fed (no biology acts in an equilibrium step); a reagent's N or S is added on the state its groups name (SO4, NO3, NO2, NH4, NH3, a lone sulfide S), any other is refused; a run that moves a coupled N or S state, or makes methane, refuses. Pass equilibrate_redox: true in the request to re-equilibrate every redox couple instead (stamped on the answer as batch_redox.policy).")
POLICY_EQUILIBRATED = "re-equilibrated by request (equilibrate_redox: true)"
#: the input blocks that make a run a batch step (as `waterchem.chemical_model` reads them)
_BATCH_KEYWORDS = frozenset({"REACTION", "EQUILIBRIUM_PHASES", "GAS_PHASE", "SURFACE", "EXCHANGE", "KINETICS", "MIX",
                             "REACTION_TEMPERATURE", "REACTION_PRESSURE", "SOLID_SOLUTIONS"})
#: data blocks a PHREEQC input opens at column 0 (a line of one of these words starts a block)
_KEYWORDS = frozenset({
    "SOLUTION", "SOLUTION_SPREAD", "SOLUTION_MODIFY", "SOLUTION_MASTER_SPECIES", "SOLUTION_SPECIES", "PHASES",
    "SURFACE_MASTER_SPECIES", "SURFACE_SPECIES", "EXCHANGE_MASTER_SPECIES", "EXCHANGE_SPECIES", "RATES", "END",
    "SELECTED_OUTPUT", "USER_PUNCH", "USER_PRINT", "KNOBS", "TITLE", "DATABASE", "EQUILIBRIUM_PHASES", "REACTION",
    "SURFACE", "EXCHANGE", "GAS_PHASE", "KINETICS", "SAVE", "USE", "PRINT", "MIX", "REACTION_TEMPERATURE",
    "REACTION_PRESSURE", "INCREMENTAL_REACTIONS", "SOLID_SOLUTIONS", "INVERSE_MODELING", "TRANSPORT", "ADVECTION",
    "COPY", "DELETE", "RUN_CELLS", "DUMP", "CALCULATE_VALUES", "NAMED_EXPRESSIONS", "ISOTOPES", "PITZER", "SIT",
    "EQUILIBRIUM_PHASES_MODIFY", "EXCHANGE_MODIFY", "SURFACE_MODIFY", "KINETICS_MODIFY", "GAS_PHASE_MODIFY",
    "SOLID_SOLUTIONS_MODIFY", "REACTION_MODIFY", "LLNL_AQUEOUS_MODEL_PARAMETERS", "GAS_BINARY_PARAMETERS"})
#: BASIC functions whose quoted argument is an element or valence state (summed over the held elements) or a species
_BASIC_TOTALS = ("TOTMOLES", "TOTMOLE", "TOTMOL", "TOT", "SYS")
_BASIC_ADDITIVE = ("MOL", "ACT")
_BASIC_SAME = ("LA", "LM", "GAMMA", "LG")
_SOLUTION_OPTIONS = {"units", "unit", "temp", "temperature", "ph", "pe", "redox", "density", "water", "pressure",
                     "isotope", "-units", "-unit", "-temp", "-temperature", "-ph", "-pe", "-redox", "-density",
                     "-water", "-pressure", "-isotope"}
_COUPLED_CHECKED = ("N(-3)", "N(0)", "N(3)", "N(5)", "S(-2)", "S(6)", "C(4)", "C(-4)")
#: coupled states a gas or phase brings into a step by mass transfer (not a redox move): N2 is N(0), CH4 is C(-4)
_PHASE_STATES = {"N2": "N(0)", "CH4": "C(-4)"}
_DECOUPLED_NAME = re.compile(r"(?<![A-Za-z])(Amm|Nitr?|Sulf(?:at)?)(?![a-z])")


WATER_REDOX_RULING_ID = 'PUBLIC-RULE-d6cee394f32b'

#: `engineering_utils.phreeqc_batch_redox.WATER_REDOX_STRICT_RULING_ID`): no metals credit, the 1e-7 mol/kgw floor, an
#: oxidant or reductant dose refusing held. Stamped on every answer beside the C15-2 id.
WATER_REDOX_STRICT_RULING_ID = 'PUBLIC-RULE-2938127a7d60'
WATER_REDOX_STATES = ("O(0)", "H(0)")
#: the formulas of a phase or reagent that sets dissolved O2 / H2 (a gas phase's, a reagent's); an electron sets the pe
_WATER_REDOX_FORMULAS = frozenset({"O2", "H2", "O2(g)", "H2(g)"})
#: SELECTED_OUTPUT options the check reads its rows by, and PHREEQC's default for each when -reset is not false
_ROW_OPTIONS = {"state": ("-state",), "soln": ("-solution", "-soln"), "water": ("-water",)}
_ROW_DEFAULTS = {"state": True, "soln": True, "water": False}

_POLICY: ContextVar[Optional[bool]] = ContextVar("waterchem_equilibrate_redox", default=None)
_RECORDS: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("waterchem_batch_redox_runs", default=None)


class BatchRedoxMovedError(PhreeqcExecutionError):
    """A held batch step moved a redox state no feed carried (`check_batch_redox`)."""


def _refuse(message: str) -> InputContractError:
    return InputContractError(
        f"{message}. A batch step holds ammonium, nitrate, nitrite, sulfide and sulfate as fed "
        f"({BATCH_REDOX_HOLD_RULING_ID}, OQ-C03-1); to re-equilibrate every redox couple instead, set "
        f"{EQUILIBRATE_REDOX_KEY}: true on the request (stamped on the answer)",
        call_stage="hold_batch_redox")


def _valence_key(name: str) -> str:
    return re.sub(r"\(\+", "(", name)


def equilibrate_requested() -> bool:
    """True when the enclosing tool call asked for ``equilibrate_redox: true``."""
    return bool(_POLICY.get())


def record_held_run(record: Dict[str, Any]) -> None:
    runs = _RECORDS.get()
    if runs is not None:
        runs.append(record)


@contextlib.contextmanager
def _collecting() -> Iterator[List[Dict[str, Any]]]:
    outer = _RECORDS.get()
    runs: List[Dict[str, Any]] = []
    token = _RECORDS.set(runs)
    try:
        yield runs
    finally:
        _RECORDS.reset(token)
        if outer is not None:
            outer.extend(runs)


def _stamp(runs: List[Dict[str, Any]], equilibrate: bool) -> Dict[str, Any]:
    held = [r for r in runs if r.get("policy") == POLICY_HELD]
    asked = [r for r in runs if r.get("policy") == POLICY_EQUILIBRATED]
    policy = POLICY_EQUILIBRATED if equilibrate and not held else POLICY_HELD if not asked else "mixed"
    states = sorted({s for r in held for s in r.get("held_states", ())})
    return {
        "policy": policy,
        "ruling_id": BATCH_REDOX_HOLD_RULING_ID,
        "held_states": states,
        "runs_held": len(held),
        "runs_checked": sum(1 for r in held if r.get("checked")),
        "runs_re_equilibrated": len(asked),
        "unchecked_fed_states": sorted({s for r in held for s in r.get("unchecked_fed_states", ())}),
        "checked_states": sorted({s for r in held for s in r.get("checked_states", ())}),
        "water_redox": {
            "ruling_id": WATER_REDOX_RULING_ID,
            "strict_ruling_id": WATER_REDOX_STRICT_RULING_ID,
            "states": list(WATER_REDOX_STATES),
            "runs_checked": sum(1 for r in held if r.get("water_redox") == "checked"),
            "exempt": sorted({r["water_redox"] for r in held if str(r.get("water_redox", "")).startswith("exempt")}),
            "basis": ("each held step's dissolved O2 and H2 (O(0), H(0)), in moles, against its feed's (the solution or "
                      "mix it uses), within the shared relative tolerance of the held inventory and the feed's own O(0) "
                      "and H(0) (C15-2); exempt where a gas phase, a Fix_pe or an O2(g)/H2(g) phase or reagent sets "
                      "them; ruled strict (WR-1/2/4 of the strict ruling): a feed's own ferrous iron taking O2 and an "
                      "oxidant or reductant dose refuse, on a floor of 1e-7 mol per kg of water"),
        },
        "basis": ("every batch step's ammonium, nitrate, nitrite, sulfide and sulfate carried on redox-uncoupled "
                  "elements (engineering_utils.phreeqc_batch_redox) and mapped back to the coupled names; each run "
                  "checked that no coupled N or S state, nor methane, appeared that no feed carried. The held states "
                  "poise no pe: a held step's pe is set by what redox is left (dissolved O2 and H2, iron, manganese, "
                  "carbon) and is not a measured redox potential"
                  if policy != POLICY_EQUILIBRATED else
                  "every redox couple, ammonium, nitrate, sulfide and sulfate included, equilibrated at one pe in "
                  "each batch step, as asked: an N or S valence it moved is a thermodynamic endpoint, not a process "
                  "result"),
    }


def holds_batch_redox(fn):
    """Wrap a batch tool: its request may carry ``equilibrate_redox`` (a boolean, default false: hold), read here and
    taken out of the request before the tool's own contract sees it; a nested tool call inherits its caller's choice.
    The outermost call's answer carries ``batch_redox`` (the policy, the states held and the runs checked)."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        data = args[0] if args else kwargs.get("input_data")
        asked = None
        if isinstance(data, dict) and EQUILIBRATE_REDOX_KEY in data:
            asked = data[EQUILIBRATE_REDOX_KEY]
            if not isinstance(asked, bool):
                return public_error(
                    "INVALID_INPUT",
                    f"Input validation error: {EQUILIBRATE_REDOX_KEY} is true or false (the named opt-in to "
                    f"re-equilibrate every redox couple in a batch step; {BATCH_REDOX_HOLD_RULING_ID}), not "
                    f"{asked!r}", {"tool_name": fn.__name__, "stage": "validate_input"})
            data = {k: v for k, v in data.items() if k != EQUILIBRATE_REDOX_KEY}
            args = (data, *args[1:]) if args else args
            if not args:
                kwargs = {**kwargs, "input_data": data}
        outermost = _RECORDS.get() is None
        token = _POLICY.set(asked) if asked is not None else None
        try:
            with _collecting() as runs:
                result = await fn(*args, **kwargs)
        finally:
            if token is not None:
                _POLICY.reset(token)
        if outermost and isinstance(result, dict) and not (result.get("code") and result.get("message")):
            result = {**result, "batch_redox": _stamp(runs, equilibrate_requested() if asked is None else asked)}
        return result

    wrapper.__doc__ = (fn.__doc__ or "").rstrip() + "\n\n" + HOLD_NOTE
    wrapper.__holds_batch_redox__ = True
    return wrapper


# --------------------------------------------------------------------------------------------------------------------
# The database's decoupling, read once per file


@dataclass(frozen=True)
class _Basis:
    path: str
    text: str
    states: Tuple[str, ...]
    definitions: str
    forward: Dict[str, str]           # coupled species -> its held name
    reverse: Dict[str, str]           # held species -> its coupled name
    element_of: Dict[str, str]        # held element -> its coupled valence state, as the database spells it
    holdable_phases: Tuple[str, ...]
    coupled_phases: frozenset
    defined_states: Tuple[str, ...]   # the coupled N, S and C states the database defines, as it spells them
    rates: Dict[str, str]             # RATES name -> its BASIC text, as the database writes it
    phase_formulas: Dict[str, str]    # every database phase -> its formula
    phase_block: str                  # the database's phases that name a held state, re-defined on the held elements
    water_states: Tuple[str, ...] = ()  # O(0), H(0) as the database spells them (C15-2); both or the check cannot run


@functools.lru_cache(maxsize=16)
def _basis_cached(path: str, mtime: float, size: int) -> Optional[_Basis]:
    from puran_mcp_support.engineering import phreeqc_batch_redox as pbr

    text = _database_text_cached(path, mtime, size)
    spelling = pbr.defined_valence_states(text)
    by_element: Dict[str, List[str]] = {}
    for key in spelling:
        by_element.setdefault(key.split("(", 1)[0], []).append(key)
    # a state is held where the database defines it beside another valence of its element: a single valence cannot move
    states = tuple(s for s in HELD_STATES if _valence_key(s) in spelling and len(by_element.get(s[0], ())) > 1)
    if not states:
        return None
    definitions = pbr.decoupled_redox_definitions(text, states)  # refuses (ValueError) a database it cannot read
    forward = pbr.decoupled_species_names(text, states)
    for line in definitions.splitlines():
        if "=" in line and not line.startswith(" "):
            defined = line.split("=", 1)[1].split()[0]
            if defined not in forward.values():
                raise ValueError(f"the held definitions of {os.path.basename(path)} define {defined}, which no coupled "
                                 "species maps to: the answer could not be mapped back")
    element_of = {pbr.DECOUPLABLE_STATES[state][0]: spelling.get(_valence_key(state), state) for state in states}
    holdable, coupled = pbr.phases_naming_held_states(text, [text], states)
    phase_block, _formulas = pbr.decoupled_phase_definitions(text, [text], holdable, states)
    defined = tuple(spelling.get(s, s) for s in _COUPLED_CHECKED if s in spelling)
    return _Basis(path=path, text=text, states=states, definitions=definitions, forward=forward,
                  reverse={v: k for k, v in forward.items()}, element_of=element_of, holdable_phases=tuple(holdable),
                  coupled_phases=frozenset(coupled), defined_states=defined, rates=_rates(text),
                  phase_formulas=pbr.phase_formulas([text]), phase_block=phase_block,
                  water_states=tuple(spelling[state] for state in WATER_REDOX_STATES if state in spelling))


def _basis(path: str) -> Optional[_Basis]:
    stat = os.stat(path)
    return _basis_cached(os.path.realpath(path), stat.st_mtime, stat.st_size)


def _rates(text: str) -> Dict[str, str]:
    """name -> BASIC text of every rate of every RATES block of ``text``."""
    rates: Dict[str, str] = {}
    for block in _blocks(text):
        if block.keyword != "RATES":
            continue
        name = None
        for line in block.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line[:1].isspace() and not stripped.startswith("-"):
                name = stripped.split()[0]
                rates[name] = ""
            elif name is not None:
                rates[name] += line + "\n"
    return rates


# --------------------------------------------------------------------------------------------------------------------
# The input, as blocks


@dataclass
class _Block:
    keyword: str
    head: str
    lines: List[str] = field(default_factory=list)


def _blocks(text: str) -> List[_Block]:
    """The input as its blocks, in order (anything before the first keyword is a block with keyword ``""``)."""
    blocks = [_Block("", "")]
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        code = line.split("#", 1)[0]
        token = code.split()[0].upper() if code.strip() and not code[:1].isspace() else None
        if token is not None and (token in _KEYWORDS or token.endswith("_RAW")):
            blocks.append(_Block(token, line))
            continue
        blocks[-1].lines.append(line)
    return blocks


def _join(blocks: List[_Block]) -> str:
    out: List[str] = []
    for block in blocks:
        if block.keyword:
            out.append(block.head)
        out.extend(block.lines)
    return "\n".join(out)


def _code(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


# --------------------------------------------------------------------------------------------------------------------
# The held run


@dataclass
class HeldBatch:
    """One run's holding: the input PHREEQC runs, and what maps its answer back and checks it."""

    input_string: str
    basis: _Basis
    requested_totals: List[str]
    added_totals: List[str]
    fed_coupled: List[str]
    reactions_as_requested: List[str]
    kinetic_rates_on_held_states: Dict[str, List[str]]
    #: C15-2: why the step's dissolved O2/H2 are not checked (a gas phase, a Fix_pe ... sets them), else None
    water_exempt: Optional[str] = None
    #: C15-2: the step's feed, {"solution": n} or {"mix": {n: fraction}}
    water_feed: Optional[Dict[str, Any]] = None
    #: the SELECTED_OUTPUT row options the check switched on that the input had off (taken off the answer)
    added_row_options: Tuple[str, ...] = ()

    @property
    def states(self) -> Tuple[str, ...]:
        return self.basis.states

    # the coupled names each held element folds into
    def _composes(self, name: str) -> List[str]:
        """The held elements whose amount belongs to a coupled total ``name`` (N, N(-3), S, S(6) ...)."""
        key = _valence_key(name)
        out = []
        for element, valence in self.basis.element_of.items():
            if key == valence.split("(", 1)[0] or key == _valence_key(valence):
                out.append(element)
        return out

    def check_and_fold(self, result: Any, *, engine: str, step: str = "batch step", rows: Any = None) -> Any:
        """Check the run's answer (`check_batch_redox`; and its dissolved O2 and H2 against its feed's on the run's
        SELECTED_OUTPUT ``rows``, `check_water_redox`) and map it back to the coupled names."""
        records: List[Dict[str, Any]] = []
        folded = self._check_and_fold(result, engine=engine, step=step, records=records)
        # after the N and S check, so a move it names keeps its own refusal
        water = self._check_water(rows, step) if self.water_exempt is None else self.water_exempt
        for record in records:
            if water == "checked":
                record["checked_states"] = sorted(set(record["checked_states"]) | set(WATER_REDOX_STATES))
            record["water_redox"] = water
            record_held_run(record)
        return folded

    def _check_and_fold(self, result: Any, *, engine: str, step: str, records: List[Dict[str, Any]]) -> Any:
        if isinstance(result, list):
            return [self._check_and_fold(item, engine=engine, step=f"{step} {index + 1}", records=records)
                    for index, item in enumerate(result)]
        if not isinstance(result, dict) or result.get("error"):
            return result
        observed = self._observed(result, engine)
        checked = self._check(observed, step)
        self._fold(result, observed, engine)
        records.append({"policy": POLICY_HELD, "held_states": list(self.states), "checked": True,
                        "checked_states": checked, "unchecked_fed_states": list(self.fed_coupled)})
        return result

    def _check_water(self, rows: Any, step: str) -> str:
        """C15-2: every batch row's O(0) and H(0), in moles (molality x the row's water), against the feed's (the
        initial-solution row of the solution it uses, or the MIX of them), within `water_redox_tolerance_mol` of the
        held inventory and the feed's own O(0) and H(0). Rows the check needs and the run did not write refuse: never
        read as zero."""
        from puran_mcp_support.engineering.phreeqc_batch_redox import check_water_redox

        def omitted(what: str) -> BatchRedoxMovedError:
            return BatchRedoxMovedError(
                f"{step}: the held run's SELECTED_OUTPUT rows omitted {what}: its dissolved O2 and H2 cannot be checked "
                f"against its feed ({WATER_REDOX_RULING_ID}, C15-2)", call_stage="check_batch_redox")

        if not rows or len(rows) < 2:
            raise omitted("every row")
        header = [_valence_key(_column(h)) for h in rows[0]]
        index = {name: i for i, name in enumerate(header)}
        held = [_valence_key(e) for e in self.basis.element_of]
        missing = [n for n in ("state", "soln", "mass_H2O", *WATER_REDOX_STATES, *held) if n not in index]
        if missing:
            raise omitted(f"the column(s) {missing}")

        def moles(row) -> Dict[str, float]:
            water = float(row[index["mass_H2O"]])
            out = {state: float(row[index[state]]) * water for state in WATER_REDOX_STATES}
            out["held"] = math.fsum(float(row[index[e]]) * water for e in held)
            out["water_kg"] = water
            return out

        initial: Dict[int, Dict[str, float]] = {}
        batch = []
        for row in rows[1:]:
            state = str(row[index["state"]]).strip().lower()
            if state == "i_soln":
                initial[int(float(row[index["soln"]]))] = moles(row)
            elif state == "react":
                batch.append(moles(row))
        feed = self.water_feed or {}
        parts = ({int(n): float(f) for n, f in feed["mix"].items()} if "mix" in feed
                 else {int(feed["solution"]): 1.0} if "solution" in feed else {})
        if not parts or any(n not in initial for n in parts):
            raise omitted(f"the initial solution(s) {sorted(parts) or 'of its feed'}")
        if not batch:
            raise omitted("its batch step's row")
        before = {key: math.fsum(fraction * initial[n][key] for n, fraction in parts.items())
                  for key in (*WATER_REDOX_STATES, "held", "water_kg")}
        for number, after in enumerate(batch, start=1):
            try:
                check_water_redox(before, after, step=f"{step} {number}" if len(batch) > 1 else step,
                                  held_mol=before["held"], water_kg=before["water_kg"])
            except ValueError as exc:
                raise BatchRedoxMovedError(
                    f"{exc}. The held states cannot take a reagent's electrons ({BATCH_REDOX_HOLD_RULING_ID}): set "
                    f"{EQUILIBRATE_REDOX_KEY}: true to re-equilibrate every couple", call_stage="check_batch_redox") from exc
        return "checked"

    def _observed(self, result: Dict[str, Any], engine: str) -> Dict[str, float]:
        """Molality of every held element and coupled state in the run's answer, keyed as `_valence_key` spells them:
        the punched columns (the standalone engine, and either engine's step rows), else phreeqpython's element totals
        (zero states omitted, as VIPhreeqc reports them). A punched answer missing one refuses: never read as zero."""
        wanted = [_valence_key(n) for n in list(self.basis.element_of) + list(self.basis.defined_states)]
        punched: Dict[str, float] = {}
        for source in (result.get("selected_output_data") or {}, result.get("_selected_output_row") or {}):
            for key, value in source.items():
                if isinstance(value, (int, float)):
                    punched[_valence_key(_column(key))] = float(value)
        if all(name in punched for name in wanted):
            return {name: punched[name] for name in wanted}
        totals = result.get("element_totals_molality") or {}
        if engine == "phreeqpython" and not result.get("_selected_output_row") and totals:
            valence = {_valence_key(k): v for k, v in totals.items() if isinstance(v, (int, float))}
            return {name: float(valence.get(name, 0.0)) for name in wanted}
        missing = [name for name in wanted if name not in punched]
        raise BatchRedoxMovedError(
            f"the held run's answer omitted {missing}: its redox states cannot be checked "
            f"({BATCH_REDOX_HOLD_RULING_ID})", call_stage="check_batch_redox")

    def _check(self, observed: Dict[str, float], step: str) -> List[str]:
        from puran_mcp_support.engineering.phreeqc_batch_redox import batch_redox_elements, check_batch_redox

        held = [_valence_key(e) for e in self.basis.element_of]
        fed = {_valence_key(s) for s in self.fed_coupled}
        elements = {e: tuple(s for s in (_valence_key(n) for n in names) if s in observed)
                    for e, names in batch_redox_elements(self.states).items()}
        # a held element carries one valence: its own amount is its element's scale, never checked; so is a coupled
        # state a feed or a gas carried, and C(4); every other coupled state was 0 before the step
        before = {state: observed[state] if (state in held or state in fed or state == "C(4)") else 0.0
                  for names in elements.values() for state in names}
        after = {state: observed[state] for names in elements.values() for state in names}
        unchecked = tuple(held) + tuple(sorted(fed))
        try:
            check_batch_redox(before, after, step=step, unchecked=unchecked, elements=elements)
        except ValueError as exc:
            note = ""
            if self.kinetic_rates_on_held_states:
                note = (" A kinetic rate reads held state(s) (" + "; ".join(
                    f"{name}: {', '.join(states)}" for name, states in self.kinetic_rates_on_held_states.items())
                        + "): its electrons may need the redox coupling.")
            raise BatchRedoxMovedError(
                f"{exc}.{note} The held states cannot move; a coupled state no feed carried appeared "
                f"({BATCH_REDOX_HOLD_RULING_ID}): set {EQUILIBRATE_REDOX_KEY}: true to re-equilibrate every couple",
                call_stage="check_batch_redox") from exc
        return sorted(s for names in elements.values() for s in names if s not in unchecked and s != "C(4)")

    def _fold(self, result: Dict[str, Any], observed: Dict[str, float], engine: str) -> None:
        held = self.basis.element_of
        amount = {element: observed.get(_valence_key(element), 0.0) for element in held}
        requested = {_valence_key(n) for n in self.requested_totals}
        requested_bases = {n.split("(", 1)[0] for n in requested}
        added = {_valence_key(n) for n in self.added_totals} - requested
        from_columns = engine != "phreeqpython" or "_selected_output_row" in result
        result.pop("_selected_output_row", None)
        # the base columns the run punched: phreeqpython's answer carries them too, backfilled into its totals by the
        # parser now that it reads the selected output before the saturation indices' DUMP run replaces it (OQ-C13-2 of
        
        punched = {_column(k) for k in (result.get("selected_output_data") or {})}
        totals = result.get("element_totals_molality")
        if isinstance(totals, dict):
            for element in held:
                totals.pop(element, None)
            if not from_columns:
                # phreeqpython's totals are its solution's valence states: each held element onto its own
                for element, valence in held.items():
                    if amount[element]:
                        key = next((k for k in totals if _valence_key(k) == _valence_key(valence)), valence)
                        totals[key] = float(totals.get(key) or 0.0) + amount[element]
                # and a punched base total (N, S) the run asked for, each with the held elements it is made of, as on
                # the standalone engine
                for key in list(totals):
                    if key in {"N", "S"} and key in punched and key in requested_bases:
                        totals[key] = float(totals[key] or 0.0) + sum(amount[e] for e in self._composes(key))
            else:
                # the columns the tool asked for (N, S ...), each with the held elements it is made of; a base the
                # tool never asked for came from a check column and goes
                for key in list(totals):
                    if key in {"N", "S", "C"} and key not in requested_bases:
                        totals.pop(key)
                    elif key in {"N", "S"}:
                        totals[key] = float(totals[key] or 0.0) + sum(amount[e] for e in self._composes(key))
        redox = result.get("element_redox_totals_molality")
        if isinstance(redox, dict):
            for key in list(redox):
                if _valence_key(key) in added or key in held:
                    redox.pop(key)
                else:
                    redox[key] = float(redox[key] or 0.0) + sum(amount[e] for e in self._composes(key))
        data = result.get("selected_output_data")
        if isinstance(data, dict):
            self._fold_columns(data, amount, added)
        for name in ("species_molalities", "species_molality", "species_activities"):
            species = result.get(name)
            if isinstance(species, dict):
                result[name] = self._fold_species(species)
        # the check's own columns leave the answer (C15-2): O(0)/H(0) backfilled as a bare base the tool never asked
        # for, and the row options the input had off (the solution number, the water; the state is text, read by none)
        if isinstance(totals, dict):
            for base in ("O", "H"):
                if base not in requested_bases and f"{base}(0)" in added:
                    totals.pop(base, None)
        for name in ("selected_output_data",):
            columns = result.get(name)
            if isinstance(columns, dict):
                if "soln" in self.added_row_options:
                    columns.pop("soln", None)
                if "state" in self.added_row_options:
                    columns.pop("state", None)
                if "water" in self.added_row_options:
                    columns.pop("mass_H2O", None)
        summary = result.get("solution_summary")
        if isinstance(summary, dict) and "water" in self.added_row_options and from_columns:
            summary.pop("mass_kg_water", None)   # phreeqpython's own summary carries its solution's mass: kept
        if isinstance(summary, dict) and "tds_calculated" in summary and engine == "phreeqpython":
            from water_chemistry_mcp.tools.phreeqc.parser import tds_from_species

            summary["tds_calculated"] = tds_from_species(result.get("species_molality") or {},
                                                         result.get("element_totals_molality") or {})

    def _fold_columns(self, data: Dict[str, Any], amount: Dict[str, float], added: set) -> None:
        for key in list(data):
            column = _column(key)
            if column in self.basis.element_of or _valence_key(column) in added:
                data.pop(key)
            elif column.startswith(("m_", "la_", "a_")):
                prefix, species = column.split("_", 1)
                coupled = self.basis.reverse.get(species)
                if coupled is None:
                    continue
                value = data.pop(key)
                target = next((k for k in data if _column(k) == f"{prefix}_{coupled}"), None)
                if target is not None and isinstance(value, (int, float)):
                    if prefix == "la":
                        data[target] = math.log10(10.0 ** float(data[target]) + 10.0 ** float(value))
                    else:
                        data[target] = float(data[target]) + float(value)
            elif _is_total(column) and self._composes(column) and isinstance(data[key], (int, float)):
                data[key] = float(data[key]) + sum(amount[e] for e in self._composes(column))

    def _fold_species(self, species: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, value in species.items():
            coupled = self.basis.reverse.get(name, name)
            if coupled in out and isinstance(value, (int, float)):
                out[coupled] = float(out[coupled]) + float(value)
            else:
                out[coupled] = value
        return out


def _column(header: str) -> str:
    name = str(header).strip().strip('"')
    if name.lower().endswith("(mol/kgw)"):
        name = name[: -len("(mol/kgw)")].strip()
    return name


def _is_total(name: str) -> bool:
    return bool(re.match(r"^[A-Z][a-z]?(\([+-]?\d+\))?$", name))


def is_batch_input(input_string: str) -> bool:
    return any(block.keyword in _BATCH_KEYWORDS for block in _blocks(input_string))


def _holds_its_own(blocks: List[_Block]) -> bool:
    from puran_mcp_support.engineering.phreeqc_batch_redox import DECOUPLABLE_STATES

    held = {entry[0] for entry in DECOUPLABLE_STATES.values()}
    for block in blocks:
        if block.keyword == "SOLUTION_MASTER_SPECIES":
            for line in block.lines:
                tokens = _code(line).split()
                if tokens and tokens[0] in held:
                    return True
    return False


_N_OR_S_KEY = re.compile(r"^(N|S)(\([+-]?\d+\))?$")


def hold_batch_redox(input_string: str, database_path: str) -> Optional[HeldBatch]:
    """The held form of one PHREEQC run, or None where nothing is to hold: not a batch step, no N or S in it, an input
    that holds its own states, or a database whose N and S each have one valence. Refuses (`InputContractError`) what
    cannot be held without guessing: a database the shared helpers cannot read, a bare N or S total in a solution, a
    redox couple on a held state, a reagent whose N or S no held state carries, a phase that needs the coupling, an
    exchanger binding a held state."""
    from puran_mcp_support.engineering import phreeqc_batch_redox as pbr

    blocks = _blocks(input_string)
    if not any(block.keyword in _BATCH_KEYWORDS for block in blocks) or _holds_its_own(blocks):
        return None
    inline = "\n".join(_join([b]) for b in blocks if b.keyword == "PHASES")
    if not _carries_n_or_s(blocks, _phase_formulas(database_path, inline)):
        return None
    name = os.path.basename(database_path)
    try:
        basis = _basis(database_path)
    except ValueError as exc:
        raise _refuse(f"{name} cannot be read by the shared batch-redox helpers ({exc}); hold its N and S on "
                      "phreeqc.dat, minteq.v4.dat, wateq4f.dat or minteq.dat") from exc
    if basis is None:
        return None
    states = basis.states
    keys = pbr.decoupled_analysis_keys(states)
    text_for_phases = [basis.text, inline] if inline else [basis.text]
    holdable, coupled = (pbr.phases_naming_held_states(basis.text, text_for_phases, states) if inline else
                         (list(basis.holdable_phases), set(basis.coupled_phases)))
    coupled = set(coupled)
    formulas = _phase_formulas(database_path, inline)
    requested: List[str] = []
    reactions: List[str] = []
    fed_coupled: set = set()
    kinetic_rates: Dict[str, List[str]] = {}
    input_rates = _rates(input_string)
    rates_to_add: Dict[str, str] = {}
    has_surface = has_exchange = False
    for block in blocks:
        kw = block.keyword
        if kw in {"SOLUTION", "SOLUTION_MODIFY"}:
            block.lines = [_held_solution_line(line, keys, fed_coupled, basis) for line in block.lines]
        elif kw == "SOLUTION_SPREAD":
            raise _refuse("a SOLUTION_SPREAD in a held batch step is not mapped onto the held elements; write each "
                          "solution as a SOLUTION block")
        elif kw == "REACTION":
            block.lines = [_held_reaction_line(line, states, coupled, formulas, reactions, fed_coupled)
                           for line in block.lines]
        elif kw == "EQUILIBRIUM_PHASES":
            block.lines = [_held_phase_line(line, states, coupled, formulas, fed_coupled) for line in block.lines]
        elif kw == "GAS_PHASE":
            for line in block.lines:
                tokens = _code(line).split()
                if tokens and not tokens[0].startswith("-") and not _is_number(tokens[0]):
                    _phase_named(tokens[0], coupled, formulas, fed_coupled, "gas")
        elif kw == "KINETICS":
            block.lines = _held_kinetics(block.lines, states, coupled, formulas, input_rates, basis, kinetic_rates,
                                         rates_to_add, fed_coupled)
        elif kw == "SURFACE":
            has_surface = True
        elif kw == "EXCHANGE":
            has_exchange = True
        elif kw == "RATES":
            block.lines = [_held_basic(line, basis) for line in block.lines]
        elif kw in {"USER_PUNCH", "USER_PRINT"}:
            block.lines = [_held_basic(line, basis) for line in block.lines]
        elif kw == "SELECTED_OUTPUT":
            block.lines = _held_selected_output(block.lines, basis, requested)
    if has_exchange and pbr.reactions_naming_decoupled_members(basis.text, text_for_phases + [input_string],
                                                               keywords=("EXCHANGE_SPECIES",), states=states):
        raise _refuse("an EXCHANGE binds a held state (an exchange species of ammonium or another held state) and is "
                      "not carried onto the held elements")
    added = list(basis.element_of) + list(basis.defined_states)
    # C15-2: dissolved O2 and H2 against the feed's, unless something in the step sets them
    water_exempt = _water_redox_exemption(blocks, formulas)
    if water_exempt is None and len(basis.water_states) < len(WATER_REDOX_STATES):
        water_exempt = f"exempt: {name} defines no O(0)/H(0)"
    water_feed: Optional[Dict[str, Any]] = None
    added_row_options: Tuple[str, ...] = ()
    if water_exempt is None:
        water_feed = _water_redox_feed(blocks)
        added += list(basis.water_states)
        if not any(b.keyword == "SELECTED_OUTPUT" for b in blocks):
            added_row_options = tuple(_ROW_OPTIONS)
        else:
            on = [_row_options_on(b.lines) for b in blocks if b.keyword == "SELECTED_OUTPUT"]
            added_row_options = tuple(option for option in _ROW_OPTIONS if not all(o[option] for o in on))
    phase_block = (pbr.decoupled_phase_definitions(basis.text, text_for_phases, holdable, states)[0] if inline
                   else basis.phase_block)
    surface_block = pbr.decoupled_surface_definitions(basis.text, text_for_phases + [input_string], states) \
        if has_surface else ""
    if not any(b.keyword == "SELECTED_OUTPUT" for b in blocks):
        end = next((i for i, b in enumerate(blocks) if b.keyword == "END"), len(blocks))
        blocks.insert(end, _Block("SELECTED_OUTPUT", "SELECTED_OUTPUT 1", ["    -reset false"]))
    for block in blocks:
        if block.keyword == "SELECTED_OUTPUT":
            # every redox state the check reads, once per block (a held element its own totals already punch is not
            # punched twice)
            punched = {_valence_key(t) for line in block.lines for t in _code(line).split()[1:]
                       if _code(line).split() and _code(line).split()[0].lower() in {"-totals", "-tot"}}
            more = [a for a in added if _valence_key(a) not in punched]
            if more:
                block.lines = _after_options(block.lines, "    -totals " + " ".join(more))
            if water_exempt is None:
                # the rows the dissolved O2/H2 check reads: which solution, which state, how much water
                for option in _ROW_OPTIONS:
                    block.lines = _after_options(block.lines, f"    {_ROW_OPTIONS[option][0]} true")
    rates_block = ""
    if rates_to_add:
        rates_block = "RATES\n" + "\n".join(f"{n}\n{body.rstrip()}" for n, body in rates_to_add.items())
    # the species definitions first; the phases, surface species and rates re-defined after every definition the
    # input makes of its own in its first simulation (PHREEQC keeps the last), so before its first END
    tail = [_Block("", "", [part]) for part in (phase_block, surface_block, rates_block) if part]
    end = next((i for i, b in enumerate(blocks) if b.keyword == "END"), len(blocks))
    blocks[end:end] = tail
    held_input = basis.definitions + "\n" + _join(blocks)
    return HeldBatch(input_string=held_input, basis=basis, requested_totals=requested,
                     added_totals=[a for a in added if a not in requested], fed_coupled=sorted(fed_coupled),
                     reactions_as_requested=reactions, kinetic_rates_on_held_states=kinetic_rates,
                     water_exempt=water_exempt, water_feed=water_feed, added_row_options=added_row_options)


def _water_redox_exemption(blocks: List[_Block], formulas: Dict[str, str]) -> Optional[str]:
    'Public engineering calculation interface.'
    reasons: List[str] = []

    def sets_them(name: str) -> bool:
        formula = formulas.get(name, "")
        return name in _WATER_REDOX_FORMULAS or formula in _WATER_REDOX_FORMULAS or "e-" in formula.split()

    for block in blocks:
        if block.keyword in {"GAS_PHASE", "GAS_PHASE_MODIFY"}:
            gases = [t[0] for t in (_code(line).split() for line in block.lines)
                     if t and not t[0].startswith("-") and not _is_number(t[0])]
            reasons.append(f"a GAS_PHASE ({', '.join(gases) or 'no component named'}) sets them")
            continue
        for line in block.lines:
            tokens = _code(line).split()
            if not tokens:
                continue
            if block.keyword == "EQUILIBRIUM_PHASES" and not tokens[0].startswith("-"):
                alternative = tokens[2] if len(tokens) > 2 and not _is_number(tokens[2]) else None
                if sets_them(tokens[0]) or (alternative and sets_them(alternative)):
                    reasons.append(f"{tokens[0]} in EQUILIBRIUM_PHASES sets them")
            elif block.keyword == "REACTION" and not tokens[0].startswith("-") and not _is_number(tokens[0]):
                if tokens[0] in _WATER_REDOX_FORMULAS:
                    reasons.append(f"the REACTION adds {tokens[0]}")
            elif block.keyword == "KINETICS" and tokens[0].lower() == "-formula":
                if any(t in _WATER_REDOX_FORMULAS for t in tokens[1:]):
                    reasons.append("a KINETICS formula adds O2 or H2")
    return "exempt: " + "; ".join(dict.fromkeys(reasons)) if reasons else None


def _water_redox_feed(blocks: List[_Block]) -> Dict[str, Any]:
    """The solution, or the MIX of solutions, a held step reacts (C15-2): its USE line, else its one MIX, else its one
    SOLUTION; all in the step's own simulation. Refuses (`_refuse`) what cannot be read without guessing: a batch in more
    than one simulation, a feed defined outside the step's simulation, two candidates and no USE."""
    simulations: List[List[_Block]] = [[]]
    for block in blocks:
        simulations[-1].append(block)
        if block.keyword == "END":
            simulations.append([])
    batches = [sim for sim in simulations if any(b.keyword in _BATCH_KEYWORDS for b in sim)]
    if len(batches) != 1:
        raise _refuse("the held step's feed cannot be read for the dissolved O2/H2 check "
                      f"({WATER_REDOX_RULING_ID}, C15-2): {len(batches)} simulations carry a batch step; write one")
    sim = batches[0]
    solutions: List[int] = []
    mixes: Dict[int, Dict[int, float]] = {}
    used: Dict[str, int] = {}
    for block in sim:
        head = _code(block.head).split()
        if block.keyword == "SOLUTION":
            solutions += _numbers(head[1] if len(head) > 1 else "1")
        elif block.keyword == "MIX":
            number = _numbers(head[1] if len(head) > 1 else "1")[0]
            fractions = {}
            for line in block.lines:
                tokens = _code(line).split()
                if len(tokens) >= 2 and _is_number(tokens[0]) and _is_number(tokens[1]):
                    fractions[int(float(tokens[0]))] = float(tokens[1])
            mixes[number] = fractions
        elif block.keyword == "USE" and len(head) >= 3 and head[1].lower() in {"solution", "mix"}:
            if head[2].isdigit():
                used[head[1].lower()] = int(head[2])
    if "mix" in used or (mixes and "solution" not in used):
        number = used.get("mix", next(iter(mixes)) if len(mixes) == 1 else None)
        if number is None or number not in mixes:
            raise _refuse("the held step's MIX cannot be read for the dissolved O2/H2 check "
                          f"({WATER_REDOX_RULING_ID}, C15-2): name it with USE mix n")
        feed = {"mix": mixes[number]}
    elif "solution" in used:
        feed = {"solution": used["solution"]}
    elif len(set(solutions)) == 1:
        feed = {"solution": solutions[0]}
    else:
        raise _refuse("the held step's feed cannot be read for the dissolved O2/H2 check "
                      f"({WATER_REDOX_RULING_ID}, C15-2): {len(set(solutions))} solutions in its simulation and no USE "
                      "solution n")
    wanted = set(feed["mix"]) if "mix" in feed else {feed["solution"]}
    if not wanted <= set(solutions):
        raise _refuse(f"the held step's feed {sorted(wanted)} is not an initial solution of its own simulation, so the "
                      f"dissolved O2/H2 check cannot read it ({WATER_REDOX_RULING_ID}, C15-2)")
    return feed


def _numbers(token: str) -> List[int]:
    """A SOLUTION/MIX number or range (``3``, ``1-3``)."""
    match = re.match(r"^(\d+)(?:-(\d+))?$", token)
    if not match:
        return [1]
    first = int(match.group(1))
    return list(range(first, int(match.group(2)) + 1)) if match.group(2) else [first]


def _row_options_on(lines: List[str]) -> Dict[str, bool]:
    """Which of the row options (state, solution number, water) a SELECTED_OUTPUT block prints: PHREEQC's defaults
    (state and solution on, water off) unless ``-reset`` or the option itself says otherwise, in order."""
    on = dict(_ROW_DEFAULTS)
    for line in lines:
        tokens = _code(line).split()
        if not tokens:
            continue
        option = tokens[0].lower()
        value = (tokens[1].lower() not in {"false", "f", "0"}) if len(tokens) > 1 else True
        if option in {"-reset", "-r"}:
            on = {key: value for key in on}
        for key, names in _ROW_OPTIONS.items():
            if option in names:
                on[key] = value
    return on


@functools.lru_cache(maxsize=16)
def _database_text_cached(path: str, mtime: float, size: int) -> str:
    with open(path, encoding="latin-1") as handle:
        return handle.read()


def _database_text(path: str) -> str:
    stat = os.stat(path)
    return _database_text_cached(os.path.realpath(path), stat.st_mtime, stat.st_size)


@functools.lru_cache(maxsize=64)
def _phase_formulas_cached(path: str, mtime: float, size: int, inline: str) -> Dict[str, str]:
    from puran_mcp_support.engineering.phreeqc_batch_redox import phase_formulas

    return phase_formulas([_database_text_cached(path, mtime, size)] + ([inline] if inline else []))


def _phase_formulas(path: str, inline: str) -> Dict[str, str]:
    """Every phase of the database and the input's own PHASES -> its formula (cached per file and inline text)."""
    stat = os.stat(path)
    return _phase_formulas_cached(os.path.realpath(path), stat.st_mtime, stat.st_size, inline)


def _carries_n_or_s(blocks: List[_Block], formulas: Dict[str, str]) -> bool:
    """Whether a solution, a reagent, a phase, a gas or a kinetic reactant of the run carries N or S."""
    from puran_mcp_support.engineering.phreeqc_batch_redox import formula_element_counts

    def formula_of(name: str) -> Optional[str]:
        return formulas.get(name)

    def has(formula: Optional[str]) -> bool:
        if not formula:
            return False
        try:
            counts = formula_element_counts(formula)
        except ValueError:
            return bool(re.search(r"(?<![A-Za-z])[NS](?![a-z])", formula))
        return bool(counts.get("N") or counts.get("S"))

    for block in blocks:
        for line in block.lines:
            tokens = _code(line).split()
            if not tokens or tokens[0].startswith("-") and block.keyword != "KINETICS":
                continue
            if block.keyword in {"SOLUTION", "SOLUTION_MODIFY", "SOLUTION_SPREAD"}:
                if _N_OR_S_KEY.match(tokens[0]):
                    return True
            elif block.keyword == "REACTION":
                if not _is_number(tokens[0]) and (has(formula_of(tokens[0])) or has(tokens[0])):
                    return True
            elif block.keyword in {"EQUILIBRIUM_PHASES", "GAS_PHASE"}:
                if has(formula_of(tokens[0])) or (len(tokens) > 2 and not _is_number(tokens[2]) and has(tokens[2])):
                    return True
            elif block.keyword == "KINETICS":
                if tokens[0].lower() == "-formula":
                    if any(has(t) for t in tokens[1:] if not _is_number(t)):
                        return True
                elif not tokens[0].startswith("-") and has(formula_of(tokens[0])):
                    return True
    return False


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _held_solution_line(line: str, keys: Dict[str, str], fed_coupled: set, basis: _Basis) -> str:
    code = _code(line)
    tokens = code.split()
    if not tokens:
        return line
    key = tokens[0]
    lowered = key.lower()
    if lowered in {"redox", "-redox"} and len(tokens) > 1:
        couple = tokens[1]
        if re.search(r"(?<![A-Za-z])[NS]\(", couple):
            raise _refuse(f"a solution's redox couple {couple} names a held state, which cannot set the pe once held; "
                          "give the pe")
        return line
    if lowered in _SOLUTION_OPTIONS:
        return line
    if key in keys:
        return line.replace(key, keys[key], 1)
    if _N_OR_S_KEY.match(key):
        if "(" not in key:
            raise _refuse(f"a solution gives a bare {key} total; in a held batch step name its valence state "
                          f"({'N(-3), N(5) or N(3)' if key == 'N' else 'S(6) or S(-2)'})")
        fed_coupled.add(_valence_key(key))
    if _valence_key(key) == "C(-4)" or (key == "C(-4)"):
        fed_coupled.add("C(-4)")
    return line


def _held_formula(formula: str, states, coupled_phases, what: str) -> str:
    from puran_mcp_support.engineering.phreeqc_batch_redox import held_reagent_formula

    if formula in coupled_phases:
        raise _refuse(f"the {what} {formula} reacts a held state in a redox reaction (it needs the coupling)")
    try:
        return held_reagent_formula(formula, states)
    except ValueError as exc:
        raise _refuse(f"the {what} {formula}: {exc}") from exc


def _phase_named(name: str, coupled_phases, formulas: Dict[str, str], fed_coupled: set, what: str) -> bool:
    """Whether ``name`` is a phase (of the database or the input). A phase whose reaction needs the coupling refuses; a
    coupled state a phase brings by mass transfer (N2 as N(0), CH4 as C(-4)) is a fed state of the step."""
    if name in coupled_phases:
        raise _refuse(f"the {what} {name} reacts a held state in a redox reaction (it needs the coupling)")
    formula = formulas.get(name)
    if formula is None:
        return False
    state = _PHASE_STATES.get(formula)
    if state:
        fed_coupled.add(state)
    return True


def _held_reaction_line(line: str, states, coupled_phases, formulas, reactions: List[str], fed_coupled: set) -> str:
    code = _code(line)
    tokens = code.split()
    if not tokens or tokens[0].startswith("-") or _is_number(tokens[0]):
        return line
    reactions.append(tokens[0])
    if _phase_named(tokens[0], coupled_phases, formulas, fed_coupled, "reactant"):
        return line                                   # a phase: its held re-definition carries it
    held = _held_formula(tokens[0], states, coupled_phases, "reagent")
    return line.replace(tokens[0], held, 1) if held != tokens[0] else line


def _held_phase_line(line: str, states, coupled_phases, formulas, fed_coupled: set) -> str:
    code = _code(line)
    tokens = code.split()
    if not tokens or tokens[0].startswith("-"):
        return line
    _phase_named(tokens[0], coupled_phases, formulas, fed_coupled, "phase")
    if len(tokens) > 2 and not _is_number(tokens[2]):
        if _phase_named(tokens[2], coupled_phases, formulas, fed_coupled, "alternative reactant"):
            return line
        held = _held_formula(tokens[2], states, coupled_phases, "alternative reactant")
        if held != tokens[2]:
            return re.sub(rf"(\s){re.escape(tokens[2])}(\s|$)", rf"\g<1>{held}\g<2>", line, count=1)
    return line


def _held_kinetics(lines: List[str], states, coupled_phases, formulas, input_rates: Dict[str, str], basis: _Basis,
                   kinetic_rates: Dict[str, List[str]], rates_to_add: Dict[str, str], fed_coupled: set) -> List[str]:
    """A KINETICS block on the held elements: each reactant's -formula on the held state its reagent groups name
    (`held_reagent_formula`: HNO3 -> HNitO3), a bare N or S refused; a reactant with no -formula is the phase of its
    name. A rate the database defines is carried into the input with its BASIC read on the held elements."""
    out: List[str] = []
    current: Optional[str] = None
    without_formula: List[str] = []
    for line in lines:
        code = _code(line)
        tokens = code.split()
        if tokens and not tokens[0].startswith("-") and not _is_number(tokens[0]):
            current = tokens[0]
            without_formula.append(current)
            body = input_rates.get(current)
            if body is None and current in basis.rates:
                body = basis.rates[current]
                rewritten = "\n".join(_held_basic(row, basis) for row in body.splitlines())
                if rewritten.strip() != body.strip():
                    rates_to_add[current] = rewritten
            names = _held_states_read(body or "", basis)
            if names:
                kinetic_rates[current] = names
            out.append(line)
            continue
        if tokens and tokens[0].lower() == "-formula" and current is not None:
            if current in without_formula:
                without_formula.remove(current)
            rebuilt: List[str] = []
            rate_states = kinetic_rates.get(current, [])
            for term in tokens[1:]:
                if _is_number(term):
                    rebuilt.append(term)
                elif term in {"N", "S"}:
                    # a bare element moves N or S without its oxygen or hydrogen, so it hands the water the
                    # element's electrons; held, they had nowhere to go but carbon (methane from the DIC)
                    neutral = "HNO3, HNO2 or NH3" if term == "N" else "H2SO4 or H2S"
                    raise _refuse(f"the kinetic reactant {current} adds or removes a bare {term}, which moves the "
                                  "element without its oxygen or hydrogen and so hands the water its electrons; write "
                                  f"the species' neutral formula ({neutral})")
                elif _phase_named(term, coupled_phases, formulas, fed_coupled, "kinetic reactant"):
                    rebuilt.append(term)
                else:
                    rebuilt.append(_held_formula(term, states, coupled_phases, "kinetic reactant"))
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}-formula {' '.join(rebuilt)}")
            continue
        out.append(line)
    for name in without_formula:                      # the reactant is the phase of its name
        _phase_named(name, coupled_phases, formulas, fed_coupled, "kinetic reactant")
    return out


def _held_states_read(basic: str, basis: _Basis) -> List[str]:
    """The held states a BASIC text reads (TOT("N(5)"), MOL("NH4+") ...), as coupled valence states."""
    found = []
    for match in re.finditer(r'\b[A-Z_]+\s*\(\s*"([^"]+)"', basic):
        arg = match.group(1)
        for element, valence in basis.element_of.items():
            if _valence_key(arg) == _valence_key(valence) or basis.forward.get(arg) and \
                    element in basis.forward[arg]:
                if valence not in found:
                    found.append(valence)
    return found


def _held_basic(line: str, basis: _Basis) -> str:
    """A BASIC line reading the held elements too: a total of N or S (or one of their held valence states) summed over
    its held elements, a held species' molality and activity summed with its held name's, and its log activity,
    log molality and activity coefficient read on the held name (the coupled species is absent in a held step)."""
    def total(match):
        fn, arg = match.group(1), match.group(2)
        key = _valence_key(arg)
        parts = [e for e, v in basis.element_of.items() if key == v.split("(", 1)[0] or key == _valence_key(v)]
        if not parts:
            return match.group(0)
        return "(" + "+".join([f'{fn}("{arg}")'] + [f'{fn}("{p}")' for p in parts]) + ")"

    def additive(match):
        fn, arg = match.group(1), match.group(2)
        held = basis.forward.get(arg)
        return f'({fn}("{arg}")+{fn}("{held}"))' if held and held != arg else match.group(0)

    def same(match):
        fn, arg = match.group(1), match.group(2)
        held = basis.forward.get(arg)
        return f'{fn}("{held}")' if held and held != arg else match.group(0)

    def surf(match):
        arg, surface = match.group(1), match.group(2)
        key = _valence_key(arg)
        parts = [e for e, v in basis.element_of.items() if key == v.split("(", 1)[0] or key == _valence_key(v)]
        if not parts:
            return match.group(0)
        return "(" + "+".join([f'SURF("{arg}","{surface}")'] + [f'SURF("{p}","{surface}")' for p in parts]) + ")"

    out = re.sub(rf'\b({"|".join(_BASIC_TOTALS)})\s*\(\s*"([^"]+)"\s*\)', total, line)
    out = re.sub(rf'\b({"|".join(_BASIC_ADDITIVE)})\s*\(\s*"([^"]+)"\s*\)', additive, out)
    out = re.sub(rf'\b({"|".join(_BASIC_SAME)})\s*\(\s*"([^"]+)"\s*\)', same, out)
    out = re.sub(r'\bSURF\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', surf, out)
    return out


def _held_selected_output(lines: List[str], basis: _Basis, requested: List[str]) -> List[str]:
    """SELECTED_OUTPUT lines that also punch the held names: each total of N or S (or a held valence state) with its
    held elements, each held species' molality or log activity with its held name; embedded BASIC read on them too."""
    out = []
    in_basic = False
    for line in lines:
        code = _code(line)
        tokens = code.split()
        option = tokens[0].lower() if tokens else ""
        if option == "-start":
            in_basic = True
        elif option == "-end":
            in_basic = False
        if in_basic and option != "-start":
            out.append(_held_basic(line, basis))
            continue
        if option in {"-totals", "-tot", "-t"}:
            names = tokens[1:]
            requested.extend(names)
            extra = []
            for name in names:
                key = _valence_key(name)
                for element, valence in basis.element_of.items():
                    if (key == valence.split("(", 1)[0] or key == _valence_key(valence)) and element not in extra:
                        extra.append(element)
            out.append(line + (" " + " ".join(e for e in extra if e not in names) if extra else ""))
            continue
        if option in {"-molalities", "-mol", "-m", "-activities", "-act", "-a"}:
            extra = [basis.forward[n] for n in tokens[1:] if basis.forward.get(n) and basis.forward[n] != n]
            out.append(line + (" " + " ".join(extra) if extra else ""))
            continue
        out.append(line)
    return out


def _after_options(lines: List[str], extra: str) -> List[str]:
    """``extra`` appended after the block's own option lines (before any trailing blank or comment lines)."""
    last = max((i for i, line in enumerate(lines) if _code(line).strip()), default=-1)
    return lines[: last + 1] + [extra] + lines[last + 1:]


def decoupled_name_in(payload: Any) -> List[str]:
    """Every dict key of an answer naming a held element or species (a mapped-back answer leaves none)."""
    found = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and _DECOUPLED_NAME.search(key):
                found.append(key)
            found += decoupled_name_in(value)
    elif isinstance(payload, list):
        for item in payload:
            found += decoupled_name_in(item)
    return found


__all__ = ["BATCH_REDOX_HOLD_RULING_ID", "BatchRedoxMovedError", "WATER_REDOX_RULING_ID", "WATER_REDOX_STATES",
           "WATER_REDOX_STRICT_RULING_ID", "EQUILIBRATE_REDOX_KEY", "HELD_STATES", "HeldBatch",
           "POLICY_EQUILIBRATED", "POLICY_HELD", "decoupled_name_in", "equilibrate_requested", "hold_batch_redox",
           "holds_batch_redox", "is_batch_input", "record_held_run"]
