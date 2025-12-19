"""
Mineral Registry Module

This module contains comprehensive data about minerals across different PHREEQC databases,
including their formulas, alternative names, and compatibility information.
The registry helps ensure mineral compatibility when using different thermodynamic databases.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Common minerals available in most standard PHREEQC databases
COMMON_MINERALS = {
    "Calcite": {"formula": "CaCO3", "alternative_names": ["Calcite"]},
    "Aragonite": {"formula": "CaCO3", "alternative_names": ["Aragonite"]},
    "Dolomite": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite", "Dolomite-ord"]},
    "Gypsum": {"formula": "CaSO4:2H2O", "alternative_names": ["Gypsum"]},
    "Anhydrite": {"formula": "CaSO4", "alternative_names": ["Anhydrite"]},
    "Halite": {"formula": "NaCl", "alternative_names": ["Halite", "NaCl"]},
    "Sylvite": {"formula": "KCl", "alternative_names": ["Sylvite", "KCl"]},
    "Quartz": {"formula": "SiO2", "alternative_names": ["Quartz", "SiO2"]},
    "Fluorite": {"formula": "CaF2", "alternative_names": ["Fluorite"]},
    "Barite": {"formula": "BaSO4", "alternative_names": ["Barite"]},
    "Celestite": {"formula": "SrSO4", "alternative_names": ["Celestite"]},
    "Witherite": {"formula": "BaCO3", "alternative_names": ["Witherite"]},
    "Strontianite": {"formula": "SrCO3", "alternative_names": ["Strontianite"]},
    "Siderite": {"formula": "FeCO3", "alternative_names": ["Siderite"]},
    "Rhodochrosite": {"formula": "MnCO3", "alternative_names": ["Rhodochrosite"]},
    "Smithsonite": {"formula": "ZnCO3", "alternative_names": ["Smithsonite"]},
    "Magnesite": {"formula": "MgCO3", "alternative_names": ["Magnesite"]},
    "Huntite": {"formula": "CaMg3(CO3)4", "alternative_names": ["Huntite"]},
    "Hematite": {"formula": "Fe2O3", "alternative_names": ["Hematite"]},
    "Magnetite": {"formula": "Fe3O4", "alternative_names": ["Magnetite"]},
    "Goethite": {"formula": "FeOOH", "alternative_names": ["Goethite", "FeO(OH)"]},
}

# Database-specific minerals with database-specific naming conventions
DATABASE_SPECIFIC_MINERALS = {
    "phreeqc.dat": {
        "Fe(OH)3(a)": {"formula": "Fe(OH)3", "alternative_names": ["Ferrihydrite", "Fe(OH)3(am)", "Fe(OH)3(amorp)"]},
        "Al(OH)3(a)": {"formula": "Al(OH)3", "alternative_names": ["Gibbsite(am)", "Al(OH)3(am)", "Gibbsite"]},
        "FeS(ppt)": {"formula": "FeS", "alternative_names": ["Mackinawite", "FeS(am)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Sphalerite": {"formula": "ZnS", "alternative_names": ["Sphalerite", "ZnS"]},
        "Dolomite-ord": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite"]},
        "Dolomite-dis": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite(d)"]},
        "Pyrolusite": {"formula": "MnO2", "alternative_names": ["Pyrolusite", "MnO2"]},
        "Hausmannite": {"formula": "Mn3O4", "alternative_names": ["Hausmannite", "Mn3O4"]},
        "Manganite": {"formula": "MnOOH", "alternative_names": ["Manganite", "MnOOH"]},
        "Mg(OH)2": {"formula": "Mg(OH)2", "alternative_names": ["Brucite", "Mg(OH)2(am)"]},  # Critical for ZLD
        "Vivianite": {"formula": "Fe3(PO4)2:8H2O", "alternative_names": ["Vivianite"]},  # Phosphate mineral
        "Jarosite-K": {"formula": "KFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-K"]},
        "Jarosite-Na": {"formula": "NaFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-Na"]},
        "Jarosite-H": {"formula": "HFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-H"]},
        "Scorodite": {"formula": "FeAsO4:2H2O", "alternative_names": ["Scorodite"]},
        "Chalcedony": {"formula": "SiO2", "alternative_names": ["Chalcedony"]},
        "SiO2(a)": {"formula": "SiO2", "alternative_names": ["SiO2(am)", "SiO2(amorp)"]},
        "Sepiolite": {"formula": "Mg4Si6O15(OH)2:6H2O", "alternative_names": ["Sepiolite"]},
        "Sepiolite(d)": {"formula": "Mg4Si6O15(OH)2:6H2O", "alternative_names": ["Sepiolite(d)"]},
        "Talc": {"formula": "Mg3Si4O10(OH)2", "alternative_names": ["Talc"]},
        "Chrysotile": {"formula": "Mg3Si2O5(OH)4", "alternative_names": ["Chrysotile"]},
        "Zn(OH)2(e)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(beta)", "epsilon-Zn(OH)2"]},
        "Zn(OH)2(a)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(am)", "Zn(OH)2(amorp)"]},
    },
    "wateq4f.dat": {
        "Ferrihydrite": {"formula": "Fe(OH)3", "alternative_names": ["Fe(OH)3(am)", "Fe(OH)3(a)"]},
        "Gibbsite": {"formula": "Al(OH)3", "alternative_names": ["Al(OH)3(am)", "Al(OH)3(a)"]},
        "Mackinawite": {"formula": "FeS", "alternative_names": ["FeS(am)", "FeS(ppt)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Sphalerite": {"formula": "ZnS", "alternative_names": ["Sphalerite", "ZnS"]},
        "Dolomite": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite-ord"]},
        "Dolomite(d)": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite-dis"]},
        "Pyrolusite": {"formula": "MnO2", "alternative_names": ["Pyrolusite", "MnO2"]},
        "Hausmannite": {"formula": "Mn3O4", "alternative_names": ["Hausmannite", "Mn3O4"]},
        "Manganite": {"formula": "MnOOH", "alternative_names": ["Manganite", "MnOOH"]},
        "Jarosite-K": {"formula": "KFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-K"]},
        "Jarosite-Na": {"formula": "NaFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-Na"]},
        "Jarosite-H": {"formula": "HFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-H"]},
        "Scorodite": {"formula": "FeAsO4:2H2O", "alternative_names": ["Scorodite"]},
        "Chalcedony": {"formula": "SiO2", "alternative_names": ["Chalcedony"]},
        "SiO2(am)": {"formula": "SiO2", "alternative_names": ["SiO2(a)", "SiO2(amorp)"]},
        "Sepiolite": {"formula": "Mg4Si6O15(OH)2:6H2O", "alternative_names": ["Sepiolite"]},
        "Sepiolite(d)": {"formula": "Mg4Si6O15(OH)2:6H2O", "alternative_names": ["Sepiolite(d)"]},
        "Talc": {"formula": "Mg3Si4O10(OH)2", "alternative_names": ["Talc"]},
        "Chrysotile": {"formula": "Mg3Si2O5(OH)4", "alternative_names": ["Chrysotile"]},
        "Zn(OH)2(beta)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(e)", "epsilon-Zn(OH)2"]},
        "Zn(OH)2(am)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(a)", "Zn(OH)2(amorp)"]},
    },
    "llnl.dat": {
        "Ferrihydrite": {"formula": "Fe(OH)3", "alternative_names": ["Fe(OH)3(am)", "Fe(OH)3(a)"]},
        "Gibbsite": {"formula": "Al(OH)3", "alternative_names": ["Al(OH)3(am)", "Al(OH)3(a)"]},
        "Mackinawite": {"formula": "FeS", "alternative_names": ["FeS(am)", "FeS(ppt)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Sphalerite": {"formula": "ZnS", "alternative_names": ["Sphalerite", "ZnS"]},
        "Dolomite": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite-ord", "Dolomite-dis"]},
        "Pyrolusite": {"formula": "MnO2", "alternative_names": ["Pyrolusite", "MnO2"]},
        "Hausmannite": {"formula": "Mn3O4", "alternative_names": ["Hausmannite", "Mn3O4"]},
        "Manganite": {"formula": "MnOOH", "alternative_names": ["Manganite", "MnOOH"]},
        "Jarosite-K": {"formula": "KFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-K"]},
        "Jarosite-Na": {"formula": "NaFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-Na"]},
        "Jarosite-H": {"formula": "HFe3(SO4)2(OH)6", "alternative_names": ["Jarosite-H"]},
        "Scorodite": {"formula": "FeAsO4:2H2O", "alternative_names": ["Scorodite"]},
        "Chalcedony": {"formula": "SiO2", "alternative_names": ["Chalcedony"]},
        "SiO2(am)": {"formula": "SiO2", "alternative_names": ["SiO2(a)", "SiO2(amorp)"]},
        "Sepiolite": {"formula": "Mg4Si6O15(OH)2:6H2O", "alternative_names": ["Sepiolite"]},
        "Talc": {"formula": "Mg3Si4O10(OH)2", "alternative_names": ["Talc"]},
        "Chrysotile": {"formula": "Mg3Si2O5(OH)4", "alternative_names": ["Chrysotile"]},
        "Zn(OH)2(beta)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(e)", "epsilon-Zn(OH)2"]},
        "Zn(OH)2(am)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(a)", "Zn(OH)2(amorp)"]},
        "Alunite": {"formula": "KAl3(SO4)2(OH)6", "alternative_names": ["Alunite"]},
        "Kaolinite": {"formula": "Al2Si2O5(OH)4", "alternative_names": ["Kaolinite"]},
        "Illite": {"formula": "K0.6Mg0.25Al2.3Si3.5O10(OH)2", "alternative_names": ["Illite"]},
        "Montmorillonite": {"formula": "(Ca,Na)0.33(Al,Mg)2Si4O10(OH)2·nH2O", "alternative_names": ["Montmorillonite"]},
        "Chlorite": {"formula": "(Mg,Fe)3(Si,Al)4O10(OH)2·(Mg,Fe)3(OH)6", "alternative_names": ["Chlorite"]},
    },
    "minteq.dat": {
        "Brucite": {"formula": "Mg(OH)2", "alternative_names": ["Mg(OH)2", "magnesium hydroxide"]},  # Critical for ZLD
        "Ferrihydrite": {"formula": "Fe(OH)3", "alternative_names": ["Fe(OH)3(am)", "Fe(OH)3(a)"]},
        "Gibbsite": {"formula": "Al(OH)3", "alternative_names": ["Al(OH)3(am)", "Al(OH)3(a)"]},
        "Mackinawite": {"formula": "FeS", "alternative_names": ["FeS(am)", "FeS(ppt)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Sphalerite": {"formula": "ZnS", "alternative_names": ["Sphalerite", "ZnS"]},
        "Dolomite": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite-ord", "Dolomite-dis"]},
        "Pyrolusite": {"formula": "MnO2", "alternative_names": ["Pyrolusite", "MnO2"]},
        "Hausmannite": {"formula": "Mn3O4", "alternative_names": ["Hausmannite", "Mn3O4"]},
        "Manganite": {"formula": "MnOOH", "alternative_names": ["Manganite", "MnOOH"]},
        "Alunite": {"formula": "KAl3(SO4)2(OH)6", "alternative_names": ["Alunite"]},
        "Kaolinite": {"formula": "Al2Si2O5(OH)4", "alternative_names": ["Kaolinite"]},
        "Chalcedony": {"formula": "SiO2", "alternative_names": ["Chalcedony"]},
        "SiO2(am)": {"formula": "SiO2", "alternative_names": ["SiO2(a)", "SiO2(amorp)"]},
        "ZnCO3(a)": {"formula": "ZnCO3", "alternative_names": ["Smithsonite", "ZnCO3"]},
        "Zincite(c)": {"formula": "ZnO", "alternative_names": ["Zincite"]},
        "Zn(OH)2(am)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(a)", "Zn(OH)2(amorp)"]},
        "Zn(OH)2(beta)": {"formula": "Zn(OH)2", "alternative_names": ["Zn(OH)2(e)", "epsilon-Zn(OH)2"]},
        "ZnSiO3": {"formula": "ZnSiO3", "alternative_names": ["ZnSiO3"]},
        "Willemite": {"formula": "Zn2SiO4", "alternative_names": ["Willemite"]},
    },
    "pitzer.dat": {
        "Fe(OH)3(a)": {"formula": "Fe(OH)3", "alternative_names": ["Ferrihydrite", "Fe(OH)3(am)"]},
        "Gibbsite": {"formula": "Al(OH)3", "alternative_names": ["Al(OH)3(am)", "Al(OH)3(a)"]},
        "Mackinawite": {"formula": "FeS", "alternative_names": ["FeS(am)", "FeS(ppt)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Dolomite": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite-ord", "Dolomite-dis"]},
        "Polyhalite": {"formula": "K2Ca2Mg(SO4)4·2H2O", "alternative_names": ["Polyhalite"]},
        "Kainite": {"formula": "KMgClSO4·3H2O", "alternative_names": ["Kainite"]},
        "Carnallite": {"formula": "KMgCl3·6H2O", "alternative_names": ["Carnallite"]},
        "Kieserite": {"formula": "MgSO4·H2O", "alternative_names": ["Kieserite"]},
        "Hexahydrite": {"formula": "MgSO4·6H2O", "alternative_names": ["Hexahydrite"]},
        "Epsomite": {"formula": "MgSO4·7H2O", "alternative_names": ["Epsomite"]},
        "Leonite": {"formula": "K2Mg(SO4)2·4H2O", "alternative_names": ["Leonite"]},
        "Bloedite": {"formula": "Na2Mg(SO4)2·4H2O", "alternative_names": ["Bloedite"]},
        "Glaserite": {"formula": "K3Na(SO4)2", "alternative_names": ["Glaserite"]},
        "Syngenite": {"formula": "K2Ca(SO4)2·H2O", "alternative_names": ["Syngenite"]},
        "Glauberite": {"formula": "Na2Ca(SO4)2", "alternative_names": ["Glauberite"]},
        "Antarcticite": {"formula": "CaCl2·6H2O", "alternative_names": ["Antarcticite"]},
        "Bischofite": {"formula": "MgCl2·6H2O", "alternative_names": ["Bischofite"]},
    },
    "sit.dat": {
        "Fe(OH)3(s)": {"formula": "Fe(OH)3", "alternative_names": ["Ferrihydrite", "Fe(OH)3(am)", "Fe(OH)3(a)"]},
        "Gibbsite": {"formula": "Al(OH)3", "alternative_names": ["Al(OH)3(am)", "Al(OH)3(a)"]},
        "FeS(ppt)": {"formula": "FeS", "alternative_names": ["Mackinawite", "FeS(am)"]},
        "Pyrite": {"formula": "FeS2", "alternative_names": ["Pyrite"]},
        "Dolomite-ord": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite"]},
        "Dolomite-dis": {"formula": "CaMg(CO3)2", "alternative_names": ["Dolomite(d)"]},
        "Pyrolusite": {"formula": "MnO2", "alternative_names": ["Pyrolusite", "MnO2"]},
        "SiO2(am)": {"formula": "SiO2", "alternative_names": ["SiO2(a)", "SiO2(amorp)"]},
        "UO2(am)": {"formula": "UO2", "alternative_names": ["UO2(am,hyd)", "UO2(am,hydrous)"]},
        "Schoepite": {"formula": "UO3·2H2O", "alternative_names": ["Schoepite"]},
        "Gummite": {"formula": "UO3·H2O", "alternative_names": ["Gummite"]},
        "Uraninite": {"formula": "UO2", "alternative_names": ["Uraninite"]},
        "CaUO4": {"formula": "CaUO4", "alternative_names": ["CaUO4"]},
        "Becquerelite": {"formula": "Ca(UO2)6O4(OH)6·8H2O", "alternative_names": ["Becquerelite"]},
        "Rutherfordine": {"formula": "UO2CO3", "alternative_names": ["Rutherfordine"]},
        "Andersonite": {"formula": "Na2Ca(UO2)(CO3)3·6H2O", "alternative_names": ["Andersonite"]},
        "Na4UO2(CO3)3": {"formula": "Na4UO2(CO3)3", "alternative_names": ["Na4UO2(CO3)3"]},
    },
}


def get_database_specific_minerals(database_name):
    """
    Returns a list of minerals specific to the given database.

    Args:
        database_name (str): Name of the database file (e.g., 'phreeqc.dat')

    Returns:
        list: List of mineral names specific to the database
    """
    # Extract the base filename if a full path is provided
    if os.path.sep in database_name:
        database_name = os.path.basename(database_name)

    # Check if we have info about this database
    if database_name in DATABASE_SPECIFIC_MINERALS:
        return list(DATABASE_SPECIFIC_MINERALS[database_name].keys())
    else:
        logger.warning(f"No database-specific mineral information available for {database_name}")
        return []


def get_mineral_formula(mineral_name, database_name=None):
    """
    Returns the chemical formula for a given mineral.

    Args:
        mineral_name (str): Name of the mineral
        database_name (str, optional): Name of the database file

    Returns:
        str: Chemical formula of the mineral, or None if not found
    """
    # First check common minerals
    if mineral_name in COMMON_MINERALS:
        return COMMON_MINERALS[mineral_name]["formula"]

    # If database is specified, check database-specific minerals
    if database_name:
        if os.path.sep in database_name:
            database_name = os.path.basename(database_name)

        if database_name in DATABASE_SPECIFIC_MINERALS:
            if mineral_name in DATABASE_SPECIFIC_MINERALS[database_name]:
                return DATABASE_SPECIFIC_MINERALS[database_name][mineral_name]["formula"]

            # Check alternative names
            for db_mineral, info in DATABASE_SPECIFIC_MINERALS[database_name].items():
                if mineral_name in info["alternative_names"]:
                    return info["formula"]

    # If not found, check all databases
    for db_name, minerals in DATABASE_SPECIFIC_MINERALS.items():
        if mineral_name in minerals:
            return minerals[mineral_name]["formula"]

        # Check alternative names
        for db_mineral, info in minerals.items():
            if mineral_name in info["alternative_names"]:
                return info["formula"]

    return None


def get_alternative_mineral_names(mineral_name, database_name=None):
    """
    Returns alternative names for a given mineral across databases.

    Args:
        mineral_name (str): Name of the mineral
        database_name (str, optional): Name of the database file

    Returns:
        list: List of alternative names for the mineral
    """
    alternatives = []

    # First check common minerals
    if mineral_name in COMMON_MINERALS:
        alternatives.extend(COMMON_MINERALS[mineral_name]["alternative_names"])

    # If database is specified, check database-specific minerals
    if database_name:
        if os.path.sep in database_name:
            database_name = os.path.basename(database_name)

        if database_name in DATABASE_SPECIFIC_MINERALS:
            if mineral_name in DATABASE_SPECIFIC_MINERALS[database_name]:
                alternatives.extend(DATABASE_SPECIFIC_MINERALS[database_name][mineral_name]["alternative_names"])

            # Check if mineral_name is an alternative name
            for db_mineral, info in DATABASE_SPECIFIC_MINERALS[database_name].items():
                if mineral_name in info["alternative_names"]:
                    # Add the primary name and all alternatives
                    alternatives.append(db_mineral)
                    alternatives.extend(info["alternative_names"])

    # If still no alternatives or additional search is needed, check all databases
    for db_name, minerals in DATABASE_SPECIFIC_MINERALS.items():
        if mineral_name in minerals:
            alternatives.extend(minerals[mineral_name]["alternative_names"])

        # Check if mineral_name is an alternative name
        for db_mineral, info in minerals.items():
            if mineral_name in info["alternative_names"]:
                # Add the primary name and all alternatives
                alternatives.append(db_mineral)
                alternatives.extend(info["alternative_names"])

    # Remove duplicates and the original name
    return list(set([alt for alt in alternatives if alt != mineral_name]))
