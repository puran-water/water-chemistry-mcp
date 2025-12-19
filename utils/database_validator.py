"""
Database Validator Module

This module provides functionality to validate PHREEQC thermodynamic databases
and extract structured information about their contents.
"""

import re
import os
import logging
import time
import hashlib
from typing import List, Dict, Optional, Set, Tuple, Any, Union

logger = logging.getLogger(__name__)

# Common PHREEQC database block names
COMMON_DATABASE_BLOCKS = [
    "SOLUTION_MASTER_SPECIES",
    "SOLUTION_SPECIES",
    "PHASES",
    "EXCHANGE_MASTER_SPECIES",
    "EXCHANGE_SPECIES",
    "SURFACE_MASTER_SPECIES",
    "SURFACE_SPECIES",
    "RATES",
    "PITZER",
    "SIT",
    "NAMED_EXPRESSIONS",
    "SELECTED_OUTPUT",
    "ISOTOPES",
    "CALCULATE_VALUES",
    "LLNL_AQUEOUS_MODEL_PARAMETERS",
]


def validate_database(database_path: str, thorough: bool = False) -> Dict[str, Any]:
    """
    Validates a PHREEQC database file and returns detailed validation results.

    Args:
        database_path: Path to the database file
        thorough: Whether to perform thorough validation (slower but more comprehensive)

    Returns:
        Dictionary with validation results
    """
    results = {"valid": False, "errors": [], "warnings": [], "blocks": [], "statistics": {}}

    if not os.path.exists(database_path):
        results["errors"].append(f"Database file not found: {database_path}")
        return results

    try:
        # Basic metadata
        file_size = os.path.getsize(database_path)
        file_mtime = time.ctime(os.path.getmtime(database_path))
        md5_hash = hashlib.md5()

        # Read and analyze the database file
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()
            md5_hash.update(content.encode("utf-8", errors="ignore"))

        # Add file metadata to results
        results["statistics"]["file_size"] = file_size
        results["statistics"]["modified_time"] = file_mtime
        results["statistics"]["md5_hash"] = md5_hash.hexdigest()

        # Check file size is reasonable
        if file_size < 1000:  # Less than 1 KB
            results["warnings"].append(f"Database file is suspiciously small: {file_size} bytes")

        # Check for required database blocks
        required_blocks = ["SOLUTION_MASTER_SPECIES", "SOLUTION_SPECIES"]

        for block in required_blocks:
            if block not in content.upper():
                results["errors"].append(f"Required block '{block}' not found in database")

        # Extract and analyze database blocks
        block_pattern = r"^([A-Z_]+)(\s+.*?)(?=^[A-Z_]+|\Z)"
        blocks = re.findall(block_pattern, content, re.MULTILINE | re.DOTALL)

        if not blocks:
            results["errors"].append("No valid blocks found in database")

        # Get all blocks defined in the database
        found_blocks = [block[0] for block in blocks]
        results["blocks"] = found_blocks

        # Check for basic element definitions
        element_pattern = r"\s*([A-Za-z]+)(?:\([^)]*\))?\s+([A-Za-z0-9_]+)(?:\([^)]*\))?\s+([0-9.-]+)\s+([A-Za-z0-9_]+)(?:\([^)]*\))?\s+([0-9.-]+)"
        element_matches = re.findall(element_pattern, content)

        if not element_matches:
            results["errors"].append("No element definitions found in database")

        # Extract detailed statistics
        if thorough:
            # Count number of elements defined
            elements = extract_database_elements(database_path)
            results["statistics"]["element_count"] = len(elements)

            # Count number of minerals defined
            minerals = extract_database_minerals(database_path)
            results["statistics"]["mineral_count"] = len(minerals)

            # Count number of solution species
            solution_species = extract_solution_species(database_path)
            results["statistics"]["solution_species_count"] = len(solution_species)

            # Check for redox couples
            redox_counts = count_redox_couples(database_path)
            results["statistics"]["redox_couple_count"] = redox_counts

            # Check for additional capabilities
            has_pitzer = "PITZER" in found_blocks
            has_sit = "SIT" in found_blocks
            has_surface = "SURFACE_MASTER_SPECIES" in found_blocks
            has_exchange = "EXCHANGE_MASTER_SPECIES" in found_blocks
            has_kinetics = "RATES" in found_blocks

            results["statistics"]["has_pitzer"] = has_pitzer
            results["statistics"]["has_sit"] = has_sit
            results["statistics"]["has_surface"] = has_surface
            results["statistics"]["has_exchange"] = has_exchange
            results["statistics"]["has_kinetics"] = has_kinetics
        else:
            # Basic statistics
            results["statistics"]["element_count"] = len(element_matches)

            # Simplified feature checks based on block presence
            results["statistics"]["has_pitzer"] = "PITZER" in found_blocks
            results["statistics"]["has_sit"] = "SIT" in found_blocks
            results["statistics"]["has_surface"] = "SURFACE_MASTER_SPECIES" in found_blocks
            results["statistics"]["has_exchange"] = "EXCHANGE_MASTER_SPECIES" in found_blocks
            results["statistics"]["has_kinetics"] = "RATES" in found_blocks

        # Format check - look for signs of malformed blocks
        malformed_blocks = []
        for block_name, block_content in blocks:
            # Check for potential syntax issues in the block content
            if re.search(r"^\s*[A-Z_]+\s*$", block_content, re.MULTILINE):
                # This might indicate a line that looks like a block header within another block
                malformed_blocks.append(block_name)

        if malformed_blocks:
            results["warnings"].append(f"Potentially malformed blocks found: {', '.join(malformed_blocks)}")

        # Final validation decision
        # Database is valid if it has no critical errors
        results["valid"] = len(results["errors"]) == 0

        return results

    except Exception as e:
        results["errors"].append(f"Error validating database: {e}")
        return results


def extract_database_elements(database_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Extracts element definitions from a database.

    Args:
        database_path: Path to the database file

    Returns:
        Dictionary mapping element symbols to their properties
    """
    elements = {}

    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        # Find the SOLUTION_MASTER_SPECIES block
        master_species_match = re.search(
            r"SOLUTION_MASTER_SPECIES\s+(.*?)(?=^\w+|\Z)", content, re.MULTILINE | re.DOTALL
        )

        if master_species_match:
            master_species_block = master_species_match.group(1)

            # Extract element definitions
            lines = master_species_block.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) >= 5:
                        # Extract primary element name (without any parentheses or qualifiers)
                        element_full = parts[0]
                        element = element_full.split("(")[0].strip()

                        if element and not element.startswith("#"):
                            species = parts[1]
                            alk = parts[2]
                            gram_formula = parts[3]
                            gram_formula_wt = parts[4]

                            elements[element_full] = {
                                "element": element,
                                "primary_species": species,
                                "alkalinity": alk,
                                "gram_formula": gram_formula,
                                "gram_formula_wt": gram_formula_wt,
                            }

        return elements

    except Exception as e:
        logger.error(f"Error extracting elements from database {database_path}: {e}")
        return {}


def extract_database_minerals(database_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Extracts mineral definitions from a database.

    Args:
        database_path: Path to the database file

    Returns:
        Dictionary mapping mineral names to their properties
    """
    minerals = {}

    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        # Find the PHASES block
        phases_match = re.search(r"PHASES\s+(.*?)(?=^\w+|\Z)", content, re.MULTILINE | re.DOTALL)

        if phases_match:
            phases_block = phases_match.group(1)

            # Extract individual mineral definitions
            mineral_matches = re.findall(
                r"^\s*([A-Za-z0-9_().-]+)\s+$(.*?)(?=^\s*[A-Za-z0-9_().-]+\s*$|\Z)",
                phases_block,
                re.MULTILINE | re.DOTALL,
            )

            for name, definition in mineral_matches:
                name = name.strip()

                # Skip comment lines that might look like mineral names
                if name.startswith("#"):
                    continue

                # Extract log K value if available
                log_k_match = re.search(r"log_k\s+([0-9.-]+)", definition, re.IGNORECASE)
                log_k = float(log_k_match.group(1)) if log_k_match else None

                # Extract other parameters
                delta_h_match = re.search(r"delta_h\s+([0-9.-]+)", definition, re.IGNORECASE)
                delta_h = float(delta_h_match.group(1)) if delta_h_match else None

                # Extract analytical expression for log K if present
                analytic_match = re.search(
                    r"(-a_e|a_e)\s+([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)\s+([0-9.-]+)",
                    definition,
                    re.IGNORECASE,
                )
                analytic = list(map(float, analytic_match.groups()[1:])) if analytic_match else None

                # Extract reaction
                reaction_lines = [
                    line.strip()
                    for line in definition.split("\n")
                    if line.strip()
                    and not line.strip().startswith("#")
                    and not re.match(r"\s*log_k\s+", line, re.IGNORECASE)
                    and not re.match(r"\s*delta_h\s+", line, re.IGNORECASE)
                    and not re.match(r"\s*(-a_e|a_e)\s+", line, re.IGNORECASE)
                ]
                reaction = " ".join(reaction_lines) if reaction_lines else None

                # Extract formula by parsing the reaction
                formula = None
                if reaction:
                    # Look for a pattern like: MgCO3 = Mg+2 + CO3-2
                    # The part before the = sign is typically the formula
                    formula_match = re.match(r"^\s*([^=]+)\s*=", reaction)
                    if formula_match:
                        formula = formula_match.group(1).strip()

                minerals[name] = {
                    "name": name,
                    "log_k": log_k,
                    "delta_h": delta_h,
                    "analytic": analytic,
                    "reaction": reaction,
                    "formula": formula,
                }

        return minerals

    except Exception as e:
        logger.error(f"Error extracting minerals from database {database_path}: {e}")
        return {}


def extract_solution_species(database_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Extracts solution species definitions from a database.

    Args:
        database_path: Path to the database file

    Returns:
        Dictionary mapping species names to their properties
    """
    species = {}

    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        # Find the SOLUTION_SPECIES block
        species_match = re.search(r"SOLUTION_SPECIES\s+(.*?)(?=^\w+|\Z)", content, re.MULTILINE | re.DOTALL)

        if species_match:
            species_block = species_match.group(1)

            # Extract individual species definitions
            species_matches = re.findall(
                r"^\s*([^=\n#]+?)(?:\s*=\s*([^\n#]*))?\s*$\s*(.*?)(?=^\s*[^=\n#]+(?:\s*=|$)|\Z)",
                species_block,
                re.MULTILINE | re.DOTALL,
            )

            for reactants, products, definition in species_matches:
                reactants = reactants.strip()

                # Skip comment lines that might look like species
                if reactants.startswith("#"):
                    continue

                # Determine if this is a reaction or a primary species
                is_reaction = bool(products.strip())

                # Extract log K value if available
                log_k_match = re.search(r"log_k\s+([0-9.-]+)", definition, re.IGNORECASE)
                log_k = float(log_k_match.group(1)) if log_k_match else None

                # Extract other parameters
                delta_h_match = re.search(r"delta_h\s+([0-9.-]+)", definition, re.IGNORECASE)
                delta_h = float(delta_h_match.group(1)) if delta_h_match else None

                # Determine the species/reaction identifier
                if is_reaction:
                    identifier = f"{reactants} = {products}"
                else:
                    identifier = reactants

                species[identifier] = {
                    "reactants": reactants,
                    "products": products.strip() if is_reaction else "",
                    "is_reaction": is_reaction,
                    "log_k": log_k,
                    "delta_h": delta_h,
                    "definition": definition.strip(),
                }

        return species

    except Exception as e:
        logger.error(f"Error extracting solution species from database {database_path}: {e}")
        return {}


def count_redox_couples(database_path: str) -> int:
    """
    Counts the number of redox couples defined in the database.

    Args:
        database_path: Path to the database file

    Returns:
        Number of redox couples
    """
    try:
        # Extract all solution species
        species_dict = extract_solution_species(database_path)

        # Look for species with "e-" in the reaction
        redox_count = 0
        for species_id, species_info in species_dict.items():
            if species_info["is_reaction"] and "e-" in species_id:
                redox_count += 1

        return redox_count

    except Exception as e:
        logger.error(f"Error counting redox couples in database {database_path}: {e}")
        return 0


def compare_databases(db_path1: str, db_path2: str) -> Dict[str, Any]:
    """
    Compares two PHREEQC databases.

    Args:
        db_path1: Path to the first database
        db_path2: Path to the second database

    Returns:
        Dictionary with comparison results
    """
    results = {
        "elements": {"db1_only": [], "db2_only": [], "common": []},
        "minerals": {"db1_only": [], "db2_only": [], "common": [], "with_different_log_k": []},
        "blocks": {"db1_only": [], "db2_only": [], "common": []},
        "statistics": {},
        "valid": {"db1": False, "db2": False},
    }

    # Validate both databases
    validation1 = validate_database(db_path1, thorough=True)
    validation2 = validate_database(db_path2, thorough=True)

    results["valid"]["db1"] = validation1["valid"]
    results["valid"]["db2"] = validation2["valid"]

    if not results["valid"]["db1"] or not results["valid"]["db2"]:
        results["errors"] = [
            f"Database 1 validation errors: {validation1.get('errors', [])}",
            f"Database 2 validation errors: {validation2.get('errors', [])}",
        ]
        return results

    # Compare blocks
    blocks1 = set(validation1["blocks"])
    blocks2 = set(validation2["blocks"])

    results["blocks"]["db1_only"] = list(blocks1 - blocks2)
    results["blocks"]["db2_only"] = list(blocks2 - blocks1)
    results["blocks"]["common"] = list(blocks1 & blocks2)

    # Compare statistics
    results["statistics"] = {"db1": validation1["statistics"], "db2": validation2["statistics"]}

    # Compare elements
    elements1 = extract_database_elements(db_path1)
    elements2 = extract_database_elements(db_path2)

    elements1_names = set(elements1.keys())
    elements2_names = set(elements2.keys())

    results["elements"]["db1_only"] = list(elements1_names - elements2_names)
    results["elements"]["db2_only"] = list(elements2_names - elements1_names)
    results["elements"]["common"] = list(elements1_names & elements2_names)

    # Compare minerals
    minerals1 = extract_database_minerals(db_path1)
    minerals2 = extract_database_minerals(db_path2)

    minerals1_names = set(minerals1.keys())
    minerals2_names = set(minerals2.keys())

    results["minerals"]["db1_only"] = list(minerals1_names - minerals2_names)
    results["minerals"]["db2_only"] = list(minerals2_names - minerals1_names)
    common_minerals = list(minerals1_names & minerals2_names)
    results["minerals"]["common"] = common_minerals

    # Check for minerals with different log K values
    different_log_k = []
    for mineral in common_minerals:
        log_k1 = minerals1[mineral]["log_k"]
        log_k2 = minerals2[mineral]["log_k"]

        if log_k1 is not None and log_k2 is not None and abs(log_k1 - log_k2) > 0.01:
            different_log_k.append(
                {"mineral": mineral, "log_k_db1": log_k1, "log_k_db2": log_k2, "difference": abs(log_k1 - log_k2)}
            )

    results["minerals"]["with_different_log_k"] = different_log_k

    return results


def scan_database_for_mineral(database_path: str, mineral_name: str) -> Dict[str, Any]:
    """
    Scans a database for a specific mineral and returns detailed information if found.

    Args:
        database_path: Path to the database file
        mineral_name: Name of the mineral to search for

    Returns:
        Dictionary with mineral information or empty dict if not found
    """
    try:
        minerals = extract_database_minerals(database_path)

        # Exact match
        if mineral_name in minerals:
            return {"found": True, "match_type": "exact", "name": mineral_name, "info": minerals[mineral_name]}

        # Case-insensitive match
        for name, info in minerals.items():
            if name.lower() == mineral_name.lower():
                return {"found": True, "match_type": "case_insensitive", "name": name, "info": info}

        # Similar name match (containing search)
        similar_matches = []
        for name, info in minerals.items():
            if mineral_name.lower() in name.lower() or name.lower() in mineral_name.lower():
                similar_matches.append({"name": name, "info": info})

        if similar_matches:
            return {"found": True, "match_type": "similar", "matches": similar_matches}

        # Formula match
        for name, info in minerals.items():
            if info.get("formula") and mineral_name.lower() in info["formula"].lower():
                return {"found": True, "match_type": "formula", "name": name, "info": info}

        # Not found
        return {
            "found": False,
            "message": f"Mineral '{mineral_name}' not found in database {os.path.basename(database_path)}",
        }

    except Exception as e:
        logger.error(f"Error scanning database for mineral {mineral_name}: {e}")
        return {"found": False, "error": str(e)}


def verify_mineral_compatibility(database_path: str, mineral_names: List[str]) -> Dict[str, Any]:
    """
    Verifies if a list of minerals is compatible with the specified database.

    Args:
        database_path: Path to the database file
        mineral_names: List of mineral names to check

    Returns:
        Dictionary with compatibility results for each mineral
    """
    results = {"compatible": [], "incompatible": [], "similar": []}

    try:
        minerals = extract_database_minerals(database_path)
        db_minerals_lower = {name.lower(): name for name in minerals.keys()}

        for mineral in mineral_names:
            # Exact match (case-insensitive)
            if mineral.lower() in db_minerals_lower:
                results["compatible"].append({"requested": mineral, "database": db_minerals_lower[mineral.lower()]})
                continue

            # Look for similar minerals
            found_similar = False
            for db_mineral_lower, db_mineral in db_minerals_lower.items():
                # Skip exact matches (already handled)
                if db_mineral_lower == mineral.lower():
                    continue

                # Check for significant overlap in the names
                if (
                    mineral.lower() in db_mineral_lower
                    or db_mineral_lower in mineral.lower()
                    or
                    # Similarity based on common word
                    any(word in db_mineral_lower.split() for word in mineral.lower().split())
                ):
                    results["similar"].append(
                        {
                            "requested": mineral,
                            "similar_to": db_mineral,
                            "confidence": (
                                "medium"
                                if mineral.lower() in db_mineral_lower or db_mineral_lower in mineral.lower()
                                else "low"
                            ),
                        }
                    )
                    found_similar = True
                    break

            # If no match or similar mineral found, mark as incompatible
            if not found_similar:
                results["incompatible"].append(mineral)

        # Add summary statistics
        results["summary"] = {
            "total": len(mineral_names),
            "compatible_count": len(results["compatible"]),
            "incompatible_count": len(results["incompatible"]),
            "similar_count": len(results["similar"]),
            "compatibility_percentage": (
                round(100 * len(results["compatible"]) / len(mineral_names)) if mineral_names else 0
            ),
        }

        return results

    except Exception as e:
        logger.error(f"Error verifying mineral compatibility: {e}")
        return {"error": str(e), "compatible": [], "incompatible": mineral_names, "similar": []}
