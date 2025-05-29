"""
Constants for water chemistry calculations.
"""
import os

# Default database paths for PhreeqPython
DEFAULT_DATABASE_NAMES = [
    'phreeqc.dat',
    'wateq4f.dat',
    'minteq.v4.dat',
    'pitzer.dat',
    'sit.dat',
    'llnl.dat'
]

# Preferred database paths for USGS PHREEQC installation
# Order indicates preference - minteq.dat is essential for water treatment (has Calcite for lime softening)
PREFERRED_DATABASE_NAMES = [
    'minteq.dat',          # MINTEQA2 database - essential for water treatment, contains Calcite, Brucite, comprehensive minerals
    'minteq.v4.dat',       # MINTEQA2 database version 4
    'wateq4f.dat',         # WATEQ4F database with comprehensive elements and minerals
    'phreeqc.dat',         # Standard PHREEQC database
    'Tipping_Hurley.dat',  # Database for humic substances
    'pitzer.dat',          # Pitzer model for high ionic strength solutions
    'sit.dat',             # Specific ion interaction theory database
    'iso.dat',             # For isotope calculations
    'PHREEQC_ThermoddemV1.10_15Dec2020.dat',  # Thermodynamic database
    'minteq.dat',          # Original MINTEQA2 database
    'Amm.dat',             # Special database for ammonium
    'frezchem.dat',        # For low-temperature calculations
    'ColdChem.dat'         # Also for low-temperature chemistry
]

# Common default mineral list for precipitation calculations
# These minerals exist in standard water treatment databases (especially minteq.dat)
UNIVERSAL_MINERALS = [
    'Calcite',     # Calcium carbonate - ESSENTIAL for lime softening
    'Aragonite',   # Calcium carbonate polymorph
    'Gypsum',      # Calcium sulfate dihydrate
    'Anhydrite',   # Calcium sulfate
    'Brucite'      # Magnesium hydroxide - CRITICAL for ZLD operations
]

# Legacy name for backward compatibility
DEFAULT_MINERALS = UNIVERSAL_MINERALS

# Specialized mineral sets for water treatment processes
SOFTENING_MINERALS = [
    'Calcite',
    'Aragonite',
    'Brucite',
    'Portlandite',  # Ca(OH)2
    'Lime'          # CaO
]

PHOSPHATE_MINERALS = [
    'Hydroxyapatite',       # Ca5(PO4)3OH
    'Fluorapatite',         # Ca5(PO4)3F
    'Vivianite',            # Fe3(PO4)2·8H2O
    'Strengite',            # FePO4·2H2O
    'Variscite'             # AlPO4·2H2O
]

SULFIDE_MINERALS = [
    'Pyrite',        # FeS2
    'Sphalerite',    # ZnS
    'Galena',        # PbS
    'Cinnabar',      # HgS
    'Chalcocite'     # Cu2S
]

# Minerals for metal removal/recovery 
# Note: These are mapped by database in the mineral registry, so use database-aware 
# selection functions to ensure compatibility
METAL_REMOVAL_MINERALS = [
    'Goethite',
    'Hematite',
    'Fe(OH)3(a)',  # For phreeqc.dat
    'Ferrihydrite', # For wateq4f.dat, llnl.dat
    'Al(OH)3(a)',   # For phreeqc.dat
    'Gibbsite',     # For wateq4f.dat, llnl.dat
    'Boehmite',
    'Zn(OH)2(a)',
    'Cu(OH)2',
    'Tenorite'
]

# Database-specific metal removal minerals (prefer these when database is known)
METAL_REMOVAL_MINERALS_BY_DB = {
    'phreeqc.dat': [
        'Fe(OH)3(a)',
        'Goethite',
        'Hematite',
        'Al(OH)3(a)',
        'Boehmite',
        'Zn(OH)2(a)',
        'Cu(OH)2'
    ],
    'wateq4f.dat': [
        'Ferrihydrite',
        'Goethite',
        'Hematite',
        'Gibbsite',
        'Boehmite',
        'Zn(OH)2(am)',
        'Cu(OH)2',
        'Tenorite'
    ],
    'llnl.dat': [
        'Ferrihydrite',
        'Goethite',
        'Hematite',
        'Gibbsite',
        'Boehmite',
        'Zn(OH)2(am)',
        'Cu(OH)2',
        'Tenorite'
    ]
}

# Conversion factors
MOLAR_MASS = {
    'CaCO3': 100.0869,  # g/mol
    'NaOH': 39.9971,   # g/mol
    'HCl': 36.4609,    # g/mol
    'H2SO4': 98.079,   # g/mol
    'NaHCO3': 84.0066  # g/mol
    # Add more as needed
}

# Unit conversion factors
MG_L_TO_MOL_KGW = {
    'Ca': 1/40.078/1000,
    'Mg': 1/24.305/1000,
    'Na': 1/22.990/1000,
    'K': 1/39.098/1000,
    'Cl': 1/35.453/1000,
    'SO4': 1/96.06/1000,
    'HCO3': 1/61.017/1000,
    # Add more as needed
}

# Alkalinity conversion
ALKALINITY_MOL_TO_MG_CACO3 = 50044  # 2 × molar mass of CaCO3 / 2 × 1000

def select_minerals_for_water_chemistry(water_analysis, database_path=None):
    """
    Selects appropriate minerals based on water chemistry and database.
    
    Args:
        water_analysis: Dictionary of element concentrations
        database_path: Path to the PHREEQC database
        
    Returns:
        List of minerals to include in the simulation
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Determine database name if path is provided
    db_name = None
    if database_path:
        db_name = os.path.basename(database_path)
        logger.info(f"Using database: {db_name}")
    
    # Step 1: Get minerals that are guaranteed to be in the database
    # Start with universal minerals that are safe across all databases
    minerals = []
    
    # First check database compatibility to avoid adding minerals not in the database
    if database_path:
        try:
            from .database_management import database_manager
            # Validate database exists and get all available minerals
            available_minerals = database_manager.get_compatible_minerals(database_path)
            
            # Verify universal minerals
            for mineral in UNIVERSAL_MINERALS:
                if mineral in available_minerals:
                    minerals.append(mineral)
                else:
                    logger.warning(f"Universal mineral '{mineral}' not found in database {db_name}")
        except Exception as e:
            logger.warning(f"Could not verify minerals in database {db_name}: {e}")
            # Fallback to universal minerals without validation
            minerals = UNIVERSAL_MINERALS.copy()
            logger.info(f"Using fallback universal minerals: {minerals}")
    else:
        # No database specified, use universal minerals
        minerals = UNIVERSAL_MINERALS.copy()
        logger.info(f"No database specified, using universal minerals: {minerals}")
    
    # Convert water analysis keys to uppercase for case-insensitive comparison
    if water_analysis:
        # Create a case-insensitive lookup
        analysis = {k.upper(): v for k, v in water_analysis.items()}
        
        # Step 2: Add minerals based on water chemistry but with database validation
        # Check for high hardness (Ca, Mg) - softening minerals
        if 'CA' in analysis and float(analysis.get('CA', 0)) > 50:
            logger.info("Detected high calcium, adding softening minerals")
            # Use database-specific validation
            if database_path:
                try:
                    for mineral in SOFTENING_MINERALS:
                        # Check if mineral is available in the database before adding
                        if mineral not in minerals:  # Skip if already added
                            if database_validator_available():
                                # Use database validator for precise validation
                                if mineral_exists_in_database(database_path, mineral):
                                    minerals.append(mineral)
                                    logger.info(f"Added softening mineral: {mineral}")
                                else:
                                    logger.info(f"Softening mineral {mineral} not available in {db_name}")
                            elif db_name in DATABASE_SPECIFIC_MINERALS and mineral in get_database_minerals(db_name):
                                # Fallback to registry validation
                                minerals.append(mineral)
                                logger.info(f"Added softening mineral: {mineral}")
                except Exception as e:
                    logger.warning(f"Error validating softening minerals: {e}")
                    # Fallback to add all softening minerals without validation
                    minerals.extend([m for m in SOFTENING_MINERALS if m not in minerals])
            else:
                # No database validation, add all softening minerals
                minerals.extend([m for m in SOFTENING_MINERALS if m not in minerals])
        
        # Check for phosphate - phosphate precipitation minerals
        if any(p in analysis for p in ['P', 'PO4', 'PHOSPHATE', 'PHOSPHORUS']):
            logger.info("Detected phosphate, adding phosphate precipitation minerals")
            # Similar validation pattern as softening minerals
            if database_path:
                try:
                    for mineral in PHOSPHATE_MINERALS:
                        if mineral not in minerals:
                            if database_validator_available():
                                if mineral_exists_in_database(database_path, mineral):
                                    minerals.append(mineral)
                                    logger.info(f"Added phosphate mineral: {mineral}")
                                else:
                                    logger.info(f"Phosphate mineral {mineral} not available in {db_name}")
                            elif db_name in DATABASE_SPECIFIC_MINERALS and mineral in get_database_minerals(db_name):
                                minerals.append(mineral)
                                logger.info(f"Added phosphate mineral: {mineral}")
                except Exception as e:
                    logger.warning(f"Error validating phosphate minerals: {e}")
                    minerals.extend([m for m in PHOSPHATE_MINERALS if m not in minerals])
            else:
                minerals.extend([m for m in PHOSPHATE_MINERALS if m not in minerals])
        
        # Check for metals - sulfide precipitation and metal removal minerals
        if any(metal in analysis for metal in ['FE', 'ZN', 'PB', 'HG', 'CU', 'CD', 'NI', 'AG']):
            logger.info("Detected metals, adding sulfide precipitation and metal removal minerals")
            
            # Add sulfide minerals first
            if database_path:
                try:
                    for mineral in SULFIDE_MINERALS:
                        if mineral not in minerals:
                            if database_validator_available():
                                if mineral_exists_in_database(database_path, mineral):
                                    minerals.append(mineral)
                                    logger.info(f"Added sulfide mineral: {mineral}")
                                else:
                                    logger.info(f"Sulfide mineral {mineral} not available in {db_name}")
                            elif db_name in DATABASE_SPECIFIC_MINERALS and mineral in get_database_minerals(db_name):
                                minerals.append(mineral)
                                logger.info(f"Added sulfide mineral: {mineral}")
                except Exception as e:
                    logger.warning(f"Error validating sulfide minerals: {e}")
                    minerals.extend([m for m in SULFIDE_MINERALS if m not in minerals])
            else:
                minerals.extend([m for m in SULFIDE_MINERALS if m not in minerals])
            
            # Add database-specific metal removal minerals
            if db_name and db_name in METAL_REMOVAL_MINERALS_BY_DB:
                logger.info(f"Adding database-specific metal removal minerals for {db_name}")
                db_specific_minerals = METAL_REMOVAL_MINERALS_BY_DB[db_name]
                
                if database_path:
                    # Validate each mineral before adding
                    try:
                        for mineral in db_specific_minerals:
                            if mineral not in minerals:
                                if database_validator_available():
                                    if mineral_exists_in_database(database_path, mineral):
                                        minerals.append(mineral)
                                        logger.info(f"Added metal removal mineral: {mineral}")
                                    else:
                                        logger.warning(f"Metal removal mineral {mineral} defined in registry but not found in database {db_name}")
                                else:
                                    minerals.append(mineral)
                    except Exception as e:
                        logger.warning(f"Error validating metal removal minerals: {e}")
                        minerals.extend([m for m in db_specific_minerals if m not in minerals])
                else:
                    minerals.extend([m for m in db_specific_minerals if m not in minerals])
            else:
                # Conservative fallback - only add common metal minerals
                logger.info("No database-specific metal removal minerals found, using conservative fallback")
                safe_metal_minerals = ['Goethite', 'Hematite']
                minerals.extend([m for m in safe_metal_minerals if m not in minerals])
        
        # Check for high iron specifically - special handling for the Ferrihydrite/Fe(OH)3 issue
        if 'FE' in analysis and float(analysis.get('FE', 0)) > 1:
            logger.info("Detected high iron, adding iron hydroxide minerals")
            
            if db_name == 'phreeqc.dat':
                # Use Fe(OH)3(a) for phreeqc.dat (no Ferrihydrite)
                logger.info("Using Fe(OH)3(a) for phreeqc.dat")
                iron_minerals = ['Fe(OH)3(a)', 'Goethite', 'Hematite']
            elif db_name in ['wateq4f.dat', 'llnl.dat', 'minteq.dat']:
                # Use Ferrihydrite for these databases
                logger.info(f"Using Ferrihydrite for {db_name}")
                iron_minerals = ['Ferrihydrite', 'Goethite', 'Hematite']
            else:
                # Conservative fallback - only Goethite and Hematite are common across all databases
                logger.info("Using conservative iron minerals")
                iron_minerals = ['Goethite', 'Hematite']
            
            # Validate each iron mineral
            if database_path:
                try:
                    for mineral in iron_minerals:
                        if mineral not in minerals:
                            if database_validator_available():
                                if mineral_exists_in_database(database_path, mineral):
                                    minerals.append(mineral)
                                    logger.info(f"Added iron mineral: {mineral}")
                                else:
                                    logger.warning(f"Iron mineral {mineral} not found in database {db_name}")
                                    
                                    # Special handling for Ferrihydrite/Fe(OH)3 issue
                                    if mineral == 'Ferrihydrite' and db_name == 'phreeqc.dat':
                                        logger.info("Substituting Fe(OH)3(a) for Ferrihydrite in phreeqc.dat")
                                        if 'Fe(OH)3(a)' not in minerals and mineral_exists_in_database(database_path, 'Fe(OH)3(a)'):
                                            minerals.append('Fe(OH)3(a)')
                            else:
                                # Less precise registry-based validation
                                if db_name in DATABASE_SPECIFIC_MINERALS and mineral in get_database_minerals(db_name):
                                    minerals.append(mineral)
                except Exception as e:
                    logger.warning(f"Error validating iron minerals: {e}")
                    minerals.extend([m for m in iron_minerals if m not in minerals])
            else:
                minerals.extend([m for m in iron_minerals if m not in minerals])
            
        # Check for high aluminum
        if 'AL' in analysis and float(analysis.get('AL', 0)) > 1:
            logger.info("Detected high aluminum, adding aluminum hydroxide minerals")
            
            if db_name == 'phreeqc.dat':
                al_minerals = ['Al(OH)3(a)', 'Boehmite']
            elif db_name in ['wateq4f.dat', 'llnl.dat', 'minteq.dat']:
                al_minerals = ['Gibbsite', 'Boehmite']
            else:
                # Conservative fallback
                al_minerals = ['Boehmite']
            
            # Validate each aluminum mineral
            if database_path:
                try:
                    for mineral in al_minerals:
                        if mineral not in minerals:
                            if database_validator_available():
                                if mineral_exists_in_database(database_path, mineral):
                                    minerals.append(mineral)
                                    logger.info(f"Added aluminum mineral: {mineral}")
                                else:
                                    logger.warning(f"Aluminum mineral {mineral} not found in database {db_name}")
                                    
                                    # Special handling for Gibbsite/Al(OH)3 issue
                                    if mineral == 'Gibbsite' and db_name == 'phreeqc.dat':
                                        logger.info("Substituting Al(OH)3(a) for Gibbsite in phreeqc.dat")
                                        if 'Al(OH)3(a)' not in minerals and mineral_exists_in_database(database_path, 'Al(OH)3(a)'):
                                            minerals.append('Al(OH)3(a)')
                            else:
                                # Less precise registry-based validation
                                if db_name in DATABASE_SPECIFIC_MINERALS and mineral in get_database_minerals(db_name):
                                    minerals.append(mineral)
                except Exception as e:
                    logger.warning(f"Error validating aluminum minerals: {e}")
                    minerals.extend([m for m in al_minerals if m not in minerals])
            else:
                minerals.extend([m for m in al_minerals if m not in minerals])
    
    # Step 3: Final database validation to ensure all minerals are compatible
    if database_path:
        try:
            from .database_management import database_manager
            
            # Log the pre-validation minerals
            logger.info(f"Pre-validation minerals: {minerals}")
            
            # Get compatible versions of all selected minerals
            mineral_map = database_manager.get_compatible_minerals(database_path, minerals)
            
            # Replace with compatible minerals, filtering out None values
            validated_minerals = [compatible for mineral, compatible in mineral_map.items() if compatible]
            
            # Log any minerals that were removed
            removed_minerals = [mineral for mineral, compatible in mineral_map.items() if not compatible]
            if removed_minerals:
                logger.warning(f"Removed incompatible minerals: {removed_minerals}")
            
            logger.info(f"Final validated minerals: {validated_minerals}")
            return validated_minerals
        except Exception as e:
            logger.warning(f"Error in final mineral validation: {e}")
            # Return the unvalidated minerals as a fallback
            return minerals
    
    logger.info(f"Final minerals (no validation): {minerals}")
    return minerals


def database_validator_available():
    """Check if database_validator module is available for precise mineral validation."""
    try:
        from . import database_validator
        return True
    except ImportError:
        return False


def mineral_exists_in_database(database_path, mineral_name):
    """
    Check if a mineral exists in the database using database_validator.
    
    Args:
        database_path: Path to the database file
        mineral_name: Name of the mineral to check
        
    Returns:
        True if the mineral exists, False otherwise
    """
    try:
        from . import database_validator
        result = database_validator.scan_database_for_mineral(database_path, mineral_name)
        return result.get('found', False)
    except Exception:
        return False


def get_database_minerals(database_name):
    """
    Get all minerals for a database from the mineral registry.
    
    Args:
        database_name: Name of the database file
        
    Returns:
        Set of mineral names
    """
    try:
        from .mineral_registry import DATABASE_SPECIFIC_MINERALS, COMMON_MINERALS
        minerals = set(COMMON_MINERALS.keys())
        if database_name in DATABASE_SPECIFIC_MINERALS:
            minerals.update(DATABASE_SPECIFIC_MINERALS[database_name].keys())
        return minerals
    except Exception:
        return set()
