"""Unit conversion helpers for canonical water analyses."""

from __future__ import annotations

from typing import Dict, Optional

from water_chemistry_mcp.utils.constants import MOLAR_MASS

from .contracts.common import ConcentrationUnit
from .formulas import normalize_formula_name


ELEMENT_MOLAR_MASS_G_MOL: Dict[str, float] = {
    "Al": 26.9815,
    "As": 74.9216,
    "B": 10.81,
    "Ba": 137.327,
    "Br": 79.904,
    "C": 12.011,
    "Ca": 40.078,
    "Cl": 35.453,
    "Cr": 51.9961,
    "Cu": 63.546,
    "F": 18.9984,
    "Fe": 55.845,
    "I": 126.9045,
    "K": 39.0983,
    "Li": 6.94,
    "Mg": 24.305,
    "Mn": 54.938,
    "N": 14.0067,
    "Na": 22.9898,
    "P": 30.9738,
    "S": 32.065,
    "Si": 28.0855,
    "Sr": 87.62,
    "Zn": 65.38,
}

FORMULA_MOLAR_MASS_G_MOL: Dict[str, float] = {
    **MOLAR_MASS,
    "Al(OH)3": 78.0036,
    "Al2(SO4)3": 342.15,
    "Al2(SO4)3·14H2O": 594.36,
    "Al2(SO4)3·18H2O": 666.43,
    "CaCO3": MOLAR_MASS.get("CaCO3", 100.0869),
    "Ca(OH)2": 74.0927,
    "Ca(OCl)2": 142.982,
    "Fe2(SO4)3": 399.88,
    "FeCl3": 162.20,
    "FeCl3·6H2O": 270.30,
    "FeSO4": 151.91,
    "FeSO4·7H2O": 278.01,
    "Fe(OH)2": 89.8597,
    "Fe(OH)3": 106.867,
    "KAl(SO4)2": 258.21,
    "KAl(SO4)2·12H2O": 474.39,
    "Mg(OH)2": 58.3197,
    "NaOCl": 74.4422,
    "SO4": 96.06,
    "HCO3": 61.017,
    "CO3": 60.009,
    # the compounds an analysis value may be stated 'as' (waterchem.analysis._AS_FORMULA_TO_ELEMENTAL)
    "SiO2": 60.0843,
    "PO4": 94.971,
    "NH4": 18.039,
    "NH3": 17.031,
    "NO3": 62.004,
    "NO2": 46.005,
    "H2S": 34.076,
    "CH4": 16.0425,
}

#: Equivalents of alkalinity per mole of the formula it is expressed as (to the CO2 end point). A molar Alkalinity is
#: equivalents, as PHREEQC reads it.
_ALKALINITY_EQ_PER_MOL: Dict[str, int] = {"CaCO3": 2, "HCO3": 1, "CO3": 2}


def base_element(analyte: str) -> str:
    """Return the base element from PHREEQC valence notation."""

    text = analyte.strip()
    if "(" in text:
        text = text.split("(", 1)[0]
    return text


def molar_mass_for(analyte: str, as_formula: Optional[str] = None) -> float:
    """Return a molar mass for an element/species or formula."""

    if as_formula:
        formula = normalize_formula_name(as_formula)
        if formula not in FORMULA_MOLAR_MASS_G_MOL:
            raise ValueError(f"No molar mass registered for formula {as_formula}")
        return FORMULA_MOLAR_MASS_G_MOL[formula]

    normalized = normalize_formula_name(analyte)
    if normalized in FORMULA_MOLAR_MASS_G_MOL:
        return FORMULA_MOLAR_MASS_G_MOL[normalized]
    key = base_element(normalized)
    if key in ELEMENT_MOLAR_MASS_G_MOL:
        return ELEMENT_MOLAR_MASS_G_MOL[key]
    raise ValueError(f"No molar mass registered for analyte {analyte}")


def convert_concentration(
    value: float,
    analyte: str,
    from_unit: ConcentrationUnit | str,
    to_unit: ConcentrationUnit | str,
    *,
    as_formula: Optional[str] = None,
) -> float:
    """Convert a concentration value between supported units."""

    src = ConcentrationUnit.normalize(from_unit)
    dst = ConcentrationUnit.normalize(to_unit)
    if src == dst:
        return float(value)
    if analyte == "Alkalinity":
        return _convert_alkalinity(float(value), src, dst, as_formula)

    if src == ConcentrationUnit.MG_L_AS_CACO3:
        mol_l = float(value) / 1000.0 / molar_mass_for("CaCO3")
    elif src == ConcentrationUnit.MG_L:
        mol_l = float(value) / 1000.0 / molar_mass_for(analyte, as_formula=as_formula)
    elif src == ConcentrationUnit.UG_L:
        mol_l = float(value) / 1_000_000.0 / molar_mass_for(analyte, as_formula=as_formula)
    elif src == ConcentrationUnit.MMOL_L:
        mol_l = float(value) / 1000.0
    elif src == ConcentrationUnit.MOL_L:
        mol_l = float(value)
    else:  # pragma: no cover - enum guards this
        raise ValueError(f"Unsupported source unit: {src}")

    if dst == ConcentrationUnit.MOL_L:
        return mol_l
    if dst == ConcentrationUnit.MMOL_L:
        return mol_l * 1000.0
    if dst == ConcentrationUnit.MG_L:
        return mol_l * molar_mass_for(analyte, as_formula=as_formula) * 1000.0
    if dst == ConcentrationUnit.UG_L:
        return mol_l * molar_mass_for(analyte, as_formula=as_formula) * 1_000_000.0
    if dst == ConcentrationUnit.MG_L_AS_CACO3:
        return mol_l * molar_mass_for("CaCO3") * 1000.0
    raise ValueError(f"Unsupported target unit: {dst}")


def _convert_alkalinity(
    value: float, src: ConcentrationUnit, dst: ConcentrationUnit, as_formula: Optional[str]
) -> float:
    """Alkalinity between units through EQUIVALENTS: a molar alkalinity is equivalents (PHREEQC's reading), a mass is
    of its formula (CaCO3 unless stated). Through moles of CaCO3 instead, 100 mg/L as CaCO3 became 0.999 'mmol/L',
    which PHREEQC reads as 0.999 meq: half of the 2.0 meq it is."""
    formula = normalize_formula_name(as_formula) if as_formula else "CaCO3"
    if formula not in _ALKALINITY_EQ_PER_MOL:
        raise ValueError(f"Alkalinity 'as {as_formula}' is not one of {sorted(_ALKALINITY_EQ_PER_MOL)}")

    def per_eq(fmt: str) -> float:
        return FORMULA_MOLAR_MASS_G_MOL[fmt] / _ALKALINITY_EQ_PER_MOL[fmt]

    if src == ConcentrationUnit.MG_L_AS_CACO3:
        eq_l = value / 1000.0 / per_eq("CaCO3")
    elif src == ConcentrationUnit.MG_L:
        eq_l = value / 1000.0 / per_eq(formula)
    elif src == ConcentrationUnit.UG_L:
        eq_l = value / 1_000_000.0 / per_eq(formula)
    elif src == ConcentrationUnit.MMOL_L:
        eq_l = value / 1000.0
    else:
        eq_l = value
    if dst == ConcentrationUnit.MOL_L:
        return eq_l
    if dst == ConcentrationUnit.MMOL_L:
        return eq_l * 1000.0
    if dst == ConcentrationUnit.MG_L_AS_CACO3:
        return eq_l * per_eq("CaCO3") * 1000.0
    if dst == ConcentrationUnit.MG_L:
        return eq_l * per_eq(formula) * 1000.0
    return eq_l * per_eq(formula) * 1_000_000.0


def alkalinity_as_caco3_to_equivalents_per_l(value_mg_l: float) -> float:
    """Convert alkalinity in mg/L as CaCO3 to equivalents per liter."""

    return float(value_mg_l) / 50044.0


def equivalents_per_l_to_alkalinity_as_caco3(eq_l: float) -> float:
    """Convert equivalents per liter to alkalinity in mg/L as CaCO3."""

    return float(eq_l) * 50044.0
