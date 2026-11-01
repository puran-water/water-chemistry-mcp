"""
Helper functions for checking and importing dependencies.
"""
import logging
import os
from typing import List, Optional
logger = logging.getLogger(__name__)
HOUSE_DEFAULT_DATABASE_NAME = 'phreeqc.dat'
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def house_default_database(repo_root: str=_REPO_ROOT) -> Optional[str]:
    """The repository's ``databases/official/phreeqc.dat`` under ``repo_root``, or None when it is not there."""
    path = os.path.join(str(repo_root), 'databases', 'official', HOUSE_DEFAULT_DATABASE_NAME)
    return path if os.path.isfile(path) else None
try:
    import phreeqpython
    PHREEQPYTHON_AVAILABLE = True
    logger.info('PhreeqPython is available')
except ImportError:
    PHREEQPYTHON_AVAILABLE = False
    logger.warning('PhreeqPython is not available. Install with: pip install phreeqpython')
DEFAULT_DATABASE = house_default_database()
DEFAULT_DATABASE_PATH = os.path.dirname(DEFAULT_DATABASE) if DEFAULT_DATABASE else None
if DEFAULT_DATABASE:
    logger.info(f'Using repo-local database: {DEFAULT_DATABASE}')
else:
    logger.warning('The house default database databases/official/phreeqc.dat is not installed; a run must name one.')

def get_available_database_paths() -> List[str]:
    """
    Returns a list of available PHREEQC database paths.
    Priority: repo-local > PhreeqPython bundled.
    """
    available_dbs = []
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sub_dir in ['databases/official', 'databases/custom']:
        local_db_dir = os.path.join(repo_root, sub_dir)
        if os.path.exists(local_db_dir) and os.path.isdir(local_db_dir):
            for file in sorted(os.listdir(local_db_dir)):
                if file.endswith('.dat'):
                    db_path = os.path.join(local_db_dir, file)
                    if db_path not in available_dbs:
                        available_dbs.append(db_path)
                        logger.debug(f'Found repo-local database: {db_path}')
    from .database_paths import OFFICIAL_DIR, CUSTOM_DIR
    for state_dir in (OFFICIAL_DIR, CUSTOM_DIR):
        if os.path.isdir(state_dir):
            available_dbs.extend((os.path.join(state_dir, name) for name in sorted(os.listdir(state_dir)) if name.endswith('.dat') and os.path.join(state_dir, name) not in available_dbs))
    if PHREEQPYTHON_AVAILABLE:
        try:
            pkg_dir = os.path.dirname(phreeqpython.__file__)
            potential_db_dirs = [os.path.join(pkg_dir, 'database'), os.path.join(pkg_dir, 'databases'), os.path.join(os.path.dirname(pkg_dir), 'database')]
            for db_dir in potential_db_dirs:
                if os.path.exists(db_dir) and os.path.isdir(db_dir):
                    from .constants import DEFAULT_DATABASE_NAMES
                    for db_name in DEFAULT_DATABASE_NAMES:
                        db_path = os.path.join(db_dir, db_name)
                        if os.path.exists(db_path) and db_path not in available_dbs:
                            available_dbs.append(db_path)
                            logger.debug(f'Found PhreeqPython bundled database: {db_path}')
                    for file in os.listdir(db_dir):
                        if file.endswith('.dat'):
                            db_path = os.path.join(db_dir, file)
                            if db_path not in available_dbs:
                                available_dbs.append(db_path)
                                logger.debug(f'Found additional PhreeqPython database: {db_path}')
                    logger.info('Found databases in PhreeqPython package')
                    break
        except Exception as e:
            logger.debug(f'Error searching PhreeqPython package for databases: {e}')
    if available_dbs:
        logger.info(f'Found {len(available_dbs)} total database files')
    else:
        logger.warning('No database files found in any location')
    return available_dbs

def get_default_database() -> Optional[str]:
    """
    Returns the house default database path (the repository's phreeqc.dat), or None when it is not installed.
    """
    return DEFAULT_DATABASE or None
