"A request can name its database at the top level and on each solution it carries (the documented shape puts it in\nboth). Before, a database named on the solution only was ignored and the tool's default answered (phreeqc.dat, or\nminteq.dat for the lime tools), and two that disagreed were settled silently in favour of the top level. Now the\nrequest's database is the one it names, wherever it names it; two different names refuse; with none named the tool's\ndeclared default answers, and the answer says which (`waterchem.answered_by`)."

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .input_key_rulings import DATABASE_CHOICE_RULING_ID


def _identity(database: str) -> str:
    """Two spellings of one file are one database (a name and its full path)."""
    try:
        from water_chemistry_mcp.utils.database_management import database_manager

        resolved = database_manager.resolve_database_path(database)
    except Exception:
        resolved = None
    return os.path.realpath(resolved) if resolved else str(database).strip()


def one_database(named: Iterable[Tuple[str, Optional[str]]]) -> Optional[str]:
    """The database a request names, given (where, value) for every place it may name one; None when it names none.
    Refuses (ValueError) two places that name different databases."""
    given = [(where, value) for where, value in named if value not in (None, "")]
    if not given:
        return None
    if len({_identity(value) for _, value in given}) > 1:
        places = ", ".join(f"{where}={value!r}" for where, value in given)
        raise ValueError(
            f"the request names more than one database ({places}): one run answers on one database, so name it "
            f"once, or name the same one in each place ({DATABASE_CHOICE_RULING_ID})"
        )
    return given[0][1]


def _solutions(values: Dict[str, Any], key: str) -> List[Tuple[str, Dict[str, Any], Any]]:
    """(where, solution dict, container, index-or-key) for a solution key; ``a[].b`` reaches ``b`` in each item of
    list ``a``."""
    found = []
    if "[]." in key:
        outer, inner = key.split("[].", 1)
        items = values.get(outer)
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict) and isinstance(item.get(inner), dict):
                    found.append((f"{outer}[{index}].{inner}.database", item, inner))
    elif isinstance(values.get(key), dict):
        found.append((f"{key}.database", values, key))
    return found


def unify_request_database(values: Any, solution_keys: Iterable[str]) -> Any:
    """A model's before-validator: the request's one database written to its top level and to every solution it carries
    (copies; the caller's dicts are not changed), so every run the tool makes is on it. A top-level default the caller
    did not set is not a name (the field's default applies only when nothing is named)."""
    if not isinstance(values, dict):
        return values
    values = dict(values)
    located = [loc for key in solution_keys for loc in _solutions(values, key)]
    named = [("database", values.get("database"))] if "database" in values else []
    named += [(where, container[field].get("database")) for where, container, field in located]
    database = one_database(named)
    if database is None:
        return values
    values["database"] = database
    for key in solution_keys:
        if "[]." in key:
            outer, inner = key.split("[].", 1)
            values[outer] = [
                {**item, inner: {**item[inner], "database": database}}
                if isinstance(item, dict) and isinstance(item.get(inner), dict) else item
                for item in values.get(outer) or []
            ]
        elif isinstance(values.get(key), dict):
            values[key] = {**values[key], "database": database}
    return values
