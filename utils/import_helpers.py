"""
Helper functions for checking and importing dependencies.
"""

import logging
import os
import sys
import glob
from typing import List, Optional

# Path to the USGS PHREEQC database files - check environment variables first, then fallback paths
USGS_PHREEQC_DATABASE_PATH = None

# First, check environment variables
env_paths = [
    os.environ.get("USGS_PHREEQC_DATABASE_PATH"),
    os.environ.get("PHREEQC_DATABASE_DIR"),
    os.environ.get("PHREEQC_DATABASE_PATH"),
]

for env_path in env_paths:
    if env_path and os.path.exists(env_path) and os.path.isdir(env_path):
        USGS_PHREEQC_DATABASE_PATH = env_path
        break

# If no environment variable path works, try hardcoded fallback paths
if USGS_PHREEQC_DATABASE_PATH is None:
    USGS_PHREEQC_DATABASE_PATHS = [
        r"C:\Program Files\USGS\phreeqc-3.8.6-17100-x64\database",  # Windows path
        r"C:\Program Files\USGS\phreeqc-3.8.6-17100-x6\database",  # Windows path (x6 variant)
        r"/mnt/c/Program Files/USGS/phreeqc-3.8.6-17100-x64/database",  # WSL path to Windows
        r"/mnt/c/Program Files/USGS/phreeqc-3.8.6-17100-x6/database",  # WSL path to Windows (x6 variant)
        r"/opt/phreeqc/database",  # Docker/Linux path
        r"/usr/local/share/phreeqc/database",  # Alternative Linux path
    ]

    # Find the first valid USGS PHREEQC database path
    for path in USGS_PHREEQC_DATABASE_PATHS:
        if os.path.exists(path):
            USGS_PHREEQC_DATABASE_PATH = path
            break

logger = logging.getLogger(__name__)

# Check for PhreeqPython
try:
    import phreeqpython

    PHREEQPYTHON_AVAILABLE = True
    logger.info("PhreeqPython is available")

    # First check if USGS PHREEQC database directory exists
    DEFAULT_DATABASE = None
    DEFAULT_DATABASE_PATH = None

    # Try USGS PHREEQC database path first
    if os.path.exists(USGS_PHREEQC_DATABASE_PATH) and os.path.isdir(USGS_PHREEQC_DATABASE_PATH):
        DEFAULT_DATABASE_PATH = USGS_PHREEQC_DATABASE_PATH
        logger.info(f"Using USGS PHREEQC database directory: {DEFAULT_DATABASE_PATH}")

        # Use preferred database files in order of preference
        from .constants import PREFERRED_DATABASE_NAMES

        for db_name in PREFERRED_DATABASE_NAMES:
            potential_path = os.path.join(DEFAULT_DATABASE_PATH, db_name)
            if os.path.exists(potential_path):
                DEFAULT_DATABASE = potential_path
                logger.info(f"Using USGS PHREEQC database: {DEFAULT_DATABASE}")
                break
        else:
            # If none of the preferred databases are found, use the first .dat file
            dat_files = glob.glob(os.path.join(DEFAULT_DATABASE_PATH, "*.dat"))
            if dat_files:
                DEFAULT_DATABASE = dat_files[0]
                logger.info(f"Using first available USGS PHREEQC database: {DEFAULT_DATABASE}")
            else:
                logger.warning(f"No .dat files found in USGS PHREEQC directory: {DEFAULT_DATABASE_PATH}")
                DEFAULT_DATABASE = None
    else:
        logger.warning(f"USGS PHREEQC database directory not found at: {USGS_PHREEQC_DATABASE_PATH}")

    # Fallback to PhreeqPython's bundled database if USGS database was not found
    if DEFAULT_DATABASE is None:
        try:
            # Try to locate the PhreeqPython database path
            pkg_dir = os.path.dirname(phreeqpython.__file__)
            potential_db_paths = [
                os.path.join(pkg_dir, "database"),
                os.path.join(pkg_dir, "databases"),
                os.path.join(os.path.dirname(pkg_dir), "database"),
            ]

            for path in potential_db_paths:
                if os.path.exists(path) and os.path.isdir(path):
                    DEFAULT_DATABASE_PATH = path
                    logger.info(f"Found PhreeqPython database directory: {DEFAULT_DATABASE_PATH}")
                    break

            if DEFAULT_DATABASE_PATH:
                from .constants import DEFAULT_DATABASE_NAMES

                for db_name in DEFAULT_DATABASE_NAMES:
                    potential_path = os.path.join(DEFAULT_DATABASE_PATH, db_name)
                    if os.path.exists(potential_path):
                        DEFAULT_DATABASE = potential_path
                        logger.info(f"Using PhreeqPython database: {DEFAULT_DATABASE}")
                        break
                else:
                    # If we didn't find any specific database from DEFAULT_DATABASE_NAMES
                    # Use the first .dat file found
                    for file in os.listdir(DEFAULT_DATABASE_PATH):
                        if file.endswith(".dat"):
                            DEFAULT_DATABASE = os.path.join(DEFAULT_DATABASE_PATH, file)
                            logger.info(f"Using alternative PhreeqPython database: {DEFAULT_DATABASE}")
                            break
                    else:
                        DEFAULT_DATABASE = None
                        logger.warning("Could not find any .dat database file in the PhreeqPython directory.")
            else:
                DEFAULT_DATABASE = None
                logger.warning("Could not find PhreeqPython database directory.")

        except Exception as e:
            DEFAULT_DATABASE = None
            DEFAULT_DATABASE_PATH = None
            logger.warning(f"Error determining PhreeqPython database path: {e}")

            # Fallback to hardcoded path if known
            potential_path = os.path.join(os.path.dirname(phreeqpython.__file__), "database", "phreeqc.dat")
            if os.path.exists(potential_path):
                DEFAULT_DATABASE = potential_path
                DEFAULT_DATABASE_PATH = os.path.dirname(potential_path)
                logger.info(f"Using fallback database path: {DEFAULT_DATABASE}")

except ImportError:
    PHREEQPYTHON_AVAILABLE = False
    DEFAULT_DATABASE = None
    DEFAULT_DATABASE_PATH = None
    logger.warning("PhreeqPython is not available. Install with: pip install phreeqpython")


def get_available_database_paths() -> List[str]:
    """
    Returns a list of available PHREEQC database paths.
    Prioritizes databases from the USGS PHREEQC installation.
    """
    available_dbs = []

    if not PHREEQPYTHON_AVAILABLE:
        return available_dbs

    # First check the USGS PHREEQC database directory
    if os.path.exists(USGS_PHREEQC_DATABASE_PATH) and os.path.isdir(USGS_PHREEQC_DATABASE_PATH):
        logger.info(f"Found USGS PHREEQC database directory: {USGS_PHREEQC_DATABASE_PATH}")

        # Add databases from USGS directory in order of preference
        from .constants import PREFERRED_DATABASE_NAMES

        # First add preferred databases in specific order
        for db_name in PREFERRED_DATABASE_NAMES:
            db_path = os.path.join(USGS_PHREEQC_DATABASE_PATH, db_name)
            if os.path.exists(db_path) and db_path not in available_dbs:
                available_dbs.append(db_path)
                logger.debug(f"Found USGS PHREEQC database: {db_path}")

        # Add any other .dat files not already in the preferred list
        for file in os.listdir(USGS_PHREEQC_DATABASE_PATH):
            if file.endswith(".dat"):
                db_path = os.path.join(USGS_PHREEQC_DATABASE_PATH, file)
                if db_path not in available_dbs:
                    available_dbs.append(db_path)
                    logger.debug(f"Found additional USGS PHREEQC database: {db_path}")
    else:
        logger.warning(f"USGS PHREEQC database directory not found at: {USGS_PHREEQC_DATABASE_PATH}")

    # Then check the PhreeqPython database directory as a fallback
    if (
        DEFAULT_DATABASE_PATH
        and os.path.exists(DEFAULT_DATABASE_PATH)
        and DEFAULT_DATABASE_PATH != USGS_PHREEQC_DATABASE_PATH
    ):
        from .constants import DEFAULT_DATABASE_NAMES

        logger.info(f"Adding databases from PhreeqPython directory: {DEFAULT_DATABASE_PATH}")

        # Add default databases in specific order
        for db_name in DEFAULT_DATABASE_NAMES:
            db_path = os.path.join(DEFAULT_DATABASE_PATH, db_name)
            if os.path.exists(db_path) and db_path not in available_dbs:
                available_dbs.append(db_path)
                logger.debug(f"Found PhreeqPython database: {db_path}")

        # Add any other .dat files not already in the list
        for file in os.listdir(DEFAULT_DATABASE_PATH):
            if file.endswith(".dat"):
                db_path = os.path.join(DEFAULT_DATABASE_PATH, file)
                if db_path not in available_dbs:
                    available_dbs.append(db_path)
                    logger.debug(f"Found additional PhreeqPython database: {db_path}")

    # If we still didn't find any databases, search in common locations
    if not available_dbs:
        try:
            pkg_dir = os.path.dirname(phreeqpython.__file__)
            potential_db_dirs = [
                os.path.join(pkg_dir, "database"),
                os.path.join(pkg_dir, "databases"),
                os.path.join(os.path.dirname(pkg_dir), "database"),
            ]

            for db_dir in potential_db_dirs:
                if os.path.exists(db_dir) and os.path.isdir(db_dir):
                    for db_name in os.listdir(db_dir):
                        if db_name.endswith(".dat"):
                            db_path = os.path.join(db_dir, db_name)
                            if db_path not in available_dbs:
                                available_dbs.append(db_path)
                                logger.debug(f"Found additional database: {db_path}")

                    # If we found databases, no need to check other locations
                    if available_dbs:
                        break
        except Exception as e:
            logger.warning(f"Error searching for additional database paths: {e}")

    if available_dbs:
        logger.info(f"Found {len(available_dbs)} total database files")
    else:
        logger.warning("No database files found in any location")

    return available_dbs


def get_default_database() -> Optional[str]:
    """
    Returns the default database path for PHREEQC simulations.
    Prioritizes USGS PHREEQC databases over PhreeqPython defaults.
    """
    # If we already selected a preferred database, return it
    if DEFAULT_DATABASE:
        return DEFAULT_DATABASE

    # Otherwise, search for available databases in order of preference
    available_dbs = get_available_database_paths()
    if available_dbs:
        # First database in the list is the highest priority
        return available_dbs[0]

    return None
