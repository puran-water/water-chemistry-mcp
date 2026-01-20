"""
Tool for simulating surface complexation interactions.

FAIL LOUDLY: This module raises typed exceptions on errors.
Uses PHREEQC SURFACE blocks for adsorption modeling.

NOTE: Surface complexation requires the allow_raw_phreeqc=True flag
for advanced configurations, as phreeqpython has limited native support.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from utils.database_management import database_manager
from utils.exceptions import (
    DatabaseLoadError,
    FeatureNotSupportedError,
    InputValidationError,
    PhreeqcSimulationError,
    SurfaceDefinitionError,
)
from utils.helpers import build_selected_output_block, build_solution_block, build_surface_block
from utils.import_helpers import PHREEQPYTHON_AVAILABLE

from .phreeqc_wrapper import PhreeqcError, run_phreeqc_simulation
from .schemas import (
    SimulateSurfaceInteractionInput,
    SimulateSurfaceInteractionOutput,
    SolutionOutput,
)

logger = logging.getLogger(__name__)


async def simulate_surface_interaction(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate surface complexation/adsorption using PHREEQC SURFACE blocks.

    This function models:
    - Adsorption on iron oxides (HFO - hydrous ferric oxide)
    - Adsorption on clay minerals
    - Adsorption on activated carbon
    - General surface complexation

    Common surface types in PHREEQC databases:
    - Hfo_w, Hfo_s: Hydrous ferric oxide (weak and strong sites)
    - Goe_w: Goethite
    - Sfo_w, Sfo_s: Hydrous ferric oxide (alternative naming)

    Args:
        input_data: Dictionary containing:
            - initial_solution: Starting water composition
            - surface_definition: Surface sites and amounts
            - database: PHREEQC database to use (must contain surface species)

    Returns:
        Dictionary containing solution state after surface equilibration

    Raises:
        InputValidationError: If input validation fails
        SurfaceDefinitionError: If surface definition is invalid
        PhreeqcSimulationError: If simulation fails
        DatabaseLoadError: If database cannot be loaded
    """
    logger.info("Running simulate_surface_interaction tool...")

    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcSimulationError("PhreeqPython is not available. Install with: pip install phreeqpython")

    # Validate input
    try:
        input_model = SimulateSurfaceInteractionInput(**input_data)
    except Exception as e:
        raise InputValidationError(f"Input validation error: {e}")

    # Resolve database - surface complexation needs a database with surface species
    database_path = database_manager.resolve_and_validate_database(input_model.database, category="surface")

    # Extract surface definition
    surface_def = input_model.surface_definition.model_dump(exclude_defaults=True)

    # Check if we have any surface definition
    has_raw_block = bool(surface_def.get("surface_block_string"))
    has_sites_info = bool(surface_def.get("sites_info"))
    has_sites = bool(surface_def.get("sites"))

    if not has_raw_block and not has_sites_info and not has_sites:
        raise SurfaceDefinitionError(
            "No surface definition provided. Provide 'surface_block_string' for raw PHREEQC input, "
            "or 'sites_info' / 'sites' for structured definition.",
            missing_fields=["surface_block_string", "sites_info", "sites"],
        )

    # Build PHREEQC input
    try:
        solution_data = input_model.initial_solution.model_dump(exclude_defaults=True)
        solution_str = build_solution_block(solution_data, solution_num=1)

        # Build SURFACE block - this will raise SurfaceDefinitionError if invalid
        surface_str = build_surface_block(surface_def, block_num=1)

        phreeqc_input = solution_str + "\n"

        # Add SURFACE_MASTER_SPECIES and SURFACE_SPECIES if provided
        if surface_def.get("sites_block_string"):
            phreeqc_input += surface_def["sites_block_string"].strip() + "\n\n"

        phreeqc_input += surface_str + "\n"
        phreeqc_input += "USE solution 1\n"
        phreeqc_input += "USE surface 1\n"
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True,
            phases=True,
            molalities=True,
            totals=True,
            surface=True,
        )
        phreeqc_input += "END\n"

        logger.debug(f"PHREEQC input:\n{phreeqc_input[:500]}...")

        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)

        # If we got a list, extract single result
        if isinstance(results, list) and results:
            results = results[0]

        # Add surface info to results
        results["surface_equilibrated"] = True
        results["surface_sites"] = _extract_surface_info(surface_def)

        logger.info("simulate_surface_interaction finished successfully.")
        return SimulateSurfaceInteractionOutput(**results).model_dump(exclude_defaults=True)

    except SurfaceDefinitionError:
        raise
    except PhreeqcError as e:
        # Check for common surface-related errors
        error_str = str(e).lower()
        if "surface" in error_str and ("not found" in error_str or "unknown" in error_str):
            raise SurfaceDefinitionError(
                f"Surface species not found in database. "
                f"The database '{os.path.basename(database_path)}' may not contain the required "
                f"surface species definitions. Try using a database like 'wateq4f.dat' or 'phreeqc.dat' "
                f"that includes surface complexation data.",
                missing_fields=["surface_species"],
                invalid_fields={"database": os.path.basename(database_path)},
            )
        raise PhreeqcSimulationError(
            f"Surface interaction simulation failed: {e}",
            phreeqc_error=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error in simulate_surface_interaction")
        raise PhreeqcSimulationError(f"Unexpected error: {e}")


def _extract_surface_info(surface_def: Dict[str, Any]) -> Dict[str, Any]:
    """Extract surface site information for output."""
    info = {}

    if surface_def.get("sites_info"):
        sites = []
        for site in surface_def["sites_info"]:
            if isinstance(site, dict):
                sites.append(
                    {
                        "name": site.get("name"),
                        "moles": site.get("moles", site.get("site_density")),
                        "specific_area_m2_g": site.get("specific_area_m2_g", site.get("specific_area")),
                        "mass_g": site.get("mass_g", site.get("mass")),
                    }
                )
        info["sites"] = sites

    elif surface_def.get("sites"):
        sites = []
        for site in surface_def["sites"]:
            if isinstance(site, str):
                sites.append({"name": site})
            elif isinstance(site, dict):
                sites.append(site)
        info["sites"] = sites

    if surface_def.get("equilibrate_with_solution_number"):
        info["equilibrated_with_solution"] = surface_def["equilibrate_with_solution_number"]

    return info


# ============================================================================
# Convenience functions for common surface types
# ============================================================================


async def simulate_hfo_adsorption(
    solution_data: Dict[str, Any],
    hfo_mass_g: float = 1.0,
    hfo_specific_area_m2_g: float = 600.0,
    weak_site_density_mol_m2: float = 2e-4,
    strong_site_density_mol_m2: float = 5e-6,
    database: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function for simulating adsorption on hydrous ferric oxide (HFO).

    Uses the Dzombak & Morel (1990) two-site model for HFO.

    Args:
        solution_data: Water composition dictionary
        hfo_mass_g: Mass of HFO in grams
        hfo_specific_area_m2_g: Specific surface area (default 600 m²/g)
        weak_site_density_mol_m2: Weak site density (default 2e-4 mol/m²)
        strong_site_density_mol_m2: Strong site density (default 5e-6 mol/m²)
        database: PHREEQC database (default will use one with HFO data)

    Returns:
        Dictionary with solution state after HFO equilibration
    """
    # Calculate site concentrations
    total_area = hfo_mass_g * hfo_specific_area_m2_g
    weak_sites_mol = weak_site_density_mol_m2 * total_area
    strong_sites_mol = strong_site_density_mol_m2 * total_area

    input_data = {
        "initial_solution": solution_data,
        "surface_definition": {
            "sites_info": [
                {
                    "name": "Hfo_w",
                    "moles": weak_sites_mol,
                    "specific_area_m2_g": hfo_specific_area_m2_g,
                    "mass_g": hfo_mass_g,
                },
                {
                    "name": "Hfo_s",
                    "moles": strong_sites_mol,
                    "specific_area_m2_g": hfo_specific_area_m2_g,
                    "mass_g": hfo_mass_g,
                },
            ],
            "equilibrate_with_solution_number": 1,
        },
        "database": database,
    }

    return await simulate_surface_interaction(input_data)
