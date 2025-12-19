"""
Database Cache Module

This module provides functionality for caching database information
to improve performance of database operations.
"""

import os
import json
import time
import hashlib
import logging
import pickle
import gzip
import tempfile
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union, List, Tuple, Set

# Import database directory constants
from .database_downloader import DATABASE_DIR, OFFICIAL_DIR, CUSTOM_DIR, CACHED_DIR, METADATA_DIR

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_EXPIRATION = timedelta(days=7)  # Default expiration time (1 week)
CACHE_COMPRESSION_LEVEL = 6  # Default compression level (1-9, higher = better compression but slower)
MEMORY_CACHE_ENABLED = True  # Enable in-memory caching for frequently accessed data
CACHE_MAX_AGE_MAP = {  # Different expiration times for different cache types
    "database_info": timedelta(days=7),  # Basic database info
    "minerals": timedelta(days=30),  # Mineral definitions rarely change
    "elements": timedelta(days=30),  # Element definitions rarely change
    "species": timedelta(days=30),  # Species definitions rarely change
    "validation": timedelta(days=14),  # Validation results
    "comparison": timedelta(days=1),  # Database comparisons
    "mineral_compatibility": timedelta(hours=12),  # Mineral compatibility checks
}

# In-memory cache
_memory_cache = {}
_memory_cache_hits = 0
_memory_cache_misses = 0
_memory_cache_max_size = 100  # Maximum number of items to store in memory cache


def get_cache_path(database_path: str, cache_type: str = "database_info") -> str:
    """
    Get the path to the cache file for a database.

    Args:
        database_path: Path to the database file
        cache_type: Type of cached data (affects expiration and directory structure)

    Returns:
        Path to the cache file
    """
    # Create a hash of the database path to use as the cache filename
    hash_obj = hashlib.md5(database_path.encode())
    hash_hex = hash_obj.hexdigest()

    # Different filename extensions based on cache_type
    if cache_type in ["minerals", "elements", "species"]:
        ext = ".pickle.gz"  # Compressed pickle for larger structured data
    else:
        ext = ".json"  # JSON for smaller data that may need human inspection

    # Create subdirectory for cache type
    cache_subdir = os.path.join(CACHED_DIR, cache_type)
    os.makedirs(cache_subdir, exist_ok=True)

    return os.path.join(cache_subdir, hash_hex + ext)


def is_cache_valid(cache_path: str, database_path: str, cache_type: str = "database_info") -> bool:
    """
    Check if a cache is valid and up-to-date.

    Args:
        cache_path: Path to the cache file
        database_path: Path to the original database file
        cache_type: Type of cached data (affects expiration time)

    Returns:
        True if the cache is valid, False otherwise
    """
    if not os.path.exists(cache_path):
        return False

    # Check if the original database file exists
    if not os.path.exists(database_path):
        return False

    try:
        # Check cache age against the appropriate expiration time
        cache_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
        expiration = CACHE_MAX_AGE_MAP.get(cache_type, CACHE_EXPIRATION)

        if datetime.now() - cache_time > expiration:
            logger.debug(f"Cache expired for {database_path} (type: {cache_type})")
            return False

        # Check if the database has been modified since the cache was created
        db_time = datetime.fromtimestamp(os.path.getmtime(database_path))
        if db_time > cache_time:
            logger.debug(f"Database {database_path} modified after cache was created")
            return False

        # Hash check for content-based cache validation (helps detect corrupted caches)
        # Only do this for small files to avoid performance impact
        file_size = os.path.getsize(cache_path)
        if file_size < 10240:  # 10KB
            try:
                # Read the first few bytes to check if it's JSON or gzip
                with open(cache_path, "rb") as f:
                    magic_bytes = f.read(2)

                if magic_bytes == b"\x1f\x8b":  # gzip magic number
                    # If it's gzip, just try to open it to validate
                    with gzip.open(cache_path, "rb") as f:
                        data = pickle.load(f)
                else:
                    # If it's JSON, try to parse it
                    with open(cache_path, "r") as f:
                        data = json.load(f)
            except Exception:
                logger.warning(f"Cache file {cache_path} appears to be corrupted")
                return False

        return True
    except Exception as e:
        logger.warning(f"Error checking cache validity: {e}")
        return False


def cache_database_info(database_path: str, database_info: Dict[str, Any], cache_type: str = "database_info") -> str:
    """
    Cache database information for faster access.

    Args:
        database_path: Path to the database file
        database_info: Dictionary of database information to cache
        cache_type: Type of cached data (affects storage format and expiration)

    Returns:
        Path to the cache file
    """
    cache_path = get_cache_path(database_path, cache_type)

    try:
        # Create cache directory if it doesn't exist
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        # Add metadata to the cached data
        cache_data = database_info.copy() if isinstance(database_info, dict) else database_info

        # Use atomic write pattern to prevent partial/corrupted cache files
        temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(cache_path))
        try:
            with os.fdopen(temp_fd, "wb" if cache_path.endswith(".gz") else "w") as f:
                if cache_path.endswith(".json"):
                    # Add metadata only for JSON caches
                    if isinstance(cache_data, dict):
                        cache_data["_cache_timestamp"] = datetime.now().isoformat()
                        cache_data["_database_path"] = database_path
                        cache_data["_cache_type"] = cache_type

                    json.dump(cache_data, f, indent=2)
                elif cache_path.endswith(".pickle.gz"):
                    with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=CACHE_COMPRESSION_LEVEL) as gzf:
                        pickle.dump(cache_data, gzf, protocol=pickle.HIGHEST_PROTOCOL)
                else:
                    # Default to JSON
                    json.dump(cache_data, f, indent=2)

            # Atomic rename for safe cache update
            shutil.move(temp_path, cache_path)

        except Exception as e:
            os.unlink(temp_path)
            raise e

        logger.debug(f"Cached {cache_type} data for {database_path}")

        # Also update the in-memory cache if enabled
        if MEMORY_CACHE_ENABLED:
            cache_key = (database_path, cache_type)
            _memory_cache[cache_key] = cache_data

            # Limit in-memory cache size
            if len(_memory_cache) > _memory_cache_max_size:
                # Simple LRU-like pruning: remove random entries when cache is full
                # In a production system, use a proper LRU cache implementation
                keys_to_remove = list(_memory_cache.keys())[:-_memory_cache_max_size]
                for key in keys_to_remove:
                    del _memory_cache[key]

        return cache_path
    except Exception as e:
        logger.warning(f"Failed to cache database info: {e}")
        return ""


def get_cached_database_info(database_path: str, cache_type: str = "database_info") -> Optional[Any]:
    """
    Get cached database information if available.

    Args:
        database_path: Path to the database file
        cache_type: Type of cached data

    Returns:
        Cached database information or None if cache is invalid
    """
    global _memory_cache_hits, _memory_cache_misses

    # Check in-memory cache first if enabled
    if MEMORY_CACHE_ENABLED:
        cache_key = (database_path, cache_type)
        if cache_key in _memory_cache:
            _memory_cache_hits += 1
            logger.debug(f"Memory cache hit for {database_path} (type: {cache_type})")

            cache_data = _memory_cache[cache_key]

            # For JSON-style caches, remove metadata
            if isinstance(cache_data, dict) and cache_type == "database_info":
                cache_data = cache_data.copy()
                cache_data.pop("_cache_timestamp", None)
                cache_data.pop("_database_path", None)
                cache_data.pop("_cache_type", None)

            return cache_data
        else:
            _memory_cache_misses += 1

    # Check disk cache
    cache_path = get_cache_path(database_path, cache_type)

    if is_cache_valid(cache_path, database_path, cache_type):
        try:
            if cache_path.endswith(".json"):
                with open(cache_path, "r") as f:
                    cache_data = json.load(f)

                # Remove cache metadata before returning
                if isinstance(cache_data, dict):
                    cache_data = cache_data.copy()
                    cache_data.pop("_cache_timestamp", None)
                    cache_data.pop("_database_path", None)
                    cache_data.pop("_cache_type", None)

            elif cache_path.endswith(".pickle.gz"):
                with gzip.open(cache_path, "rb") as f:
                    cache_data = pickle.load(f)
            else:
                # Unknown format
                logger.warning(f"Unknown cache format: {cache_path}")
                return None

            # Update memory cache with disk data
            if MEMORY_CACHE_ENABLED:
                cache_key = (database_path, cache_type)
                _memory_cache[cache_key] = cache_data

            logger.debug(f"Using cached {cache_type} data for {database_path}")
            return cache_data

        except Exception as e:
            logger.warning(f"Failed to read cache {cache_path}: {e}")
            # Try to recover by invalidating the cache
            try:
                os.remove(cache_path)
            except:
                pass

    return None


def invalidate_cache(database_path: str, cache_type: Optional[str] = None) -> bool:
    """
    Invalidate the cache for a database.

    Args:
        database_path: Path to the database file
        cache_type: Type of cached data to invalidate (None = all types)

    Returns:
        True if the cache was invalidated, False otherwise
    """
    if cache_type:
        # Invalidate specific cache type
        cache_path = get_cache_path(database_path, cache_type)

        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                logger.debug(f"Invalidated {cache_type} cache for {database_path}")

                # Also remove from memory cache
                if MEMORY_CACHE_ENABLED:
                    cache_key = (database_path, cache_type)
                    _memory_cache.pop(cache_key, None)

                return True
            except Exception as e:
                logger.warning(f"Failed to invalidate cache: {e}")

        return False
    else:
        # Invalidate all cache types
        success = True
        for cache_type in CACHE_MAX_AGE_MAP.keys():
            cache_path = get_cache_path(database_path, cache_type)

            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                    logger.debug(f"Invalidated {cache_type} cache for {database_path}")

                    # Also remove from memory cache
                    if MEMORY_CACHE_ENABLED:
                        cache_key = (database_path, cache_type)
                        _memory_cache.pop(cache_key, None)

                except Exception as e:
                    logger.warning(f"Failed to invalidate {cache_type} cache: {e}")
                    success = False

        return success


def clear_all_caches() -> Dict[str, int]:
    """
    Clear all database caches.

    Returns:
        Dictionary with count of cache files cleared by type
    """
    counts = {cache_type: 0 for cache_type in CACHE_MAX_AGE_MAP.keys()}
    counts["unknown"] = 0

    if not os.path.exists(CACHED_DIR):
        return counts

    try:
        # Clear in-memory cache
        if MEMORY_CACHE_ENABLED:
            _memory_cache.clear()
            logger.debug("Cleared in-memory cache")

        # Clear all subdirectories
        for cache_type in CACHE_MAX_AGE_MAP.keys():
            cache_subdir = os.path.join(CACHED_DIR, cache_type)
            if os.path.exists(cache_subdir):
                for cache_file in os.listdir(cache_subdir):
                    cache_path = os.path.join(cache_subdir, cache_file)
                    try:
                        os.remove(cache_path)
                        counts[cache_type] += 1
                    except Exception as e:
                        logger.warning(f"Failed to remove cache file {cache_file}: {e}")

        # Also check for any cache files in the main cache directory
        for cache_file in os.listdir(CACHED_DIR):
            if os.path.isfile(os.path.join(CACHED_DIR, cache_file)):
                try:
                    os.remove(os.path.join(CACHED_DIR, cache_file))
                    counts["unknown"] += 1
                except Exception as e:
                    logger.warning(f"Failed to remove cache file {cache_file}: {e}")

        total_cleared = sum(counts.values())
        logger.info(f"Cleared {total_cleared} database cache files")
        return counts
    except Exception as e:
        logger.warning(f"Failed to clear cache directory: {e}")
        return counts


def get_cache_stats() -> Dict[str, Any]:
    """
    Get statistics about the cache system.

    Returns:
        Dictionary with cache statistics
    """
    stats = {
        "memory_cache_enabled": MEMORY_CACHE_ENABLED,
        "memory_cache_size": len(_memory_cache) if MEMORY_CACHE_ENABLED else 0,
        "memory_cache_hits": _memory_cache_hits,
        "memory_cache_misses": _memory_cache_misses,
        "disk_cache_files": {},
        "disk_cache_size_bytes": 0,
        "expiration_days": {k: v.days for k, v in CACHE_MAX_AGE_MAP.items()},
    }

    # Check disk cache
    if os.path.exists(CACHED_DIR):
        for cache_type in CACHE_MAX_AGE_MAP.keys():
            cache_subdir = os.path.join(CACHED_DIR, cache_type)
            if os.path.exists(cache_subdir):
                cache_files = [f for f in os.listdir(cache_subdir) if os.path.isfile(os.path.join(cache_subdir, f))]
                stats["disk_cache_files"][cache_type] = len(cache_files)

                # Calculate total size for this cache type
                type_size = sum(os.path.getsize(os.path.join(cache_subdir, f)) for f in cache_files)
                stats["disk_cache_size_bytes"] += type_size

    return stats


def cache_mineral_compatibility(database_path: str, minerals: List[str], result: Dict[str, Any]) -> None:
    """
    Cache mineral compatibility check results.

    Args:
        database_path: Path to the database file
        minerals: List of minerals checked
        result: Compatibility check result
    """
    # Create a stable key for the minerals list
    minerals_key = "_".join(sorted(minerals))
    minerals_hash = hashlib.md5(minerals_key.encode()).hexdigest()

    # Create a custom cache key/path for this specific combination
    cache_type = "mineral_compatibility"
    cache_path = os.path.join(CACHED_DIR, cache_type, f"{os.path.basename(database_path)}_{minerals_hash}.json")

    try:
        # Create cache directory if it doesn't exist
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        cache_data = {
            "database_path": database_path,
            "minerals": minerals,
            "result": result,
            "_cache_timestamp": datetime.now().isoformat(),
        }

        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)

        logger.debug(f"Cached mineral compatibility for {len(minerals)} minerals with {database_path}")
    except Exception as e:
        logger.warning(f"Failed to cache mineral compatibility: {e}")


def get_cached_mineral_compatibility(database_path: str, minerals: List[str]) -> Optional[Dict[str, Any]]:
    """
    Get cached mineral compatibility check results.

    Args:
        database_path: Path to the database file
        minerals: List of minerals to check

    Returns:
        Cached compatibility check result or None if not available
    """
    # Create a stable key for the minerals list
    minerals_key = "_".join(sorted(minerals))
    minerals_hash = hashlib.md5(minerals_key.encode()).hexdigest()

    # Get the cache path for this specific combination
    cache_type = "mineral_compatibility"
    cache_path = os.path.join(CACHED_DIR, cache_type, f"{os.path.basename(database_path)}_{minerals_hash}.json")

    if os.path.exists(cache_path):
        try:
            # Check if cache is expired
            cache_time = datetime.fromtimestamp(os.path.getmtime(cache_path))
            expiration = CACHE_MAX_AGE_MAP.get(cache_type, CACHE_EXPIRATION)

            if datetime.now() - cache_time > expiration:
                logger.debug(f"Mineral compatibility cache expired")
                return None

            # Check if database file was modified
            if os.path.exists(database_path):
                db_time = datetime.fromtimestamp(os.path.getmtime(database_path))
                if db_time > cache_time:
                    logger.debug(f"Database modified after cache was created")
                    return None

            with open(cache_path, "r") as f:
                cache_data = json.load(f)

            logger.debug(f"Using cached mineral compatibility for {len(minerals)} minerals with {database_path}")
            return cache_data["result"]

        except Exception as e:
            logger.warning(f"Failed to read mineral compatibility cache: {e}")

    return None
