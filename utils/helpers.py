"""
Helper functions for water chemistry calculations.

FAIL LOUDLY: These functions raise typed exceptions instead of returning
empty strings on error. This ensures errors are never silently ignored.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .exceptions import (
    GasPhaseError,
    InputValidationError,
    KineticsDefinitionError,
    RedoxSpecificationError,
    SurfaceDefinitionError,
)

logger = logging.getLogger(__name__)


def build_fix_pe_phase() -> str:
    """
    Generate PHREEQC PHASES block for Fix_pe pseudo-phase.

    This pseudo-phase allows constraining electron activity (pe) throughout
    a simulation. The reaction e- = e- with log_k = 0 means that at equilibrium,
    the saturation index equals log(a_e-) = -pe.

    To fix pe at a specific value, use in EQUILIBRIUM_PHASES with:
        Fix_pe  <SI>  <initial_moles>
    Where SI = -target_pe (e.g., SI = 4.0 to fix pe at -4.0 for anaerobic)

    This approach models systems with sufficient reducing/oxidizing capacity
    to maintain the specified pe without requiring explicit redox agents.

    Returns:
        PHREEQC PHASES block string defining the Fix_pe pseudo-phase
    """
    return """PHASES
Fix_pe
    e- = e-
    log_k 0.0
"""


def build_solution_block(solution_data: Dict[str, Any], solution_num: int = 1, solution_number: int = None) -> str:
    """Builds a PHREEQC SOLUTION block string."""
    # Handle both solution_num and solution_number parameters
    if solution_number is not None:
        solution_num = solution_number

    lines = [f"SOLUTION {solution_num}"]

    # Element mapping for common wastewater parameters
    # NOTE: minteq.v4.dat uses bare element names as master species (e.g., P -> PO4-3)
    # Do NOT map P to P(5) as it breaks TOT("P") in USER_PUNCH
    # Users can specify valence explicitly if needed: P(5), Fe(3), S(-2), etc.
    #
    # WARNING: For anaerobic conditions, users MUST specify sulfide as "S(-2)" not "S"
    # because "S" maps to sulfate S(6), which removes the key reducing agent!
    ELEMENT_MAPPING = {
        # "P": "P",  # Phosphorus - don't remap, minteq.v4.dat uses P as master species
        "N": "N(5)",  # Nitrogen as nitrate (use N(-3) for ammonia)
        "Fe": "Fe(2)",  # Iron defaults to ferrous (use Fe(3) for ferric)
        "S": "S(6)",  # Sulfur as sulfate - USE "S(-2)" for sulfide in anaerobic!
        "As": "As(5)",  # Arsenic as arsenate
        "Mn": "Mn(2)",  # Manganese as Mn2+
        "Cr": "Cr(6)",  # Chromium as chromate
    }

    # Check for common anaerobic input mistakes - warn if "S" used without valence
    # when pe suggests anaerobic conditions
    pe_value = solution_data.get("pe", 4.0)
    analysis = solution_data.get("analysis", {})
    if pe_value < 0 and "S" in analysis and "S(-2)" not in analysis:
        logger.warning(
            "Anaerobic conditions (pe < 0) detected but 'S' used without valence. "
            "'S' maps to sulfate S(6), not sulfide. For anaerobic digesters, "
            "use 'S(-2)' for sulfide to enable Fe reduction and FeS precipitation."
        )
    # Use defaults from schema if not provided
    lines.append(f"    temp      {solution_data.get('temperature_celsius', 25.0)}")
    lines.append(f"    pressure  {solution_data.get('pressure_atm', 1.0)}")
    lines.append(f"    units     {solution_data.get('units', 'mg/L')}")

    if "density" in solution_data and solution_data["density"] is not None:
        lines.append(f"    density   {solution_data['density']}")

    # NOTE: Auto-redox couple detection has been removed.
    # pe is now constrained using the Fix_pe pseudo-phase in EQUILIBRIUM_PHASES
    # for both anaerobic and aerobic modes. This provides more robust pe control
    # than relying on sulfide redox couple, which has insufficient buffering
    # capacity against oxidizing agents like FeCl3.

    # Handle pH/pe/charge balance priority
    if "charge_balance" in solution_data and solution_data["charge_balance"]:
        lines.append(f"    pH        {solution_data.get('ph', solution_data.get('pH', 7.0))}")  # pH still needed
        lines.append(f"    pe        {solution_data.get('pe', 4.0)}")  # Still need pe
        lines.append(f"    {solution_data['charge_balance']} charge")  # Charge balance on specified element
    elif "redox" in solution_data and solution_data["redox"]:
        lines.append(
            f"    pH        {solution_data.get('ph', solution_data.get('pH', 7.0))}"
        )  # Need pH if using redox couple
        lines.append(f"    redox     {solution_data['redox']}")
    elif ("ph" in solution_data and solution_data["ph"] is not None) or (
        "pH" in solution_data and solution_data["pH"] is not None
    ):
        # Handle both lowercase and uppercase pH
        ph_value = solution_data.get("ph") or solution_data.get("pH")
        lines.append(f"    pH        {ph_value}")
        pe_value = solution_data.get("pe", 4.0)
        # Allow negative pe values for anaerobic conditions
        lines.append(f"    pe        {pe_value}")
    else:
        # If no pH and no charge balance specified, default values
        logger.warning(f"No pH or charge_balance specified for SOLUTION {solution_num}. Defaulting pH=7.0, pe=4.0.")
        lines.append(f"    pH        7.0")
        lines.append(f"    pe        4.0")

    analysis = solution_data.get("analysis", {})
    for element, value in analysis.items():
        # Apply element mapping if needed
        element_to_use = ELEMENT_MAPPING.get(element, element)

        if isinstance(value, (int, float)):
            # Use 14 chars to ensure space after long element names like "Alkalinity"
            lines.append(f"    {element_to_use:<14}{value}")
        elif isinstance(value, str):  # Handle 'Alkalinity as CaCO3 120' or 'S(6) 96'
            parts = value.split()
            if len(parts) >= 2:
                # Check if second part is a number (e.g., "Ca 40")
                try:
                    float(parts[1])
                    lines.append(f"    {parts[0]:<14}{' '.join(parts[1:])}")
                except ValueError:
                    # Handle format like "as CaCO3 100" -> "100 as CaCO3"
                    # PHREEQC expects: Alkalinity    100 as CaCO3
                    if parts[0].lower() == "as" and len(parts) >= 3:
                        # Input: "as CaCO3 100" -> Output: "100 as CaCO3"
                        try:
                            num_val = float(parts[-1])
                            unit_parts = parts[:-1]  # "as CaCO3"
                            lines.append(f"    {element_to_use:<14}{num_val} {' '.join(unit_parts)}")
                        except ValueError:
                            # Can't parse number, pass raw
                            lines.append(f"    {element_to_use:<14}{value}")
                    else:
                        # Other string formats, pass as-is
                        lines.append(f"    {element_to_use:<14}{value}")
            else:
                lines.append(f"    {element_to_use:<14}{value}")  # Pass raw string
        elif isinstance(value, dict):
            val_num = value.get("value")
            if val_num is not None:
                line = f"    {element:<14}{val_num}"
                if "as" in value:
                    line += f" as {value['as']}"
                if value.get("charge", False):
                    line += " charge"
                lines.append(line)

    return "\n".join(lines) + "\n"


def build_reaction_block(reactants: List[Dict[str, Any]], reaction_num: int = 1) -> str:
    """
    Builds a PHREEQC REACTION block string.

    IMPORTANT: PHREEQC requires specific formatting for reactions to avoid parsing errors.
    Format follows these rules:
    1. First line is always REACTION number
    2. Next line(s) are reactant formulas followed by their stoichiometric coefficients
    3. Final line is total moles followed by optional 'in X steps'
    """
    # Start reaction block
    lines = [f"REACTION {reaction_num}"]

    # Generate reactant formula entries
    formula_lines = []
    total_amount = 0
    units = None

    # First gather all formulas with their stoichiometric coefficients (always 1.0 for simple additions)
    for reactant in reactants:
        formula = reactant.get("formula")
        amount = reactant.get("amount")
        if not units:
            units = reactant.get("units", "mmol")  # Use first reactant's units or default to mmol

        if formula and amount is not None:
            # Add formula with explicit 1.0 coefficient (critical for PHREEQC parsing)
            formula_lines.append(f"    {formula} 1.0")
            total_amount += float(amount)

    # FAIL LOUDLY: No valid reactants is an error, not a silent no-op
    if not formula_lines:
        raise InputValidationError(
            "No valid reactants provided for REACTION block. " "Each reactant must have 'formula' and 'amount' fields."
        )

    # Add formula lines to reaction block
    lines.extend(formula_lines)

    # Handle advanced step specifications if present
    if reactants and "steps" in reactants[0] and isinstance(reactants[0]["steps"], dict):
        steps_data = reactants[0]["steps"]
        amounts = steps_data.get("amounts", [])
        step_units = steps_data.get("units", units)
        count = steps_data.get("count")

        if amounts and count:
            steps_str = " ".join(map(str, amounts))
            lines.append(f"    {steps_str} {step_units} in {count} steps")
        elif amounts:
            steps_str = " ".join(map(str, amounts))
            lines.append(f"    {steps_str} {step_units}")
    else:
        # Add standard 'total moles in 1 step' line - with a space between the amount and units
        # This spacing is crucial for PHREEQC to parse correctly
        lines.append(f"    {total_amount} {units} in 1 steps")

    return "\n".join(lines) + "\n"


def build_equilibrium_phases_block(
    phases: List[Dict[str, Any]],
    block_num: int = 1,
    allow_empty: bool = False,
    precipitation_only: bool = True,
) -> str:
    """
    Builds an EQUILIBRIUM_PHASES block.

    Args:
        phases: List of phase definitions with 'name', optional 'target_si', 'initial_moles'
        block_num: Block number for PHREEQC
        allow_empty: If True, return empty string for no valid phases (for optional use cases).
                     If False (default), raise InputValidationError.
        precipitation_only: If True (default), minerals can only precipitate (initial_moles=0).
                           If False, minerals can also dissolve (initial_moles=10).
                           This is critical for dosing simulations where we don't want
                           pre-equilibration with minerals that could alter pH.

    Returns:
        PHREEQC EQUILIBRIUM_PHASES block string

    Raises:
        InputValidationError: If no valid phases and allow_empty=False
    """
    # Default initial_moles based on mode:
    # - precipitation_only=True (default): 0.0 - minerals can only form if supersaturated
    # - precipitation_only=False: 10.0 - minerals can dissolve or precipitate
    default_initial_moles = 0.0 if precipitation_only else 10.0

    lines = [f"EQUILIBRIUM_PHASES {block_num}"]
    valid_phases = False
    for phase_info in phases:
        name = phase_info.get("name")
        if name:
            target_si = phase_info.get("target_si", 0.0)
            initial_moles = phase_info.get("initial_moles", default_initial_moles)
            lines.append(f"    {name:<15} {target_si:<8} {initial_moles}")
            valid_phases = True

    if not valid_phases:
        if allow_empty:
            return ""
        raise InputValidationError(
            "No valid phases provided for EQUILIBRIUM_PHASES block. " "Each phase must have a 'name' field."
        )
    return "\n".join(lines) + "\n"


def build_equilibrium_phases_with_pe_constraint(
    phases: List[Dict[str, Any]],
    pe_constraint: Optional[Dict[str, Any]] = None,
    block_num: int = 1,
    allow_empty: bool = False,
    precipitation_only: bool = True,
) -> str:
    """
    Builds an EQUILIBRIUM_PHASES block with optional pe constraint.

    This function extends build_equilibrium_phases_block() to include pe
    constraint pseudo-phases for thermodynamically correct redox modeling.

    pe constraint methods:
    - "fix_pe": Uses Fix_pe pseudo-phase to maintain constant pe
      (models system with sufficient reducing/oxidizing capacity)
    - "o2_equilibrium": Equilibrates with O2(g) at specified partial pressure
      (thermodynamically correct for aerobic conditions)
    - "none" or None: No pe constraint (pe determined by solution equilibrium)

    Args:
        phases: List of phase definitions with 'name', optional 'target_si', 'initial_moles'
        pe_constraint: Dict with pe constraint specification:
            {
                "method": "fix_pe" | "o2_equilibrium" | "none",
                "target_pe": float,  # Required for fix_pe method
                "o2_si": float,      # Required for o2_equilibrium (log pO2, e.g., -0.68 for atmospheric)
            }
        block_num: Block number for PHREEQC
        allow_empty: If True, return empty string for no valid phases
        precipitation_only: If True (default), minerals can only precipitate

    Returns:
        PHREEQC EQUILIBRIUM_PHASES block string with pe constraint if specified

    Example:
        >>> pe_constraint = {"method": "fix_pe", "target_pe": -4.0}
        >>> phases = [{"name": "Vivianite", "target_si": 0.0}]
        >>> result = build_equilibrium_phases_with_pe_constraint(phases, pe_constraint)
        # Returns block with Fix_pe at SI=4.0 (= -target_pe) to fix pe at -4.0
    """
    # Default initial_moles based on mode
    default_initial_moles = 0.0 if precipitation_only else 10.0

    lines = [f"EQUILIBRIUM_PHASES {block_num}"]
    valid_phases = False

    # Add pe constraint phase first (if specified)
    if pe_constraint and pe_constraint.get("method"):
        method = pe_constraint.get("method")

        if method == "fix_pe":
            # Fix_pe pseudo-phase: target value is pe directly (not SI!)
            # Based on USGS PHREEQC Ca-As-Mahoney.out example:
            #   Fix_e- 10.0335... O2(g) 100  -> results in pe = -10.034
            # The input value is -pe (i.e., log a(e-)), not SI
            # So for target pe=-4, use value=4.0; for target pe=4, use value=-4.0
            target_pe = pe_constraint.get("target_pe", 0.0)
            fix_value = -target_pe  # Input value = -pe = log(a_e-)

            # WARNING: For reducing conditions (pe < 0), O2(g) as reactant may cause
            # pe drift because O2 is an oxidant and cannot maintain reducing conditions.
            # PHREEQC would need pO2 → 0 to achieve low pe, which is numerically difficult.
            # However, we still use O2(g) because H2(g) is NOT in minteq.v4.dat database,
            # and this approach works reasonably well with force_equality.
            if target_pe < 0:
                logger.debug(
                    f"Fix_pe at pe={target_pe:.1f} using O2(g) with force_equality. "
                    "This maintains reducing conditions despite O2(g) being an oxidant."
                )

            # Use O2(g) as reactant with force_equality to improve pe control
            # Large initial moles (100) allows both directions of adjustment.
            lines.append(f"    Fix_pe          {fix_value:.4f} O2(g) 100")
            lines.append(f"        -force_equality true")
            valid_phases = True
            logger.info(f"Added Fix_pe constraint: value={fix_value:.4f} (target pe={target_pe:.1f}) using O2(g)")

        elif method == "o2_equilibrium":
            # O2(g) equilibrium for aerobic conditions
            # SI = log(pO2), e.g., -0.68 for atmospheric (pO2 = 0.21 atm)
            o2_si = pe_constraint.get("o2_si", -0.68)  # Default: atmospheric
            # Use moderate initial_moles to avoid overwhelming the solver
            lines.append(f"    O2(g)           {o2_si:<8.2f} 1.0")
            valid_phases = True
            logger.info(f"Added O2(g) equilibrium constraint: SI={o2_si:.2f}")

    # Add mineral phases
    for phase_info in phases:
        name = phase_info.get("name")
        if name:
            target_si = phase_info.get("target_si", 0.0)
            initial_moles = phase_info.get("initial_moles", default_initial_moles)
            lines.append(f"    {name:<15} {target_si:<8} {initial_moles}")
            valid_phases = True

    if not valid_phases:
        if allow_empty:
            return ""
        raise InputValidationError(
            "No valid phases provided for EQUILIBRIUM_PHASES block. " "Each phase must have a 'name' field."
        )
    return "\n".join(lines) + "\n"


def build_mix_block(mix_num: int, solution_map: Dict[int, float]) -> str:
    """
    Builds a MIX block.

    Args:
        mix_num: MIX block number
        solution_map: {solution_number: fraction/volume}

    Returns:
        PHREEQC MIX block string

    Raises:
        InputValidationError: If solution_map is empty
    """
    if not solution_map:
        raise InputValidationError(
            "Empty solution_map provided for MIX block. " "At least one solution with a fraction/volume is required."
        )
    lines = [f"MIX {mix_num}"]
    for sol_num, factor in solution_map.items():
        lines.append(f"    {sol_num:<5} {factor}")
    return "\n".join(lines) + "\n"


def build_gas_phase_block(gas_def: Dict[str, Any], block_num: int = 1) -> str:
    """
    Builds a GAS_PHASE block.

    Args:
        gas_def: Gas phase definition with 'type', 'initial_components', and other parameters
        block_num: Block number for PHREEQC

    Returns:
        PHREEQC GAS_PHASE block string

    Raises:
        GasPhaseError: If gas type is unknown or no components are defined
    """
    lines = [f"GAS_PHASE {block_num}"]
    gas_type = gas_def.get("type", "fixed_pressure")

    if gas_type == "fixed_pressure":
        lines.append(f"    -fixed_pressure")
        lines.append(f"    -pressure    {gas_def.get('fixed_pressure_atm', 1.0)}")
        lines.append(f"    -volume      {gas_def.get('initial_volume_liters', 1.0)}")
        lines.append(f"    -temperature {gas_def.get('temperature_celsius', 25.0)}")
        for component, pp in gas_def.get("initial_components", {}).items():
            lines.append(f"    {component:<15} {pp}")
    elif gas_type == "fixed_volume":
        lines.append(f"    -fixed_volume")
        lines.append(f"    -volume      {gas_def.get('initial_volume_liters', 1.0)}")
        lines.append(f"    -pressure    {gas_def.get('fixed_pressure_atm', 1.0)}")  # Still needs initial P
        lines.append(f"    -temperature {gas_def.get('temperature_celsius', 25.0)}")
        for component, moles in gas_def.get("initial_components", {}).items():
            lines.append(f"    {component:<15} {moles}")  # Moles input for fixed_volume
    else:
        raise GasPhaseError(
            f"Unknown gas phase type: '{gas_type}'. Valid types are 'fixed_pressure' or 'fixed_volume'.",
            gas_components=gas_def.get("initial_components"),
            issue=f"Invalid type: {gas_type}",
        )

    # FAIL LOUDLY: No gas components is an error
    # Header (1) + settings (4) = 5 lines before any components
    if len(lines) <= 5:  # Only header and settings, no components
        raise GasPhaseError(
            "No gas components defined in GAS_PHASE block. "
            "At least one component is required in 'initial_components'.",
            gas_components=gas_def.get("initial_components"),
            issue="No components defined",
        )
    return "\n".join(lines) + "\n"


def build_surface_block(surface_def: Dict[str, Any], block_num: int = 1) -> str:
    """
    Builds a PHREEQC SURFACE block.

    Handles both raw string input and structured site definitions.
    SURFACE blocks can be complex with many options, so this implementation
    focuses on the most common use cases.

    Formats for PHREEQC SURFACE blocks:

    SURFACE 1
        Hfo_w  0.2  600  1.0   # weak site, site density 0.2 mol/L, 600 m2/g specific area, 1.0 g total mass
        Hfo_s  0.05 600  1.0   # strong site
        -equilibrate with solution 1
        -no_edl                  # optional flags

    Args:
        surface_def: Surface definition with sites, densities, etc.
        block_num: Block number for PHREEQC

    Returns:
        PHREEQC SURFACE block string

    Raises:
        SurfaceDefinitionError: If surface definition is invalid or missing required fields
    """
    # Check for different input formats and process accordingly

    # Case 1: Raw SURFACE block string provided (highest precedence)
    if surface_def.get("surface_block_string"):
        raw_block = surface_def["surface_block_string"].strip()
        # Make sure the block starts with "SURFACE X" where X is the block number
        if not raw_block.upper().startswith(f"SURFACE {block_num}"):
            # Fix the block number to match our requested block_num
            raw_block = re.sub(r"^SURFACE\s+\d+", f"SURFACE {block_num}", raw_block, flags=re.IGNORECASE)
        return f"{raw_block}\n"

    # Case 2: Structured site information
    elif surface_def.get("sites_info") or "mass" in surface_def or "sites" in surface_def:
        lines = [f"SURFACE {block_num}"]

        # Different schemas might use different keys, so handle both formats
        sites_info = surface_def.get("sites_info", [])

        # Handle simplified format with just "sites" list
        if "sites" in surface_def and not sites_info:
            sites = surface_def.get("sites", [])
            # Convert simplified sites format to sites_info format
            site_density = surface_def.get("site_density", 0.005)  # Default site density
            specific_area = surface_def.get("specific_area", 600)  # Default specific area
            mass = surface_def.get("mass", 1.0)  # Default mass

            sites_info = []
            for site in sites:
                if isinstance(site, dict) and "name" in site:
                    site_name = site["name"]
                    # Use override values if provided, otherwise use defaults
                    site_info = {
                        "name": site_name,
                        "moles": site.get("moles", site_density),
                        "specific_area_m2_g": site.get("specific_area_m2_g", specific_area),
                        "mass_g": site.get("mass_g", mass),
                    }
                    sites_info.append(site_info)
                elif isinstance(site, str):
                    # Just a site name string
                    site_info = {
                        "name": site,
                        "moles": site_density,
                        "specific_area_m2_g": specific_area,
                        "mass_g": mass,
                    }
                    sites_info.append(site_info)

        # Add equilibration directive
        equilibrate_num = surface_def.get("equilibrate_with_solution_number", 1)
        lines.append(f"    -equilibrate {equilibrate_num}")

        # Track valid sites for error reporting
        valid_site_count = 0
        skipped_sites = []

        # Add site definitions
        for site_info in sites_info:
            # Handle different possible formats for site info
            if isinstance(site_info, dict):
                name = site_info.get("name")  # e.g., Hfo_w

                # Different schemas might use different keys
                moles = site_info.get("moles", site_info.get("site_density"))
                area = site_info.get("specific_area_m2_g", site_info.get("specific_area", site_info.get("area")))
                mass = site_info.get("mass_g", site_info.get("mass"))

                # Handle various formats with missing components
                if name and moles is not None and area is not None and mass is not None:
                    # Full format: name moles specific_area mass
                    lines.append(f"    {name}  {moles}  {area}  {mass}")
                    valid_site_count += 1
                elif name and moles is not None:
                    # Simplified format: just name and moles
                    lines.append(f"    {name}  {moles}")
                    valid_site_count += 1
                elif name:
                    # Minimal format: just the name with default values
                    lines.append(f"    {name}  0.01")  # Default site density
                    valid_site_count += 1
                else:
                    skipped_sites.append(str(site_info))
            elif isinstance(site_info, str):
                # Just a site name string
                lines.append(f"    {site_info}  0.01")  # Default site density
                valid_site_count += 1

        # Add any additional options
        if surface_def.get("no_edl", False):
            lines.append("    -no_edl")
        if surface_def.get("donnan", False):
            lines.append("    -donnan")
        if surface_def.get("only_counter_ions", False):
            lines.append("    -only_counter_ions")

        # FAIL LOUDLY: No valid site definitions is an error
        if valid_site_count == 0:
            raise SurfaceDefinitionError(
                "No valid site definitions found for SURFACE block. " "Each site must have at least a 'name' field.",
                missing_fields=["name"] if skipped_sites else None,
                invalid_fields={"skipped_sites": ", ".join(skipped_sites)} if skipped_sites else None,
            )

        # Add SURFACE_MASTER_SPECIES and SURFACE_SPECIES blocks if provided
        full_block = ""
        if surface_def.get("sites_block_string"):
            full_block += f"{surface_def['sites_block_string'].strip()}\n\n"
        full_block += "\n".join(lines) + "\n"
        return full_block

    # Case 3: No valid surface definition
    else:
        raise SurfaceDefinitionError(
            "Invalid surface definition provided. "
            "Provide either 'surface_block_string' for raw input, or "
            "'sites'/'sites_info' for structured input.",
            missing_fields=["surface_block_string", "sites", "sites_info"],
        )


def build_kinetics_block(kinetics_def: Dict[str, Any], time_def: Dict[str, Any], block_num: int = 1) -> Tuple[str, str]:
    """
    Builds KINETICS and RATES blocks.

    Handles both raw string input and structured input with auto-generation.
    PHREEQC KINETICS and RATES blocks can be complex, so this function
    handles common cases and falls back to raw strings for advanced use.

    Args:
        kinetics_def: Dictionary with kinetics definition, either as raw strings or structured
        time_def: Dictionary with time step definition, either as raw values or parameters
        block_num: Block number to use

    Returns:
        Tuple of (rates_string, kinetics_string)

    Raises:
        KineticsDefinitionError: If kinetics definition is invalid or incomplete
    """
    # Step 1: Generate RATES block
    rates_str = ""

    # Check if the user provided a raw RATES block
    if kinetics_def.get("rates_block_string"):
        rates_str = kinetics_def.get("rates_block_string", "")

        # Ensure the block starts with "RATES" as required by PHREEQC
        if not rates_str.strip().upper().startswith("RATES"):
            rates_str = f"RATES\n{rates_str}"

        logger.debug(f"Using raw RATES block string: {rates_str[:100]}...")

    # Check for structured rate definitions to generate the RATES block
    elif kinetics_def.get("rates") and isinstance(kinetics_def["rates"], list):
        rates_lines = ["RATES"]
        valid_rates = 0

        for rate_def in kinetics_def["rates"]:
            if isinstance(rate_def, dict):
                name = rate_def.get("name")
                rate_law = rate_def.get("rate_law", "")

                if name and rate_law:
                    # Format the rate law with proper indentation
                    rates_lines.append(f"\n{name}")

                    # Ensure the START and END are in the rate law, add if missing
                    if "START" not in rate_law:
                        rates_lines.append("-start")

                    # Add the rate law code with indentation
                    if isinstance(rate_law, str):
                        for line in rate_law.splitlines():
                            rates_lines.append(f"    {line}")

                    if "END" not in rate_law and "-end" not in rate_law:
                        rates_lines.append("-end")

                    valid_rates += 1

        if valid_rates > 0:
            rates_str = "\n".join(rates_lines)
            logger.debug(f"Generated RATES block from structured data: {rates_str[:100]}...")

    logger.debug(f"Complete RATES block: {rates_str}")

    # Step 2: Generate KINETICS block
    kinetics_lines = [f"KINETICS {block_num}"]
    reaction_count = 0

    # Check if the user provided a raw KINETICS block
    if kinetics_def.get("kinetics_block_string"):
        kinetics_inner_str = kinetics_def.get("kinetics_block_string", "")
        if kinetics_inner_str:
            # Add indentation if missing from user input
            kinetics_lines.extend([f"    {line.strip()}" for line in kinetics_inner_str.strip().splitlines()])
            reaction_count = 1  # Assume at least one reaction in raw input
            logger.debug(f"Using raw KINETICS block string")

    # Check for structured reaction definitions to generate the KINETICS block
    elif kinetics_def.get("reactions") and isinstance(kinetics_def["reactions"], list):
        for reaction in kinetics_def["reactions"]:
            if isinstance(reaction, dict):
                name = reaction.get("name")
                formula = reaction.get("formula")
                parameters = reaction.get("parameters", {})
                custom_line = reaction.get("custom_kinetics_line")

                if name:
                    # If there's a custom_kinetics_line, use it directly
                    if custom_line:
                        kinetics_lines.append(f"    {custom_line}")
                    else:
                        # Generate a standard kinetics entry
                        kinetics_lines.append(f"    {name}")
                        if formula:
                            # Handle either string or dict formats for formula
                            if isinstance(formula, str):
                                kinetics_lines.append(f"        -formula {formula}")
                            elif isinstance(formula, dict):
                                # Convert dict to formatted string
                                formula_parts = []
                                for elem, coef in formula.items():
                                    formula_parts.append(f"{elem} {coef}")
                                formula_str = " ".join(formula_parts)
                                kinetics_lines.append(f"        -formula {formula_str}")

                        # Add parameters
                        if parameters:
                            for param_name, param_value in parameters.items():
                                kinetics_lines.append(f"        -{param_name} {param_value}")

                    reaction_count += 1

        logger.debug(f"Generated KINETICS block from structured data")

    # FAIL LOUDLY: No kinetics reactions defined is an error
    if reaction_count == 0:
        missing_fields = []
        if not kinetics_def.get("kinetics_block_string"):
            missing_fields.append("kinetics_block_string")
        if not kinetics_def.get("reactions"):
            missing_fields.append("reactions")

        raise KineticsDefinitionError(
            "No kinetics reactions defined. Provide either 'kinetics_block_string' "
            "for raw PHREEQC input, or 'reactions' list with structured definitions.",
            missing_fields=missing_fields,
        )

    # Step 3: Add time steps
    # Determine time steps format based on available information

    # Try to get time step information from time_def
    time_info_added = False

    # Case 1: Raw time values list
    if "time_values" in time_def and time_def.get("time_values"):
        time_values = time_def.get("time_values", [])
        time_units = time_def.get("units", "seconds")

        if time_values and isinstance(time_values, list) and len(time_values) > 0:
            time_values_str = " ".join(map(str, time_values))
            kinetics_lines.append(f"    -steps {time_values_str} {time_units}")
            logger.debug(f"Added time steps from raw values: {time_values_str} {time_units}")
            time_info_added = True

    # Case 2: Count and duration parameters - both needed
    if not time_info_added and "count" in time_def and "duration" in time_def:
        count = time_def.get("count")
        duration = time_def.get("duration")

        if count and duration and count > 0 and duration > 0:
            units = time_def.get("duration_units", "seconds")
            # Generate equal time steps
            step_size = duration / count
            kinetics_lines.append(f"    -steps {step_size} {units} in {count} steps")
            logger.debug(f"Added time steps from duration/count: {step_size} {units} in {count} steps")
            time_info_added = True

    # Case 3: Fallback - use default time step if no valid time information
    if not time_info_added:
        logger.warning("Using default time step of 3600 seconds")
        kinetics_lines.append("    -steps 3600 seconds")
        time_info_added = True

    # Finalize blocks with newlines
    rates_str_final = rates_str.strip() + "\n\n" if rates_str else ""
    kinetics_str_final = "\n".join(kinetics_lines) + "\n"

    logger.debug(f"Complete KINETICS block: {kinetics_str_final}")

    return rates_str_final, kinetics_str_final


def build_selected_output_block(
    block_num: int = 1,
    elements: bool = True,
    totals: bool = True,
    molalities: bool = True,
    activities: bool = False,
    phases: bool = True,
    saturation_indices: bool = True,
    gases: bool = True,
    surface: bool = True,
    exchange: bool = True,
    kinetics: bool = True,
    user_punch: bool = False,
    composite_parameters: bool = False,
) -> str:
    """
    Builds a SELECTED_OUTPUT block with optional composite parameter calculations.

    Args:
        composite_parameters: If True, adds PHREEQC-native calculations for composite
                             parameters like total hardness, carbonate alkalinity, etc.
    """
    lines = [
        f"SELECTED_OUTPUT {block_num}",
        "    -reset false",  # Append to default selected output
    ]

    # Use compatible options
    lines.append("    -temp true")
    lines.append("    -pH true")
    lines.append("    -pe true")
    lines.append("    -alk true")
    lines.append("    -mu true")  # ionic strength
    lines.append("    -water true")
    # Note: Specific conductance must be extracted from solution object, not SELECTED_OUTPUT

    if totals:
        lines.append("    -tot true")  # Element totals
    if molalities:
        lines.append("    -mol true")  # Species molalities
    if activities:
        lines.append("    -act true")  # Species activities
    if phases:
        lines.append("    -eq true")  # Equilibrium phases
    if saturation_indices:
        lines.append("    -si true")  # Saturation indices
    if gases:
        lines.append("    -gas true")  # Gas phase components

    if user_punch or composite_parameters:
        lines.append("    -user_punch true")

    # Add composite parameter calculations using PHREEQC's native calculation engine
    if composite_parameters:
        lines.append("    # Composite parameters calculated by PHREEQC")

        # Total hardness as CaCO3 (mg/L): (Ca + Mg) * 50000 (equivalent weight of CaCO3)
        lines.append('    -headings "Total_Hardness_CaCO3" "Carbonate_Alkalinity_CaCO3" "TDS_Species"')
        lines.append("    -start")
        lines.append('        10 total_hardness = (TOT("Ca") + TOT("Mg")) * 50000')
        lines.append('        20 carb_alk = (MOL("HCO3-") + 2*MOL("CO3-2")) * 50000')
        lines.append("        30 tds_calc = 0")
        lines.append("        40 FOR i = 1 TO MOL_NUMBER")
        lines.append("        50   species_name$ = MOL_NAME$(i)")
        lines.append('        60   IF species_name$ <> "H2O" AND species_name$ <> "H+" AND species_name$ <> "OH-" THEN')
        lines.append("        70     molal = MOL(species_name$)")
        lines.append("        80     mw = EQ_WEIGHT(species_name$)")
        lines.append("        90     IF mw > 0 THEN tds_calc = tds_calc + molal * mw * 1000")
        lines.append("        100  ENDIF")
        lines.append("        110 NEXT i")
        lines.append("        120 PUNCH total_hardness, carb_alk, tds_calc")
        lines.append("    -end")

    # Avoid problematic options
    # No -elements, -surface, -exchange, -pressure, -density

    return "\n".join(lines) + "\n"


def build_phase_linked_surface_block(
    surface_name: str,
    phase_name: str,
    sites_per_mole: float = 0.005,
    specific_area_per_mole: float = 53300.0,
    weak_to_strong_ratio: float = 40.0,
    equilibrate_solution: int = 1,
    block_num: int = 1,
    no_edl: bool = False,
    database: str = "minteq.v4.dat",
) -> str:
    """
    Builds a phase-linked SURFACE block for HFO (hydrous ferric oxide) adsorption.

    Phase-linked surfaces tie the number of adsorption sites to the amount of
    a precipitated mineral phase (e.g., Fe(OH)3 or Ferrihydrite). This models
    adsorption onto freshly precipitated iron hydroxide surfaces.

    PHREEQC syntax for phase-linked surfaces:
        <binding-site formula> <phase name> equilibrium_phase <sites_per_mole> [specific_area_per_mole]

    Example:
        Hfo_sOH  Ferrihydrite  equilibrium_phase  0.005  53300
        Hfo_wOH  Ferrihydrite  equilibrium_phase  0.2    53300

    Args:
        surface_name: Base surface name (e.g., "Hfo" for hydrous ferric oxide)
        phase_name: Equilibrium phase to link to (e.g., "Ferrihydrite", "Fe(OH)3(a)")
        sites_per_mole: Strong site density (mol sites / mol phase), default 0.005
        specific_area_per_mole: Specific surface area (m²/mol phase), default 53300
        weak_to_strong_ratio: Ratio of weak to strong sites, default 40 (Dzombak & Morel)
        equilibrate_solution: Solution number to equilibrate with
        block_num: SURFACE block number
        no_edl: If True, disable electric double layer calculations
        database: Database name to determine correct binding-site formulas

    Returns:
        PHREEQC SURFACE block string with phase-linked sites

    Raises:
        SurfaceDefinitionError: If required parameters are missing or invalid
    """
    if not surface_name:
        raise SurfaceDefinitionError(
            "Surface name required for phase-linked SURFACE block", missing_fields=["surface_name"]
        )
    if not phase_name:
        raise SurfaceDefinitionError(
            "Phase name required for phase-linked SURFACE block", missing_fields=["phase_name"]
        )

    # Determine binding-site formula names based on database
    # minteq.v4.dat and most databases use Hfo_sOH (strong) and Hfo_wOH (weak)
    strong_site = f"{surface_name}_sOH"
    weak_site = f"{surface_name}_wOH"

    # Calculate weak site density from ratio
    weak_sites_per_mole = sites_per_mole * weak_to_strong_ratio

    lines = [f"SURFACE {block_num}"]

    # Add equilibration directive first
    lines.append(f"    -equilibrate {equilibrate_solution}")

    # Strong sites - phase linked
    # Format: <site> <phase> equilibrium_phase <sites_per_mole> <specific_area>
    lines.append(f"    {strong_site}  {phase_name}  equilibrium_phase  {sites_per_mole}  {specific_area_per_mole}")

    # Weak sites - phase linked (same phase, higher site density)
    lines.append(f"    {weak_site}  {phase_name}  equilibrium_phase  {weak_sites_per_mole}  {specific_area_per_mole}")

    # Optional: disable EDL for simpler/faster calculations
    if no_edl:
        lines.append("    -no_edl")

    return "\n".join(lines) + "\n"


def build_user_punch_for_partitioning(
    phases: List[str],
    surface_name: str = "Hfo",
    elements: List[str] = None,
    include_solution_totals: bool = True,
    block_num: int = 1,
) -> str:
    """
    Builds a USER_PUNCH block to extract Fe/P partitioning data.

    This creates PHREEQC BASIC code to output:
    - Moles of each equilibrium phase (precipitation)
    - Moles of elements adsorbed on surfaces
    - Total dissolved element concentrations

    The output headers are designed to be parsed by _parse_selected_output().

    PHREEQC BASIC functions used:
    - EQUI("PhaseName"): Returns moles of equilibrium phase
    - SURF("element", "surface"): Returns moles of element on surface
    - TOT("element"): Returns total dissolved concentration (mol/kgw)

    Args:
        phases: List of equilibrium phase names to track (e.g., ["Strengite", "Vivianite"])
        surface_name: Base surface name without underscore (e.g., "Hfo")
        elements: Elements to track on surface and in solution (default: ["P", "Fe"])
        include_solution_totals: If True, also output dissolved totals
        block_num: USER_PUNCH block number

    Returns:
        PHREEQC USER_PUNCH block string

    Example output headers:
        equi_Strengite, equi_Vivianite, surf_P_Hfo, surf_Fe_Hfo, tot_P, tot_Fe
    """
    if elements is None:
        elements = ["P", "Fe"]

    lines = [f"USER_PUNCH {block_num}"]

    # Build headers list
    headers = []
    punch_expressions = []
    line_num = 10

    # Phase moles (precipitation amounts)
    for phase in phases:
        header = f"equi_{phase}"
        headers.append(header)
        punch_expressions.append(f'{line_num} equi_{phase.replace("(", "_").replace(")", "_")} = EQUI("{phase}")')
        line_num += 10

    # Surface-adsorbed elements
    for elem in elements:
        header = f"surf_{elem}_{surface_name}"
        headers.append(header)
        # SURF("element", "surface") - surface name is base name without underscore
        punch_expressions.append(f'{line_num} surf_{elem} = SURF("{elem}", "{surface_name}")')
        line_num += 10

    # Dissolved totals
    if include_solution_totals:
        for elem in elements:
            header = f"tot_{elem}"
            headers.append(header)
            punch_expressions.append(f'{line_num} tot_{elem} = TOT("{elem}")')
            line_num += 10

    # Build the USER_PUNCH block
    headers_str = " ".join([f'"{h}"' for h in headers])
    lines.append(f"    -headings {headers_str}")
    lines.append("    -start")

    # Add calculation expressions
    for expr in punch_expressions:
        lines.append(f"    {expr}")

    # Build PUNCH statement with all variables
    var_names = []
    for phase in phases:
        var_names.append(f'equi_{phase.replace("(", "_").replace(")", "_")}')
    for elem in elements:
        var_names.append(f"surf_{elem}")
    if include_solution_totals:
        for elem in elements:
            var_names.append(f"tot_{elem}")

    lines.append(f"    {line_num} PUNCH {', '.join(var_names)}")
    lines.append("    -end")

    return "\n".join(lines) + "\n"
