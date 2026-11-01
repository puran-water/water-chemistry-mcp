"The SOLUTION writers supply pH 7 and pe 4 when an input gives neither (PHREEQC's own defaults). That stays, since no\nwater-chemistry answer is a governing result (the P screen's ``qualification`` authorizes nothing), but it is no longer\nsilent: the input contracts record each default here while a registered tool answers, and `waterchem.answered_by`\nputs them on the answer as ``solution_basis_defaults``. Two values given for one setting are refused where the input is\nread (`waterchem.analysis.canonicalize_water_analysis`)."

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

BASIS_DEFAULTS_RULING_ID = 'PUBLIC-RULE-8226e460e1d8'
_DEFAULTS: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("waterchem_basis_defaults", default=None)
_BASES = {
    "pH": "the input gave no pH and does not balance charge on pH: the solution is written at pH 7 (PHREEQC's default)",
    "pe": "the input gave no pe and no redox couple: the solution's redox states given as totals are distributed at "
          "pe 4 (PHREEQC's default)",
}


def record_default(field: str, value: float) -> None:
    """Called where an input is read, once per default it leaves to the house; deduplicated per answer."""
    entries = _DEFAULTS.get()
    if entries is None:
        return
    entry = {"field": field, "value": float(value), "basis": _BASES[field], "ruling_id": BASIS_DEFAULTS_RULING_ID}
    if entry not in entries:
        entries.append(entry)


@contextlib.contextmanager
def collecting_basis_defaults() -> Iterator[List[Dict[str, Any]]]:
    outer = _DEFAULTS.get()
    entries: List[Dict[str, Any]] = []
    token = _DEFAULTS.set(entries)
    try:
        yield entries
    finally:
        _DEFAULTS.reset(token)
        if outer is not None:
            outer.extend(entry for entry in entries if entry not in outer)


def record_schema_defaults(model: Any) -> Any:
    """For an input model whose pe defaults to 4.0: record the pe the schema supplied when the caller gave none and no
    redox couple (``model_fields_set``), and the pH 7 when it gave no pH and does not balance charge on pH."""
    given = getattr(model, "model_fields_set", set())
    if "pe" not in given and not getattr(model, "redox", None):
        record_default("pe", 4.0)
    charge = str(getattr(model, "charge_balance", "") or "").strip().lower()
    if getattr(model, "ph", None) is None and charge != "ph":
        record_default("pH", 7.0)
    return model
