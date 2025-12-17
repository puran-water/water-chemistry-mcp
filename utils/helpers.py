"""
Helper functions for water chemistry calculations.
"""

import logging
import re
from typing import Dict, Any, List, Optional, Union, Tuple

logger = logging.getLogger(__name__)

def build_solution_block(solution_data: Dict[str, Any], solution_num: int = 1, solution_number: int = None) -> str:
    """Builds a PHREEQC SOLUTION block string."""
    # Handle both solution_num and solution_number parameters
    if solution_number is not None:
        solution_num = solution_number
    
    lines = [f"SOLUTION {solution_num}"]
    
    # Element mapping for common wastewater parameters
    ELEMENT_MAPPING = {
        'P': 'P(5)',  # Phosphorus as phosphate
        'N': 'N(5)',  # Nitrogen as nitrate (use N(-3) for ammonia)
        'Fe': 'Fe(2)',  # Iron defaults to ferrous (use Fe(3) for ferric)
        'S': 'S(6)',  # Sulfur as sulfate (use S(-2) for sulfide)
        'As': 'As(5)',  # Arsenic as arsenate
        'Mn': 'Mn(2)',  # Manganese as Mn2+
        'Cr': 'Cr(6)',  # Chromium as chromate
    }
    # Use defaults from schema if not provided
    lines.append(f"    temp      {solution_data.get('temperature_celsius', 25.0)}")
    lines.append(f"    pressure  {solution_data.get('pressure_atm', 1.0)}")
    lines.append(f"    units     {solution_data.get('units', 'mg/L')}")

    if 'density' in solution_data and solution_data['density'] is not None:
        lines.append(f"    density   {solution_data['density']}")

    # Handle pH/pe/charge balance priority
    if 'charge_balance' in solution_data and solution_data['charge_balance']:
        lines.append(f"    pH        {solution_data.get('ph', solution_data.get('pH', 7.0))}") # pH still needed
        lines.append(f"    pe        {solution_data.get('pe', 4.0)}") # Still need pe
        lines.append(f"    {solution_data['charge_balance']} charge") # Charge balance on specified element
    elif 'redox' in solution_data and solution_data['redox']:
         lines.append(f"    pH        {solution_data.get('ph', solution_data.get('pH', 7.0))}") # Need pH if using redox couple
         lines.append(f"    redox     {solution_data['redox']}")
    elif ('ph' in solution_data and solution_data['ph'] is not None) or \
           ('pH' in solution_data and solution_data['pH'] is not None):
        # Handle both lowercase and uppercase pH
        ph_value = solution_data.get('ph') or solution_data.get('pH')
        lines.append(f"    pH        {ph_value}")
        pe_value = solution_data.get('pe', 4.0)
        # Allow negative pe values for anaerobic conditions
        lines.append(f"    pe        {pe_value}")
    else:
        # If no pH and no charge balance specified, default values
        logger.warning(f"No pH or charge_balance specified for SOLUTION {solution_num}. Defaulting pH=7.0, pe=4.0.")
        lines.append(f"    pH        7.0")
        lines.append(f"    pe        4.0")

    analysis = solution_data.get('analysis', {})
    for element, value in analysis.items():
        # Apply element mapping if needed
        element_to_use = ELEMENT_MAPPING.get(element, element)
        
        if isinstance(value, (int, float)):
            lines.append(f"    {element_to_use:<10}{value}")
        elif isinstance(value, str): # Handle 'Alkalinity as CaCO3 120' or 'S(6) 96'
            parts = value.split()
            if len(parts) >= 2:
                 # Check if second part is a number (e.g., "Ca 40")
                 try:
                      float(parts[1])
                      lines.append(f"    {parts[0]:<10}{' '.join(parts[1:])}")
                 except ValueError:
                     # Assume format like "Alkalinity as CaCO3 120"
                     lines.append(f"    {element:<10}{value}")
            else:
                 lines.append(f"    {element:<10}{value}") # Pass raw string
        elif isinstance(value, dict):
             val_num = value.get('value')
             if val_num is not None:
                 line = f"    {element:<10}{val_num}"
                 if 'as' in value:
                     line += f" as {value['as']}"
                 if value.get('charge', False):
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
        formula = reactant.get('formula')
        amount = reactant.get('amount')
        if not units:
            units = reactant.get('units', 'mmol')  # Use first reactant's units or default to mmol
        
        if formula and amount is not None:
            # Add formula with explicit 1.0 coefficient (critical for PHREEQC parsing)
            formula_lines.append(f"    {formula} 1.0")
            total_amount += float(amount)
    
    # If no valid reactants, return empty string
    if not formula_lines:
        return ""
    
    # Add formula lines to reaction block
    lines.extend(formula_lines)
    
    # Handle advanced step specifications if present
    if reactants and 'steps' in reactants[0] and isinstance(reactants[0]['steps'], dict):
        steps_data = reactants[0]['steps']
        amounts = steps_data.get('amounts', [])
        step_units = steps_data.get('units', units)
        count = steps_data.get('count')
        
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

def build_equilibrium_phases_block(phases: List[Dict[str, Any]], block_num: int = 1) -> str:
    """Builds an EQUILIBRIUM_PHASES block."""
    lines = [f"EQUILIBRIUM_PHASES {block_num}"]
    valid_phases = False
    for phase_info in phases:
        name = phase_info.get('name')
        if name:
            target_si = phase_info.get('target_si', 0.0)
            initial_moles = phase_info.get('initial_moles', 10.0) # Usually excess unless specified
            lines.append(f"    {name:<15} {target_si:<8} {initial_moles}")
            valid_phases = True
    if not valid_phases:
        return ""
    return "\n".join(lines) + "\n"

def build_mix_block(mix_num: int, solution_map: Dict[int, float]) -> str:
    """Builds a MIX block. solution_map: {solution_number: fraction/volume}."""
    if not solution_map:
        return ""
    lines = [f"MIX {mix_num}"]
    for sol_num, factor in solution_map.items():
        lines.append(f"    {sol_num:<5} {factor}")
    return "\n".join(lines) + "\n"

def build_gas_phase_block(gas_def: Dict[str, Any], block_num: int = 1) -> str:
    """Builds a GAS_PHASE block."""
    lines = [f"GAS_PHASE {block_num}"]
    gas_type = gas_def.get('type', 'fixed_pressure')

    if gas_type == 'fixed_pressure':
        lines.append(f"    -fixed_pressure")
        lines.append(f"    -pressure    {gas_def.get('fixed_pressure_atm', 1.0)}")
        lines.append(f"    -volume      {gas_def.get('initial_volume_liters', 1.0)}")
        lines.append(f"    -temperature {gas_def.get('temperature_celsius', 25.0)}")
        for component, pp in gas_def.get('initial_components', {}).items():
            lines.append(f"    {component:<15} {pp}")
    elif gas_type == 'fixed_volume':
        lines.append(f"    -fixed_volume")
        lines.append(f"    -volume      {gas_def.get('initial_volume_liters', 1.0)}")
        lines.append(f"    -pressure    {gas_def.get('fixed_pressure_atm', 1.0)}") # Still needs initial P
        lines.append(f"    -temperature {gas_def.get('temperature_celsius', 25.0)}")
        for component, moles in gas_def.get('initial_components', {}).items():
            lines.append(f"    {component:<15} {moles}") # Moles input for fixed_volume
    else:
        logger.error(f"Unknown gas phase type: {gas_type}")
        return ""

    if len(lines) <= 1: # Only header means no components added
        return ""
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
    """
    # Check for different input formats and process accordingly
    
    # Case 1: Raw SURFACE block string provided (highest precedence)
    if surface_def.get('surface_block_string'):
        raw_block = surface_def['surface_block_string'].strip()
        # Make sure the block starts with "SURFACE X" where X is the block number
        if not raw_block.upper().startswith(f"SURFACE {block_num}"):
            # Fix the block number to match our requested block_num
            raw_block = re.sub(r"^SURFACE\s+\d+", f"SURFACE {block_num}", raw_block, flags=re.IGNORECASE)
        return f"{raw_block}\n"
    
    # Case 2: Structured site information
    elif surface_def.get('sites_info') or 'mass' in surface_def or 'sites' in surface_def:
        lines = [f"SURFACE {block_num}"]
        
        # Different schemas might use different keys, so handle both formats
        sites_info = surface_def.get('sites_info', [])
        
        # Handle simplified format with just "sites" list
        if 'sites' in surface_def and not sites_info:
            sites = surface_def.get('sites', [])
            # Convert simplified sites format to sites_info format
            site_density = surface_def.get('site_density', 0.005)  # Default site density
            specific_area = surface_def.get('specific_area', 600)   # Default specific area
            mass = surface_def.get('mass', 1.0)                    # Default mass
            
            sites_info = []
            for site in sites:
                if isinstance(site, dict) and 'name' in site:
                    site_name = site['name']
                    # Use override values if provided, otherwise use defaults
                    site_info = {
                        'name': site_name,
                        'moles': site.get('moles', site_density),
                        'specific_area_m2_g': site.get('specific_area_m2_g', specific_area),
                        'mass_g': site.get('mass_g', mass)
                    }
                    sites_info.append(site_info)
                elif isinstance(site, str):
                    # Just a site name string
                    site_info = {
                        'name': site,
                        'moles': site_density,
                        'specific_area_m2_g': specific_area,
                        'mass_g': mass
                    }
                    sites_info.append(site_info)
        
        # Add equilibration directive
        equilibrate_num = surface_def.get('equilibrate_with_solution_number', 1)
        lines.append(f"    -equilibrate {equilibrate_num}")
        
        # Add site definitions
        for site_info in sites_info:
            # Handle different possible formats for site info
            if isinstance(site_info, dict):
                name = site_info.get('name')  # e.g., Hfo_w
                
                # Different schemas might use different keys
                moles = site_info.get('moles', site_info.get('site_density'))
                area = site_info.get('specific_area_m2_g', 
                                    site_info.get('specific_area', 
                                                site_info.get('area')))
                mass = site_info.get('mass_g', site_info.get('mass'))
                
                # Handle various formats with missing components
                if name and moles is not None and area is not None and mass is not None:
                    # Full format: name moles specific_area mass
                    lines.append(f"    {name}  {moles}  {area}  {mass}")
                elif name and moles is not None:
                    # Simplified format: just name and moles
                    lines.append(f"    {name}  {moles}")
                elif name:
                    # Minimal format: just the name with default values
                    lines.append(f"    {name}  0.01")  # Default site density
                else:
                    logger.warning(f"Skipping site info missing required name: {site_info}")
            elif isinstance(site_info, str):
                # Just a site name string
                lines.append(f"    {site_info}  0.01")  # Default site density
        
        # Add any additional options
        if surface_def.get('no_edl', False):
            lines.append("    -no_edl")
        if surface_def.get('donnan', False):
            lines.append("    -donnan")
        if surface_def.get('only_counter_ions', False):
            lines.append("    -only_counter_ions")
        
        # Create the block if we have at least one site definition
        if len(lines) > 2:  # More than just SURFACE and -equilibrate lines
            # Add SURFACE_MASTER_SPECIES and SURFACE_SPECIES blocks if provided
            full_block = ""
            if surface_def.get('sites_block_string'):
                full_block += f"{surface_def['sites_block_string'].strip()}\n\n"
            full_block += "\n".join(lines) + "\n"
            return full_block
        else:
            logger.warning("No valid site definitions found for SURFACE block")
            return ""
    
    # Case 3: No valid surface definition
    else:
        logger.error("Invalid surface definition provided")
        return ""

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
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Step 1: Generate RATES block
        rates_str = ""
        
        # Check if the user provided a raw RATES block
        if kinetics_def.get('rates_block_string'):
            rates_str = kinetics_def.get('rates_block_string', '')
            
            # Ensure the block starts with "RATES" as required by PHREEQC
            if not rates_str.strip().upper().startswith("RATES"):
                rates_str = f"RATES\n{rates_str}"
                
            logger.debug(f"Using raw RATES block string: {rates_str[:100]}...")
        
        # Check for structured rate definitions to generate the RATES block
        elif kinetics_def.get('rates') and isinstance(kinetics_def['rates'], list):
            rates_lines = ["RATES"]
            
            for rate_def in kinetics_def['rates']:
                if isinstance(rate_def, dict):
                    name = rate_def.get('name')
                    rate_law = rate_def.get('rate_law', '')
                    
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
            
            if len(rates_lines) > 1:  # More than just the RATES header
                rates_str = "\n".join(rates_lines)
                logger.debug(f"Generated RATES block from structured data: {rates_str[:100]}...")
        
        logger.debug(f"Complete RATES block: {rates_str}")
        
        # Step 2: Generate KINETICS block
        kinetics_lines = [f"KINETICS {block_num}"]
        
        # Check if the user provided a raw KINETICS block
        if kinetics_def.get('kinetics_block_string'):
            kinetics_inner_str = kinetics_def.get('kinetics_block_string', '')
            if kinetics_inner_str:
                # Add indentation if missing from user input
                kinetics_lines.extend([f"    {line.strip()}" for line in kinetics_inner_str.strip().splitlines()])
                logger.debug(f"Using raw KINETICS block string")
        
        # Check for structured reaction definitions to generate the KINETICS block
        elif kinetics_def.get('reactions') and isinstance(kinetics_def['reactions'], list):
            for reaction in kinetics_def['reactions']:
                if isinstance(reaction, dict):
                    name = reaction.get('name')
                    formula = reaction.get('formula')
                    parameters = reaction.get('parameters', {})
                    custom_line = reaction.get('custom_kinetics_line')
                    
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
            
            logger.debug(f"Generated KINETICS block from structured data")
        
        # If no kinetics blocks were defined, we can't proceed
        if len(kinetics_lines) <= 1:  # Just the header
            logger.error("No kinetics reactions defined")
            return "", ""
        
        # Step 3: Add time steps
        # Determine time steps format based on available information
        
        # Try to get time step information from time_def
        time_info_added = False
        
        # Case 1: Raw time values list
        if 'time_values' in time_def and time_def.get('time_values'):
            time_values = time_def.get('time_values', [])
            time_units = time_def.get('units', 'seconds')
            
            if time_values and isinstance(time_values, list) and len(time_values) > 0:
                time_values_str = " ".join(map(str, time_values))
                kinetics_lines.append(f"    -steps {time_values_str} {time_units}")
                logger.debug(f"Added time steps from raw values: {time_values_str} {time_units}")
                time_info_added = True
        
        # Case 2: Count and duration parameters - both needed
        if not time_info_added and 'count' in time_def and 'duration' in time_def:
            count = time_def.get('count')
            duration = time_def.get('duration')
            
            if count and duration and count > 0 and duration > 0:
                units = time_def.get('duration_units', 'seconds')
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
    
    except Exception as e:
        logger.error(f"Error building kinetics block: {e}", exc_info=True)
        return "", ""

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
        "    -reset false", # Append to default selected output
    ]
    
    # Use compatible options
    lines.append("    -temp true")
    lines.append("    -pH true")
    lines.append("    -pe true")
    lines.append("    -alk true")
    lines.append("    -mu true")  # ionic strength
    lines.append("    -water true")
    # Note: Specific conductance must be extracted from solution object, not SELECTED_OUTPUT
    
    if totals: lines.append("    -tot true")  # Element totals
    if molalities: lines.append("    -mol true")  # Species molalities
    if activities: lines.append("    -act true")  # Species activities
    if phases: lines.append("    -eq true")  # Equilibrium phases
    if saturation_indices: lines.append("    -si true")  # Saturation indices
    if gases: lines.append("    -gas true")  # Gas phase components
    
    # Add composite parameter calculations using PHREEQC's native calculation engine
    if composite_parameters:
        lines.append("    # Composite parameters calculated by PHREEQC")
        
        # Total hardness as CaCO3 (mg/L): (Ca + Mg) * 50000 (equivalent weight of CaCO3)
        lines.append('    -user_punch true')
        lines.append('    -headings "Total_Hardness_CaCO3" "Carbonate_Alkalinity_CaCO3" "TDS_Species"')
        lines.append('    -start')
        lines.append('        10 total_hardness = (TOT("Ca") + TOT("Mg")) * 50000')
        lines.append('        20 carb_alk = (MOL("HCO3-") + 2*MOL("CO3-2")) * 50000')
        lines.append('        30 tds_calc = 0')
        lines.append('        40 FOR i = 1 TO MOL_NUMBER')
        lines.append('        50   species_name$ = MOL_NAME$(i)')
        lines.append('        60   IF species_name$ <> "H2O" AND species_name$ <> "H+" AND species_name$ <> "OH-" THEN')
        lines.append('        70     molal = MOL(species_name$)')
        lines.append('        80     mw = EQ_WEIGHT(species_name$)')
        lines.append('        90     IF mw > 0 THEN tds_calc = tds_calc + molal * mw * 1000')
        lines.append('        100  ENDIF')
        lines.append('        110 NEXT i')
        lines.append('        120 PUNCH total_hardness, carb_alk, tds_calc')
        lines.append('    -end')
    
    # Avoid problematic options
    # No -elements, -surface, -exchange, -pressure, -density

    return "\n".join(lines) + "\n"
