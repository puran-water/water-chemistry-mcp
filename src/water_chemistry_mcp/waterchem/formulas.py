"""Formula normalization helpers for public reagent inputs."""

from __future__ import annotations

import re
from typing import Dict

HYDRATE_DOT = "·"

FORMULA_ALIASES: Dict[str, str] = {
    "alum": "KAl(SO4)2·12H2O",
    "potassium alum": "KAl(SO4)2·12H2O",
    "kal(so4)2·12h2o": "KAl(SO4)2·12H2O",
    "kal(so4)2.12h2o": "KAl(SO4)2·12H2O",
    "kal(so4)2:12h2o": "KAl(SO4)2·12H2O",
    "aluminum sulfate": "Al2(SO4)3",
    "aluminium sulfate": "Al2(SO4)3",
    "al2(so4)3": "Al2(SO4)3",
    "al2(so4)3·14h2o": "Al2(SO4)3·14H2O",
    "al2(so4)3.14h2o": "Al2(SO4)3·14H2O",
    "al2(so4)3:14h2o": "Al2(SO4)3·14H2O",
    "al2(so4)3·18h2o": "Al2(SO4)3·18H2O",
    "al2(so4)3.18h2o": "Al2(SO4)3·18H2O",
    "al2(so4)3:18h2o": "Al2(SO4)3·18H2O",
    "ferric sulfate": "Fe2(SO4)3",
    "fe2(so4)3": "Fe2(SO4)3",
    "ferric chloride": "FeCl3",
    "fecl3": "FeCl3",
    "ferric chloride hexahydrate": "FeCl3·6H2O",
    "fecl3·6h2o": "FeCl3·6H2O",
    "fecl3.6h2o": "FeCl3·6H2O",
    "fecl3:6h2o": "FeCl3·6H2O",
    "ferrous sulfate": "FeSO4",
    "feso4": "FeSO4",
    "ferrous sulfate heptahydrate": "FeSO4·7H2O",
    "feso4·7h2o": "FeSO4·7H2O",
    "feso4.7h2o": "FeSO4·7H2O",
    "feso4:7h2o": "FeSO4·7H2O",
}

PHREEQC_REACTION_FORMULA_ALIASES: Dict[str, str] = {
    "Al2(SO4)3·14H2O": "Al2(SO4)3",
    "Al2(SO4)3·18H2O": "Al2(SO4)3",
    "FeCl3·6H2O": "FeCl3",
    "FeSO4·7H2O": "FeSO4",
    "KAl(SO4)2·12H2O": "KAl(SO4)2",
}


def normalize_formula_name(formula: str) -> str:
    """Normalize common reagent aliases and hydrate separators."""

    text = " ".join(str(formula).strip().split())
    alias = FORMULA_ALIASES.get(text.lower())
    if alias:
        return alias
    compact = re.sub(r"\s+", "", text).replace("⋅", HYDRATE_DOT)
    compact = re.sub(r"(?<=[A-Za-z0-9\)])[:\.·](?=\d+H2O)", HYDRATE_DOT, compact, flags=re.IGNORECASE)
    return FORMULA_ALIASES.get(compact.lower(), compact)


def phreeqc_formula_for_reactant(formula: str) -> str:
    """Return the PHREEQC reaction formula for a public reagent formula."""

    normalized = normalize_formula_name(formula)
    return PHREEQC_REACTION_FORMULA_ALIASES.get(normalized, normalized)
