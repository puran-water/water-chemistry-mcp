"""
Helper functions for PHREEQC database management.
"""

import logging
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .import_helpers import get_available_database_paths, get_default_database, USGS_PHREEQC_DATABASE_PATH

logger = logging.getLogger(__name__)


def list_available_databases() -> List[Tuple[str, str]]:
    """
    Returns a list of available databases with their descriptions.

    Returns:
        List of tuples containing (database_path, description)
    """
    databases = []
    available_dbs = get_available_database_paths()

    # Database descriptions
    descriptions = {
        "phreeqc.dat": "Standard PHREEQC database with moderate set of elements",
        "wateq4f.dat": "WATEQ4F database with comprehensive elements and minerals",
        "minteq.v4.dat": "MINTEQA2 version 4 database with comprehensive set of elements",
        "minteq.dat": "Original MINTEQA2 database",
        "llnl.dat": "Lawrence Livermore National Laboratory database - most comprehensive",
        "pitzer.dat": "Pitzer model for high ionic strength solutions",
        "sit.dat": "Specific ion interaction theory database",
        "iso.dat": "Database for isotope calculations",
        "Amm.dat": "Special database for ammonium chemistry",
        "ColdChem.dat": "Database for low-temperature chemistry",
        "frezchem.dat": "Database for frozen chemistry and brines at low temperatures",
        "Tipping_Hurley.dat": "Database for humic substances",
        "PHREEQC_ThermoddemV1.10_15Dec2020.dat": "Thermodynamic database from Thermoddem project",
        "Concrete_PHR.dat": "Database for concrete chemistry (PHREEQC)",
        "Concrete_PZ.dat": "Database for concrete chemistry (Pitzer)",
    }

    for db_path in available_dbs:
        db_name = os.path.basename(db_path)
        description = descriptions.get(db_name, "No description available")
        databases.append((db_path, description))

    return databases


def get_database_element_count(database_path: str) -> int:
    """
    Counts the number of elements defined in a PHREEQC database.

    Args:
        database_path: Path to the PHREEQC database file

    Returns:
        Number of elements found or 0 if unable to determine
    """
    if not os.path.exists(database_path):
        logger.warning(f"Database path does not exist: {database_path}")
        return 0

    try:
        elements = set()
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        # Find the SOLUTION_MASTER_SPECIES block
        master_block_match = re.search(
            r"SOLUTION_MASTER_SPECIES\s+([^#].*?)(?:^\s*$|^[A-Z])", content, re.MULTILINE | re.DOTALL | re.IGNORECASE
        )
        if not master_block_match:
            logger.warning(f"Could not find SOLUTION_MASTER_SPECIES block in {database_path}")
            return 0

        master_block = master_block_match.group(1)
        lines = master_block.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if parts:
                # First part is the element
                elements.add(parts[0].strip())

        return len(elements)

    except Exception as e:
        logger.warning(f"Error examining database {database_path}: {e}")
        return 0


def get_database_mineral_count(database_path: str) -> int:
    """
    Counts the number of minerals defined in a PHREEQC database.

    Args:
        database_path: Path to the PHREEQC database file

    Returns:
        Number of minerals found or 0 if unable to determine
    """
    if not os.path.exists(database_path):
        logger.warning(f"Database path does not exist: {database_path}")
        return 0

    try:
        minerals = set()
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        # Find the PHASES block
        phases_block_match = re.search(
            r"PHASES\s+([^#].*?)(?:^\s*$|^[A-Z])", content, re.MULTILINE | re.DOTALL | re.IGNORECASE
        )
        if not phases_block_match:
            logger.warning(f"Could not find PHASES block in {database_path}")
            return 0

        phases_block = phases_block_match.group(1)

        # Find all mineral definitions
        # Minerals typically start at the beginning of a line with a name
        mineral_matches = re.findall(r"^\s*(\S+)\s*$", phases_block, re.MULTILINE)

        if mineral_matches:
            for mineral in mineral_matches:
                minerals.add(mineral.strip())

        return len(minerals)

    except Exception as e:
        logger.warning(f"Error examining database {database_path}: {e}")
        return 0


def get_database_stats() -> List[Dict[str, any]]:
    """
    Gathers statistics about available PHREEQC databases.

    Returns:
        List of dictionaries containing database info and stats
    """
    db_stats = []
    available_databases = list_available_databases()

    for db_path, description in available_databases:
        db_name = os.path.basename(db_path)
        is_from_usgs = USGS_PHREEQC_DATABASE_PATH in db_path
        is_default = db_path == get_default_database()

        # Get some basic stats about the database
        element_count = get_database_element_count(db_path)
        mineral_count = get_database_mineral_count(db_path)

        db_stats.append(
            {
                "name": db_name,
                "path": db_path,
                "description": description,
                "is_usgs": is_from_usgs,
                "is_default": is_default,
                "element_count": element_count,
                "mineral_count": mineral_count,
            }
        )

    # Sort by element count (largest first) then mineral count
    db_stats.sort(key=lambda x: (-x["element_count"], -x["mineral_count"]))

    return db_stats


def recommend_database(purpose: str = "general") -> str:
    """
    Recommends a database based on the specific purpose.

    Args:
        purpose: The intended use case (general, high_salinity, minerals, isotopes, concrete, etc.)

    Returns:
        Path to the recommended database
    """
    db_stats = get_database_stats()

    if not db_stats:
        logger.warning("No databases found for recommendation")
        return get_default_database() or ""

    # Make recommendations based on purpose
    if purpose.lower() == "high_salinity":
        # For high salinity solutions, prefer Pitzer model
        for db in db_stats:
            if "pitzer" in db["name"].lower():
                return db["path"]

    elif purpose.lower() == "minerals":
        # For mineral calculations, prefer databases with most minerals
        # Already sorted by mineral count
        return db_stats[0]["path"]

    elif purpose.lower() == "isotopes":
        # For isotope calculations
        for db in db_stats:
            if "iso" in db["name"].lower():
                return db["path"]

    elif purpose.lower() == "concrete":
        # For concrete chemistry
        for db in db_stats:
            if "concrete" in db["name"].lower():
                return db["path"]

    elif purpose.lower() == "low_temperature":
        # For low temperature chemistry
        for db in db_stats:
            if "coldchem" in db["name"].lower() or "frezchem" in db["name"].lower():
                return db["path"]

    # Default to LLNL database if available, otherwise first one with most elements
    for db in db_stats:
        if "llnl" in db["name"].lower():
            return db["path"]

    # Fall back to the first database with most elements
    return db_stats[0]["path"]
