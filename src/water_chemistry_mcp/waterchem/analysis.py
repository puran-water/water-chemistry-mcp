"""Canonical analyte handling and PHREEQC SOLUTION translation."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, MutableMapping, Optional, Tuple

from .contracts.common import AnalyteAmount, CanonicalWaterAnalysis, ConcentrationUnit
from .input_key_rulings import (
    COMPOUND_KEY_RULING_ID,
    DATABASE_CHOICE_RULING_ID,
    INPUT_KEY_RULING_ID,
    WATERCHEM_KEYS_RULING_ID,
)
from .units import ELEMENT_MOLAR_MASS_G_MOL, FORMULA_MOLAR_MASS_G_MOL, convert_concentration



ANALYTE_ALIASES = {
    "alk": "Alkalinity",
    "alk as caco3": "Alkalinity",
    "alkalinity": "Alkalinity",
    "alkalinity as caco3": "Alkalinity",
    "ca": "Ca",
    "calcium": "Ca",
    "cl": "Cl",
    "chloride": "Cl",
    "fe": "Fe",
    "iron": "Fe",
    "fe2+": "Fe(2)",
    "fe(2+)": "Fe(2)",
    "fe3+": "Fe(3)",
    "fe(3+)": "Fe(3)",
    "k": "K",
    "potassium": "K",
    "mg": "Mg",
    "magnesium": "Mg",
    "na": "Na",
    "sodium": "Na",
    "nitrate": "N(5)",
    "no3": "N(5)",
    "nitrite": "N(3)",
    "no2": "N(3)",
    "ammonia": "N(-3)",
    "ammonium": "N(-3)",
    "nh4": "N(-3)",
    "phosphate": "P",
    "phosphorus": "P",
    "po4": "P",
    "p": "P",
    "sulfate": "S(6)",
    "so4": "S(6)",
    "sulfide": "S(-2)",
    "s(-2)": "S(-2)",
    "s(6)": "S(6)",
    "silica": "Si",
    "silicon": "Si",
    "si": "Si",
    "sio2": "Si",
    "reactive silica": "Si",
    "dissolved silica": "Si",
    "so4-2": "S(6)",
    "so42-": "S(6)",
    "sulphate": "S(6)",
    "ba": "Ba",
    "barium": "Ba",
    "sr": "Sr",
    "strontium": "Sr",
    "f": "F",
    "fluoride": "F",
    "b": "B",
    "boron": "B",
    "al": "Al",
    "aluminium": "Al",
    "aluminum": "Al",
    "mn": "Mn",
    "manganese": "Mn",
    "ba2+": "Ba",
    "sr2+": "Sr",
}

#: Every element / redox state defined as a SOLUTION_MASTER_SPECIES across the
#: shipped databases (phreeqc.dat, minteq.v4.dat, pitzer.dat, wateq4f.dat).
#:
#: A regex was used here first and was too permissive: it accepted any
#: element-shaped token, so a typo like ``Xx`` passed validation, reached PHREEQC
#: as an unknown element, and was silently ignored -- the exact silent-drop this
#: check exists to stop. A compound formula (``SiO2``, ``HCO3``) is not a master
#: species and must arrive through ANALYTE_ALIASES.
PHREEQC_MASTER_SPECIES = frozenset({
    "Ag", "Al", "As", "As(3)", "As(5)", "B", "Ba", "Be", "Br", "C", "C(-4)",
    "C(4)", "Ca", "Cd", "Cl", "Co", "Co(2)", "Co(3)", "Cr", "Cr(2)", "Cr(3)",
    "Cr(6)", "Cs", "Cu", "Cu(1)", "Cu(2)", "E", "F", "Fe", "Fe(2)", "Fe(3)",
    "H", "H(0)", "H(1)", "Hg", "Hg(0)", "Hg(1)", "Hg(2)", "I", "K", "Li", "Mg",
    "Mn", "Mn(2)", "Mn(3)", "Mn(6)", "Mn(7)", "Mo", "N", "N(-3)", "N(0)",
    "N(3)", "N(5)", "Na", "Ni", "O", "O(-2)", "O(0)", "P", "Pb", "Rb", "S",
    "S(-2)", "S(6)", "Sb", "Sb(3)", "Sb(5)", "Se", "Se(-2)", "Se(4)", "Se(6)",
    "Si", "Sn", "Sn(2)", "Sn(4)", "Sr", "Tl", "Tl(1)", "Tl(3)", "U", "U(3)",
    "U(4)", "U(5)", "U(6)", "V", "V(2)", "V(3)", "V(4)", "V(5)", "Zn",
})

#: The carbonate-species keys and the formula each names. Its value is neither alkalinity nor total inorganic carbon:
#: PHREEQC reads ``Alkalinity`` against Ca0.5(CO3)0.5 (phreeqc.dat) or HCO3 (minteq.v4.dat), so a bare ``HCO3: 61``
#: had silently become "61 mg/L as CaCO3" (~1.22x) and a bare ``CO3`` ~1.67x. Accepted only with its meaning stated
#: (`INPUT_KEY_RULING_ID`): 'as alkalinity' (the value is alkalinity expressed as the key's formula) or 'as species'
#: (the value is the free ion, ion pairs excluded (`WATERCHEM_KEYS_RULING_ID`, B3), converted to C(4) at the input pH on
#: the run's own database). Its meaning is needed in every unit: a molar HCO3 is still meq, free ion or total.
_CARBONATE_SPECIES_KEYS = {
    "hco3": "HCO3", "hco3-": "HCO3", "bicarbonate": "HCO3",
    "co3": "CO3", "co3-2": "CO3", "co3--": "CO3", "carbonate": "CO3",
}
_AMBIGUOUS_CARBONATE_ALIASES = frozenset(_CARBONATE_SPECIES_KEYS)
#: PHREEQC's species name for each carbonate formula (the same in every shipped database).
CARBONATE_SPECIES = {"HCO3": "HCO3-", "CO3": "CO3-2"}
#: Equivalents of alkalinity (protons taken to the CO2 end point) per mole of each formula alkalinity is expressed as.
#: PHREEQC's own ``as CO3`` on Alkalinity counts ONE equivalent per mole (100 mg/L read as 1.667 meq), so an
#: alkalinity stated as a formula is converted here, never handed to PHREEQC on that formula.
ALKALINITY_EQUIVALENTS_PER_MOL = {"HCO3": 1, "CO3": 2, "CaCO3": 2}
#: The marker a carbonate-species line carries until the run resolves it to the C(4) total on its own database
#: (`waterchem.carbonate_species`). PHREEQC refuses an unresolved one ("Expected a mineral name"), so a species amount
#: can never be read as a total.
SPECIES_MARKER = "unresolved_species_basis:"

#: The redox state an element key is written as. Fe -> Fe(2) and Mn -> Mn(2) whatever the database: ruled unchanged (C4 of
#: `DATABASE_CHOICE_RULING_ID`), so on a database that defines the element but not that state (pitzer.dat: Fe, not Fe(2))
#: the run is refused by name (`waterchem.defined_analytes`), never rewritten to the element.
PHREEQC_DEFAULT_SPECIES = {
    "As": "As(5)",
    "Cr": "Cr(6)",
    "Fe": "Fe(2)",
    "Mn": "Mn(2)",
    "N": "N(5)",
    "S": "S(6)",
}

# Conversion of a concentration expressed "as <compound>" to the elemental /
# species basis PHREEQC expects, keyed by (canonical_analyte, formula) ->
# multiplier = elemental_mass / compound_mass. Lets callers pass the common
# lab bases (mg/L as SO4, as SiO2, as NO3, hardness as CaCO3) instead of
# pre-converting. A formula naming the element/species itself is factor 1.0.
# Formulas are written as chemistry writes them (they are also written to PHREEQC, `canonical_basis_formula`); a
# caller's spelling is matched case-insensitively (`_normalize_as_formula`).
_AS_FORMULA_TO_ELEMENTAL: Dict[Tuple[str, str], float] = {
    # PHREEQC reads a mg/L input against the master species' INPUT FORMULA
    # (SOLUTION_MASTER_SPECIES column 4), which is NOT always the element:
    #   Si  -> SiO2 (60.08)      S / S(6) -> SO4 (96.06)
    #   Ca  -> Ca                N / N(5) -> N            P -> P
    # So "as SiO2" and "as SO4" are ALREADY the basis PHREEQC wants and must be
    # factor 1.0; it is "as Si" and "as S" that need converting UP. These two
    # rows previously carried the elemental ratio, which double-converted and
    # under-reported silica by 2.14x and sulfate by 3.0x — silently, and in the
    # direction that makes a scaling screen look safe.
    # Not every database agrees (minteq.dat reads Si as H4SiO4, 37.5 % low), so a value on these two
    # analytes is written to PHREEQC WITH its basis ("96.06 as SO4"), which every database reads alike.
    ("S(6)", "SO4"): 1.0,
    ("S(6)", "SO4-2"): 1.0,
    ("S(6)", "S"): 96.06 / 32.06,
    ("Si", "SiO2"): 1.0,
    ("Si", "Si"): 60.084 / 28.086,
    # C(4) is read on a different formula by each database (61.02 mg/L: 1.000 mmol on phreeqc.dat, as HCO3; 1.017 on
    # minteq.v4.dat, as CO3; 5.08 on sit.dat, as C), so a bare mass of it is refused and a stated one is converted to
    # the house basis, HCO3 (phreeqc.dat's; `utils.phreeqc_database_introspection.HOUSE_INPUT_GFW`), and written with
    # it (`WATERCHEM_KEYS_RULING_ID`).
    ("C(4)", "HCO3"): 1.0,
    ("C(4)", "CO3"): FORMULA_MOLAR_MASS_G_MOL["HCO3"] / FORMULA_MOLAR_MASS_G_MOL["CO3"],
    ("C(4)", "C"): FORMULA_MOLAR_MASS_G_MOL["HCO3"] / ELEMENT_MOLAR_MASS_G_MOL["C"],
    # C(-4) likewise (16.04 mg/L: 1.000 mmol on phreeqc.dat, wateq4f.dat and llnl.dat, as CH4; 1.336 on sit.dat, as C),
    # so a bare mass of it is refused and a stated one is converted to the house basis, CH4 (phreeqc.dat's), and
    # written with it (C5 of `DATABASE_CHOICE_RULING_ID`).
    ("C(-4)", "CH4"): 1.0,
    ("C(-4)", "C"): FORMULA_MOLAR_MASS_G_MOL["CH4"] / ELEMENT_MOLAR_MASS_G_MOL["C"],
    ("N(5)", "NO3"): 14.007 / 62.004,
    ("N(5)", "N"): 1.0,
    ("N(3)", "NO2"): 14.007 / 46.005,
    ("N(3)", "N"): 1.0,
    ("N(-3)", "NH4"): 14.007 / 18.039,
    ("N(-3)", "NH3"): 14.007 / 17.031,
    ("N(-3)", "N"): 1.0,
    ("P", "PO4"): 30.974 / 94.971,
    ("P", "P"): 1.0,
    # S(-2) is read as S (phreeqc.dat, minteq.v4.dat: gfw formula S; wateq4f.dat: 32.064).
    ("S(-2)", "S"): 1.0,
    ("S(-2)", "H2S"): 32.06 / 34.076,
    ("Ca", "CaCO3"): 40.078 / 100.087,
    ("Ca", "Ca"): 1.0,
    ("Mg", "CaCO3"): 24.305 / 100.087,
    ("Mg", "Mg"): 1.0,
}
#: A bare value whose key does not say whose mass it is is refused in these units only: a molar amount has no mass basis
#: to misread (`WATERCHEM_KEYS_RULING_ID`, B1).
_MASS_UNITS = frozenset({ConcentrationUnit.MG_L, ConcentrationUnit.UG_L})
_MOLAR_UNITS = frozenset({ConcentrationUnit.MMOL_L, ConcentrationUnit.MOL_L})
#: An element key PHREEQC reads on the input row of one of its redox states, where the key is written unchanged (not
#: remapped, as `PHREEQC_DEFAULT_SPECIES` remaps S and N). Every shipped database reads ``C`` on the input formula of
#: ``C(4)`` (61.02 mg/L of either: 1.000 mmol on phreeqc.dat, 1.017 on minteq.v4.dat, 5.08 on sit.dat), and PHREEQC
#: refuses the two in one SOLUTION ("Carbon entered more than once"): one basis and one inventory. Ruled so: "key C
#: follows C(4)" (C2 of `DATABASE_CHOICE_RULING_ID`).
_READ_ON_THE_ROW_OF = {"C": "C(4)"}


def _normalize_as_formula(formula: str) -> str:
    return re.sub(r"[\s^+]", "", str(formula)).upper()


_AS_FORMULA_INDEX: Dict[Tuple[str, str], Tuple[str, float]] = {
    (analyte, _normalize_as_formula(formula)): (formula, factor)
    for (analyte, formula), factor in _AS_FORMULA_TO_ELEMENTAL.items()
}


def _table_analyte(canonical_name: str) -> str:
    """The analyte the basis table lists for a canonical name: itself, or the redox state PHREEQC writes it as
    (``S`` is written S(6), ``N`` is written N(5)) or reads it on (``C`` on C(4))."""
    canonical_name = _READ_ON_THE_ROW_OF.get(canonical_name, canonical_name)
    if any(analyte == canonical_name for analyte, _ in _AS_FORMULA_TO_ELEMENTAL):
        return canonical_name
    species = phreeqc_species_name(canonical_name)
    if any(analyte == species for analyte, _ in _AS_FORMULA_TO_ELEMENTAL):
        return species
    return canonical_name


def canonical_basis_formula(canonical_name: str) -> Optional[str]:
    """The formula a mass of ``canonical_name`` is a mass OF, when that is not its element; None when it is.

    Derived from the basis table, never listed: an analyte whose own element row converts (``("S(6)", "S")`` is 2.996,
    ``("Si", "Si")`` 2.139, ``("C(4)", "C")`` 5.080) has its factor-1.0 compound as its basis (SO4, SiO2, HCO3). Every
    other analyte is read as its element (P, N(-3), N(5), S(-2), Ca, Mg, Cl ...)."""
    analyte = _table_analyte(canonical_name)
    element = analyte.split("(", 1)[0]
    own = _AS_FORMULA_INDEX.get((analyte, element.upper()))
    if own is None or own[1] == 1.0:
        return None
    compounds = sorted(
        formula for (name, formula), factor in _AS_FORMULA_TO_ELEMENTAL.items()
        if name == analyte and factor == 1.0 and formula != element
    )
    return compounds[0] if compounds else None


def _apply_as_basis(
    canonical_name: str, value: float, as_formula: str, unit: Optional[ConcentrationUnit] = None
) -> float:
    """Convert ``value`` expressed 'as as_formula' to the elemental/species
    basis for ``canonical_name``. Raises if the pair is unsupported.

    The factors are mass ratios. A molar amount (mmol/L, mol/L) is unchanged by
    its stated basis: every formula listed for an analyte carries one atom of
    its element (one mmol of PO4 is one mmol of P), so the mass ratio would
    misstate it (0.33x for 'as PO4')."""
    analyte = _table_analyte(canonical_name)
    element = analyte.split("(", 1)[0]
    found = _AS_FORMULA_INDEX.get((analyte, _normalize_as_formula(as_formula)))
    if found is None and _normalize_as_formula(as_formula) == element.upper() and canonical_basis_formula(analyte) is None:
        found = (element, 1.0)  # an analyte read as its element may always say so
    if found is None:
        raise ValueError(
            f"Unsupported 'as {as_formula}' qualifier for {canonical_name}. "
            f"Supported bases: {stated_bases(canonical_name) or [element]} "
            "or provide the elemental/species concentration directly."
        )
    if unit in _MOLAR_UNITS:
        return value
    return value * found[1]


def _element_name(symbol: str) -> Optional[str]:
    """The element's English name (``P`` -> ``phosphorus``), None for a symbol that is not an element."""
    import periodictable

    try:
        return str(periodictable.elements.symbol(symbol).name).lower()
    except ValueError:
        return None


def basis_ambiguous_analyte(name: str) -> Optional[str]:
    """The analyte a key names when a bare value under it does not say whose mass it is; None for a key that does.

    Two ways, both derived from the parser's basis table (`_AS_FORMULA_TO_ELEMENTAL`), never from a list of keys:
    - the key names a COMPOUND of its analyte (``PO4``, ``phosphate``, ``NH4``, ``ammonia``, ``SO4``, ``silica``,
      ``sulfide``): the analyte has a mass basis other than its element (PO4 for P, NH4 for N(-3), SO4 for S(6), SiO2
      for Si, H2S for S(-2), CaCO3 for Ca and Mg) and the key is not the analyte's own name, its element's symbol or
      its element's name (`COMPOUND_KEY_RULING_ID`);
    - the key names the analyte or its element, but a mass of it is read as a compound, not the element (``S(6)``,
      ``S``, ``Si``, ``silicon``: read as SO4 and SiO2, `INPUT_KEY_RULING_ID`; ``C(4)``, ``C``: HCO3 on phreeqc.dat, CO3
      on minteq.v4.dat, C on sit.dat, `WATERCHEM_KEYS_RULING_ID`; `canonical_basis_formula`).
    An analyte read as its element with no compound basis (``chloride`` -> Cl, ``fe2+`` -> Fe(2)) is never ambiguous.
    Ambiguity is a property of the key; the parser refuses the bare value in a MASS unit only (B1 of
    `WATERCHEM_KEYS_RULING_ID`: a molar amount has no mass basis). Refuses (ValueError) a key the parser does not
    accept, as `normalize_analyte_name` does."""
    canonical = normalize_analyte_name(name)
    if canonical == "Alkalinity":
        return None
    analyte = _table_analyte(canonical)
    element = analyte.split("(", 1)[0]
    key = name.strip().lower()
    if key in {canonical.lower(), analyte.lower(), element.lower(), _element_name(element)}:
        return analyte if canonical_basis_formula(analyte) else None
    compound_bases = [
        formula for name_, formula in _AS_FORMULA_TO_ELEMENTAL
        if name_ == analyte and formula != element
    ]
    return analyte if compound_bases else None


def stated_bases(canonical_name: str) -> list:
    """The formulas a value of ``canonical_name`` may be stated 'as' (`_AS_FORMULA_TO_ELEMENTAL`), as written there."""
    analyte = _table_analyte(canonical_name)
    return sorted({formula for name, formula in _AS_FORMULA_TO_ELEMENTAL if name == analyte})


#: The carbon rows a refusal also cites: C2 (the key C follows C(4)) and C5 (a bare mass of C(-4) is refused) of
#: `DATABASE_CHOICE_RULING_ID`. The key C is told C2 as well, since it is refused on the C(4) row.
_CARBON_RULINGS = {
    "C(4)": f"; C2 of {DATABASE_CHOICE_RULING_ID}: the key C follows C(4)",
    "C(-4)": f"; C5 of {DATABASE_CHOICE_RULING_ID}",
}


def _basis_refusal(key: str, analyte: str) -> str:
    """Why a bare MASS under ``key`` is refused, and how to state it (or give it in a molar unit)."""
    element = analyte.split("(", 1)[0]
    formulas = stated_bases(analyte)
    basis = canonical_basis_formula(analyte)
    molar = "or give it in mmol/L or mol/L, which has no mass basis to misread"
    carbon = _CARBON_RULINGS.get(_READ_ON_THE_ROW_OF.get(normalize_analyte_name(key), analyte), "")
    if basis is not None and str(key).strip().lower() in {analyte.lower(), element.lower(), _element_name(element),
                                                          normalize_analyte_name(key).lower()}:
        return (
            f"analysis key {key!r} is refused in a mass unit ({INPUT_KEY_RULING_ID}; {WATERCHEM_KEYS_RULING_ID}{carbon}): "
            f"phreeqc.dat reads a mass of {analyte} as {basis}, not as {element}, and other databases need not read it "
            f"alike, so a bare mass does not say which mass it is. State the basis: {{'value': x, 'as': '{basis}'}} or "
            f"'x as {element}' (formulas for {analyte}: {formulas}), {molar}."
        )
    return (
        f"analysis key {key!r} is refused in a mass unit ({COMPOUND_KEY_RULING_ID}, every tool since "
        f"{INPUT_KEY_RULING_ID}; {WATERCHEM_KEYS_RULING_ID}): it names a compound of {analyte}, so its mass does "
        f"not say whether it is a mass of the compound or of the element. Pass {analyte!r} on its own basis, or state "
        f"the basis: {{'value': x, 'as': '<formula>'}} or 'x as <formula>' (formulas for {analyte}: {formulas}), "
        f"{molar}."
    )


def _carbonate_refusal(key: str) -> str:
    formula = _CARBONATE_SPECIES_KEYS[str(key).strip().lower()]
    return (
        f"analysis key {key!r} is refused ({INPUT_KEY_RULING_ID}; C1 of {DATABASE_CHOICE_RULING_ID}: a meaning in a "
        f"molar unit too), ambiguous in every unit: a {formula} value is "
        "neither alkalinity nor total inorganic carbon until its meaning is stated (PHREEQC reads 'Alkalinity' as "
        "CaCO3, so a bare value had become 'mg/L as CaCO3', ~1.22x for HCO3 and ~1.67x for CO3). State it: "
        f"{{'value': x, 'as': 'alkalinity'}} (alkalinity expressed as {formula}; also 'alkalinity_as_HCO3', "
        "'alkalinity_as_CO3', 'alkalinity_as_CaCO3') maps to Alkalinity; "
        f"{{'value': x, 'as': 'species'}} (the free {CARBONATE_SPECIES[formula]} ion, ion pairs excluded, "
        f"{WATERCHEM_KEYS_RULING_ID}) is converted to C(4) at the input's pH on the run's own database (refused "
        "without a pH); a titration figure is 'alkalinity'. Or pass 'Alkalinity' (mg/L as CaCO3) or 'C(4)' with its "
        "basis ({'value': x, 'as': 'HCO3'}) directly."
    )


_ALK_AS_RE = re.compile(
    r"^\s*(?:(?P<prefix>as)\s+(?P<formula>[A-Za-z0-9()_]+)\s+)?(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"(?:\s+(?:as)\s+(?P<formula_suffix>[A-Za-z0-9()_]+))?\s*$"
)


def normalize_analyte_name(name: str) -> str:
    """Return the canonical analyte key for a user-supplied name."""

    key = name.strip()
    lowered = key.lower()
    if lowered in _AMBIGUOUS_CARBONATE_ALIASES:
        raise ValueError(_carbonate_refusal(name))
    canonical = ANALYTE_ALIASES.get(lowered)
    if canonical is not None:
        return canonical
    if key == "Alkalinity" or key in PHREEQC_MASTER_SPECIES:
        return key
    raise ValueError(
        f"Unrecognised analyte {name!r}. PHREEQC accepts element or redox-state "
        "names (Ca, Mg, S(6), N(-3)); a compound formula must be a known alias. "
        "Previously an unknown key was passed through and silently ignored by "
        "PHREEQC, returning a water missing that constituent with no warning. "
        f"Known aliases: {sorted(set(ANALYTE_ALIASES))}"
    )


def phreeqc_species_name(canonical_name: str) -> str:
    """Return the PHREEQC species name for a canonical analyte."""

    return PHREEQC_DEFAULT_SPECIES.get(canonical_name, canonical_name)


def _value_unit_and_stated(
    raw_value: Any, default_unit: ConcentrationUnit, raw_name: str = "value"
) -> Tuple[float, ConcentrationUnit, Optional[str]]:
    """A value's number, its unit and the basis it states ('as <formula>'), from a number, a mapping
    (``{"value": 184, "as": "PO4", "units": "mg/L"}``) or a string (``"184 as PO4"``, ``"as PO4 184"``)."""
    if isinstance(raw_value, bool):
        raise ValueError(f"Unsupported value type for {raw_name}: bool")
    if isinstance(raw_value, (int, float)):
        return float(raw_value), default_unit, None
    if isinstance(raw_value, Mapping):
        if raw_value.get("value") is None:
            raise ValueError(f"{raw_name} mapping requires a 'value' field")
        unit = ConcentrationUnit.normalize(raw_value.get("units", default_unit))
        stated = raw_value.get("as")
        return float(raw_value["value"]), unit, (str(stated) if stated else None)
    if isinstance(raw_value, str):
        match = _ALK_AS_RE.match(raw_value)
        if not match:
            return float(raw_value), default_unit, None
        stated = match.group("formula") or match.group("formula_suffix")
        return float(match.group("value")), default_unit, stated
    raise ValueError(f"Unsupported value type for {raw_name}: {type(raw_value).__name__}")


def _alkalinity_equivalents_per_l(value: float, unit: ConcentrationUnit, formula: str) -> float:
    """Equivalents per litre of an alkalinity stated as ``formula`` (HCO3, CO3 or CaCO3) in ``unit``."""
    if formula not in ALKALINITY_EQUIVALENTS_PER_MOL:
        raise ValueError(
            f"Alkalinity 'as {formula}' is not supported: state it as one of {sorted(ALKALINITY_EQUIVALENTS_PER_MOL)}"
        )
    per_mol = ALKALINITY_EQUIVALENTS_PER_MOL[formula]
    if unit == ConcentrationUnit.MG_L_AS_CACO3:
        return value / 1000.0 / FORMULA_MOLAR_MASS_G_MOL["CaCO3"] * ALKALINITY_EQUIVALENTS_PER_MOL["CaCO3"]
    if unit == ConcentrationUnit.MG_L:
        return value / 1000.0 / FORMULA_MOLAR_MASS_G_MOL[formula] * per_mol
    if unit == ConcentrationUnit.UG_L:
        return value / 1_000_000.0 / FORMULA_MOLAR_MASS_G_MOL[formula] * per_mol
    if unit == ConcentrationUnit.MMOL_L:
        return value / 1000.0 * per_mol
    return value * per_mol


def _alkalinity_amount(eq_per_l: float, unit: ConcentrationUnit) -> Tuple[float, ConcentrationUnit, Optional[str]]:
    """An alkalinity of ``eq_per_l`` as the canonical analyte holds it: mg/L as CaCO3 for a mass unit, equivalents in
    the molar unit (PHREEQC reads a molar Alkalinity as equivalents)."""
    if unit == ConcentrationUnit.MMOL_L:
        return eq_per_l * 1000.0, unit, None
    if unit == ConcentrationUnit.MOL_L:
        return eq_per_l, unit, None
    caco3_per_eq = FORMULA_MOLAR_MASS_G_MOL["CaCO3"] / ALKALINITY_EQUIVALENTS_PER_MOL["CaCO3"]
    return eq_per_l * caco3_per_eq * 1000.0, ConcentrationUnit.MG_L_AS_CACO3, "CaCO3"


def _parse_alkalinity(value: Any, default_unit: ConcentrationUnit) -> Tuple[float, ConcentrationUnit, Optional[str]]:
    """Alkalinity as mg/L as CaCO3 (a mass unit) or as equivalents (a molar unit).

    A bare number in a mass unit is mg/L as CaCO3 (the house basis); in a molar unit it is equivalents, as PHREEQC reads
    it. Before, a bare number was taken as mg/L as CaCO3 whatever the solution's unit and then written as mmol of CaCO3,
    so ``Alkalinity: 2`` in a mmol/L solution reached PHREEQC as 0.02 meq (measured). A stated formula (HCO3, CO3, CaCO3) is
    converted by its equivalents here: PHREEQC's own 'as CO3' counts one equivalent per mole."""
    number, unit, stated = _value_unit_and_stated(value, default_unit, "Alkalinity")
    if unit == ConcentrationUnit.MG_L_AS_CACO3:
        if stated and _normalize_as_formula(stated) != "CACO3":
            raise ValueError(f"Alkalinity in mg/L as CaCO3 cannot also be stated 'as {stated}'")
        formula = "CaCO3"
    elif stated:
        formula = next(
            (f for f in ALKALINITY_EQUIVALENTS_PER_MOL if _normalize_as_formula(f) == _normalize_as_formula(stated)),
            stated,
        )
    elif unit in _MOLAR_UNITS:
        return number, unit, None
    else:
        formula = "CaCO3"
    return _alkalinity_amount(_alkalinity_equivalents_per_l(number, unit, formula), unit)


def _carbonate_meaning(stated: str, key_formula: str) -> Tuple[str, Optional[str]]:
    """('alkalinity', the formula it is expressed as) or ('species', None) for a carbonate key's stated meaning."""
    token = re.sub(r"[\s\-]+", "_", str(stated).strip().lower())
    if token == "species":
        return "species", None
    match = re.fullmatch(r"alkalinity(?:_as_(?P<formula>hco3|co3|caco3))?", token)
    if match is None:
        raise ValueError
    return "alkalinity", {"hco3": "HCO3", "co3": "CO3", "caco3": "CaCO3"}.get(match.group("formula") or "", key_formula)


def _parse_carbonate(raw_name: str, raw_value: Any, default_unit: ConcentrationUnit) -> AnalyteAmount:
    """A carbonate-species key with its meaning stated (`INPUT_KEY_RULING_ID`): 'as alkalinity' maps to Alkalinity;
    'as species' (the free ion only, ion pairs excluded: `WATERCHEM_KEYS_RULING_ID`, B3) to C(4), resolved to the
    total at run time on the run's own database (`SPECIES_MARKER`, `waterchem.carbonate_species`)."""
    formula = _CARBONATE_SPECIES_KEYS[str(raw_name).strip().lower()]
    value, unit, stated = _value_unit_and_stated(raw_value, default_unit, raw_name)
    if not stated:
        raise ValueError(_carbonate_refusal(raw_name))
    try:
        meaning, expressed_as = _carbonate_meaning(stated, formula)
    except ValueError:
        raise ValueError(_carbonate_refusal(raw_name)) from None
    if meaning == "alkalinity":
        if unit == ConcentrationUnit.MG_L_AS_CACO3:
            expressed_as = "CaCO3"
        number, alk_unit, as_formula = _alkalinity_amount(_alkalinity_equivalents_per_l(value, unit, expressed_as), unit)
        return AnalyteAmount(
            name="Alkalinity", value=number, unit=alk_unit, raw_name=raw_name, raw_value=raw_value,
            as_formula=as_formula, phreeqc_name="Alkalinity",
        )
    if unit not in _MASS_UNITS | _MOLAR_UNITS:
        raise ValueError(f"analysis key {raw_name!r} as species must be in mg/L, ug/L, mmol/L or mol/L")
    return AnalyteAmount(
        name="C(4)", value=value, unit=unit, raw_name=raw_name, raw_value=raw_value, as_formula=formula,
        phreeqc_name="C(4)", species_basis=CARBONATE_SPECIES[formula],
    )


_SETTLED_SPECIES_RE = re.compile(
    r"^\s*(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+as\s+(?P<formula>\S+)\s+"
    + re.escape(SPECIES_MARKER) + r"(?P<species>\S+)\s*$"
)


def _parse_settled_species(raw_name: str, raw_value: str, default_unit: ConcentrationUnit) -> AnalyteAmount:
    """A species amount as `settled_analysis` writes it (``"600.0 as HCO3 unresolved_species_basis:HCO3-"`` under
    C(4)), read back by a caller that validates the settled analysis again (the phosphorus screen does)."""
    match = _SETTLED_SPECIES_RE.match(raw_value)
    name = normalize_analyte_name(raw_name)
    formula = match.group("formula") if match else None
    if match is None or name != "C(4)" or CARBONATE_SPECIES.get(formula) != match.group("species"):
        raise ValueError(f"analysis key {raw_name!r}: {raw_value!r} is not a species amount this parser wrote")
    return AnalyteAmount(
        name=name, value=float(match.group("value")), unit=default_unit, raw_name=raw_name, raw_value=raw_value,
        as_formula=formula, phreeqc_name=name, species_basis=match.group("species"),
    )


def _parse_analyte(
    raw_name: str,
    raw_value: Any,
    default_unit: ConcentrationUnit,
) -> AnalyteAmount:
    if str(raw_name).strip().lower() in _CARBONATE_SPECIES_KEYS:
        return _parse_carbonate(raw_name, raw_value, default_unit)
    if isinstance(raw_value, str) and SPECIES_MARKER in raw_value:
        return _parse_settled_species(raw_name, raw_value, default_unit)
    name = normalize_analyte_name(raw_name)
    if name == "Alkalinity":
        value, unit, as_formula = _parse_alkalinity(raw_value, default_unit)
        return AnalyteAmount(
            name=name, value=value, unit=unit, raw_name=raw_name, raw_value=raw_value, as_formula=as_formula,
            phreeqc_name=phreeqc_species_name(name),
        )
    value, unit, stated = _value_unit_and_stated(raw_value, default_unit, raw_name)
    if unit == ConcentrationUnit.MG_L_AS_CACO3:
        stated = None  # the unit states the basis (as CaCO3); the unit registry converts it
    elif unit in _MASS_UNITS:
        # a bare MASS whose key does not say whose mass it is; a molar amount has none to misread
        # (`WATERCHEM_KEYS_RULING_ID`, B1), and is taken as the analyte's moles
        ambiguous = basis_ambiguous_analyte(raw_name)
        if stated is None and ambiguous is not None:
            raise ValueError(_basis_refusal(raw_name, ambiguous))
    if stated:
        # Convert the "as <compound>" basis to the elemental/species basis
        # (e.g. mg/L as NO3 -> N(5), as S -> S(6) as SO4).
        value = _apply_as_basis(name, value, stated, unit)
    return AnalyteAmount(
        name=name,
        value=value,
        unit=unit,
        raw_name=raw_name,
        raw_value=raw_value,
        # a mass of S(6), Si or C(4) is a mass of SO4, SiO2 or HCO3, in whatever unit the value came: converted on that
        # formula and written with it to a mass-unit SOLUTION, never left to the database
        as_formula=canonical_basis_formula(name),
        phreeqc_name=phreeqc_species_name(name),
    )


def _identity(analyte: AnalyteAmount) -> str:
    """The inventory an analyte fills: one key per inventory (``S`` and ``S(6)`` are one; ``calcium`` and ``Ca``; ``C``
    and ``C(4)``)."""
    return phreeqc_species_name(_READ_ON_THE_ROW_OF.get(analyte.name, analyte.name))


def _one_value(setting: str, given) -> None:
    'Public engineering calculation interface.'
    values = {}
    for key, value in given:
        try:
            values[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{setting} given as {key}={value!r} is not a number") from None
    if len(set(values.values())) > 1:
        raise ValueError(
            f'two values for one {setting}: {values}; the input is read once, so give one (review M19, PUBLIC-RULE-8226e460e1d8)'
        )


def canonicalize_water_analysis(input_data: Mapping[str, Any]) -> CanonicalWaterAnalysis:
    """Canonicalize user water-analysis input for tool use.

    Refuses (ValueError) every key whose bare value in a mass unit does not say whose mass it is
    (`basis_ambiguous_analyte`; a molar value is the analyte's moles, `WATERCHEM_KEYS_RULING_ID`), a carbonate species
    without its meaning, a species amount without a pH it can be converted at, and two keys naming one inventory
    (`INPUT_KEY_RULING_ID`; before, the later key silently won)."""

    data: MutableMapping[str, Any] = dict(input_data)
    analysis = dict(data.get("analysis") or {})

    # one value per setting (review M19): a second value that disagrees had been dropped silently
    _one_value("pH", [(key, data[key]) for key in ("ph", "pH") if data.get(key) is not None]
               + [(f"analysis.{key}", analysis[key]) for key in ("ph", "pH") if analysis.get(key) is not None])
    _one_value("temperature", [(key, data[key]) for key in
                               ("temperature_celsius", "temperature", "temp", "Temperature", "Temp")
                               if data.get(key) is not None])
    if data.get("redox") and data.get("pe") is not None and float(data["pe"]) != 4.0:
        raise ValueError(
            f'the input gives pe {data['pe']} and the redox couple {data['redox']!r}: the solution is written on the redox couple and the pe would be dropped; give one (review M19, PUBLIC-RULE-8226e460e1d8)'
        )
    ph = data.get("ph", data.get("pH"))
    for key in ("ph", "pH"):
        if key in analysis and ph is None:
            ph = analysis.pop(key)
        elif key in analysis:
            analysis.pop(key)

    if "temperature_celsius" not in data:
        for alias in ("temperature", "temp", "Temperature", "Temp"):
            if alias in data:
                data["temperature_celsius"] = data[alias]
                break

    units = ConcentrationUnit.normalize(data.get("units", ConcentrationUnit.MG_L))
    analytes = {}
    named: Dict[str, str] = {}
    for raw_name, raw_value in analysis.items():
        analyte = _parse_analyte(raw_name, raw_value, units)
        identity = _identity(analyte)
        if identity in named:
            raise ValueError(
                f"analysis keys {named[identity]!r} and {raw_name!r} both name {identity}: one inventory, one key "
                f"({INPUT_KEY_RULING_ID})"
            )
        named[identity] = str(raw_name)
        analytes[analyte.name] = analyte

    charge_balance = data.get("charge_balance")
    charge_on_ph = bool(charge_balance) and str(charge_balance).strip().lower() == "ph"
    for analyte in analytes.values():
        if analyte.species_basis is None:
            continue
        if ph is None or charge_on_ph:
            raise ValueError(
                f"analysis key {analyte.raw_name!r} is a {analyte.species_basis} species amount: it is converted to C(4) "
                f"at the input's pH, and this input {'balances charge on pH' if ph is not None else 'gives no pH'}. "
                f"Give the measured pH ({INPUT_KEY_RULING_ID})."
            )
        if charge_balance and not charge_on_ph and str(charge_balance).strip() in {
            "C(4)", "C", str(analyte.raw_name)
        }:
            raise ValueError(
                f"charge_balance on {charge_balance!r} would move the C(4) a {analyte.species_basis} species amount fixes "
                f"({INPUT_KEY_RULING_ID}): balance on another analyte"
            )
    if charge_balance and not charge_on_ph:
        charge_target = normalize_analyte_name(str(charge_balance))
        has_charge_analyte = any(
            charge_target == analyte.name
            or str(charge_balance) == (analyte.phreeqc_name or analyte.name)
            or str(charge_balance) == analyte.raw_name
            for analyte in analytes.values()
        )
        if not has_charge_analyte:
            raise ValueError(
                "charge_balance must be 'pH' or match an analyte with a concentration "
                f"(missing concentration for {charge_balance})"
            )

    from .basis_defaults import record_default

    if ph is None and not charge_on_ph:
        record_default("pH", 7.0)
    if data.get("pe") is None and not data.get("redox"):
        record_default("pe", 4.0)
    return CanonicalWaterAnalysis(
        analytes=analytes,
        ph=float(ph) if ph is not None else None,
        pe=float(data.get("pe", 4.0)) if data.get("pe", 4.0) is not None else None,
        temperature_celsius=float(data.get("temperature_celsius", 25.0)),
        pressure_atm=float(data.get("pressure_atm", 1.0)),
        units=units,
        charge_balance=charge_balance,
        density=data.get("density"),
        redox=data.get("redox"),
        allow_extreme_ph=bool(data.get("allow_extreme_ph", False)),
    )


def settled_analysis(solution: Mapping[str, Any]) -> Dict[str, Any]:
    """``solution`` with every analysis value settled for a raw SOLUTION writer
    (`waterchem.phreeqc_input.build_solution_block`), refusing as `canonicalize_water_analysis` does.

    Keys become the analytes' canonical names. An element-basis value, and any value in a molar solution, becomes a
    plain number in the solution's units; a value of an analyte read as a compound (S(6), Si, C(4)) in a mass unit
    becomes ``"x as SO4"`` / ``"x as SiO2"`` / ``"x as HCO3"``, which every database reads alike; Alkalinity ``"x as
    CaCO3"`` (mass) or equivalents (molar); a carbonate species its `SPECIES_MARKER` line, resolved on the run's
    database. A pH given inside the analysis stays where it was. A value
    stated in other units than the solution's is refused: state it in the solution's units."""
    if not isinstance(solution, Mapping) or not isinstance(solution.get("analysis"), Mapping):
        return dict(solution) if isinstance(solution, Mapping) else solution
    water = canonicalize_water_analysis(solution)
    units = water.units
    settled: Dict[str, Any] = {
        key: value for key, value in solution["analysis"].items() if str(key).strip().lower() == "ph"
    }
    for analyte in water.analytes.values():
        if analyte.name == "Alkalinity":
            value = analyte.value if analyte.unit == units else analyte_value_as(analyte, units)
            settled["Alkalinity"] = f"{value!r} as CaCO3" if units in _MASS_UNITS else value
            continue
        if analyte.unit != units:
            raise ValueError(
                f"analysis key {analyte.raw_name!r} is in {analyte.unit.value}, the solution in {units.value}: state it "
                "in the solution's units"
            )
        if analyte.species_basis:
            settled[analyte.name] = f"{analyte.value!r} as {analyte.as_formula} {SPECIES_MARKER}{analyte.species_basis}"
        elif analyte.as_formula and units in _MASS_UNITS:
            settled[analyte.name] = f"{analyte.value!r} as {analyte.as_formula}"
        else:
            settled[analyte.name] = analyte.value
    return {**solution, "analysis": settled}


def _full(value: Any) -> str:
    """A number as PHREEQC reads it, at full precision: the shortest text that reads back as the same float (repr), an
    integral value without its ".0" (as ``:g`` wrote it)."""
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def build_solution_block_from_canonical(
    water: CanonicalWaterAnalysis,
    *,
    solution_num: int = 1,
) -> str:
    """Build a PHREEQC SOLUTION block from canonical water analysis, every number at full precision (review M19: the
    ``:g`` format had rounded them to six significant figures)."""

    lines = [f"SOLUTION {solution_num}"]
    lines.append(f"    temp      {_full(water.temperature_celsius)}")
    lines.append(f"    pressure  {_full(water.pressure_atm)}")
    lines.append(f"    units     {water.units.value}")

    if water.density is not None:
        lines.append(f"    density   {_full(water.density)}")

    charge_balance = str(water.charge_balance).strip() if water.charge_balance else None
    charge_on_ph = bool(charge_balance and charge_balance.lower() == "ph")
    charge_target = None if (not charge_balance or charge_on_ph) else normalize_analyte_name(charge_balance)

    if charge_balance:
        ph_suffix = " charge" if charge_on_ph else ""
        lines.append(f"    pH        {_full(water.ph if water.ph is not None else 7.0)}{ph_suffix}")
        lines.append(f"    pe        {_full(water.pe if water.pe is not None else 4.0)}")
    elif water.redox:
        lines.append(f"    pH        {_full(water.ph if water.ph is not None else 7.0)}")
        lines.append(f"    redox     {water.redox}")
    else:
        lines.append(f"    pH        {_full(water.ph if water.ph is not None else 7.0)}")
        lines.append(f"    pe        {_full(water.pe if water.pe is not None else 4.0)}")

    mass_solution = water.units in _MASS_UNITS
    for analyte in water.analytes.values():
        phreeqc_name = analyte.phreeqc_name or analyte.name
        value = analyte.value
        if analyte.unit != water.units:
            value = convert_concentration(
                analyte.value,
                analyte.name,
                analyte.unit,
                water.units,
                as_formula=analyte.as_formula,
            )
        charge_suffix = ""
        if charge_target:
            if charge_target == analyte.name or charge_balance == phreeqc_name or charge_balance == analyte.raw_name:
                charge_suffix = " charge"

        if analyte.species_basis:
            # a carbonate species amount: the run resolves it to the C(4) total on its own database
            # (waterchem.carbonate_species); PHREEQC refuses the line unresolved
            lines.append(
                f"    {phreeqc_name:<14}{value!r} as {analyte.as_formula} {SPECIES_MARKER}{analyte.species_basis}"
            )
        elif analyte.as_formula and (mass_solution or water.units == ConcentrationUnit.MG_L_AS_CACO3):
            lines.append(f"    {phreeqc_name:<14}{_full(value)} as {analyte.as_formula}{charge_suffix}")
        else:
            lines.append(f"    {phreeqc_name:<14}{_full(value)}{charge_suffix}")

    return "\n".join(lines) + "\n"


def analyte_value_as(
    analyte: AnalyteAmount,
    target_unit: ConcentrationUnit | str,
) -> float:
    """Return an analyte concentration converted through the unit registry."""

    return convert_concentration(
        analyte.value,
        analyte.name,
        analyte.unit,
        target_unit,
        as_formula=analyte.as_formula,
    )
