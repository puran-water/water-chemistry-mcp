"""
Convergence strategies for handling difficult PHREEQC simulations.
"""

import logging
import re
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ConvergenceStrategy:
    """Strategies for handling convergence failures in PHREEQC simulations."""

    @staticmethod
    def add_relaxed_knobs(input_string: str, ionic_strength: Optional[float] = None) -> str:
        """Add relaxed KNOBS settings for better convergence."""

        # Different KNOBS based on ionic strength
        if ionic_strength and ionic_strength > 1.0:
            # High ionic strength needs special handling
            knobs = """
KNOBS
    -iterations 1000
    -convergence_tolerance 1e-8
    -tolerance 1e-10
    -step_size 10
    -pe_step_size 5
    -diagonal_scale true
    -debug_model true
"""
        else:
            # Standard relaxed settings
            knobs = """
KNOBS
    -iterations 500
    -convergence_tolerance 1e-9
    -tolerance 1e-11
    -step_size 100
    -diagonal_scale true
"""

        # Check if KNOBS already exists
        if "KNOBS" in input_string:
            # Replace existing KNOBS
            input_string = re.sub(r"KNOBS.*?(?=\n[A-Z]|\Z)", knobs, input_string, flags=re.DOTALL)
        else:
            # Add KNOBS at the beginning
            input_string = knobs + input_string

        return input_string

    @staticmethod
    def simplify_model(input_string: str) -> str:
        """Simplify the model by removing complex phases temporarily."""

        # Remove SURFACE blocks
        input_string = re.sub(r"SURFACE.*?(?=\n[A-Z]|\Z)", "", input_string, flags=re.DOTALL)

        # Remove EXCHANGE blocks
        input_string = re.sub(r"EXCHANGE.*?(?=\n[A-Z]|\Z)", "", input_string, flags=re.DOTALL)

        # Limit equilibrium phases to most common ones
        if "EQUILIBRIUM_PHASES" in input_string:
            # Keep only essential minerals
            essential_minerals = ["Calcite", "Gypsum", "Fe(OH)3(a)", "Al(OH)3(a)"]

            # Extract current equilibrium phases block
            eq_match = re.search(r"EQUILIBRIUM_PHASES\s+\d+\n(.*?)(?=\n[A-Z]|\Z)", input_string, flags=re.DOTALL)
            if eq_match:
                eq_block = eq_match.group(1)
                new_eq_lines = ["EQUILIBRIUM_PHASES 1"]

                for mineral in essential_minerals:
                    if mineral.lower() in eq_block.lower():
                        # Find the line with this mineral
                        mineral_match = re.search(f"{mineral}.*$", eq_block, flags=re.IGNORECASE | re.MULTILINE)
                        if mineral_match:
                            new_eq_lines.append(f"    {mineral_match.group(0)}")

                if len(new_eq_lines) > 1:
                    new_eq_block = "\n".join(new_eq_lines) + "\n"
                    input_string = input_string.replace(eq_match.group(0), new_eq_block)

        return input_string

    @staticmethod
    def add_background_electrolyte(input_string: str) -> str:
        """Add small amount of NaCl for numerical stability."""

        # Find SOLUTION block
        solution_match = re.search(r"SOLUTION\s+\d+\n(.*?)(?=\n[A-Z]|\Z)", input_string, flags=re.DOTALL)

        if solution_match:
            solution_block = solution_match.group(0)

            # Check if Na and Cl are already present
            has_na = re.search(r"^\s*Na\s+", solution_block, flags=re.MULTILINE)
            has_cl = re.search(r"^\s*Cl\s+", solution_block, flags=re.MULTILINE)

            # Add small amounts if not present
            additions = []
            if not has_na:
                additions.append("    Na        0.1")
            if not has_cl:
                additions.append("    Cl        0.1")

            if additions:
                # Insert before the end of SOLUTION block
                insertion_point = solution_block.rfind("\n")
                new_solution = (
                    solution_block[:insertion_point] + "\n" + "\n".join(additions) + solution_block[insertion_point:]
                )
                input_string = input_string.replace(solution_block, new_solution)

        return input_string

    @staticmethod
    def switch_to_pitzer_database(input_string: str, current_database: str) -> Optional[str]:
        """
        Suggest switching to PITZER database for high ionic strength.
        Returns the suggested database path or None.
        """

        # Check if already using pitzer
        if "pitzer" in current_database.lower():
            return None

        # Look for high concentrations that suggest high ionic strength
        high_conc_pattern = r"^\s*(?:Na|Cl|Ca|Mg|K|SO4|S\(6\))\s+(\d+(?:\.\d+)?)"
        matches = re.findall(high_conc_pattern, input_string, flags=re.MULTILINE)

        total_conc = sum(float(m) for m in matches)

        # If total concentration > 10000 mg/L, suggest PITZER
        if total_conc > 10000:
            logger.info(
                f"High ionic strength detected (total conc: {total_conc} mg/L). " f"Suggesting PITZER database."
            )
            return "pitzer.dat"

        return None
