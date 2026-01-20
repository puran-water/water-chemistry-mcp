"""
Mass balance validation for water chemistry calculations.
Ensures all mass is accounted for in precipitation reactions.
"""

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def calculate_mass_balance(
    initial_solution: Dict[str, float],
    final_solution: Dict[str, float],
    precipitates: Dict[str, float],
    added_chemicals: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Calculate mass balance for major elements.

    Args:
        initial_solution: Initial element concentrations (mol/L)
        final_solution: Final element concentrations (mol/L)
        precipitates: Precipitated phases and amounts (mol/L)
        added_chemicals: List of added chemicals with formula and amount

    Returns:
        Dict with mass balance results and any discrepancies
    """
    mass_balance = {}

    # Define mineral compositions (moles of element per mole of mineral)
    mineral_compositions = {
        "Calcite": {"Ca": 1, "C": 1},
        "Aragonite": {"Ca": 1, "C": 1},
        "Vaterite": {"Ca": 1, "C": 1},
        "Dolomite": {"Ca": 1, "Mg": 1, "C": 2},
        "Brucite": {"Mg": 1},
        "Mg(OH)2": {"Mg": 1},
        "Portlandite": {"Ca": 1},
        "Gypsum": {"Ca": 1, "S": 1},
        "Anhydrite": {"Ca": 1, "S": 1},
        "Ferrihydrite": {"Fe": 1},
        "Fe(OH)3(a)": {"Fe": 1},
        "Al(OH)3(a)": {"Al": 1},
        "SiO2(a)": {"Si": 1},
        "Sepiolite": {"Mg": 4, "Si": 6},
        "Barite": {"Ba": 1, "S": 1},
        "Celestite": {"Sr": 1, "S": 1},
        "Fluorite": {"Ca": 1, "F": 2},
        "Hydroxyapatite": {"Ca": 5, "P": 3},
    }

    # Track elements
    elements_to_check = ["Ca", "Mg", "Fe", "Al", "Si", "S", "P", "C", "Ba", "Sr"]

    for element in elements_to_check:
        # Initial amount
        initial = initial_solution.get(element, 0)

        # Added amount from chemicals
        added = 0
        if added_chemicals:
            for chemical in added_chemicals:
                # Parse chemical formula to get element contribution
                # Simplified - would need full formula parser
                if element == "Ca" and "Ca(OH)2" in chemical.get("formula", ""):
                    added += chemical.get("amount", 0)
                elif element == "Mg" and "Mg(OH)2" in chemical.get("formula", ""):
                    added += chemical.get("amount", 0)
                # Add more chemical parsing as needed

        # Final amount in solution
        final = final_solution.get(element, 0)

        # Amount in precipitates
        precipitated = 0
        for mineral, amount in precipitates.items():
            if mineral in mineral_compositions:
                comp = mineral_compositions[mineral]
                if element in comp:
                    precipitated += amount * comp[element]

        # Calculate balance
        total_initial = initial + added
        total_final = final + precipitated

        balance_error = abs(total_initial - total_final)
        balance_percent = (balance_error / total_initial * 100) if total_initial > 0 else 0

        mass_balance[element] = {
            "initial": initial,
            "added": added,
            "final_solution": final,
            "precipitated": precipitated,
            "total_initial": total_initial,
            "total_final": total_final,
            "balance_error": balance_error,
            "balance_percent": balance_percent,
            "balanced": balance_percent < 5,  # Less than 5% error
        }

    # Overall assessment
    unbalanced_elements = [
        e for e, data in mass_balance.items() if not data["balanced"] and data["total_initial"] > 0.001
    ]

    mass_balance["summary"] = {
        "all_balanced": len(unbalanced_elements) == 0,
        "unbalanced_elements": unbalanced_elements,
        "max_error_percent": max(
            [
                data["balance_percent"]
                for data in mass_balance.values()
                if isinstance(data, dict) and "balance_percent" in data
            ],
            default=0,
        ),
    }

    if unbalanced_elements:
        logger.warning(f"Mass balance issues for elements: {unbalanced_elements}")

    return mass_balance


def add_mass_balance_to_output(results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add mass balance validation to tool output.

    Args:
        results: Tool output dictionary

    Returns:
        Results with mass_balance section added
    """
    try:
        # Extract needed data
        initial_comp = results.get("initial_solution", {}).get("composition", {})
        final_comp = results.get("solution_summary", {}).get("composition", {})
        precipitates = results.get("precipitated_phases", {})
        reactants = results.get("reactants_added", [])

        if initial_comp and final_comp:
            mass_balance = calculate_mass_balance(initial_comp, final_comp, precipitates, reactants)

            results["mass_balance"] = mass_balance

            # Add warning if mass balance issues detected
            if not mass_balance["summary"]["all_balanced"]:
                if "warnings" not in results:
                    results["warnings"] = []
                results["warnings"].append(
                    f"Mass balance discrepancy detected for: {mass_balance['summary']['unbalanced_elements']}"
                )

    except Exception as e:
        logger.error(f"Error calculating mass balance: {e}")

    return results
