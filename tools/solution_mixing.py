"""
Tool for simulating the mixing of multiple solutions.
"""

import logging
import os
from typing import Dict, Any, List, Optional
import asyncio

from utils.database_management import database_manager
from .schemas import SimulateSolutionMixingInput, SimulateSolutionMixingOutput
from .phreeqc_wrapper import run_phreeqc_simulation, PhreeqcError
from utils.helpers import (
    build_solution_block, 
    build_mix_block,
    build_equilibrium_phases_block,
    build_selected_output_block
)
from utils.constants import DEFAULT_MINERALS

logger = logging.getLogger(__name__)

async def simulate_solution_mixing(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulates mixing multiple solutions and calculates the resulting equilibrium.
    
    Args:
        input_data: Dictionary containing:
            - solutions_to_mix: List of solutions and their mixing fractions/volumes
            - database: Path to PHREEQC database file (optional)
    
    Returns:
        Dictionary containing detailed solution properties after mixing
    """
    logger.info("Running simulate_solution_mixing tool...")
    
    # Create pydantic model from input data for validation
    try:
        input_model = SimulateSolutionMixingInput(**input_data)
    except Exception as e:
        logger.error(f"Input validation error: {e}")
        return {"error": f"Input validation error: {e}"}
    
    # Validate database if provided
    database_path = input_model.database
    if database_path:
        resolved_path = database_manager.resolve_database_path(database_path)
        if resolved_path and database_manager.validate_database_path(resolved_path):
            database_path = resolved_path
            logger.info(f"Using resolved database path: {database_path}")
        else:
            logger.warning(f"Invalid database path: {database_path}, using recommended database instead")
            # For solution mixing, use a general purpose database
            recommended_db = database_manager.recommend_database('general')
            logger.info(f"Using recommended database: {recommended_db}")
            database_path = recommended_db
    else:
        # No database specified, use recommended
        database_path = database_manager.recommend_database('general')
        logger.info(f"No database specified, using recommended database: {database_path}")
    
    try:
        phreeqc_input = ""
        mix_map = {}
        solutions_input = input_model.solutions_to_mix

        # Determine whether we're using explicit volumes or fractions
        any_volume = any(getattr(s, 'volume_L', None) is not None for s in solutions_input)
        any_fraction = any(getattr(s, 'fraction', None) is not None for s in solutions_input)
        # Back-compat: if only legacy field present
        if not any_volume and not any_fraction:
            any_fraction = any(getattr(s, 'fraction_or_volume', None) is not None or getattr(s, 'volume_fraction', None) is not None for s in solutions_input)

        # Build solution blocks and compute weights
        raw_weights = []  # raw numbers (volumes or fractions)
        for i, sol_input in enumerate(solutions_input):
            sol_num = i + 1
            phreeqc_input += build_solution_block(
                sol_input.solution.model_dump(exclude_defaults=True),
                solution_num=sol_num
            )

            # Prefer explicit fields
            if any_volume:
                w = getattr(sol_input, 'volume_L', None)
            else:
                # Fractions: new field or legacy
                w = getattr(sol_input, 'fraction', None)
                if w is None:
                    # Legacy support
                    w = getattr(sol_input, 'fraction_or_volume', None)
                    if w is None:
                        w = getattr(sol_input, 'volume_fraction', None)
            raw_weights.append(w)

        # If all weights missing for fractions, default to equal
        if not any_volume:
            if all(w is None for w in raw_weights):
                weights = [1.0 / len(solutions_input)] * len(solutions_input)
                logger.info("No fractions provided; defaulting to equal fractions.")
            else:
                # Replace None weights with zero for normalization, then normalize
                tmp = [w if (w is not None and w >= 0) else 0.0 for w in raw_weights]
                s = sum(tmp)
                if s <= 0:
                    weights = [1.0 / len(tmp)] * len(tmp)
                    logger.info("Non-positive fraction sum; defaulting to equal fractions.")
                else:
                    # Normalize to sum 1.0
                    weights = [w / s for w in tmp]
                    if abs(sum(tmp) - 1.0) > 1e-6:
                        logger.info("Normalizing volume fractions to sum to 1.0")
        else:
            # Volumes: fill missing with 0 and use as-is
            weights = [w if (w is not None and w >= 0) else 0.0 for w in raw_weights]
            total_vol = sum(weights)
            if total_vol <= 0:
                raise ValueError("All provided volumes are missing or zero; cannot perform volume-based mixing")

        # Prepare MIX mapping (PHREEQC accepts either normalized fractions or absolute volumes)
        for i, w in enumerate(weights, start=1):
            mix_map[i] = w
        
        if any_volume:
            logger.info("Treating mixing inputs as volumes (L).")
        else:
            logger.info("Treating mixing inputs as fractions (normalized).")
        
        # Build mix block
        phreeqc_input += build_mix_block(mix_num=1, solution_map=mix_map)
        phreeqc_input += "USE mix 1\n"  # Mix result becomes the active solution
        
        # IMPORTANT: Precipitation is ALWAYS enabled by default
        # The only exception is if it's explicitly disabled in the input model
        allow_precipitation = getattr(input_model, 'allow_precipitation', True)
        
        if allow_precipitation:
            # Get water chemistry profile from the mixture of solutions
            # Combine the analyses of all solutions for mineral selection
            combined_analysis = {}
            # For mineral selection, use fraction weights (convert volumes to fractions if needed)
            if any_volume:
                total = sum(weights)
                frac_weights = [w / total for w in weights]
            else:
                frac_weights = weights

            for sol_input, wfrac in zip(solutions_input, frac_weights):
                if hasattr(sol_input.solution, 'analysis') and sol_input.solution.analysis:
                    for element, conc in sol_input.solution.analysis.items():
                        if element in combined_analysis:
                            combined_analysis[element] += float(conc) * wfrac
                        else:
                            combined_analysis[element] = float(conc) * wfrac
            
            # Select appropriate minerals based on water chemistry
            from utils.constants import select_minerals_for_water_chemistry
            compatible_minerals = select_minerals_for_water_chemistry(combined_analysis, database_path)
            
            logger.info(f"Selected minerals for precipitation based on water chemistry: {', '.join(compatible_minerals)}")
            
            # Build equilibrium phases block with compatible minerals
            if compatible_minerals:
                phases_to_consider = [{'name': name} for name in compatible_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(phases_to_consider, block_num=1)
                
                if equilibrium_phases_str:
                    phreeqc_input += equilibrium_phases_str
                    phreeqc_input += "USE equilibrium_phases 1\n"
                    logger.info("Enabled precipitation in solution mixing")
                else:
                    logger.warning("Failed to build equilibrium phases block for solution mixing")
            else:
                logger.warning("No compatible minerals found for precipitation in solution mixing")
        
        # Add selected output
        phreeqc_input += build_selected_output_block(
            block_num=1,
            saturation_indices=True, 
            phases=True, 
            molalities=True, 
            totals=True
        ) + "END\n"
        
        # Run simulation
        results = await run_phreeqc_simulation(phreeqc_input, database_path=database_path)
        
        # If we got a list, extract the single result
        if isinstance(results, list) and results:
            results = results[0]
            
        # Convert to output model
        output_model = SimulateSolutionMixingOutput(**results)
        
        logger.info("simulate_solution_mixing tool finished successfully.")
        return output_model.model_dump(exclude_defaults=True)
        
    except PhreeqcError as e:
        logger.error(f"Solution mixing tool failed: {e}")
        return {"error": str(e)}
        
    except Exception as e:
        logger.exception("Unexpected error in simulate_solution_mixing")
        return {"error": f"Unexpected server error: {e}"}
