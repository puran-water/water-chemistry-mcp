"""
Database Downloader Module

This module provides functionality to download, verify, and manage PHREEQC databases.
"""

import concurrent.futures
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests

from .database_registry import (
    DATABASE_FEATURES,
    OFFICIAL_DATABASES,
    get_database_url,
    register_custom_database_metadata,
)

logger = logging.getLogger(__name__)

# Constants
DATABASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "databases")
OFFICIAL_DIR = os.path.join(DATABASE_DIR, "official")
CUSTOM_DIR = os.path.join(DATABASE_DIR, "custom")
CACHED_DIR = os.path.join(DATABASE_DIR, "cached")
METADATA_DIR = os.path.join(DATABASE_DIR, "metadata")

# Download settings
DOWNLOAD_TIMEOUT = 60  # seconds
DOWNLOAD_RETRY_COUNT = 3
CHUNK_SIZE = 8192  # bytes
PARALLEL_DOWNLOADS = 3  # For batch downloads

# Ensure directories exist
for dir_path in [DATABASE_DIR, OFFICIAL_DIR, CUSTOM_DIR, CACHED_DIR, METADATA_DIR]:
    os.makedirs(dir_path, exist_ok=True)


class DownloadProgress:
    """Helper class to track download progress across threads."""

    def __init__(self, total_items: int):
        self.total = total_items
        self.completed = 0
        self.failed = 0
        self.lock = threading.Lock()

    def increment_completed(self):
        with self.lock:
            self.completed += 1

    def increment_failed(self):
        with self.lock:
            self.failed += 1


def download_database(
    database_name: str, force_update: bool = False, progress_callback: Optional[Callable[[float], None]] = None
) -> Optional[str]:
    """
    Downloads a PHREEQC database from the official source with progress reporting.

    Args:
        database_name: Name of the database to download
        force_update: Whether to force update even if the database exists
        progress_callback: Optional callback function that receives progress percentage

    Returns:
        Path to the downloaded database or None if download failed
    """
    if database_name not in OFFICIAL_DATABASES:
        logger.error(f"Unknown database: {database_name}")
        return None

    database_info = OFFICIAL_DATABASES[database_name]
    url = database_info["download_url"]
    version = database_info.get("version", "Unknown")

    # Target path for the database
    target_path = os.path.join(OFFICIAL_DIR, database_name)

    # Check if the database already exists
    if os.path.exists(target_path) and not force_update:
        # Check if it's the latest version
        metadata_path = os.path.join(METADATA_DIR, "database_versions.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)

                if database_name in metadata and metadata[database_name]["version"] == version:
                    logger.info(f"Database {database_name} (version {version}) already exists.")
                    # Call progress callback with 100% if provided
                    if progress_callback:
                        progress_callback(100.0)
                    return target_path
            except Exception as e:
                logger.warning(f"Failed to read database metadata: {e}")

    # Create a temporary file for downloading to ensure atomicity
    temp_file = tempfile.NamedTemporaryFile(delete=False, dir=OFFICIAL_DIR, prefix=f"{database_name}_tmp_")
    temp_path = temp_file.name
    temp_file.close()

    # Retry loop for resilience
    for attempt in range(DOWNLOAD_RETRY_COUNT):
        try:
            logger.info(f"Downloading database {database_name} from {url} (attempt {attempt+1}/{DOWNLOAD_RETRY_COUNT})")
            response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()

            # Get the total file size
            total_size = int(response.headers.get("content-length", 0))

            # Download the file
            downloaded = 0
            last_progress_log = 0

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:  # Filter out keep-alive chunks
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Update progress
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100

                            # Call progress callback if provided
                            if progress_callback:
                                progress_callback(progress)

                            # Log progress periodically (every 25%)
                            if progress >= last_progress_log + 25:
                                last_progress_log = int(progress / 25) * 25
                                logger.info(f"Downloaded {last_progress_log}% of {database_name}")

            # Verify the database before moving it to the final location
            # First import to avoid circular imports
            from .database_validator import validate_database

            validation_result = validate_database(temp_path)
            if not validation_result["valid"]:
                errors = validation_result.get("errors", ["Unknown validation error"])
                logger.error(f"Downloaded database {database_name} is invalid: {errors}")
                os.remove(temp_path)

                # If this was the last attempt, give up
                if attempt == DOWNLOAD_RETRY_COUNT - 1:
                    if progress_callback:
                        progress_callback(-1.0)  # Signal failure
                    return None

                # Otherwise, retry
                continue

            # Move the file to the final location
            shutil.move(temp_path, target_path)

            # Update metadata
            update_database_metadata(database_name, version, target_path)

            logger.info(f"Successfully downloaded database {database_name} (version {version})")

            # Final progress update
            if progress_callback:
                progress_callback(100.0)

            return target_path

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout downloading {database_name}, retrying...")
            continue

        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error downloading {database_name}, retrying...")
            continue

        except Exception as e:
            logger.error(f"Failed to download database {database_name}: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

            # Final progress update on failure
            if progress_callback:
                progress_callback(-1.0)  # Signal failure

            return None

    # If we get here, all attempts failed
    if progress_callback:
        progress_callback(-1.0)  # Signal failure
    return None


def verify_database(database_path: str) -> bool:
    """
    Verifies that a downloaded database file is valid.

    Args:
        database_path: Path to the database file

    Returns:
        True if the database is valid, False otherwise
    """
    # First import to avoid circular imports
    from .database_validator import validate_database

    # Use the more thorough validator
    validation_result = validate_database(database_path)
    return validation_result["valid"]


def update_database_metadata(database_name: str, version: str, database_path: str) -> None:
    """
    Updates the metadata for a database.

    Args:
        database_name: Name of the database
        version: Version of the database
        database_path: Path to the database file
    """
    metadata_path = os.path.join(METADATA_DIR, "database_versions.json")

    # Load existing metadata if available
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read database metadata, creating new file: {e}")
            metadata = {}
    else:
        metadata = {}

    # Calculate file hash for integrity checking
    file_hash = calculate_file_hash(database_path)

    # Update metadata
    metadata[database_name] = {
        "version": version,
        "download_date": datetime.now().isoformat(),
        "file_path": database_path,
        "file_hash": file_hash,
        "file_size": os.path.getsize(database_path),
    }

    # Save metadata
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)


def calculate_file_hash(file_path: str) -> str:
    """
    Calculates an MD5 hash of a file for integrity checking.

    Args:
        file_path: Path to the file

    Returns:
        MD5 hash of the file as a hex string
    """
    hash_md5 = hashlib.md5()

    with open(file_path, "rb") as f:
        # Read in 64k chunks
        for chunk in iter(lambda: f.read(65536), b""):
            hash_md5.update(chunk)

    return hash_md5.hexdigest()


def _download_worker(database_name: str, force_update: bool, progress: DownloadProgress) -> Tuple[str, Optional[str]]:
    """Worker function for parallel downloads."""
    result = download_database(database_name, force_update)

    if result:
        progress.increment_completed()
    else:
        progress.increment_failed()

    return database_name, result


def download_all_official_databases(force_update: bool = False, parallel: bool = True) -> Dict[str, str]:
    """
    Downloads all official PHREEQC databases.

    Args:
        force_update: Whether to force update existing databases
        parallel: Whether to download databases in parallel

    Returns:
        Dictionary mapping database names to their file paths
    """
    results = {}
    total_databases = len(OFFICIAL_DATABASES)

    if parallel and total_databases > 1:
        # Parallel download using ThreadPoolExecutor
        progress = DownloadProgress(total_databases)

        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as executor:
            # Submit all download tasks
            future_to_db = {
                executor.submit(_download_worker, db_name, force_update, progress): db_name
                for db_name in OFFICIAL_DATABASES
            }

            # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_db):
                db_name, db_path = future.result()
                if db_path:
                    results[db_name] = db_path

                # Log progress
                completed = progress.completed + progress.failed
                logger.info(f"Progress: {completed}/{total_databases} databases processed")
    else:
        # Sequential download
        for i, database_name in enumerate(OFFICIAL_DATABASES):
            logger.info(f"Downloading {database_name} ({i+1}/{total_databases})...")
            database_path = download_database(database_name, force_update)

            if database_path:
                results[database_name] = database_path
                logger.info(f"Successfully downloaded {database_name}")
            else:
                logger.warning(f"Failed to download {database_name}")

    # Final status log
    success_count = len(results)
    logger.info(f"Downloaded {success_count} of {total_databases} databases successfully")

    # Add a warning if some databases failed
    if success_count < total_databases:
        failed_dbs = [db for db in OFFICIAL_DATABASES if db not in results]
        logger.warning(f"Failed to download these databases: {', '.join(failed_dbs)}")

    return results


def get_available_databases() -> Dict[str, Dict[str, Any]]:
    """
    Gets information about all available databases (downloaded and not yet downloaded).

    Returns:
        Dictionary mapping database names to their metadata
    """
    result = {}

    # Load the metadata file
    metadata_path = os.path.join(METADATA_DIR, "database_versions.json")
    metadata = {}
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read database metadata: {e}")

    # Get metadata for all official databases
    for db_name, db_info in OFFICIAL_DATABASES.items():
        db_path = os.path.join(OFFICIAL_DIR, db_name)
        is_downloaded = os.path.exists(db_path)

        db_metadata = {
            "name": db_name,
            "description": db_info.get("description", ""),
            "is_downloaded": is_downloaded,
            "path": db_path if is_downloaded else None,
            "version": db_info.get("version", "Unknown"),
            "citation": db_info.get("citation", ""),
            "source": "USGS Official",
        }

        # Add feature information if available
        if db_name in DATABASE_FEATURES:
            db_metadata["features"] = DATABASE_FEATURES[db_name]

        # Add download information if available
        if db_name in metadata:
            db_metadata["download_date"] = metadata[db_name].get("download_date", "Unknown")
            db_metadata["file_size"] = metadata[db_name].get("file_size", 0)
            db_metadata["file_hash"] = metadata[db_name].get("file_hash", "")

        result[db_name] = db_metadata

    # Get metadata for custom databases
    custom_db_path = os.path.join(METADATA_DIR, "custom_databases.json")
    if os.path.exists(custom_db_path):
        try:
            with open(custom_db_path, "r") as f:
                custom_dbs = json.load(f)

            for db_name, db_info in custom_dbs.items():
                db_path = db_info.get("path", "")
                result[db_name] = {
                    "name": db_name,
                    "description": db_info.get("description", "Custom database"),
                    "is_downloaded": os.path.exists(db_path),
                    "path": db_path,
                    "imported_date": db_info.get("imported_date", "Unknown"),
                    "source": "Custom",
                    "original_path": db_info.get("original_path", ""),
                    "features": db_info.get("features", {}),
                }
        except Exception as e:
            logger.warning(f"Failed to read custom database metadata: {e}")

    return result


def check_for_updates() -> Dict[str, Dict[str, Any]]:
    """
    Checks if there are updates available for downloaded databases.

    Returns:
        Dictionary mapping database names to update information
    """
    # In a real implementation, this would check online for newer versions
    # For now, we'll just check if the local version matches the one in OFFICIAL_DATABASES

    results = {}
    metadata_path = os.path.join(METADATA_DIR, "database_versions.json")

    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            for db_name, db_info in OFFICIAL_DATABASES.items():
                current_version = db_info.get("version")
                db_path = os.path.join(OFFICIAL_DIR, db_name)
                is_downloaded = os.path.exists(db_path)

                update_info = {
                    "name": db_name,
                    "is_downloaded": is_downloaded,
                    "current_version": current_version,
                    "local_version": None,
                    "update_available": False,
                    "download_date": None,
                }

                if db_name in metadata:
                    local_version = metadata[db_name].get("version")
                    update_info["local_version"] = local_version
                    update_info["download_date"] = metadata[db_name].get("download_date")
                    update_info["update_available"] = local_version != current_version
                else:
                    update_info["update_available"] = is_downloaded  # If downloaded but no metadata, might need update

                results[db_name] = update_info
        except Exception as e:
            logger.warning(f"Failed to read database version metadata: {e}")
            # Set basic information if metadata can't be read
            for db_name, db_info in OFFICIAL_DATABASES.items():
                db_path = os.path.join(OFFICIAL_DIR, db_name)
                is_downloaded = os.path.exists(db_path)

                results[db_name] = {
                    "name": db_name,
                    "is_downloaded": is_downloaded,
                    "current_version": db_info.get("version", "Unknown"),
                    "local_version": None,
                    "update_available": is_downloaded,  # Conservatively suggest updating if metadata is missing
                    "download_date": None,
                }
    else:
        # No metadata file means we don't know about updates for any databases
        for db_name, db_info in OFFICIAL_DATABASES.items():
            db_path = os.path.join(OFFICIAL_DIR, db_name)
            is_downloaded = os.path.exists(db_path)

            results[db_name] = {
                "name": db_name,
                "is_downloaded": is_downloaded,
                "current_version": db_info.get("version", "Unknown"),
                "local_version": None,
                "update_available": is_downloaded,  # Conservatively suggest updating if metadata is missing
                "download_date": None,
            }

    return results


def import_custom_database(
    source_path: str, name: Optional[str] = None, description: Optional[str] = None
) -> Optional[str]:
    """
    Imports a user-provided custom database into the managed database directory.

    Args:
        source_path: Path to the source database file
        name: Optional name for the database (defaults to filename)
        description: Optional description of the database

    Returns:
        Path to the imported database or None if import failed
    """
    if not os.path.exists(source_path):
        logger.error(f"Source database file not found: {source_path}")
        return None

    # Extract the filename if no name is provided
    if name is None:
        name = os.path.basename(source_path)

    # Ensure the name has a .dat extension
    if not name.endswith(".dat"):
        name += ".dat"

    # Target path for the custom database
    target_path = os.path.join(CUSTOM_DIR, name)

    try:
        # Copy the database file
        shutil.copy2(source_path, target_path)

        # Verify the database - first import to avoid circular imports
        from .database_validator import validate_database

        validation_result = validate_database(target_path)
        if not validation_result["valid"]:
            os.remove(target_path)
            errors = validation_result.get("errors", ["Unknown validation error"])
            logger.error(f"Invalid database file: {source_path}. Errors: {errors}")
            return None

        # Extract metadata from the validation result
        metadata = {
            "path": target_path,
            "description": description or f"Custom database imported from {source_path}",
            "imported_date": datetime.now().isoformat(),
            "original_path": source_path,
            "validation": validation_result,
            "file_size": os.path.getsize(target_path),
            "file_hash": calculate_file_hash(target_path),
        }

        # Register the metadata
        register_custom_database_metadata(target_path, metadata)

        logger.info(f"Successfully imported custom database: {name}")
        return target_path

    except Exception as e:
        logger.error(f"Failed to import custom database: {e}")
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except:
                pass
        return None


def get_database_file_info(database_path: str) -> Dict[str, Any]:
    """
    Gets detailed information about a database file.

    Args:
        database_path: Path to the database file

    Returns:
        Dictionary with database file information
    """
    if not os.path.exists(database_path):
        return {"exists": False, "error": "File not found"}

    try:
        # Get basic file information
        file_size = os.path.getsize(database_path)
        modified_time = datetime.fromtimestamp(os.path.getmtime(database_path))
        file_hash = calculate_file_hash(database_path)

        # Validate the database
        from .database_validator import validate_database

        validation_result = validate_database(database_path, thorough=True)

        # Extract database content information
        from .database_validator import extract_database_elements, extract_database_minerals

        elements = extract_database_elements(database_path)
        minerals = extract_database_minerals(database_path)

        # Compile the information
        info = {
            "exists": True,
            "file_name": os.path.basename(database_path),
            "file_path": database_path,
            "file_size_bytes": file_size,
            "file_size_kb": file_size / 1024,
            "modified_time": modified_time.isoformat(),
            "file_hash": file_hash,
            "is_valid": validation_result["valid"],
            "validation": validation_result,
            "element_count": len(elements),
            "mineral_count": len(minerals),
            "has_pitzer": validation_result["statistics"].get("has_pitzer", False),
            "has_sit": validation_result["statistics"].get("has_sit", False),
            "has_surface": validation_result["statistics"].get("has_surface", False),
            "has_exchange": validation_result["statistics"].get("has_exchange", False),
            "has_kinetics": validation_result["statistics"].get("has_kinetics", False),
        }

        return info

    except Exception as e:
        logger.error(f"Error getting database file info: {e}")
        return {
            "exists": True,
            "file_name": os.path.basename(database_path),
            "file_path": database_path,
            "error": str(e),
        }
