"""
Database management module for PHREEQC.

Provides centralized functionality for database selection, validation, and information.
"""

import logging
import os
import shutil
import glob
import json
from typing import Dict, List, Optional, Tuple, Union

from .import_helpers import get_available_database_paths, get_default_database, USGS_PHREEQC_DATABASE_PATH
from .mineral_registry import COMMON_MINERALS, DATABASE_SPECIFIC_MINERALS

# Import database cache functions - will be imported later to avoid circular imports
database_cache = None
database_downloader = None

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages PHREEQC database files and their selection.
    """

    def __init__(self):
        """Initialize the database manager."""
        self.available_databases = get_available_database_paths()
        self.default_database = get_default_database()
        self._database_info_cache = {}  # Cache database info to avoid repeated file reads

    def resolve_database_path(self, database_path: str) -> Optional[str]:
        """
        Resolves a database path or name to a full path.

        Args:
            database_path: Full path or just database filename

        Returns:
            Full path to database if found, None otherwise
        """
        # If it's already a valid full path, return it
        if os.path.exists(database_path) and database_path.endswith(".dat"):
            return database_path

        # If it's just a filename, try to find it in available databases
        if not os.path.dirname(database_path):  # No directory component
            db_name = database_path
            for db in self.available_databases:
                if os.path.basename(db) == db_name:
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
        # Try to resolve the path first
        resolved_path = self.resolve_database_path(database_path)
        if resolved_path:
            database_path = resolved_path

        # Check if file exists
        if not os.path.exists(database_path):
            logger.warning(f"Database file does not exist: {database_path}")
            return False

        # Check if file has .dat extension
        if not database_path.endswith(".dat"):
            logger.warning(f"Database file does not have .dat extension: {database_path}")
            return False

        # Basic validation - check for SOLUTION_MASTER_SPECIES and SOLUTION_SPECIES blocks
        try:
            with open(database_path, "r", errors="ignore") as f:
                content = f.read().upper()
                if "SOLUTION_MASTER_SPECIES" not in content:
                    logger.warning(f"Database file does not contain SOLUTION_MASTER_SPECIES: {database_path}")
                    return False
                if "SOLUTION_SPECIES" not in content:
                    logger.warning(f"Database file does not contain SOLUTION_SPECIES: {database_path}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Error validating database file {database_path}: {e}")
            return False

    def get_database_info(self, database_path: str) -> Dict:
        """
        Returns information about a database file.

        Args:
            database_path: Path to the database file

        Returns:
            Dictionary with database information
        """
        # Import cache module on first use (to avoid circular imports)
        global database_cache
        if database_cache is None:
            try:
                from . import database_cache as db_cache

                database_cache = db_cache
            except ImportError:
                logger.warning("Database cache module not available")

        # Check in-memory cache first
        if database_path in self._database_info_cache:
            return self._database_info_cache[database_path]

        # Try to get from disk cache if available
        if database_cache is not None:
            cached_info = database_cache.get_cached_database_info(database_path)
            if cached_info:
                # Store in in-memory cache too
                self._database_info_cache[database_path] = cached_info
                return cached_info

        # Basic info
        db_name = os.path.basename(database_path)
        is_usgs = USGS_PHREEQC_DATABASE_PATH is not None and database_path.startswith(USGS_PHREEQC_DATABASE_PATH)
        is_default = database_path == self.default_database

        # Read file to get more detailed info
        try:
            with open(database_path, "r", errors="ignore") as f:
                content = f.read()

            # Count elements (rough estimate)
            element_count = content.upper().count("ELEMENT")

            # Count phases/minerals (rough estimate)
            mineral_count = content.upper().count("PHASES")

            # Extract database version or creation date if available
            version_info = "Unknown"
            for line in content.split("\n")[:50]:  # Check first 50 lines
                if "version" in line.lower() or "revised" in line.lower() or "modified" in line.lower():
                    version_info = line.strip()
                    break

            # Analyze database capabilities
            features = {
                "surface_complexation": "SURFACE_MASTER_SPECIES" in content.upper(),
                "redox": "REDOX" in content.upper(),
                "exchange": "EXCHANGE_MASTER_SPECIES" in content.upper(),
                "isotopes": "ISOTOPE" in content.upper(),
                "solids": "PHASES" in content.upper(),
                "kinetics": "RATES" in content.upper(),
                "pitzer": "PITZER" in content.upper(),
            }

            info = {
                "name": db_name,
                "path": database_path,
                "is_usgs": is_usgs,
                "is_default": is_default,
                "element_count": element_count,
                "mineral_count": mineral_count,
                "version_info": version_info,
                "features": features,
                "file_size_kb": os.path.getsize(database_path) // 1024,
            }

            # Cache the result both in memory and on disk
            self._database_info_cache[database_path] = info

            # Store in disk cache for future use
            if database_cache is not None:
                database_cache.cache_database_info(database_path, info)

            return info

        except Exception as e:
            logger.error(f"Error getting database info for {database_path}: {e}")
            error_info = {
                "name": db_name,
                "path": database_path,
                "is_usgs": is_usgs,
                "is_default": is_default,
                "error": str(e),
            }
            # Don't cache error results
            return error_info

    def recommend_database(self, purpose: str = "general") -> str:
        """
        Recommends a database based on the specific purpose.

        Args:
            purpose: The intended use case ('general', 'high_salinity', 'minerals', 'isotopes', etc.)

        Returns:
            Path to the recommended database
        """
        # Ensure we have the available databases
        if not self.available_databases:
            logger.warning("No databases available for recommendation")
            return self.default_database or ""

        # Collect database information
        db_info_list = [self.get_database_info(db) for db in self.available_databases]

        # Filter by purpose
        if purpose.lower() == "high_salinity":
            # For high salinity solutions, prefer Pitzer model
            pitzer_dbs = [db for db in db_info_list if db["features"].get("pitzer", False)]
            if pitzer_dbs:
                return pitzer_dbs[0]["path"]

        elif purpose.lower() == "minerals":
            # For mineral calculations, prefer databases with most minerals
            db_info_list.sort(key=lambda x: x.get("mineral_count", 0), reverse=True)
            return db_info_list[0]["path"]

        elif purpose.lower() == "isotopes":
            # For isotope calculations
            isotope_dbs = [db for db in db_info_list if db["features"].get("isotopes", False)]
            if isotope_dbs:
                return isotope_dbs[0]["path"]

        elif purpose.lower() == "surface":
            # For surface complexation
            surface_dbs = [db for db in db_info_list if db["features"].get("surface_complexation", False)]
            if surface_dbs:
                return surface_dbs[0]["path"]

        elif purpose.lower() == "kinetics":
            # For kinetic reactions
            kinetic_dbs = [db for db in db_info_list if db["features"].get("kinetics", False)]
            if kinetic_dbs:
                return kinetic_dbs[0]["path"]

        # Default recommendation strategy for general purpose
        # Prioritize USGS databases first
        usgs_dbs = [db for db in db_info_list if db.get("is_usgs", False)]
        if usgs_dbs:
            # Look for phreeqc.dat first - it has common minerals like Calcite
            for db in usgs_dbs:
                if "phreeqc.dat" in db["name"].lower():
                    return db["path"]
            # Then try WATEQ4F which is comprehensive
            for db in usgs_dbs:
                if "wateq" in db["name"].lower():
                    return db["path"]
            # LLNL is comprehensive but missing common minerals
            for db in usgs_dbs:
                if "llnl" in db["name"].lower():
                    return db["path"]

        # If no USGS databases or specific matches, use best available based on features
        db_info_list.sort(key=lambda x: sum(1 for f in x.get("features", {}).values() if f), reverse=True)
        if db_info_list:
            return db_info_list[0]["path"]

        # Fallback to default
        return self.default_database or ""

    def get_compatible_minerals(
        self, database_path: str, requested_minerals: Optional[List[str]] = None
    ) -> Union[List[str], Dict[str, str]]:
        """
        Returns a list of minerals compatible with the specified database.

        Args:
            database_path: Path to the PHREEQC database
            requested_minerals: Optional list of requested minerals

        Returns:
            If requested_minerals is None: List of all compatible minerals
            If requested_minerals is provided: Dictionary mapping requested minerals to
                                             compatible alternatives or None if not compatible
        """
        # Get the database filename
        db_name = os.path.basename(database_path)

        # Start with common minerals available in all databases
        compatible_minerals = list(COMMON_MINERALS.keys())

        # Add database-specific minerals if available
        if db_name in DATABASE_SPECIFIC_MINERALS:
            compatible_minerals.extend(DATABASE_SPECIFIC_MINERALS[db_name].keys())

        # Skip database file validation - the mineral registry is authoritative
        # The database_validator.extract_database_minerals() has regex issues and
        # incorrectly reports that common minerals like Calcite don't exist
        logger.debug(f"Using mineral registry for {db_name} without file validation")

        # If specific minerals were requested, check compatibility and suggest alternatives
        if requested_minerals:
            mineral_mapping = {}
            for mineral in requested_minerals:
                if mineral in compatible_minerals:
                    mineral_mapping[mineral] = mineral  # Direct match
                else:
                    # Try to find an alternative
                    found_alternative = False

                    # Check if this mineral is listed as an alternative name for any mineral in this database
                    if db_name in DATABASE_SPECIFIC_MINERALS:
                        for db_mineral, info in DATABASE_SPECIFIC_MINERALS[db_name].items():
                            if mineral in info["alternative_names"]:
                                # Verify this mineral is actually in the database
                                if db_mineral in compatible_minerals:
                                    mineral_mapping[mineral] = db_mineral
                                    found_alternative = True
                                    logger.info(
                                        f"Substituting mineral '{mineral}' with '{db_mineral}' for database {db_name}"
                                    )
                                    break

                    # If not found in the specific database, check common minerals
                    if not found_alternative:
                        for common_mineral, info in COMMON_MINERALS.items():
                            if mineral in info["alternative_names"]:
                                # Verify this mineral is actually in the database
                                if common_mineral in compatible_minerals:
                                    mineral_mapping[mineral] = common_mineral
                                    found_alternative = True
                                    logger.info(
                                        f"Substituting mineral '{mineral}' with common mineral '{common_mineral}'"
                                    )
                                    break

                    # Special case handling for common problematic minerals
                    if not found_alternative:
                        if (
                            mineral == "Ferrihydrite"
                            and db_name == "phreeqc.dat"
                            and "Fe(OH)3(a)" in compatible_minerals
                        ):
                            mineral_mapping[mineral] = "Fe(OH)3(a)"
                            found_alternative = True
                            logger.info(
                                f"Special case: Substituting '{mineral}' with 'Fe(OH)3(a)' for database {db_name}"
                            )
                        elif mineral == "Brucite" and db_name == "phreeqc.dat" and "Mg(OH)2" in compatible_minerals:
                            mineral_mapping[mineral] = "Mg(OH)2"
                            found_alternative = True
                            logger.info(f"Special case: Substituting '{mineral}' with 'Mg(OH)2' for database {db_name}")
                        elif mineral == "Strengite" and db_name == "phreeqc.dat" and "Vivianite" in compatible_minerals:
                            mineral_mapping[mineral] = "Vivianite"
                            found_alternative = True
                            logger.info(f"Special case: Using Vivianite as phosphate mineral for database {db_name}")

                    # If still not found, try all other databases to find a potential match
                    if not found_alternative:
                        # Get the formula for the requested mineral
                        mineral_formula = self._get_mineral_formula(mineral)
                        if mineral_formula:
                            # Try to find a mineral with the same formula in the target database
                            for db_mineral, db_info in DATABASE_SPECIFIC_MINERALS.get(db_name, {}).items():
                                if db_info["formula"] == mineral_formula and db_mineral in compatible_minerals:
                                    mineral_mapping[mineral] = db_mineral
                                    found_alternative = True
                                    logger.info(
                                        f"Substituting mineral '{mineral}' with '{db_mineral}' based on formula match ({mineral_formula})"
                                    )
                                    break

                    # Try a more comprehensive approach if still not found
                    if not found_alternative:
                        for other_db, minerals in DATABASE_SPECIFIC_MINERALS.items():
                            if other_db == db_name:
                                continue  # Skip the current database, already checked

                            for other_mineral, info in minerals.items():
                                if mineral in info["alternative_names"] or info["formula"] == self._get_mineral_formula(
                                    mineral
                                ):
                                    # Found a potential substitute in another database
                                    # Try to find this formula in the current database
                                    formula = info["formula"]
                                    for db_mineral, db_info in DATABASE_SPECIFIC_MINERALS.get(db_name, {}).items():
                                        if db_info["formula"] == formula and db_mineral in compatible_minerals:
                                            mineral_mapping[mineral] = db_mineral
                                            found_alternative = True
                                            logger.info(
                                                f"Substituting mineral '{mineral}' with '{db_mineral}' based on formula match"
                                            )
                                            break

                                    if found_alternative:
                                        break

                            if found_alternative:
                                break

                    if not found_alternative:
                        # No compatible alternative found, try to suggest the closest match
                        closest_match = None
                        mineral_formula = self._get_mineral_formula(mineral)

                        if mineral_formula:
                            # Find minerals with similar formulas
                            for db_mineral in compatible_minerals:
                                formula = self._get_mineral_formula(db_mineral)
                                if formula and self._similar_formulas(formula, mineral_formula):
                                    closest_match = db_mineral
                                    break

                        if closest_match:
                            mineral_mapping[mineral] = closest_match
                            logger.warning(
                                f"Using best guess: Substituting '{mineral}' with '{closest_match}' for database {db_name}"
                            )
                        else:
                            # No compatible alternative found
                            mineral_mapping[mineral] = None
                            logger.warning(
                                f"No compatible substitute found for mineral '{mineral}' in database '{db_name}'"
                            )

            return mineral_mapping

        # If no specific minerals requested, return all compatible minerals
        return compatible_minerals

    def _similar_formulas(self, formula1: str, formula2: str) -> bool:
        """
        Check if two chemical formulas are similar (same elements, possibly different proportions).

        Args:
            formula1: First chemical formula
            formula2: Second chemical formula

        Returns:
            True if formulas have the same elements, False otherwise
        """
        import re

        def extract_elements(formula):
            # Extract element symbols from a chemical formula
            return set(re.findall(r"([A-Z][a-z]?)", formula))

        elements1 = extract_elements(formula1)
        elements2 = extract_elements(formula2)

        # Consider formulas similar if they share at least 2/3 of elements
        common_elements = elements1.intersection(elements2)
        if len(common_elements) >= min(len(elements1), len(elements2)) * 2 / 3:
            return True

        return False

    def _get_mineral_formula(self, mineral_name: str) -> Optional[str]:
        """
        Helper method to get a mineral formula from the registry.

        Args:
            mineral_name: Name of the mineral

        Returns:
            Formula if found, None otherwise
        """
        # Check common minerals first
        if mineral_name in COMMON_MINERALS:
            return COMMON_MINERALS[mineral_name]["formula"]

        # Check all database-specific minerals
        for db_name, minerals in DATABASE_SPECIFIC_MINERALS.items():
            if mineral_name in minerals:
                return minerals[mineral_name]["formula"]

            # Check alternative names
            for db_mineral, info in minerals.items():
                if mineral_name in info["alternative_names"]:
                    return info["formula"]

        return None

    def register_custom_database(
        self, database_path: str, name: Optional[str] = None, description: Optional[str] = None
    ) -> Optional[str]:
        """
        Register a user-provided custom database.

        Args:
            database_path: Path to the original database file
            name: Optional name for the database (defaults to filename)
            description: Optional description of the database

        Returns:
            Path to the registered database or None if registration failed
        """
        # Import downloader module on first use (to avoid circular imports)
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader

                database_downloader = db_downloader
            except ImportError:
                logger.warning("Database downloader module not available")
                return None

        if not os.path.exists(database_path):
            logger.error(f"Database file not found: {database_path}")
            return None

        # Extract the filename if no name is provided
        if name is None:
            name = os.path.basename(database_path)

        # Ensure the name has a .dat extension
        if not name.endswith(".dat"):
            name += ".dat"

        # Create the custom database directory
        custom_dir = os.path.join(database_downloader.DATABASE_DIR, "custom")
        os.makedirs(custom_dir, exist_ok=True)

        # Target path for the custom database
        target_path = os.path.join(custom_dir, name)

        # Copy the database file
        try:
            shutil.copy2(database_path, target_path)

            # Validate the database
            if not self.validate_database_path(target_path):
                os.remove(target_path)
                logger.error(f"Invalid database file: {database_path}")
                return None

            # Register the database in the metadata
            metadata_path = os.path.join(database_downloader.METADATA_DIR, "custom_databases.json")

            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r") as f:
                        metadata = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read custom database metadata, creating new: {e}")
                    metadata = {}
            else:
                metadata = {}

            import datetime

            metadata[name] = {
                "path": target_path,
                "description": description or f"Custom database imported from {database_path}",
                "imported_date": datetime.datetime.now().isoformat(),
                "original_path": database_path,
            }

            os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)

            # Refresh available databases
            self.available_databases = get_available_database_paths()

            logger.info(f"Successfully registered custom database: {name}")
            return target_path

        except Exception as e:
            logger.error(f"Failed to register custom database: {e}")
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except:
                    pass
            return None

    def download_database(self, database_name: str, force_update: bool = False) -> Optional[str]:
        """
        Download an official database.

        Args:
            database_name: Name of the database to download
            force_update: Whether to force update even if the database exists

        Returns:
            Path to the downloaded database or None if download failed
        """
        # Import downloader module on first use (to avoid circular imports)
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader

                database_downloader = db_downloader
            except ImportError:
                logger.warning("Database downloader module not available")
                return None

        # Download the database
        result = database_downloader.download_database(database_name, force_update)

        if result:
            # Refresh available databases
            self.available_databases = get_available_database_paths()

        return result

    def get_available_databases(self) -> Dict[str, Dict]:
        """
        Get information about all available databases.

        Returns:
            Dictionary mapping database names to their metadata
        """
        # Import downloader module on first use (to avoid circular imports)
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader

                database_downloader = db_downloader
            except ImportError:
                logger.warning("Database downloader module not available")
                return {}

        return database_downloader.get_available_databases()

    def check_for_updates(self) -> Dict[str, bool]:
        """
        Check if there are updates available for downloaded databases.

        Returns:
            Dictionary mapping database names to update availability
        """
        # Import downloader module on first use (to avoid circular imports)
        global database_downloader
        if database_downloader is None:
            try:
                from . import database_downloader as db_downloader

                database_downloader = db_downloader
            except ImportError:
                logger.warning("Database downloader module not available")
                return {}

        return database_downloader.check_for_updates()

    def get_database_path(self, database_name: str) -> Optional[str]:
        """
        Returns the full path to a database by name.

        Args:
            database_name: Name of the database (e.g., 'phreeqc.dat', 'pitzer.dat')

        Returns:
            Full path to the database or None if not found
        """
        # Ensure database name has .dat extension
        if not database_name.lower().endswith(".dat"):
            database_name = database_name + ".dat"
            logger.info(f"Added .dat extension to database name: {database_name}")

        # First check if we have this database in available_databases
        for db_path in self.available_databases:
            if os.path.basename(db_path).lower() == database_name.lower():
                logger.info(f"Found database {database_name} in available_databases: {db_path}")
                return db_path

        # If not found, try to search in common PHREEQC locations
        # Make sure we include both Windows and WSL paths
        wsl_prefixes = ["", "/mnt/c"]  # Empty string for native paths, /mnt/c for WSL paths to Windows

        # Start with common base paths
        base_paths = [
            # USGS PHREEQC installation
            USGS_PHREEQC_DATABASE_PATH,
            # MCP server database directory
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "databases", "official"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "databases", "custom"),
            # PhreeqPython database directory (try multiple possible locations)
            os.path.join(os.path.dirname(__file__), "..", "..", "phreeqpython", "phreeqpython", "database"),
            os.path.join(os.path.dirname(__file__), "..", "..", "phreeqpython", "database"),
            # Common PHREEQC installation locations (standardizing on both Windows path formats)
            "/usr/local/share/phreeqc/database",
            "/usr/share/phreeqc/database",
        ]

        # Add Windows-specific paths with prefixes for WSL compatibility
        windows_paths = [
            "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database",
            "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x6\\database",
            "C:\\Program Files\\USGS\\phreeqc-3.7.3-15968-x64\\database",
            "C:\\Program Files\\USGS\\phreeqc-3.6.2-15100-x64\\database",
            "C:\\Program Files\\USGS\\phreeqc-3.5.0-14000-x64\\database",
            "C:\\Program Files\\USGS\\phreeqc-3.4.0-12927-x64\\database",
        ]

        # Create common_paths by combining base_paths and Windows paths with prefixes
        common_paths = list(base_paths)  # Start with base paths

        # Add Windows paths with appropriate prefixes for WSL
        for prefix in wsl_prefixes:
            for win_path in windows_paths:
                # For WSL paths, convert Windows backslashes to forward slashes
                if prefix:
                    win_path_conv = win_path.replace("\\", "/")
                    common_paths.append(os.path.join(prefix, win_path_conv))
                else:
                    common_paths.append(win_path)

        logger.info(f"Searching for database {database_name} in {len(common_paths)} common locations")

        # Search for the database in these paths
        for path in common_paths:
            try:
                if path and os.path.exists(path):
                    db_path = os.path.join(path, database_name)
                    if os.path.exists(db_path):
                        logger.info(f"Found database {database_name} at: {db_path}")
                        return db_path
            except Exception as e:
                logger.debug(f"Error checking path {path}: {e}")
                pass

        # Last resort - try searching in cached database directory
        try:
            cached_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "databases", "cached")
            if os.path.exists(cached_dir):
                # Try exact match first
                db_path = os.path.join(cached_dir, database_name)
                if os.path.exists(db_path):
                    logger.info(f"Found database {database_name} in cached directory: {db_path}")
                    return db_path

                # Try case-insensitive search as a fallback
                for filename in os.listdir(cached_dir):
                    if filename.lower() == database_name.lower():
                        db_path = os.path.join(cached_dir, filename)
                        logger.info(f"Found database {database_name} (as {filename}) in cached directory: {db_path}")
                        return db_path
        except Exception as e:
            logger.debug(f"Error checking cached directory: {e}")

        # Database not found
        logger.warning(f"Database {database_name} not found in any known locations")

        # As a fallback, try to see if a different default database is available
        if database_name.lower() != "phreeqc.dat":
            # Try to get phreeqc.dat as a fallback
            fallback_path = self.get_database_path("phreeqc.dat")
            if fallback_path:
                logger.warning(f"Using phreeqc.dat as fallback for {database_name}")
                return fallback_path

        return None

    def get_database_descriptions(self) -> Dict[str, str]:
        """
        Returns a dictionary of database names and their descriptions.

        Returns:
            Dictionary mapping database filename to description
        """
        descriptions = {
            "phreeqc.dat": "Standard PHREEQC database with moderate set of elements",
            "wateq4f.dat": "WATEQ4F database with comprehensive elements and minerals for natural waters",
            "minteq.v4.dat": "MINTEQA2 version 4 database with comprehensive set of elements",
            "minteq.dat": "Original MINTEQA2 database for chemical equilibrium modeling",
            "llnl.dat": "Lawrence Livermore National Laboratory database - most comprehensive",
            "pitzer.dat": "Pitzer model for high ionic strength solutions and brines",
            "sit.dat": "Specific ion interaction theory database for high ionic strength",
            "iso.dat": "Database for isotope calculations and fractionation",
            "Amm.dat": "Special database for ammonium chemistry",
            "ColdChem.dat": "Database for low-temperature chemistry",
            "frezchem.dat": "Database for frozen chemistry and brines at low temperatures",
            "Tipping_Hurley.dat": "Database for humic substances and organic matter",
            "PHREEQC_ThermoddemV1.10_15Dec2020.dat": "Thermodynamic database from Thermoddem project",
            "Concrete_PHR.dat": "Database for concrete chemistry (PHREEQC)",
            "Concrete_PZ.dat": "Database for concrete chemistry (Pitzer)",
            "core10.dat": "Core database with minimal elements",
            "Kinec.v2.dat": "Kinetics database version 2",
            "Kinec_v3.dat": "Kinetics database version 3",
        }

        # Add custom databases if available
        try:
            custom_db_path = os.path.join(database_downloader.METADATA_DIR, "custom_databases.json")
            if os.path.exists(custom_db_path):
                with open(custom_db_path, "r") as f:
                    custom_dbs = json.load(f)

                for db_name, db_info in custom_dbs.items():
                    descriptions[db_name] = db_info.get("description", "Custom database")
        except Exception as e:
            logger.warning(f"Failed to load custom database descriptions: {e}")

        return descriptions


# Singleton instance
database_manager = DatabaseManager()
