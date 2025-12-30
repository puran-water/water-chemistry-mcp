"""
Tool for querying thermodynamic databases.

FAIL LOUDLY: This module raises typed exceptions on errors.
Supports querying minerals, species, elements, and keyword blocks.
"""

import logging
import os
import re
from typing import Dict, Any, List, Optional, Union
from difflib import SequenceMatcher

from utils.database_management import database_manager
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE
from utils.exceptions import (
    InputValidationError,
    DatabaseNotFoundError,
    DatabaseLoadError,
    TermNotFoundError,
    AmbiguousQueryError,
    DatabaseQueryError,
)
from .schemas import QueryThermoDatabaseInput, QueryThermoDatabaseOutput

logger = logging.getLogger(__name__)


# ============================================================================
# Database parsing utilities
# ============================================================================


def _parse_database_file(database_path: str) -> Dict[str, Any]:
    """
    Parse a PHREEQC database file and extract key information.

    Returns dict with:
        - solution_master_species: dict of element -> master species info
        - solution_species: dict of species -> reaction/log_K info
        - phases: dict of mineral name -> phase info
        - surface_master_species: dict
        - surface_species: dict
        - exchange_master_species: dict
        - exchange_species: dict
        - rates: dict of rate names
    """
    result = {
        "solution_master_species": {},
        "solution_species": {},
        "phases": {},
        "surface_master_species": {},
        "surface_species": {},
        "exchange_master_species": {},
        "exchange_species": {},
        "rates": {},
        "raw_blocks": {},
    }

    if not os.path.exists(database_path):
        raise DatabaseNotFoundError(
            f"Database file not found: {database_path}",
            database_path=database_path,
        )

    try:
        with open(database_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        raise DatabaseLoadError(
            f"Failed to read database file: {e}",
            database_path=database_path,
        )

    # Split into blocks by keywords
    keyword_pattern = r"^(SOLUTION_MASTER_SPECIES|SOLUTION_SPECIES|PHASES|SURFACE_MASTER_SPECIES|SURFACE_SPECIES|EXCHANGE_MASTER_SPECIES|EXCHANGE_SPECIES|RATES|END)\s*$"

    current_block = None
    current_content = []
    lines = content.split("\n")

    for line in lines:
        stripped = line.strip()

        # Check if this is a keyword line
        match = re.match(keyword_pattern, stripped, re.IGNORECASE)
        if match:
            # Save previous block
            if current_block and current_content:
                result["raw_blocks"][current_block] = "\n".join(current_content)
                _parse_block(result, current_block, current_content)

            current_block = match.group(1).upper()
            current_content = []
        elif current_block:
            current_content.append(line)

    # Save last block
    if current_block and current_content:
        result["raw_blocks"][current_block] = "\n".join(current_content)
        _parse_block(result, current_block, current_content)

    return result


def _parse_block(result: Dict[str, Any], block_type: str, lines: List[str]) -> None:
    """Parse a specific block type and add to result."""

    if block_type == "SOLUTION_MASTER_SPECIES":
        _parse_master_species_block(result["solution_master_species"], lines)

    elif block_type == "SOLUTION_SPECIES":
        _parse_species_block(result["solution_species"], lines)

    elif block_type == "PHASES":
        _parse_phases_block(result["phases"], lines)

    elif block_type == "SURFACE_MASTER_SPECIES":
        _parse_master_species_block(result["surface_master_species"], lines)

    elif block_type == "SURFACE_SPECIES":
        _parse_species_block(result["surface_species"], lines)

    elif block_type == "EXCHANGE_MASTER_SPECIES":
        _parse_master_species_block(result["exchange_master_species"], lines)

    elif block_type == "EXCHANGE_SPECIES":
        _parse_species_block(result["exchange_species"], lines)

    elif block_type == "RATES":
        _parse_rates_block(result["rates"], lines)


def _parse_master_species_block(target_dict: Dict[str, Any], lines: List[str]) -> None:
    """Parse SOLUTION_MASTER_SPECIES or similar blocks."""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split()
        if len(parts) >= 4:
            element = parts[0]
            master_species = parts[1]
            alkalinity = parts[2] if len(parts) > 2 else "0.0"
            gfw = parts[3] if len(parts) > 3 else "0.0"

            target_dict[element] = {
                "master_species": master_species,
                "alkalinity_contribution": alkalinity,
                "gram_formula_weight": gfw,
            }


def _parse_species_block(target_dict: Dict[str, Any], lines: List[str]) -> None:
    """Parse SOLUTION_SPECIES or similar blocks."""
    current_species = None
    current_data = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check for reaction line (contains = sign)
        if "=" in stripped and not stripped.startswith("-"):
            # Save previous species
            if current_species and current_data:
                target_dict[current_species] = current_data

            # Parse new reaction
            parts = stripped.split("=")
            if len(parts) >= 2:
                reactants = parts[0].strip()
                products = parts[1].strip()
                current_species = products.split()[0] if products else None
                current_data = {
                    "reaction": stripped,
                    "reactants": reactants,
                    "products": products,
                }
            else:
                current_species = None
                current_data = {}

        elif current_species and stripped.startswith("-"):
            # Parameter line
            param_parts = stripped[1:].split()
            if len(param_parts) >= 2:
                param_name = param_parts[0].lower()
                param_value = " ".join(param_parts[1:])

                if param_name == "log_k":
                    try:
                        current_data["log_k"] = float(param_value)
                    except ValueError:
                        current_data["log_k_expression"] = param_value
                elif param_name == "delta_h":
                    current_data["delta_h"] = param_value
                elif param_name == "analytic":
                    current_data["analytic"] = param_value
                else:
                    current_data[param_name] = param_value

    # Save last species
    if current_species and current_data:
        target_dict[current_species] = current_data


def _parse_phases_block(target_dict: Dict[str, Any], lines: List[str]) -> None:
    """Parse PHASES block for mineral definitions."""
    current_mineral = None
    current_data = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check for mineral name (not indented, doesn't start with -)
        if not line.startswith(" ") and not line.startswith("\t") and not stripped.startswith("-"):
            # Could be mineral name or reaction
            if "=" not in stripped:
                # Save previous mineral
                if current_mineral and current_data:
                    target_dict[current_mineral] = current_data

                current_mineral = stripped.split()[0]
                current_data = {"name": current_mineral}
            elif current_mineral:
                # This is the reaction line
                current_data["reaction"] = stripped
                parts = stripped.split("=")
                if len(parts) >= 2:
                    current_data["reactants"] = parts[0].strip()
                    current_data["products"] = parts[1].strip()

        elif current_mineral and stripped.startswith("-"):
            # Parameter line
            param_parts = stripped[1:].split()
            if len(param_parts) >= 2:
                param_name = param_parts[0].lower()
                param_value = " ".join(param_parts[1:])

                if param_name == "log_k":
                    try:
                        current_data["log_k"] = float(param_value)
                    except ValueError:
                        current_data["log_k_expression"] = param_value
                elif param_name == "delta_h":
                    current_data["delta_h"] = param_value
                elif param_name == "analytic":
                    current_data["analytic"] = param_value
                else:
                    current_data[param_name] = param_value

        elif current_mineral and "=" in stripped:
            # Reaction line (indented)
            current_data["reaction"] = stripped
            parts = stripped.split("=")
            if len(parts) >= 2:
                current_data["reactants"] = parts[0].strip()
                current_data["products"] = parts[1].strip()

    # Save last mineral
    if current_mineral and current_data:
        target_dict[current_mineral] = current_data


def _parse_rates_block(target_dict: Dict[str, Any], lines: List[str]) -> None:
    """Parse RATES block for kinetic rate definitions."""
    current_rate = None
    current_code = []
    in_code = False

    for line in lines:
        stripped = line.strip()

        # Check for rate name (not indented)
        if not line.startswith(" ") and not line.startswith("\t") and stripped and not stripped.startswith("-"):
            # Save previous rate
            if current_rate and current_code:
                target_dict[current_rate] = {
                    "name": current_rate,
                    "code": "\n".join(current_code),
                }

            current_rate = stripped
            current_code = []
            in_code = False

        elif current_rate:
            if stripped.lower() == "-start":
                in_code = True
            elif stripped.lower() == "-end":
                in_code = False
            elif in_code:
                current_code.append(line)

    # Save last rate
    if current_rate and current_code:
        target_dict[current_rate] = {
            "name": current_rate,
            "code": "\n".join(current_code),
        }


def _find_similar_terms(term: str, available_terms: List[str], max_results: int = 5) -> List[str]:
    """Find similar terms using string matching."""
    term_lower = term.lower()

    # Calculate similarity scores
    scores = []
    for available in available_terms:
        ratio = SequenceMatcher(None, term_lower, available.lower()).ratio()
        if ratio > 0.4:  # Minimum similarity threshold
            scores.append((available, ratio))

    # Sort by score descending
    scores.sort(key=lambda x: x[1], reverse=True)

    return [s[0] for s in scores[:max_results]]


# ============================================================================
# Main query function
# ============================================================================


async def query_thermodynamic_database(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query a thermodynamic database for information about species, minerals, or elements.

    Args:
        input_data: Dictionary containing:
            - query_term: Element, species, mineral name, or keyword to search
            - query_type: Type of query ('species', 'mineral', 'element_info', 'keyword_block')
            - database: Optional database path

    Returns:
        Dictionary containing query results

    Raises:
        InputValidationError: If input validation fails
        DatabaseNotFoundError: If database cannot be found
        TermNotFoundError: If the query term is not found
        AmbiguousQueryError: If the query matches multiple items
    """
    logger.info("Running query_thermodynamic_database tool...")

    # Validate input
    try:
        input_model = QueryThermoDatabaseInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    query_term = input_model.query_term
    query_type = input_model.query_type.lower()

    # Validate query_type
    valid_types = ["species", "mineral", "element_info", "keyword_block"]
    if query_type not in valid_types:
        raise InputValidationError(
            f"Invalid query_type: '{query_type}'. Valid types: {', '.join(valid_types)}"
        )

    # Resolve database
    database_path = database_manager.resolve_and_validate_database(
        input_model.database, category="general"
    )

    # Parse database
    try:
        db_data = _parse_database_file(database_path)
    except (DatabaseNotFoundError, DatabaseLoadError):
        raise
    except Exception as e:
        raise DatabaseQueryError(f"Failed to parse database: {e}")

    # Execute query based on type
    results = None

    if query_type == "mineral":
        results = _query_mineral(query_term, db_data)

    elif query_type == "species":
        results = _query_species(query_term, db_data)

    elif query_type == "element_info":
        results = _query_element(query_term, db_data)

    elif query_type == "keyword_block":
        results = _query_keyword_block(query_term, db_data)

    return QueryThermoDatabaseOutput(
        query_term=query_term,
        query_type=query_type,
        database_used=os.path.basename(database_path),
        results=results,
    ).model_dump(exclude_defaults=True)


def _query_mineral(term: str, db_data: Dict[str, Any]) -> Dict[str, Any]:
    """Query for mineral phase information."""
    phases = db_data.get("phases", {})

    # Try exact match (case-insensitive)
    for name, data in phases.items():
        if name.lower() == term.lower():
            return data

    # Try partial match
    matches = []
    term_lower = term.lower()
    for name, data in phases.items():
        if term_lower in name.lower():
            matches.append(name)

    if len(matches) == 1:
        return phases[matches[0]]
    elif len(matches) > 1:
        raise AmbiguousQueryError(
            f"Query '{term}' matched multiple minerals: {', '.join(matches[:10])}",
            term=term,
            matches=matches[:20],
        )

    # Not found - provide suggestions
    suggestions = _find_similar_terms(term, list(phases.keys()))
    raise TermNotFoundError(
        f"Mineral '{term}' not found in database",
        term=term,
        query_type="mineral",
        suggestions=suggestions,
    )


def _query_species(term: str, db_data: Dict[str, Any]) -> Dict[str, Any]:
    """Query for species information."""
    species = db_data.get("solution_species", {})

    # Try exact match (case-insensitive)
    for name, data in species.items():
        if name.lower() == term.lower():
            return data

    # Try partial match
    matches = []
    term_lower = term.lower()
    for name, data in species.items():
        if term_lower in name.lower():
            matches.append(name)

    if len(matches) == 1:
        return species[matches[0]]
    elif len(matches) > 1:
        raise AmbiguousQueryError(
            f"Query '{term}' matched multiple species: {', '.join(matches[:10])}",
            term=term,
            matches=matches[:20],
        )

    # Not found - provide suggestions
    suggestions = _find_similar_terms(term, list(species.keys()))
    raise TermNotFoundError(
        f"Species '{term}' not found in database",
        term=term,
        query_type="species",
        suggestions=suggestions,
    )


def _query_element(term: str, db_data: Dict[str, Any]) -> Dict[str, Any]:
    """Query for element/master species information."""
    master_species = db_data.get("solution_master_species", {})

    # Try exact match (case-insensitive)
    for name, data in master_species.items():
        if name.lower() == term.lower():
            result = data.copy()
            result["element"] = name

            # Also find related species
            species = db_data.get("solution_species", {})
            related = []
            for sp_name, sp_data in species.items():
                if term.lower() in sp_name.lower() or term.lower() in sp_data.get("reaction", "").lower():
                    related.append(sp_name)
            result["related_species"] = related[:20]

            return result

    # Not found - provide suggestions
    suggestions = _find_similar_terms(term, list(master_species.keys()))
    raise TermNotFoundError(
        f"Element '{term}' not found in database master species",
        term=term,
        query_type="element_info",
        suggestions=suggestions,
    )


def _query_keyword_block(term: str, db_data: Dict[str, Any]) -> str:
    """Query for a raw keyword block."""
    raw_blocks = db_data.get("raw_blocks", {})

    # Normalize term
    term_upper = term.upper().replace(" ", "_")

    if term_upper in raw_blocks:
        return raw_blocks[term_upper]

    # Try partial match
    for block_name, content in raw_blocks.items():
        if term_upper in block_name:
            return content

    raise TermNotFoundError(
        f"Keyword block '{term}' not found. Available blocks: {', '.join(raw_blocks.keys())}",
        term=term,
        query_type="keyword_block",
        suggestions=list(raw_blocks.keys()),
    )
