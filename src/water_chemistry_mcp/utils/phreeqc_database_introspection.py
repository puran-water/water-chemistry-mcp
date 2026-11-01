"""Read what a PHREEQC database actually declares, by parsing its keyword blocks.

Substring counting over the raw file text does not work and produced three wrong
database selections: every one of phreeqc.dat, pitzer.dat and Amm.dat "contains
PITZER" because the word appears in their comments, seven of eight databases
"support surface complexation" for the same reason, and `content.count("PHASES")`
returns 1 or 2 for every database whether it declares 71 phases or 636.

The one that matters most for design work is the INPUT BASIS. Column 4 of
SOLUTION_MASTER_SPECIES is the gram formula weight a mg/L input is divided by, and
databases disagree:

    phreeqc.dat   Si  H4SiO4  0  SiO2      28.0843   -> mg/L read as SiO2   (60.08)
    minteq.dat    Si  H4SiO4  0  96.1155   28.0843   -> mg/L read as H4SiO4 (96.12)

The same "Si: 50" is 0.832 mmol/L in one and 0.520 mmol/L in the other, a 37.5%
difference in silica with no error raised. Alkalinity disagrees the same way
(as CaCO3 vs as HCO3). Callers must be able to see which basis they got.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

#: PHREEQC's own block keywords. This must be an explicit SET, never a pattern
#: like ^[A-Z][A-Z0-9_]*$: real phase names are spelled that way too, so a
#: pattern reads U3O8, UF4, UO3, V2O5, VO and VOSO4 in minteq.v4.dat as block
#: headers and truncates PHASES from 636 phases to 180, losing Strengite,
#: Vivianite and Hydroxylapatite.
BLOCK_KEYWORDS = frozenset({
    "SOLUTION_MASTER_SPECIES", "SOLUTION_SPECIES", "PHASES", "EXCHANGE_MASTER_SPECIES",
    "EXCHANGE_SPECIES", "SURFACE_MASTER_SPECIES", "SURFACE_SPECIES", "RATES", "END",
    "PITZER", "SIT", "LLNL_AQUEOUS_MODEL_PARAMETERS", "NAMED_EXPRESSIONS", "ISOTOPES",
    "ISOTOPE_ALPHAS", "ISOTOPE_RATIOS", "CALCULATE_VALUES", "SPECIES", "MASTER_SPECIES",
    "SELECTED_OUTPUT", "MEAN_GAMMAS", "RATE_PARAMETERS_PK", "RATE_PARAMETERS_SVD",
    "RATE_PARAMETERS_HERMANSKA",
})

# Elements whose input formula differs between shipped databases. Reporting the
# basis for these is the difference between a right and a wrong silica number.
BASIS_SENSITIVE_ELEMENTS = ("Si", "Alkalinity", "S(6)", "C(4)", "N", "P", "Fe")


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def read_blocks(content: str) -> Dict[str, List[str]]:
    """Split a database into its top-level keyword blocks."""
    blocks: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in content.splitlines():
        line = _strip_comment(raw)
        if not line:
            continue
        token = line.split()[0].upper()
        if token in BLOCK_KEYWORDS and not raw.startswith((" ", "\t")):
            current = token
            blocks.setdefault(current, [])
            continue
        if current is not None:
            blocks[current].append(line)
    return blocks


def parse_master_species(content: str) -> Dict[str, Dict[str, Any]]:
    """Map element -> {master_species, alkalinity_contribution, input_formula, gfw}.

    `input_formula` is column 4 verbatim: a chemical formula (phreeqc.dat writes
    `SiO2`) or a bare gram formula weight (minteq.dat writes `96.1155`). Both mean
    the same thing to PHREEQC -- the weight a mg/L value is divided by.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for line in read_blocks(content).get("SOLUTION_MASTER_SPECIES", []):
        parts = line.split()
        if len(parts) < 4:
            continue
        element, master, alk, formula = parts[0], parts[1], parts[2], parts[3]
        try:
            alk_value = float(alk)
        except ValueError:
            continue
        entry: Dict[str, Any] = {
            "master_species": master,
            "alkalinity_contribution": alk_value,
            "input_formula": formula,
        }
        try:
            entry["input_gfw"] = float(formula)
            entry["input_basis"] = "gram formula weight"
        except ValueError:
            entry["input_gfw"] = None
            entry["input_basis"] = f"as {formula}"
        if len(parts) >= 5:
            try:
                entry["element_gfw"] = float(parts[4])
            except ValueError:
                pass
        out[element] = entry
    return out


def parse_phase_names(content: str) -> List[str]:
    """Phase names declared in PHASES: a name at column 0 inside the block."""
    names: List[str] = []
    for line in read_blocks(content).get("PHASES", []):
        if line[:1].isspace():
            continue
        name = line.split()[0]
        # A phase name is followed by its dissolution reaction on later lines;
        # the name line itself carries no '=' and no leading whitespace.
        if "=" not in line:
            names.append(name)
    return names


def describe_database(path: str) -> Dict[str, Any]:
    """Declared capability of one database, from its blocks rather than its prose."""
    with open(path, "r", errors="ignore") as handle:
        content = handle.read()
    blocks = read_blocks(content)
    master = parse_master_species(content)
    phases = parse_phase_names(content)
    return {
        "name": os.path.basename(path),
        "path": path,
        "blocks": sorted(blocks),
        "features": {
            "pitzer": "PITZER" in blocks,
            "sit": "SIT" in blocks,
            "surface_complexation": "SURFACE_MASTER_SPECIES" in blocks,
            "exchange": "EXCHANGE_MASTER_SPECIES" in blocks,
            "isotopes": "ISOTOPES" in blocks or "ISOTOPE_RATIOS" in blocks,
            "kinetics": "RATES" in blocks,
            "solids": bool(phases),
        },
        "element_count": len(master),
        "mineral_count": len(phases),
        "elements": sorted(master),
        "master_species": master,
    }


def element_input_basis(path: str, analyte_names, stated_bases: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """State the basis this database applies to each supplied analyte.

    Returned on every result so a caller can see, without reading the .dat file,
    that their 50 mg/L of Si was taken as SiO2 and not as H4SiO4. An analyte whose
    SOLUTION line states its formula (``Si 60.08 as SiO2``, ``stated_bases``) is read
    on that formula by every database, and is reported so.
    """
    master = describe_database(path)["master_species"]
    basis: Dict[str, Any] = {}
    unrecognised: List[str] = []
    stated_bases = stated_bases or {}
    for name in analyte_names:
        if name in stated_bases:
            entry = master.get(name) or master.get(name.split("(", 1)[0]) or {}
            basis[name] = {
                "input_basis": f"as {stated_bases[name]} (stated on the input line; the database's own basis is not used)",
                "input_gfw": None,
                "master_species": entry.get("master_species"),
            }
            continue
        entry = master.get(name)
        if entry is None:
            # "Ca" resolves directly; "S(6)" may be declared as its redox parent.
            parent = name.split("(", 1)[0]
            entry = master.get(parent)
            if entry is None:
                unrecognised.append(name)
                continue
        basis[name] = {
            "input_basis": entry["input_basis"],
            "input_gfw": entry["input_gfw"],
            "master_species": entry["master_species"],
        }
    result: Dict[str, Any] = {"database": os.path.basename(path), "analytes": basis}
    if unrecognised:
        result["unrecognised"] = unrecognised

    # Say so when a lab number was read against something other than the basis
    # the lab reported it in. Silence here is what made a 37.5% silica error
    # look like a converged answer.
    differs = {}
    for name, entry in basis.items():
        if name in stated_bases:
            continue
        house_gfw = HOUSE_INPUT_GFW.get(name)
        db_gfw = entry["input_gfw"]
        if db_gfw is None:
            db_gfw = _FORMULA_GFW.get(str(entry["input_basis"]).removeprefix("as ").strip())
        if house_gfw is None or db_gfw is None or abs(db_gfw - house_gfw) < 1e-6:
            continue
        differs[name] = {
            "house_basis_gfw": house_gfw,
            "database_basis_gfw": db_gfw,
            "supplied_value_means": f"{house_gfw / db_gfw:.4f} x what this database read",
        }
    if differs:
        result["differs_from_house_basis"] = differs
        result["warning"] = (
            "Supplied mg/L was read against this database's basis, which is NOT the "
            "house lab basis for the analytes listed. Convert with "
            "utils.phreeqc_database_introspection.to_database_basis(), or pass a "
            "database that uses the lab basis (phreeqc.dat)."
        )
    return result


#: The HOUSE input basis, which is the basis a water-analysis LAB reports against
#: and therefore the basis every project characterisation arrives in. It is
#: phreeqc.dat's convention, which is why phreeqc.dat is the house default
#: (ruled by Hersh 2026-09-09). Values are the gram formula weight a mg/L number
#: is divided by to get moles.
HOUSE_INPUT_GFW = {
    "Si": 60.0843,        # as SiO2
    "Alkalinity": 50.045,  # as CaCO3
    "S(6)": 96.0616,      # as SO4
    "C": 61.0171,         # as HCO3
    "C(4)": 61.0171,      # as HCO3
    "C(-4)": 16.042,      
    "N": 14.0067,
    "P": 30.9738,
}


def to_database_basis(analytes: Dict[str, float], database_path: str) -> Dict[str, Any]:
    """Restate lab-basis mg/L values in the basis the given database reads.

    Moles are what the chemistry depends on, so a value is rescaled by the ratio
    of the two gram formula weights: 50 mg/L SiO2 is 0.832 mmol/L, which minteq.dat
    only receives correctly as 80.0 mg/L because it divides by H4SiO4 (96.12).
    Pass a database that already uses the house basis and the values come back
    unchanged.

    Returns {"analytes": <converted>, "conversions": <what changed and why>}.
    """
    master = describe_database(database_path)["master_species"]
    converted: Dict[str, float] = {}
    conversions: Dict[str, Any] = {}
    for name, value in analytes.items():
        house_gfw = HOUSE_INPUT_GFW.get(name)
        entry = master.get(name) or master.get(name.split("(", 1)[0])
        db_gfw = (entry or {}).get("input_gfw")
        if db_gfw is None and entry is not None:
            # The database names a formula rather than a weight. Resolve the ones
            # the house basis covers; leave anything else alone rather than guess.
            db_gfw = _FORMULA_GFW.get((entry.get("input_formula") or "").strip())
        if house_gfw is None or db_gfw is None or abs(db_gfw - house_gfw) < 1e-6:
            converted[name] = value
            continue
        converted[name] = value * db_gfw / house_gfw
        conversions[name] = {
            "house_basis_gfw": house_gfw,
            "database_basis_gfw": db_gfw,
            "supplied_mg_l": value,
            "sent_mg_l": converted[name],
            "note": f"restated from the lab basis into {os.path.basename(database_path)}'s basis",
        }
    return {"analytes": converted, "conversions": conversions}


#: Gram formula weights for the input formulas the house basis covers, so a
#: database that writes a formula in column 4 can be compared with one that
#: writes a weight.
_FORMULA_GFW = {
    "SiO2": 60.0843,
    "Ca0.5(CO3)0.5": 50.045,
    "CaCO3": 100.0869,
    "SO4": 96.0616,
    "HCO3": 61.0171,
    "CO3": 60.0089,
    # sit.dat reads C(4) and C(-4) "as C"; without these two the report fell silent on both
    "C": 12.011,
    "CH4": 16.042,
}
