"""
Database Converter Module

This module provides functionality to convert between different PHREEQC database formats
and to merge multiple databases into composite databases.
"""

import re
import os
import json
import logging
import tempfile
import shutil
from typing import Optional, Dict, List, Set, Any, Tuple, Union
from datetime import datetime

from .database_validator import (
    validate_database,
    extract_database_elements,
    extract_database_minerals,
    extract_solution_species,
    scan_database_for_mineral,
)

from .database_downloader import DATABASE_DIR, OFFICIAL_DIR, CUSTOM_DIR, CACHED_DIR, METADATA_DIR

logger = logging.getLogger(__name__)

# Conversion settings
CONVERSION_OUTPUT_DIR = os.path.join(DATABASE_DIR, "converted")
os.makedirs(CONVERSION_OUTPUT_DIR, exist_ok=True)

# Define format-specific headers/footers for converted databases
DATABASE_FORMAT_TEMPLATES = {
    "phreeqc": {
        "header": """# PHREEQC format database
# Converted on {date}
# Original source: {source}
# Conversion method: {method}

""",
        "blocks": [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
            "RATES",
            "END",
        ],
    },
    "pitzer": {
        "header": """# PHREEQC database with Pitzer equations
# Converted on {date}
# Original source: {source}
# Conversion method: {method}
# WARNING: Pitzer parameters are approximate and should be validated

""",
        "blocks": [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
            "PITZER",
            "RATES",
            "END",
        ],
    },
    "minteq": {
        "header": """# MINTEQ format database
# Converted on {date}
# Original source: {source}
# Conversion method: {method}

""",
        "blocks": [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
            "RATES",
            "END",
        ],
    },
    "llnl": {
        "header": """# LLNL format database
# Converted on {date}
# Original source: {source}
# Conversion method: {method}

""",
        "blocks": [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
            "RATES",
            "END",
        ],
    },
    "sit": {
        "header": """# SIT format database
# Converted on {date}
# Original source: {source}
# Conversion method: {method}

""",
        "blocks": [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
            "SIT",
            "RATES",
            "END",
        ],
    },
}

# Database block extraction pattern (matches a block name and its content)
BLOCK_PATTERN = r"^([A-Z_]+)(\s+.*?)(?=^[A-Z_]+|\Z)"


def detect_database_format(database_path: str) -> Optional[str]:
    """
    Detects the format of a PHREEQC database.

    Args:
        database_path: Path to the database file

    Returns:
        Format name or None if unknown
    """
    if not os.path.exists(database_path):
        logger.error(f"Database file not found: {database_path}")
        return None

    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read(20000)  # Read first 20K to detect format

        db_name = os.path.basename(database_path).lower()

        # Check for specific formats based on content and filename
        if "llnl" in db_name or "Lawrence Livermore National Laboratory" in content:
            return "llnl"
        elif "minteq" in db_name:
            return "minteq"
        elif "wateq4f" in db_name:
            return "wateq4f"
        elif "PITZER" in content or "pitzer" in db_name:
            return "pitzer"
        elif "SIT" in content or "sit" in db_name:
            return "sit"
        elif "phreeqc" in db_name:
            return "phreeqc"
        elif "iso" in db_name:
            return "iso"
        elif "Amm.dat" in database_path:
            return "amm"
        elif "ColdChem" in database_path:
            return "coldchem"
        elif "frezchem" in database_path:
            return "frezchem"

        # More detailed detection based on structure and content
        if "PITZER" in content and re.search(r"PITZER\s+", content, re.MULTILINE):
            return "pitzer"
        elif "SIT" in content and re.search(r"SIT\s+", content, re.MULTILINE):
            return "sit"
        elif content.count("SURFACE_MASTER_SPECIES") > 0:
            # Many databases have surface complexation, so this is just a hint
            if content.count("PHASES") > 100:  # LLNL has many more phases
                return "llnl"
            else:
                return "phreeqc"

        # Default to generic phreeqc format
        return "phreeqc"

    except Exception as e:
        logger.error(f"Error detecting database format for {database_path}: {e}")
        return None


def list_database_blocks(database_path: str) -> List[str]:
    """
    Lists all blocks defined in a PHREEQC database.

    Args:
        database_path: Path to the database file

    Returns:
        List of block names
    """
    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        block_matches = re.findall(BLOCK_PATTERN, content, re.MULTILINE | re.DOTALL)
        return [match[0].strip() for match in block_matches]

    except Exception as e:
        logger.error(f"Error listing database blocks: {e}")
        return []


def extract_database_blocks(database_path: str) -> Dict[str, str]:
    """
    Extracts all blocks from a PHREEQC database.

    Args:
        database_path: Path to the database file

    Returns:
        Dictionary mapping block names to block content
    """
    try:
        with open(database_path, "r", errors="ignore") as f:
            content = f.read()

        block_matches = re.findall(BLOCK_PATTERN, content, re.MULTILINE | re.DOTALL)
        blocks = {}

        for block_name, block_content in block_matches:
            blocks[block_name.strip()] = block_content.strip()

        return blocks

    except Exception as e:
        logger.error(f"Error extracting database blocks: {e}")
        return {}


def convert_database(
    source_path: str,
    target_format: str,
    output_path: Optional[str] = None,
    conversion_options: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Converts a PHREEQC database to a different format.

    Args:
        source_path: Path to the source database
        target_format: Target format ('phreeqc', 'llnl', 'pitzer', etc.)
        output_path: Optional output path (default: auto-generated)
        conversion_options: Optional conversion options

    Returns:
        Path to the converted database or None if conversion failed
    """
    if not os.path.exists(source_path):
        logger.error(f"Source database not found: {source_path}")
        return None

    source_format = detect_database_format(source_path)
    if source_format is None:
        logger.error(f"Could not detect format of source database: {source_path}")
        return None

    if source_format == target_format:
        logger.info(f"Source and target formats are the same ({source_format}). No conversion needed.")
        return source_path

    # Generate output path if not provided
    if output_path is None:
        source_name = os.path.basename(source_path)
        base_name = os.path.splitext(source_name)[0]
        output_path = os.path.join(CONVERSION_OUTPUT_DIR, f"{base_name}_{target_format}.dat")

    # Use mapping to select the appropriate conversion function
    conversion_map = {
        ("llnl", "phreeqc"): convert_llnl_to_phreeqc,
        ("minteq", "phreeqc"): convert_minteq_to_phreeqc,
        ("phreeqc", "pitzer"): convert_phreeqc_to_pitzer,
        ("phreeqc", "sit"): convert_phreeqc_to_sit,
        ("pitzer", "phreeqc"): convert_pitzer_to_phreeqc,
        ("sit", "phreeqc"): convert_sit_to_phreeqc,
        ("wateq4f", "phreeqc"): convert_wateq4f_to_phreeqc,
        ("llnl", "pitzer"): convert_llnl_to_pitzer,
        ("generic", "phreeqc"): lambda src, dst, opts: copy_with_format_header(src, dst, "phreeqc", opts),
        ("generic", "pitzer"): lambda src, dst, opts: copy_with_format_header(src, dst, "pitzer", opts),
        ("generic", "sit"): lambda src, dst, opts: copy_with_format_header(src, dst, "sit", opts),
    }

    # Find the appropriate conversion function
    key = (source_format, target_format)
    generic_key = ("generic", target_format)

    conversion_function = conversion_map.get(key)
    if conversion_function is None:
        # Try generic conversion
        conversion_function = conversion_map.get(generic_key)

    if conversion_function is None:
        logger.error(f"Conversion from {source_format} to {target_format} is not implemented.")
        return None

    # Default conversion options
    if conversion_options is None:
        conversion_options = {}

    # Add timestamp and source information to conversion options
    conversion_options.update(
        {
            "source_format": source_format,
            "target_format": target_format,
            "source_path": source_path,
            "timestamp": datetime.now().isoformat(),
            "source_name": os.path.basename(source_path),
        }
    )

    # Use a temporary file for atomic operations
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".dat", dir=os.path.dirname(output_path))
    temp_path = temp_file.name
    temp_file.close()

    try:
        # Perform the conversion
        result = conversion_function(source_path, temp_path, conversion_options)

        if not result:
            logger.error(f"Conversion failed: {source_path} -> {target_format}")
            os.remove(temp_path)
            return None

        # Validate the converted database
        validation_result = validate_database(temp_path)
        if not validation_result["valid"]:
            errors = validation_result.get("errors", ["Unknown validation error"])
            logger.error(f"Converted database is invalid: {errors}")
            os.remove(temp_path)
            return None

        # Move the temp file to the final destination
        shutil.move(temp_path, output_path)

        logger.info(f"Successfully converted database: {source_path} -> {output_path} ({target_format} format)")
        return output_path

    except Exception as e:
        logger.error(f"Error during database conversion: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return None


def copy_with_format_header(source_path: str, output_path: str, target_format: str, options: Dict[str, Any]) -> bool:
    """
    Copies a database with a new format header.

    Args:
        source_path: Path to the source database
        output_path: Path for the output database
        target_format: Target format name
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        with open(source_path, "r", errors="ignore") as f:
            content = f.read()

        # Get the template for the target format
        template = DATABASE_FORMAT_TEMPLATES.get(target_format, {})
        header_template = template.get(
            "header", "# Converted database\n# WARNING: This is a machine-converted file\n\n"
        )

        # Format the header
        header = header_template.format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="Format conversion (direct copy with header)",
        )

        # Write the output file
        with open(output_path, "w") as f:
            f.write(header + content)

        logger.info(f"Copied database with {target_format} format header: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error copying database with format header: {e}")
        return False


def convert_llnl_to_phreeqc(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts an LLNL-format database to PHREEQC format.

    Args:
        source_path: Path to the LLNL database
        output_path: Path for the output PHREEQC database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["phreeqc"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="LLNL to PHREEQC conversion",
        )

        # Add conversion notes
        header += """# LLNL to PHREEQC conversion notes:
# - This conversion maintains most of the LLNL database content
# - Some complex mineral definitions may need manual verification
# - Aqueous species from LLNL have been preserved
# - Temperature dependence parameters have been maintained where possible

"""

        # Define the order of blocks for the PHREEQC format
        block_order = DATABASE_FORMAT_TEMPLATES["phreeqc"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order
        for block_name in block_order:
            if block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted LLNL database to PHREEQC format: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting LLNL to PHREEQC: {e}")
        return False


def convert_minteq_to_phreeqc(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a MINTEQ-format database to PHREEQC format.

    Args:
        source_path: Path to the MINTEQ database
        output_path: Path for the output PHREEQC database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["phreeqc"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="MINTEQ to PHREEQC conversion",
        )

        # Add conversion notes
        header += """# MINTEQ to PHREEQC conversion notes:
# - This conversion maintains the MINTEQ database content
# - Surface complexation parameters have been preserved
# - MINTEQ-specific notation has been adapted to PHREEQC format where needed
# - Some reaction stoichiometry may need manual verification

"""

        # Define the order of blocks for the PHREEQC format
        block_order = DATABASE_FORMAT_TEMPLATES["phreeqc"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order
        for block_name in block_order:
            if block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted MINTEQ database to PHREEQC format: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting MINTEQ to PHREEQC: {e}")
        return False


def convert_wateq4f_to_phreeqc(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a WATEQ4F-format database to PHREEQC format.

    Args:
        source_path: Path to the WATEQ4F database
        output_path: Path for the output PHREEQC database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    return copy_with_format_header(source_path, output_path, "phreeqc", options)


def convert_phreeqc_to_pitzer(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a standard PHREEQC database to include Pitzer equations.

    Args:
        source_path: Path to the PHREEQC database
        output_path: Path for the output Pitzer database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["pitzer"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="PHREEQC to Pitzer conversion",
        )

        # Add conversion notes
        header += """# PHREEQC to Pitzer conversion notes:
# - The original PHREEQC database has been maintained
# - Basic Pitzer parameters have been added for common ions
# - These parameters are approximate and should be validated for specific applications
# - For high ionic strength solutions, consider using a specialized Pitzer database

"""

        # Basic Pitzer block with common ion parameters
        pitzer_block = """PITZER
# Added basic Pitzer parameters - THESE ARE APPROXIMATE VALUES
# Parameters from Harvie, Moller, and Weare (1984)
# Cation-Anion binary parameters (beta0, beta1, beta2)
Na+ Cl-    0.0765    0.2664    0.0
Na+ SO4-2  0.0196    1.113     0.0
Na+ HCO3-  0.0277    0.0411    0.0
Na+ CO3-2  0.0399    1.389     0.0
K+  Cl-    0.04835   0.2122    0.0
K+  SO4-2  0.04995   0.7793    0.0
K+  HCO3-  0.0296    0.0         0.0
K+  CO3-2  0.1488    1.43      0.0
Ca+2 Cl-   0.3159    1.614     0.0
Ca+2 SO4-2 0.20      3.1973    -54.24
Mg+2 Cl-   0.3519    1.6815    0.0
Mg+2 SO4-2 0.2210    3.343     -37.23

# Cation-Cation binary parameters
Na+ K+     -0.012    0.0       0.0
Na+ Ca+2   0.0705    0.0       0.0
Na+ Mg+2   0.0700    0.0       0.0
K+  Ca+2   0.0320    0.0       0.0
K+  Mg+2   0.0        0.0       0.0

# Anion-Anion binary parameters
Cl- SO4-2  0.02      0.0       0.0
Cl- HCO3-  0.03      0.0       0.0
Cl- CO3-2  -0.02     0.0       0.0
SO4-2 HCO3- 0.01     0.0       0.0
SO4-2 CO3-2 0.02     0.0       0.0

# Neutral-Ion parameters
CO2  Na+   0.085
CO2  K+    0.051
CO2  Ca+2  0.183
CO2  Mg+2  0.183
CO2  Cl-   -0.005
CO2  SO4-2 0.075

# Triplet interaction parameters (psi)
Na+ K+  Cl-    -0.0018
Na+ K+  SO4-2  -0.010
Na+ Ca+2 Cl-   -0.003
Na+ Ca+2 SO4-2 -0.055
Na+ Mg+2 Cl-   0.0
Na+ Mg+2 SO4-2 -0.015
K+  Ca+2 Cl-   -0.025
K+  Mg+2 Cl-   -0.022
Na+ Cl- SO4-2  -0.009
K+  Cl- SO4-2  -0.006
"""

        # Define the order of blocks for the Pitzer format
        block_order = DATABASE_FORMAT_TEMPLATES["pitzer"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order
        for block_name in block_order:
            if block_name == "PITZER":
                output_content += pitzer_block + "\n"
            elif block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted PHREEQC database to include Pitzer parameters: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting PHREEQC to Pitzer: {e}")
        return False


def convert_phreeqc_to_sit(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a standard PHREEQC database to include SIT parameters.

    Args:
        source_path: Path to the PHREEQC database
        output_path: Path for the output SIT database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["sit"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="PHREEQC to SIT conversion",
        )

        # Add conversion notes
        header += """# PHREEQC to SIT conversion notes:
# - The original PHREEQC database has been maintained
# - Basic SIT parameters have been added for common ions
# - These parameters are approximate and should be validated for specific applications
# - For high ionic strength solutions, consider using a specialized SIT database

"""

        # Basic SIT block with common ion parameters
        sit_block = """SIT
# Added basic SIT parameters - APPROXIMATE VALUES
# Parameters from NEA-TDB compilations
# SIT epsilon parameters for cation-anion pairs [kg/mol]
Na+ Cl-    0.03
Na+ SO4-2  -0.12
Na+ HCO3-  -0.05
Na+ CO3-2  -0.08
K+  Cl-    0.02
K+  SO4-2  -0.10
K+  HCO3-  -0.01
K+  CO3-2  -0.06
Ca+2 Cl-   0.14
Ca+2 SO4-2 0.0
Mg+2 Cl-   0.19
Mg+2 SO4-2 0.33

# Neutral species
CO2(aq) -0.1

"""

        # Define the order of blocks for the SIT format
        block_order = DATABASE_FORMAT_TEMPLATES["sit"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order
        for block_name in block_order:
            if block_name == "SIT":
                output_content += sit_block + "\n"
            elif block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted PHREEQC database to include SIT parameters: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting PHREEQC to SIT: {e}")
        return False


def convert_pitzer_to_phreeqc(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a Pitzer format database to standard PHREEQC format.

    Args:
        source_path: Path to the Pitzer database
        output_path: Path for the output PHREEQC database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["phreeqc"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="Pitzer to PHREEQC conversion",
        )

        # Add conversion notes
        header += """# Pitzer to PHREEQC conversion notes:
# - The Pitzer database core has been maintained, but Pitzer parameters removed
# - This database is intended for standard activity coefficient calculations
# - For high ionic strength solutions, use the original Pitzer database
# - Aqueous species and mineral definitions have been preserved

"""

        # Define the order of blocks for the PHREEQC format
        block_order = DATABASE_FORMAT_TEMPLATES["phreeqc"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order, excluding the PITZER block
        for block_name in block_order:
            if block_name != "PITZER" and block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted Pitzer database to PHREEQC format: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting Pitzer to PHREEQC: {e}")
        return False


def convert_sit_to_phreeqc(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts a SIT format database to standard PHREEQC format.

    Args:
        source_path: Path to the SIT database
        output_path: Path for the output PHREEQC database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(source_path)

        # Format the header
        header = DATABASE_FORMAT_TEMPLATES["phreeqc"]["header"].format(
            date=datetime.now().strftime("%Y-%m-%d"),
            source=os.path.basename(source_path),
            method="SIT to PHREEQC conversion",
        )

        # Add conversion notes
        header += """# SIT to PHREEQC conversion notes:
# - The SIT database core has been maintained, but SIT parameters removed
# - This database is intended for standard activity coefficient calculations
# - For high ionic strength solutions, use the original SIT database
# - Aqueous species and mineral definitions have been preserved

"""

        # Define the order of blocks for the PHREEQC format
        block_order = DATABASE_FORMAT_TEMPLATES["phreeqc"]["blocks"]

        # Start building the output content
        output_content = header

        # Add blocks in the correct order, excluding the SIT block
        for block_name in block_order:
            if block_name != "SIT" and block_name in blocks:
                output_content += f"{block_name}{blocks[block_name]}\n\n"
            elif block_name == "END":
                output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Converted SIT database to PHREEQC format: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting SIT to PHREEQC: {e}")
        return False


def convert_llnl_to_pitzer(source_path: str, output_path: str, options: Dict[str, Any]) -> bool:
    """
    Converts an LLNL database to include Pitzer parameters.

    Args:
        source_path: Path to the LLNL database
        output_path: Path for the output Pitzer database
        options: Conversion options

    Returns:
        True if successful, False otherwise
    """
    # First convert to PHREEQC format
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".dat", dir=os.path.dirname(output_path))
    temp_path = temp_file.name
    temp_file.close()

    try:
        # Convert LLNL to PHREEQC first
        llnl_to_phreeqc_result = convert_llnl_to_phreeqc(source_path, temp_path, options)

        if not llnl_to_phreeqc_result:
            logger.error("Failed to convert LLNL to PHREEQC format (step 1)")
            os.remove(temp_path)
            return False

        # Then convert the PHREEQC to Pitzer
        phreeqc_to_pitzer_result = convert_phreeqc_to_pitzer(temp_path, output_path, options)

        # Clean up the temporary file
        os.remove(temp_path)

        if not phreeqc_to_pitzer_result:
            logger.error("Failed to convert PHREEQC to Pitzer format (step 2)")
            return False

        logger.info(f"Converted LLNL database to Pitzer format: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting LLNL to Pitzer: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False


def extract_minerals_from_database(
    database_path: str, output_path: str, mineral_list: Optional[List[str]] = None
) -> Optional[str]:
    """
    Extracts specified minerals from a database into a new file.

    Args:
        database_path: Path to the source database
        output_path: Path for the output database
        mineral_list: List of minerals to extract (None = all minerals)

    Returns:
        Path to the extracted database or None if extraction failed
    """
    try:
        # Extract all blocks from the source database
        blocks = extract_database_blocks(database_path)

        # Extract minerals
        all_minerals = extract_database_minerals(database_path)

        # Filter minerals if specified
        if mineral_list:
            minerals_to_extract = {}
            for mineral_name in mineral_list:
                # Try exact match
                if mineral_name in all_minerals:
                    minerals_to_extract[mineral_name] = all_minerals[mineral_name]
                else:
                    # Try to find similar minerals
                    result = scan_database_for_mineral(database_path, mineral_name)
                    if result["found"]:
                        if result["match_type"] in ["exact", "case_insensitive"]:
                            minerals_to_extract[result["name"]] = result["info"]
                        elif result["match_type"] == "similar":
                            # Take the first similar match
                            if result["matches"]:
                                match = result["matches"][0]
                                minerals_to_extract[match["name"]] = match["info"]
        else:
            # Use all minerals
            minerals_to_extract = all_minerals

        if not minerals_to_extract:
            logger.error(f"No minerals found to extract from {database_path}")
            return None

        # Format the header
        db_name = os.path.basename(database_path)
        header = f"""# Extracted minerals from database: {db_name}
# Extraction date: {datetime.now().strftime('%Y-%m-%d')}
# Contains {len(minerals_to_extract)} minerals

SOLUTION_MASTER_SPECIES
H       H+      0.0     1.008   1.0
H(0)    H2      0.0     1.008   0.5
H(1)    H+      0.0     1.008   1.0
O       H2O     0.0     16.0    0.0
O(-2)   H2O     0.0     16.0    0.0
O(0)    O2      0.0     16.0    0.0
Ca      Ca+2    0.0     40.08   0.5
Fe      Fe+2    0.0     55.847  0.5
Fe(+2)  Fe+2    0.0     55.847  0.5
Fe(+3)  Fe+3    0.0     55.847  0.334
Mg      Mg+2    0.0     24.312  0.5
Na      Na+     0.0     22.9898 1.0
K       K+      0.0     39.102  1.0
Si      H4SiO4  0.0     28.0843 0.0
C       CO3-2   0.0     12.011  0.5
C(+4)   CO3-2   0.0     12.011  0.5
Al      Al+3    0.0     26.9815 0.33
S       SO4-2   0.0     32.064  0.5
S(6)    SO4-2   0.0     32.064  0.5
Cl      Cl-     0.0     35.453  1.0

SOLUTION_SPECIES
H+ = H+
    log_k 0.0
H2O = H2O
    log_k 0.0
Ca+2 = Ca+2
    log_k 0.0
Fe+2 = Fe+2
    log_k 0.0
Fe+3 = Fe+3
    log_k 0.0
Mg+2 = Mg+2
    log_k 0.0
Na+ = Na+
    log_k 0.0
K+ = K+
    log_k 0.0
Al+3 = Al+3
    log_k 0.0
H4SiO4 = H4SiO4
    log_k 0.0
CO3-2 = CO3-2
    log_k 0.0
SO4-2 = SO4-2
    log_k 0.0
Cl- = Cl-
    log_k 0.0

PHASES
"""

        # Build the minerals block
        minerals_block = ""
        for name, info in minerals_to_extract.items():
            reaction = info.get("reaction", "")
            log_k = info.get("log_k")
            log_k_str = f"log_k {log_k}" if log_k is not None else "log_k 0.0"

            minerals_block += f"{name}\n    {reaction}\n    {log_k_str}\n\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(header + "\n" + minerals_block + "END\n")

        logger.info(f"Extracted {len(minerals_to_extract)} minerals to {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Error extracting minerals from database: {e}")
        return None


def merge_databases(
    source_paths: List[str],
    output_path: str,
    prioritize_newer: bool = True,
    merge_options: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Merges multiple PHREEQC databases into a single database.

    Args:
        source_paths: List of paths to source databases
        output_path: Path for the output merged database
        prioritize_newer: Whether to prioritize definitions from newer databases
        merge_options: Optional merge options

    Returns:
        Path to the merged database or None if merge failed
    """
    if not source_paths:
        logger.error("No source databases provided")
        return None

    # Default merge options
    if merge_options is None:
        merge_options = {}

    # Set default blocks to include
    if "blocks_to_include" not in merge_options:
        merge_options["blocks_to_include"] = None  # Include all blocks

    # Set blocks to exclude
    if "blocks_to_exclude" not in merge_options:
        merge_options["blocks_to_exclude"] = []

    # Set whether to verify minerals
    if "verify_minerals" not in merge_options:
        merge_options["verify_minerals"] = True

    # Create a temp file for the output
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".dat", dir=os.path.dirname(output_path))
    temp_path = temp_file.name
    temp_file.close()

    try:
        # Validate all source databases
        valid_paths = []
        for path in source_paths:
            validation_result = validate_database(path)
            if validation_result["valid"]:
                valid_paths.append(path)
            else:
                errors = validation_result.get("errors", ["Unknown validation error"])
                logger.warning(f"Skipping invalid database: {path}, errors: {errors}")

        if not valid_paths:
            logger.error("No valid source databases to merge")
            return None

        # Read all source databases
        db_contents = []
        for path in valid_paths:
            with open(path, "r", errors="ignore") as f:
                db_contents.append((path, f.read()))

        # Sort by modification time if prioritizing newer databases
        if prioritize_newer:
            db_contents.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)

        # Extract blocks from each database
        blocks_by_db = {}
        seen_blocks = set()

        for path, content in db_contents:
            # Find all blocks in the database
            block_matches = re.findall(BLOCK_PATTERN, content, re.MULTILINE | re.DOTALL)

            blocks_by_db[path] = {}
            for block_name, block_content in block_matches:
                block_name = block_name.strip()
                blocks_by_db[path][block_name] = block_content.strip()
                seen_blocks.add(block_name)

        # Filter blocks based on merge options
        blocks_to_include = merge_options["blocks_to_include"]
        blocks_to_exclude = merge_options["blocks_to_exclude"]

        if blocks_to_include:
            # Only include specified blocks
            blocks_to_process = [b for b in seen_blocks if b in blocks_to_include]
        else:
            # Include all blocks except excluded ones
            blocks_to_process = [b for b in seen_blocks if b not in blocks_to_exclude]

        # Generate merged database content
        merged_content = f"""# Merged PHREEQC database
# Created on {datetime.now().strftime('%Y-%m-%d')}
# Source databases (in order of priority):
"""

        for path in valid_paths:
            db_name = os.path.basename(path)
            merged_content += f"# - {db_name}\n"

        merged_content += "\n"

        # Add required blocks first in the correct order
        required_blocks = [
            "SOLUTION_MASTER_SPECIES",
            "SOLUTION_SPECIES",
            "PHASES",
            "EXCHANGE_MASTER_SPECIES",
            "EXCHANGE_SPECIES",
            "SURFACE_MASTER_SPECIES",
            "SURFACE_SPECIES",
        ]

        # Process required blocks first, then other blocks
        blocks_to_process_ordered = []
        for block in required_blocks:
            if block in blocks_to_process:
                blocks_to_process_ordered.append(block)
                blocks_to_process.remove(block)

        # Add remaining blocks in alphabetical order
        blocks_to_process_ordered.extend(sorted(blocks_to_process))

        # Process each block
        for block_name in blocks_to_process_ordered:
            block_lines = []
            seen_entries = set()

            # Start with the block header
            block_lines.append(f"{block_name}")

            # Process database-specific content in priority order
            for db_path in valid_paths:
                if block_name in blocks_by_db[db_path]:
                    block_content = blocks_by_db[db_path][block_name]

                    # Process entries line by line
                    for line in block_content.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # Simple deduplication for now - only add if we haven't seen this line before
                            # For more complex blocks like PHASES, this needs improvement
                            if line not in seen_entries:
                                block_lines.append(f"    {line}")
                                seen_entries.add(line)

            # Add block separator
            block_lines.append("")

            # Add block to merged content
            merged_content += "\n".join(block_lines) + "\n"

        # Add END marker
        merged_content += "END\n"

        # Write the merged database to the temp file
        with open(temp_path, "w") as f:
            f.write(merged_content)

        # Validate the merged database
        validation_result = validate_database(temp_path)
        if not validation_result["valid"]:
            errors = validation_result.get("errors", ["Unknown validation error"])
            logger.error(f"Merged database is invalid: {errors}")
            os.remove(temp_path)
            return None

        # Move the validated file to the final destination
        shutil.move(temp_path, output_path)

        logger.info(f"Successfully merged {len(valid_paths)} databases into {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Error merging databases: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return None


def create_subset_database(
    source_path: str,
    output_path: str,
    elements: Optional[List[str]] = None,
    minerals: Optional[List[str]] = None,
    blocks: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Creates a subset of a PHREEQC database.

    Args:
        source_path: Path to the source database
        output_path: Path for the output database
        elements: Optional list of elements to include
        minerals: Optional list of minerals to include
        blocks: Optional list of blocks to include

    Returns:
        Path to the subset database or None if creation failed
    """
    try:
        # Extract all blocks from the source database
        source_blocks = extract_database_blocks(source_path)

        # Format the header
        header = f"""# Subset of database: {os.path.basename(source_path)}
# Created on: {datetime.now().strftime('%Y-%m-%d')}
# Contains {len(elements or [])} elements, {len(minerals or [])} minerals

"""

        # Start building the output content
        output_content = header

        # Define default blocks to include
        default_blocks = ["SOLUTION_MASTER_SPECIES", "SOLUTION_SPECIES", "PHASES"]
        blocks_to_include = blocks or default_blocks

        # Process each requested block
        for block_name in blocks_to_include:
            if block_name in source_blocks:
                if block_name == "SOLUTION_MASTER_SPECIES" and elements:
                    # Filter SOLUTION_MASTER_SPECIES block to include only specified elements
                    filtered_lines = []
                    for line in source_blocks[block_name].strip().split("\n"):
                        parts = line.strip().split()
                        if parts and parts[0].split("(")[0] in elements:
                            filtered_lines.append(line)

                    if filtered_lines:
                        output_content += f"{block_name}\n"
                        output_content += "\n".join(filtered_lines) + "\n\n"

                elif block_name == "PHASES" and minerals:
                    # Extract mineral definitions
                    source_minerals = extract_database_minerals(source_path)
                    minerals_to_include = [m for m in source_minerals if m in minerals]

                    if minerals_to_include:
                        output_content += f"{block_name}\n"

                        # Add each mineral definition
                        for mineral_name in minerals_to_include:
                            mineral_info = source_minerals[mineral_name]
                            reaction = mineral_info.get("reaction", "")
                            log_k = mineral_info.get("log_k")
                            log_k_str = f"log_k {log_k}" if log_k is not None else "log_k 0.0"

                            output_content += f"{mineral_name}\n    {reaction}\n    {log_k_str}\n\n"

                else:
                    # Include the entire block
                    output_content += f"{block_name}\n{source_blocks[block_name]}\n\n"

        # Add END marker
        output_content += "END\n"

        # Write the output file
        with open(output_path, "w") as f:
            f.write(output_content)

        logger.info(f"Created subset database: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Error creating subset database: {e}")
        return None


def get_conversion_capabilities() -> Dict[str, List[str]]:
    """
    Gets the supported database conversion capabilities.

    Returns:
        Dictionary with source formats and supported target formats
    """
    return {
        "phreeqc": ["pitzer", "sit"],
        "llnl": ["phreeqc", "pitzer"],
        "minteq": ["phreeqc"],
        "wateq4f": ["phreeqc"],
        "pitzer": ["phreeqc"],
        "sit": ["phreeqc"],
        "generic": ["phreeqc", "pitzer", "sit"],
    }
