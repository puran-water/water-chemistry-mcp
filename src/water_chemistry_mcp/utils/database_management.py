"""
Database management module for PHREEQC.

Provides centralized functionality for database selection, validation, and information.
"""
import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Union
from .exceptions import DatabaseNotFoundError
from .import_helpers import get_available_database_paths, get_default_database
database_cache = None
database_downloader = None
logger = logging.getLogger(__name__)

def _redact_public_path(value: str) -> str:
    try:
        from water_chemistry_mcp.waterchem.contracts.common import redact_paths
        return str(redact_paths(value))
    except Exception:
        return os.path.basename(str(value))

class DatabaseManager:
    """
    Manages PHREEQC database files and their selection.
    """

    def __init__(self):
        """Initialize the database manager."""
        self.available_databases = get_available_database_paths()
        self.default_database = get_default_database()
        self._database_info_cache = {}

    def resolve_database_path(self, database_path: str) -> Optional[str]:
        """
        Resolves a database path or name to a full path.

        Searches available databases list (repo-local first, then bundled).

        Args:
            database_path: Full path or just database filename

        Returns:
            Full path to database if found, None otherwise
        """
        if os.path.dirname(database_path):
            return database_path if os.path.exists(database_path) and database_path.endswith('.dat') else None
        for db in self.available_databases:
            if os.path.basename(db) == database_path:
                return db
        return None

    def validate_database_path(self, database_path: str) -> bool:
        """
        Validates if a database path exists and is a valid PHREEQC database file.

        Args:
            database_path: Path to the database file

        Returns:
            True if valid, False otherwise
        """
        resolved_path = self.resolve_database_path(database_path)
        if resolved_path:
            database_path = resolved_path
        redacted_path = _redact_public_path(database_path)
        if not os.path.exists(database_path):
            logger.warning('Database file does not exist: %s', redacted_path)
            return False
        if not database_path.endswith('.dat'):
            logger.warning('Database file does not have .dat extension: %s', redacted_path)
            return False
        try:
            with open(database_path, 'r', errors='ignore') as f:
                content = f.read().upper()
                if 'SOLUTION_MASTER_SPECIES' not in content:
                    logger.warning('Database file does not contain SOLUTION_MASTER_SPECIES: %s', redacted_path)
                    return False
                if 'SOLUTION_SPECIES' not in content:
                    logger.warning('Database file does not contain SOLUTION_SPECIES: %s', redacted_path)
                    return False
            return True
        except Exception as e:
            logger.error('Error validating database file %s: %s', redacted_path, _redact_public_path(str(e)))
            return False

    def get_database_info(self, database_path: str) -> Dict:
        """
        Returns information about a database file.

        Args:
            database_path: Path to the database file

        Returns:
            Dictionary with database information
        """
        global database_cache
        if database_cache is None:
            try:
                from . import database_cache as db_cache
                database_cache = db_cache
            except ImportError:
                logger.warning('Database cache module not available')
        if database_path in self._database_info_cache:
            return self._database_info_cache[database_path]
        if database_cache is not None:
            cached_info = database_cache.get_cached_database_info(database_path)
            if cached_info:
                self._database_info_cache[database_path] = cached_info
                return cached_info
        db_name = os.path.basename(database_path)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        is_repo_local = database_path.startswith(os.path.join(repo_root, 'databases'))
        is_default = database_path == self.default_database
        try:
            with open(database_path, 'r', errors='ignore') as f:
                content = f.read()
            from .phreeqc_database_introspection import describe_database
            declared = describe_database(database_path)
            element_count = declared['element_count']
            mineral_count = declared['mineral_count']
            version_info = 'Unknown'
            for line in content.split('\n')[:50]:
                if 'version' in line.lower() or 'revised' in line.lower() or 'modified' in line.lower():
                    version_info = line.strip()
                    break
            features = dict(declared['features'])
            info = {'name': db_name, 'path': database_path, 'is_repo_local': is_repo_local, 'is_default': is_default, 'element_count': element_count, 'mineral_count': mineral_count, 'version_info': version_info, 'features': features, 'master_species': declared['master_species'], 'file_size_kb': os.path.getsize(database_path) // 1024}
            self._database_info_cache[database_path] = info
            if database_cache is not None:
                database_cache.cache_database_info(database_path, info)
            return info
        except Exception as e:
            logger.error(f'Error getting database info for {database_path}: {e}')
            error_info = {'name': db_name, 'path': database_path, 'is_repo_local': is_repo_local, 'is_default': is_default, 'error': str(e)}
            return error_info
    CATEGORY_DEFAULTS = {'general': ('phreeqc.dat', None), 'minerals': ('phreeqc.dat', None), 'surface': ('minteq.v4.dat', 'surface_complexation'), 'kinetics': ('phreeqc.dat', 'kinetics')}

    def recommend_database(self, purpose: str='general') -> str:
        """
        The database a tool category runs on when the caller names none: one named file, the repository's copy
        first (a same-named file bundled with phreeqpython is a dependency's, and answered before). Refuses
        (DatabaseNotFoundError) an unknown category, an absent file, or one lacking the category's feature.
        """
        from water_chemistry_mcp.waterchem.input_key_rulings import DATABASE_CHOICE_RULING_ID
        purpose = str(purpose or 'general').lower()
        if purpose not in self.CATEGORY_DEFAULTS:
            raise DatabaseNotFoundError(f'No database category {purpose!r}: the categories are {sorted(self.CATEGORY_DEFAULTS)}; name a database to run on another ({DATABASE_CHOICE_RULING_ID}).', database_path=purpose)
        name, feature = self.CATEGORY_DEFAULTS[purpose]
        path = self.resolve_database_path(name)
        if not path:
            raise DatabaseNotFoundError(f'The {purpose!r} category runs on {name}, which is not installed; name a database ({DATABASE_CHOICE_RULING_ID}).', database_path=name)
        if feature and (not self.get_database_info(path).get('features', {}).get(feature)):
            raise DatabaseNotFoundError(f'The {purpose!r} category runs on {name}, which does not declare {feature}; name a database ({DATABASE_CHOICE_RULING_ID}).', database_path=name)
        return path

    def resolve_and_validate_database(self, database_path: Optional[str], category: str='general') -> str:
        """
        Centralized database resolution with validation.

        This is the recommended way to handle database selection in tools. A named database is resolved (repository
        copy first) or refused; with none named, the category's one default (`recommend_database`) or a refusal.

        Args:
            database_path: Database path/name provided by user, or None
            category: Tool category whose default answers when no database is named
                      ('general', 'minerals', 'surface', 'kinetics')

        Returns:
            Validated database path ready for use with PHREEQC

        Example:
            >>> db_path = database_manager.resolve_and_validate_database("minteq.dat", "general")
            >>> # Now db_path is guaranteed to be a valid, existing database path
        """
        if database_path:
            resolved_path = self.resolve_database_path(database_path)
            if resolved_path and self.validate_database_path(resolved_path):
                logger.debug(f'Using resolved database path: {resolved_path}')
                return resolved_path
            redacted_path = _redact_public_path(database_path)
            logger.warning('Invalid database path: %s', redacted_path)
            raise DatabaseNotFoundError(f'Invalid explicit database path: {redacted_path}', database_path=redacted_path)
        recommended_db = self.recommend_database(category)
        logger.debug(f'Using recommended database: {recommended_db}')
        return recommended_db

    def get_compatible_minerals(self, database_path: str, requested_minerals: Optional[List[str]]=None) -> Union[List[str], Dict[str, str]]:
        'The minerals of a database, or the phases that answer for the minerals requested.'
        from water_chemistry_mcp.waterchem.phase_choice import defined_phases, resolve_phases
        if not requested_minerals:
            return sorted(defined_phases(database_path).values())
        resolve_phases(database_path, list(requested_minerals))
        return {mineral: resolve_phases(database_path, [mineral])[0] for mineral in requested_minerals}

    def register_custom_database(self, database_path: str, name: Optional[str]=None, description: Optional[str]=None) -> Optional[str]:
        """
        Register a user-provided custom database.

        Args:
            database_path: Path to the original database file
            name: Optional name for the database (defaults to filename)
            description: Optional description of the database

        Returns:
            Path to the registered database or None if registration failed
        """
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader
                database_downloader = db_downloader
            except ImportError:
                logger.warning('Database downloader module not available')
                return None
        if not os.path.exists(database_path):
            logger.error(f'Database file not found: {database_path}')
            return None
        if name is None:
            name = os.path.basename(database_path)
        if not name.endswith('.dat'):
            name += '.dat'
        custom_dir = os.path.join(database_downloader.DATABASE_DIR, 'custom')
        os.makedirs(custom_dir, exist_ok=True)
        target_path = os.path.join(custom_dir, name)
        try:
            shutil.copy2(database_path, target_path)
            if not self.validate_database_path(target_path):
                os.remove(target_path)
                logger.error(f'Invalid database file: {database_path}')
                return None
            os.makedirs(database_downloader.METADATA_DIR, exist_ok=True)
            metadata_path = os.path.join(database_downloader.METADATA_DIR, 'custom_databases.json')
            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, 'r') as f:
                        metadata = json.load(f)
                except Exception as e:
                    logger.warning(f'Failed to read custom database metadata, creating new: {e}')
                    metadata = {}
            else:
                metadata = {}
            import datetime
            metadata[name] = {'path': target_path, 'description': description or f'Custom database imported from {database_path}', 'imported_date': datetime.datetime.now().isoformat(), 'original_path': database_path}
            os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            self.available_databases = get_available_database_paths()
            logger.info(f'Successfully registered custom database: {name}')
            return target_path
        except Exception as e:
            logger.error(f'Failed to register custom database: {e}')
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except:
                    pass
            return None

    def download_database(self, database_name: str, force_update: bool=False) -> Optional[str]:
        """
        Download an official database.

        Args:
            database_name: Name of the database to download
            force_update: Whether to force update even if the database exists

        Returns:
            Path to the downloaded database or None if download failed
        """
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader
                database_downloader = db_downloader
            except ImportError:
                logger.warning('Database downloader module not available')
                return None
        result = database_downloader.download_database(database_name, force_update)
        if result:
            self.available_databases = get_available_database_paths()
        return result

    def get_available_databases(self) -> Dict[str, Dict]:
        """
        Get information about all available databases.

        Returns:
            Dictionary mapping database names to their metadata
        """
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader
                database_downloader = db_downloader
            except ImportError:
                logger.warning('Database downloader module not available')
                return {}
        return database_downloader.get_available_databases()

    def check_for_updates(self) -> Dict[str, bool]:
        """
        Check if there are updates available for downloaded databases.

        Returns:
            Dictionary mapping database names to update availability
        """
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader
                database_downloader = db_downloader
            except ImportError:
                logger.warning('Database downloader module not available')
                return {}
        return database_downloader.check_for_updates()

    def get_database_path(self, database_name: str) -> Optional[str]:
        """
        Returns the full path to a database by name.

        Searches only the pre-discovered available_databases list (repo-local,
        PhreeqPython bundled). No secondary filesystem searches or silent fallbacks.

        Args:
            database_name: Name of the database (e.g., 'phreeqc.dat', 'pitzer.dat')

        Returns:
            Full path to the database or None if not found
        """
        if not database_name.lower().endswith('.dat'):
            database_name = database_name + '.dat'
        for db_path in self.available_databases:
            if os.path.basename(db_path).lower() == database_name.lower():
                return db_path
        logger.warning(f'Database {database_name} not found in available databases')
        return None

    def get_database_descriptions(self) -> Dict[str, str]:
        """
        Returns a dictionary of database names and their descriptions.

        Returns:
            Dictionary mapping database filename to description
        """
        descriptions = {'phreeqc.dat': 'Standard PHREEQC database with moderate set of elements', 'wateq4f.dat': 'WATEQ4F database with comprehensive elements and minerals for natural waters', 'minteq.v4.dat': 'MINTEQA2 version 4 database with comprehensive set of elements', 'minteq.dat': 'Original MINTEQA2 database for chemical equilibrium modeling', 'llnl.dat': 'Lawrence Livermore National Laboratory database - most comprehensive', 'pitzer.dat': 'Pitzer model for high ionic strength solutions and brines', 'sit.dat': 'Specific ion interaction theory database for high ionic strength', 'iso.dat': 'Database for isotope calculations and fractionation', 'Amm.dat': 'Special database for ammonium chemistry', 'ColdChem.dat': 'Database for low-temperature chemistry', 'frezchem.dat': 'Database for frozen chemistry and brines at low temperatures', 'Tipping_Hurley.dat': 'Database for humic substances and organic matter', 'PHREEQC_ThermoddemV1.10_15Dec2020.dat': 'Thermodynamic database from Thermoddem project', 'Concrete_PHR.dat': 'Database for concrete chemistry (PHREEQC)', 'Concrete_PZ.dat': 'Database for concrete chemistry (Pitzer)', 'core10.dat': 'Core database with minimal elements', 'Kinec.v2.dat': 'Kinetics database version 2', 'Kinec_v3.dat': 'Kinetics database version 3'}
        try:
            custom_db_path = os.path.join(database_downloader.METADATA_DIR, 'custom_databases.json')
            if os.path.exists(custom_db_path):
                with open(custom_db_path, 'r') as f:
                    custom_dbs = json.load(f)
                for db_name, db_info in custom_dbs.items():
                    descriptions[db_name] = db_info.get('description', 'Custom database')
        except Exception as e:
            logger.warning(f'Failed to load custom database descriptions: {e}')
        return descriptions
database_manager = DatabaseManager()
