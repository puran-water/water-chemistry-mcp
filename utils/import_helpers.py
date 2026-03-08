"""
Helper functions for checking and importing dependencies.
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# Check for PhreeqPython
try:
    import phreeqpython

    PHREEQPYTHON_AVAILABLE = True
    logger.info("PhreeqPython is available")

    # Initialize database paths
    DEFAULT_DATABASE = None
    DEFAULT_DATABASE_PATH = None

    # PRIORITY 1: Repo-local databases
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _sub_dir in ["databases/official", "databases/custom"]:
        _local_db_dir = os.path.join(_repo_root, _sub_dir)
        if os.path.exists(_local_db_dir) and os.path.isdir(_local_db_dir):
            DEFAULT_DATABASE_PATH = _local_db_dir
            logger.info(f"Found repo-local database directory: {DEFAULT_DATABASE_PATH}")
            break

    if DEFAULT_DATABASE_PATH:
        from .constants import DEFAULT_DATABASE_NAMES

        for db_name in DEFAULT_DATABASE_NAMES:
            potential_path = os.path.join(DEFAULT_DATABASE_PATH, db_name)
            if os.path.exists(potential_path):
                DEFAULT_DATABASE = potential_path
                logger.info(f"Using repo-local database: {DEFAULT_DATABASE}")
                break
        else:
            # Use first .dat file found
            for file in sorted(os.listdir(DEFAULT_DATABASE_PATH)):
                if file.endswith(".dat"):
                    DEFAULT_DATABASE = os.path.join(DEFAULT_DATABASE_PATH, file)
                    logger.info(f"Using repo-local database: {DEFAULT_DATABASE}")
                    break

    # PRIORITY 2: PhreeqPython bundled databases (if no repo-local found)
    if DEFAULT_DATABASE is None:
        try:
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
                        logger.info(f"Using PhreeqPython bundled database: {DEFAULT_DATABASE}")
                        break
                else:
                    for file in os.listdir(DEFAULT_DATABASE_PATH):
                        if file.endswith(".dat"):
                            DEFAULT_DATABASE = os.path.join(DEFAULT_DATABASE_PATH, file)
                            logger.info(f"Using PhreeqPython database: {DEFAULT_DATABASE}")
                            break
        except Exception as e:
            logger.debug(f"Error locating PhreeqPython bundled databases: {e}")

    if DEFAULT_DATABASE is None:
        logger.warning("No PHREEQC databases found. Some features may not work.")

except ImportError:
    PHREEQPYTHON_AVAILABLE = False
    DEFAULT_DATABASE = None
    DEFAULT_DATABASE_PATH = None
    logger.warning("PhreeqPython is not available. Install with: pip install phreeqpython")


def get_available_database_paths() -> List[str]:
    """
    Returns a list of available PHREEQC database paths.
    Priority: repo-local > PhreeqPython bundled.
    """
    available_dbs = []

    # PRIORITY 1: Repo-local databases (databases/official/ and databases/custom/)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub_dir in ["databases/official", "databases/custom"]:
        local_db_dir = os.path.join(repo_root, sub_dir)
        if os.path.exists(local_db_dir) and os.path.isdir(local_db_dir):
            for file in sorted(os.listdir(local_db_dir)):
                if file.endswith(".dat"):
                    db_path = os.path.join(local_db_dir, file)
                    if db_path not in available_dbs:
                        available_dbs.append(db_path)
                        logger.debug(f"Found repo-local database: {db_path}")

    # PRIORITY 2: PhreeqPython bundled databases (for databases not in repo)
    if PHREEQPYTHON_AVAILABLE:
        try:
            pkg_dir = os.path.dirname(phreeqpython.__file__)
            potential_db_dirs = [
                os.path.join(pkg_dir, "database"),
                os.path.join(pkg_dir, "databases"),
                os.path.join(os.path.dirname(pkg_dir), "database"),
            ]

            for db_dir in potential_db_dirs:
                if os.path.exists(db_dir) and os.path.isdir(db_dir):
                    from .constants import DEFAULT_DATABASE_NAMES

                    # Add preferred databases first
                    for db_name in DEFAULT_DATABASE_NAMES:
                        db_path = os.path.join(db_dir, db_name)
                        if os.path.exists(db_path) and db_path not in available_dbs:
                            available_dbs.append(db_path)
                            logger.debug(f"Found PhreeqPython bundled database: {db_path}")

                    # Add any other .dat files
                    for file in os.listdir(db_dir):
                        if file.endswith(".dat"):
                            db_path = os.path.join(db_dir, file)
                            if db_path not in available_dbs:
                                available_dbs.append(db_path)
                                logger.debug(f"Found additional PhreeqPython database: {db_path}")

                    logger.info("Found databases in PhreeqPython package")
                    break
        except Exception as e:
            logger.debug(f"Error searching PhreeqPython package for databases: {e}")

    if available_dbs:
        logger.info(f"Found {len(available_dbs)} total database files")
    else:
        logger.warning("No database files found in any location")

    return available_dbs


def get_default_database() -> Optional[str]:
    """
    Returns the default database path for PHREEQC simulations.
    Prioritizes repo-local databases.
    """
    if DEFAULT_DATABASE:
        return DEFAULT_DATABASE

    available_dbs = get_available_database_paths()
    if available_dbs:
        return available_dbs[0]

    return None
