"""
Database Registry Module

This module contains metadata about PHREEQC thermodynamic databases,
including their sources, versions, and download URLs.
"""
import logging
import os
import json
from typing import Dict, List, Optional, Set, Union, Any

# Path constants
from .database_downloader import (
    DATABASE_DIR, OFFICIAL_DIR, CUSTOM_DIR, CACHED_DIR, METADATA_DIR
)

logger = logging.getLogger(__name__)

# Define official PHREEQC databases with metadata
OFFICIAL_DATABASES = {
    'phreeqc.dat': {
        'description': 'Standard PHREEQC database with moderate set of elements',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/phreeqc.dat',
        'version': '3.8.6',
        'citation': 'Parkhurst, D.L., and Appelo, C.A.J., 2013, Description of input and examples for PHREEQC version 3--A computer program for speciation, batch-reaction, one-dimensional transport, and inverse geochemical calculations: U.S. Geological Survey Techniques and Methods, book 6, chap. A43, 497 p.',
        'last_updated': '2021-03-01'
    },
    'wateq4f.dat': {
        'description': 'WATEQ4F database with comprehensive elements and minerals for natural waters',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/wateq4f.dat',
        'version': '3.8.6',
        'citation': 'Ball, J.W., and Nordstrom, D.K., 1991, User\'s manual for WATEQ4F, with revised thermodynamic data base and test cases for calculating speciation of major, trace, and redox elements in natural waters: U.S. Geological Survey Open-File Report 91-183, 189 p.',
        'last_updated': '2021-03-01'
    },
    'minteq.v4.dat': {
        'description': 'MINTEQA2 version 4 database with comprehensive set of elements',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/minteq.v4.dat',
        'version': '4.0',
        'citation': 'Allison, J.D., Brown, D.S., and Novo-Gradac, K.J., 1991, MINTEQA2/PRODEFA2, A geochemical assessment model for environmental systems: version 3.0 user\'s manual: Environmental Research Laboratory, Office of Research and Development, U.S. Environmental Protection Agency, Athens, GA.',
        'last_updated': '2020-10-05'
    },
    'minteq.dat': {
        'description': 'Original MINTEQA2 database for chemical equilibrium modeling',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/minteq.dat',
        'version': '3.8.6',
        'citation': 'Allison, J.D., Brown, D.S., and Novo-Gradac, K.J., 1991, MINTEQA2/PRODEFA2, A geochemical assessment model for environmental systems: version 3.0 user\'s manual: Environmental Research Laboratory, Office of Research and Development, U.S. Environmental Protection Agency, Athens, GA.',
        'last_updated': '2021-03-01'
    },
    'llnl.dat': {
        'description': 'Lawrence Livermore National Laboratory database - most comprehensive',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/llnl.dat',
        'version': '3.8.6',
        'citation': 'Johnson, J.W., Oelkers, E.H., and Helgeson, H.C., 1992, SUPCRT92: A software package for calculating the standard molal thermodynamic properties of minerals, gases, aqueous species, and reactions from 1 to 5000 bar and 0 to 1000°C: Computers & Geosciences, v. 18, no. 7, p. 899-947.',
        'last_updated': '2021-03-01'
    },
    'pitzer.dat': {
        'description': 'Pitzer model for high ionic strength solutions and brines',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/pitzer.dat',
        'version': '3.8.6',
        'citation': 'Plummer, L.N., Parkhurst, D.L., Fleming, G.W., and Dunkle, S.A., 1988, A computer program incorporating Pitzer\'s equations for calculation of geochemical reactions in brines: U.S. Geological Survey Water-Resources Investigations Report 88-4153, 310 p.',
        'last_updated': '2021-03-01'
    },
    'sit.dat': {
        'description': 'Specific ion interaction theory database for high ionic strength',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/sit.dat',
        'version': '3.8.6',
        'citation': 'Grenthe, I., Fuger, J., Konings, R.J.M., Lemire, R.J., Muller, A.B., Nguyen-Trung, C., and Wanner, H., 1992, Chemical thermodynamics of uranium: Nuclear Energy Agency, Organisation for Economic Co-operation and Development, Paris.',
        'last_updated': '2021-03-01'
    },
    'iso.dat': {
        'description': 'Database for isotope calculations and fractionation',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/iso.dat',
        'version': '3.8.6',
        'citation': 'Parkhurst, D.L., and Appelo, C.A.J., 2013, Description of input and examples for PHREEQC version 3--A computer program for speciation, batch-reaction, one-dimensional transport, and inverse geochemical calculations: U.S. Geological Survey Techniques and Methods, book 6, chap. A43, 497 p.',
        'last_updated': '2021-03-01'
    },
    'Amm.dat': {
        'description': 'Special database for ammonium chemistry',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/Amm.dat',
        'version': '3.8.6',
        'citation': 'Parkhurst, D.L., and Appelo, C.A.J., 2013, Description of input and examples for PHREEQC version 3--A computer program for speciation, batch-reaction, one-dimensional transport, and inverse geochemical calculations: U.S. Geological Survey Techniques and Methods, book 6, chap. A43, 497 p.',
        'last_updated': '2021-03-01'
    },
    'ColdChem.dat': {
        'description': 'Database for low-temperature chemistry',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/ColdChem.dat',
        'version': '3.8.6',
        'citation': 'Marion, G.M. and Kargel, J.S., 2008, Cold Aqueous Planetary Geochemistry with FREZCHEM: From Modeling to the Search for Life at the Limits: Springer, New York, 251 p.',
        'last_updated': '2021-03-01'
    },
    'frezchem.dat': {
        'description': 'Database for frozen chemistry and brines at low temperatures',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/frezchem.dat',
        'version': '3.8.6',
        'citation': 'Marion, G.M. and Kargel, J.S., 2008, Cold Aqueous Planetary Geochemistry with FREZCHEM: From Modeling to the Search for Life at the Limits: Springer, New York, 251 p.',
        'last_updated': '2021-03-01'
    },
    'Tipping_Hurley.dat': {
        'description': 'Database for humic substances and organic matter',
        'download_url': 'https://wwwbrr.cr.usgs.gov/projects/GWC_coupled/phreeqc/database/Tipping_Hurley.dat',
        'version': '3.8.6',
        'citation': 'Tipping, E., and Hurley, M.A., 1992, A unifying model of cation binding by humic substances: Geochimica et Cosmochimica Acta, v. 56, p. 3627-3641.',
        'last_updated': '2021-03-01'
    },
}

# Define database compatibilities and features
DATABASE_FEATURES = {
    'phreeqc.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'Si', 'Al', 'Fe', 'Mn', 'N', 'B', 'Ba', 'Sr', 'P'],
        'features': ['acid_base', 'solids_dissolution', 'redox', 'surface_complexation'],
        'mineral_count': 56,
        'complexity': 'moderate',
        'recommended_for': ['general', 'natural_waters', 'acid_mine_drainage'],
        'limitations': ['limited_heavy_metals', 'no_organic_matter', 'limited_redox_minerals'],
        'special_requirements': None
    },
    'wateq4f.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'Si', 'Al', 'Fe', 'Mn', 'N', 'B', 'Ba', 'Sr', 'P', 
                    'Cu', 'Zn', 'Cd', 'Pb', 'Li', 'As', 'Se'],
        'features': ['acid_base', 'solids_dissolution', 'redox', 'adsorption', 'surface_complexation'],
        'mineral_count': 118,
        'complexity': 'high',
        'recommended_for': ['natural_waters', 'heavy_metals', 'contamination'],
        'limitations': ['limited_high_salinity', 'no_organic_matter'],
        'special_requirements': None
    },
    'llnl.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'Si', 'Al', 'Fe', 'Mn', 'N', 'B', 'Ba', 'Sr', 'P',
                    'Cu', 'Zn', 'Cd', 'Pb', 'Li', 'As', 'Se', 'U', 'Am', 'Th', 'Pu', 'Np', 'Ra', 'Other_actinides'],
        'features': ['acid_base', 'solids_dissolution', 'redox', 'radioactive_elements', 'surface_complexation'],
        'mineral_count': 1014,
        'complexity': 'very_high',
        'recommended_for': ['minerals', 'radioactive_elements', 'comprehensive'],
        'limitations': ['calculation_speed', 'high_ionic_strength'],
        'special_requirements': 'More memory and processing power due to large database'
    },
    'minteq.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'Si', 'Al', 'Fe', 'Mn', 'N', 'B', 'Ba', 'Sr', 'P',
                    'Cu', 'Zn', 'Cd', 'Pb', 'Heavy_metals'],
        'features': ['acid_base', 'solids_dissolution', 'redox', 'surface_complexation', 'adsorption'],
        'mineral_count': 92,
        'complexity': 'high',
        'recommended_for': ['heavy_metals', 'adsorption', 'surface_chemistry'],
        'limitations': ['limited_high_salinity', 'limited_radioactive_elements'],
        'special_requirements': None
    },
    'pitzer.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'Br', 'N'],
        'features': ['high_ionic_strength', 'brines', 'salt_systems'],
        'mineral_count': 35,
        'complexity': 'moderate',
        'recommended_for': ['high_salinity', 'brines', 'salt_lakes', 'evaporation'],
        'limitations': ['limited_element_set', 'limited_minerals', 'no_organic_matter'],
        'special_requirements': None
    },
    'sit.dat': {
        'elements': ['H', 'O', 'Na', 'K', 'Ca', 'Mg', 'C', 'S', 'Cl', 'F', 'U', 'Other_actinides'],
        'features': ['high_ionic_strength', 'radioactive_elements'],
        'mineral_count': 28,
        'complexity': 'moderate',
        'recommended_for': ['high_salinity', 'nuclear_waste', 'radioactive_elements'],
        'limitations': ['limited_element_set', 'limited_minerals'],
        'special_requirements': None
    },
    'iso.dat': {
        'elements': ['H', 'O', 'C', 'S', 'N', 'Cl', 'H-isotopes', 'O-isotopes', 'C-isotopes', 'S-isotopes'],
        'features': ['isotopes', 'fractionation'],
        'mineral_count': 19,
        'complexity': 'moderate',
        'recommended_for': ['isotopes', 'environmental_tracing', 'geochemical_evolution'],
        'limitations': ['specialized_only', 'limited_element_set'],
        'special_requirements': 'Knowledge of isotope geochemistry'
    }
}

# Map database properties to application domains
DOMAIN_DATABASE_RECOMMENDATIONS = {
    'drinking_water': ['phreeqc.dat', 'wateq4f.dat'],
    'groundwater': ['wateq4f.dat', 'phreeqc.dat', 'llnl.dat'],
    'wastewater': ['minteq.dat', 'wateq4f.dat'],
    'geothermal': ['llnl.dat', 'wateq4f.dat'],
    'mining': ['wateq4f.dat', 'minteq.dat', 'llnl.dat'],
    'brines': ['pitzer.dat', 'sit.dat'],
    'nuclear': ['sit.dat', 'llnl.dat'],
    'isotopes': ['iso.dat'],
    'concrete': ['cement.dat', 'Concrete_PHR.dat'],
    'desalination': ['pitzer.dat', 'phreeqc.dat'],
    'general': ['phreeqc.dat', 'wateq4f.dat'],
    'heavy_metals': ['wateq4f.dat', 'minteq.dat'],
    'organic_contaminants': ['Tipping_Hurley.dat'],
    'low_temperature': ['ColdChem.dat', 'frezchem.dat'],
    'high_ionic_strength': ['pitzer.dat', 'sit.dat'],
    'surface_complexation': ['minteq.dat', 'wateq4f.dat', 'phreeqc.dat'],
    'acid_mine_drainage': ['wateq4f.dat', 'phreeqc.dat'],
    'radioactive_waste': ['sit.dat', 'llnl.dat'],
    'mineral_equilibria': ['llnl.dat', 'wateq4f.dat']
}

# Database compatibility issues and known bugs
DATABASE_ISSUES = {
    'phreeqc.dat': [
        {'issue': 'Missing Ferrihydrite mineral', 
         'workaround': 'Use Fe(OH)3(a) instead of Ferrihydrite'},
        {'issue': 'Limited heavy metal species', 
         'workaround': 'Use wateq4f.dat for heavy metal calculations'}
    ],
    'wateq4f.dat': [
        {'issue': 'May have convergence issues at high ionic strength', 
         'workaround': 'Use pitzer.dat for high salinity solutions'},
        {'issue': 'Some mineral names differ from other databases', 
         'workaround': 'Check mineral registry for name mappings'}
    ],
    'llnl.dat': [
        {'issue': 'Very large database can cause slow calculations', 
         'workaround': 'Increase memory allocation and consider using a subset of the database'},
        {'issue': 'May overestimate mineral precipitation in some cases', 
         'workaround': 'Verify results with laboratory data when available'}
    ],
    'pitzer.dat': [
        {'issue': 'Limited set of elements compared to other databases', 
         'workaround': 'Only use for brine and high ionic strength calculations'},
        {'issue': 'Not suitable for redox calculations', 
         'workaround': 'Use wateq4f.dat for redox-sensitive systems'}
    ]
}

def get_recommended_database_for_domain(domain: str) -> Optional[str]:
    """
    Get the recommended database for a specific application domain.
    
    Args:
        domain: Application domain (e.g., 'drinking_water', 'groundwater', etc.)
        
    Returns:
        Name of the recommended database or None if domain not recognized
    """
    if domain.lower() in DOMAIN_DATABASE_RECOMMENDATIONS:
        recommendations = DOMAIN_DATABASE_RECOMMENDATIONS[domain.lower()]
        if recommendations:
            return recommendations[0]  # Return the first (most recommended) database
    return None

def get_database_url(database_name: str) -> Optional[str]:
    """
    Get the download URL for a specific database.
    
    Args:
        database_name: Name of the database
        
    Returns:
        URL for downloading the database or None if not found
    """
    if database_name in OFFICIAL_DATABASES:
        return OFFICIAL_DATABASES[database_name]['download_url']
    return None

def get_all_available_databases() -> List[str]:
    """
    Get a list of all available official databases.
    
    Returns:
        List of database names
    """
    return list(OFFICIAL_DATABASES.keys())

def get_database_metadata(database_name: str) -> Optional[Dict[str, Any]]:
    """
    Get complete metadata for a specific database.
    
    Args:
        database_name: Name of the database
        
    Returns:
        Dictionary with database metadata or None if not found
    """
    metadata = {}
    
    # Add basic info from OFFICIAL_DATABASES
    if database_name in OFFICIAL_DATABASES:
        metadata.update(OFFICIAL_DATABASES[database_name])
    else:
        return None
        
    # Add features and compatibility info if available
    if database_name in DATABASE_FEATURES:
        metadata.update(DATABASE_FEATURES[database_name])
    
    # Add known issues if available
    if database_name in DATABASE_ISSUES:
        metadata['known_issues'] = DATABASE_ISSUES[database_name]
        
    return metadata

def register_custom_database_metadata(database_path: str, metadata: Dict[str, Any]) -> bool:
    """
    Register metadata for a custom database.
    
    Args:
        database_path: Path to the database file
        metadata: Dictionary containing metadata for the database
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract the database filename
        db_name = os.path.basename(database_path)
        
        # Create metadata directory if it doesn't exist
        os.makedirs(METADATA_DIR, exist_ok=True)
        
        # Path to the custom databases metadata file
        metadata_path = os.path.join(METADATA_DIR, 'custom_databases.json')
        
        # Load existing metadata if available
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    custom_dbs = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read custom database metadata, creating new: {e}")
                custom_dbs = {}
        else:
            custom_dbs = {}
        
        # Add or update metadata for this database
        if db_name in custom_dbs:
            # Update existing entry
            custom_dbs[db_name].update(metadata)
        else:
            # Add new entry
            custom_dbs[db_name] = metadata
        
        # Save updated metadata
        with open(metadata_path, 'w') as f:
            json.dump(custom_dbs, f, indent=2)
        
        logger.info(f"Registered metadata for custom database: {db_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to register custom database metadata: {e}")
        return False

def get_custom_database_metadata(database_name: Optional[str] = None) -> Union[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Get metadata for custom databases.
    
    Args:
        database_name: Optional name of the database to get metadata for.
                      If None, returns metadata for all custom databases.
        
    Returns:
        If database_name is specified: Dictionary with database metadata or None if not found
        If database_name is None: Dictionary mapping database names to their metadata
    """
    try:
        # Path to the custom databases metadata file
        metadata_path = os.path.join(METADATA_DIR, 'custom_databases.json')
        
        # Check if the metadata file exists
        if not os.path.exists(metadata_path):
            if database_name:
                return None
            else:
                return {}
        
        # Load metadata
        with open(metadata_path, 'r') as f:
            custom_dbs = json.load(f)
        
        # Return metadata for the specified database or all databases
        if database_name:
            return custom_dbs.get(database_name)
        else:
            return custom_dbs
            
    except Exception as e:
        logger.error(f"Failed to read custom database metadata: {e}")
        if database_name:
            return None
        else:
            return {}

def get_database_compatibility_issues(database_name: str) -> List[Dict[str, str]]:
    """
    Get known compatibility issues and workarounds for a database.
    
    Args:
        database_name: Name of the database
        
    Returns:
        List of dictionaries with issue and workaround keys
    """
    if database_name in DATABASE_ISSUES:
        return DATABASE_ISSUES[database_name]
    return []

def suggest_alternative_database(current_database: str, domain: Optional[str] = None) -> Optional[str]:
    """
    Suggest an alternative database that might be better suited for a specific domain.
    
    Args:
        current_database: Current database being used
        domain: Optional application domain
        
    Returns:
        Name of the suggested alternative database or None if the current is already optimal
    """
    # If domain is specified, use domain-specific recommendations
    if domain and domain.lower() in DOMAIN_DATABASE_RECOMMENDATIONS:
        recommendations = DOMAIN_DATABASE_RECOMMENDATIONS[domain.lower()]
        if recommendations and current_database != recommendations[0]:
            return recommendations[0]
    
    # General recommendations based on database limitations
    if current_database == 'phreeqc.dat' and domain in ['heavy_metals', 'contamination']:
        return 'wateq4f.dat'
    
    if current_database in ['phreeqc.dat', 'wateq4f.dat', 'llnl.dat'] and domain in ['brines', 'high_salinity']:
        return 'pitzer.dat'
    
    if current_database != 'llnl.dat' and domain in ['minerals', 'comprehensive']:
        return 'llnl.dat'
    
    # No better alternative found
    return None