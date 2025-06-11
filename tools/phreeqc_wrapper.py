"""
PHREEQC wrapper module for water chemistry calculations.
"""

import logging
import re
import os
import asyncio
import itertools
import numpy as np
import math
from typing import Optional, Dict, Any, List, Tuple, Union
from functools import lru_cache

logger = logging.getLogger(__name__)

# Cache for database paths
@lru_cache(maxsize=128)
def _get_phreeqc_rates_path() -> Optional[str]:
    """
    Find the phreeqc_rates.dat file with caching.
    
    Returns:
        Path to phreeqc_rates.dat or None if not found
    """
    # Common locations to check
    potential_paths = [
        "/mnt/c/Program Files/USGS/phreeqc-3.8.6-17100-x64/database/phreeqc_rates.dat",
        "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\database\\phreeqc_rates.dat",
        "/usr/local/share/phreeqc/database/phreeqc_rates.dat",
        "/usr/share/phreeqc/database/phreeqc_rates.dat",
        os.path.join(os.path.dirname(__file__), "..", "databases", "official", "phreeqc_rates.dat"),
    ]
    
    # For Windows, convert paths if needed
    if os.name == 'nt':
        # On Windows, use the Windows path
        potential_paths.insert(0, r"C:\Program Files\USGS\phreeqc-3.8.6-17100-x64\database\phreeqc_rates.dat")
    
    # Check each path
    for path in potential_paths:
        if os.path.exists(path):
            logger.debug(f"Found phreeqc_rates.dat at: {path}")
            return path
    
    # Try to find it relative to PHREEQC installation
    try:
        # Check if there's a PHREEQC_DATABASE environment variable
        phreeqc_dir = os.environ.get('PHREEQC_DATABASE')
        if phreeqc_dir:
            rates_path = os.path.join(phreeqc_dir, "phreeqc_rates.dat")
            if os.path.exists(rates_path):
                return rates_path
    except:
        pass
    
    return None

# Import helper functions
from utils.helpers import (
    build_solution_block,
    build_reaction_block,
    build_equilibrium_phases_block,
    build_mix_block,
    build_gas_phase_block,
    build_surface_block,
    build_kinetics_block,
    build_selected_output_block
)
from utils.import_helpers import PHREEQPYTHON_AVAILABLE, DEFAULT_DATABASE

def get_mineral_alternatives(mineral_name, database_path=None):
    """
    Returns a list of alternative mineral names that could be used in place of the given mineral.
    
    Args:
        mineral_name (str): Name of the mineral to find alternatives for
        database_path (str, optional): Path to the PHREEQC database
        
    Returns:
        dict: Dictionary with alternative minerals and their formulas
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Extract database name from path
        db_name = None
        if database_path:
            db_name = os.path.basename(database_path)
            
        # Get formula of the requested mineral
        try:
            from utils.mineral_registry import get_mineral_formula, get_alternative_mineral_names
            formula = get_mineral_formula(mineral_name, db_name)
            
            if not formula:
                logger.warning(f"Could not find formula for mineral '{mineral_name}' in registry")
                return {}
                
            # Get all alternative names for this mineral
            alternatives = get_alternative_mineral_names(mineral_name, db_name)
            
            # Check which alternatives are actually valid in this database
            valid_alternatives = {}
            
            if database_path:
                from utils.constants import mineral_exists_in_database, database_validator_available
                
                if database_validator_available():
                    # Validate each alternative
                    for alt in alternatives:
                        if mineral_exists_in_database(database_path, alt):
                            alt_formula = get_mineral_formula(alt, db_name) or formula
                            valid_alternatives[alt] = alt_formula
                            logger.info(f"Found valid alternative '{alt}' ({alt_formula}) for '{mineral_name}' in {db_name}")
                else:
                    # If we can't validate, return all alternatives
                    for alt in alternatives:
                        alt_formula = get_mineral_formula(alt, db_name) or formula
                        valid_alternatives[alt] = alt_formula
                        
                # Special case for common problematic minerals
                if mineral_name == 'Ferrihydrite' and db_name == 'phreeqc.dat':
                    valid_alternatives['Fe(OH)3(a)'] = 'Fe(OH)3'
                elif mineral_name == 'Fe(OH)3(a)' and db_name in ['wateq4f.dat', 'llnl.dat', 'minteq.dat']:
                    valid_alternatives['Ferrihydrite'] = 'Fe(OH)3'
                elif mineral_name == 'Gibbsite' and db_name == 'phreeqc.dat':
                    valid_alternatives['Al(OH)3(a)'] = 'Al(OH)3'
                elif mineral_name == 'Al(OH)3(a)' and db_name in ['wateq4f.dat', 'llnl.dat', 'minteq.dat']:
                    valid_alternatives['Gibbsite'] = 'Al(OH)3'
            else:
                # If no database path, just return all alternatives with their formulas
                for alt in alternatives:
                    alt_formula = get_mineral_formula(alt) or formula
                    valid_alternatives[alt] = alt_formula
                    
            return valid_alternatives
            
        except ImportError:
            logger.warning("Mineral registry not available")
            return {}
            
    except Exception as e:
        logger.error(f"Error finding mineral alternatives: {e}")
        return {}


def extract_minerals_from_input(input_string):
    """
    Extract mineral names from a PHREEQC input string.
    
    Args:
        input_string (str): PHREEQC input string
        
    Returns:
        list: List of mineral names found in the input
    """
    minerals = []
    
    # Extract minerals from EQUILIBRIUM_PHASES blocks
    eq_phases_pattern = r'EQUILIBRIUM_PHASES\s+\d+(?:\s*#[^\n]*)?\n(.*?)(?=^\S|\Z)'
    eq_phases_matches = re.finditer(eq_phases_pattern, input_string, re.MULTILINE | re.DOTALL)
    
    for match in eq_phases_matches:
        block_content = match.group(1)
        # Extract mineral names from each line
        for line in block_content.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith(';'):
                parts = line.split()
                if parts:
                    mineral_name = parts[0].strip()
                    if mineral_name and not mineral_name.startswith('-'):
                        minerals.append(mineral_name)
    
    # Extract minerals from SI calculations in SELECTED_OUTPUT
    si_pattern = r'-si\s+([A-Za-z0-9_\(\)\-\.]+)'
    si_matches = re.finditer(si_pattern, input_string, re.IGNORECASE)
    for match in si_matches:
        minerals.append(match.group(1))
    
    # Remove duplicates while preserving order
    unique_minerals = []
    for mineral in minerals:
        if mineral not in unique_minerals:
            unique_minerals.append(mineral)
    
    return unique_minerals

def get_truncated_input(input_string, max_length=500):
    """
    Create a truncated version of the input string for error context.
    
    Args:
        input_string (str): Original input string
        max_length (int): Maximum length to return
        
    Returns:
        str: Truncated input string with focusing on relevant parts
    """
    # If input is already short, return it as is
    if len(input_string) <= max_length:
        return input_string
    
    # Find important blocks for debugging
    important_blocks = [
        "EQUILIBRIUM_PHASES",
        "SOLUTION",
        "REACTION",
        "GAS_PHASE",
        "KINETICS",
        "SURFACE"
    ]
    
    # Extract snippets of important blocks
    snippets = []
    for block in important_blocks:
        block_pattern = f"{block}\\s+\\d+(?:\\s*#[^\\n]*)?\\n(.*?)(?=^\\S|\\Z)"
        block_matches = re.finditer(block_pattern, input_string, re.MULTILINE | re.DOTALL)
        
        for match in block_matches:
            block_content = match.group(0)
            # Truncate long blocks
            if len(block_content) > 200:
                block_lines = block_content.splitlines()
                if len(block_lines) > 8:
                    truncated_block = "\n".join(block_lines[:4]) + "\n...\n" + "\n".join(block_lines[-2:])
                else:
                    truncated_block = block_content[:200] + "..."
            else:
                truncated_block = block_content
                
            snippets.append(truncated_block)
    
    # If no important blocks were found or snippets are still too long, 
    # fall back to truncating the beginning and end
    if not snippets or sum(len(s) for s in snippets) > max_length:
        return input_string[:max_length//2] + "\n...\n" + input_string[-max_length//2:]
    
    # Join snippets with separator
    return "\n\n...\n\n".join(snippets)

class PhreeqcError(Exception):
    """
    Custom exception for PHREEQC simulation errors with enhanced information.
    
    Attributes:
        message (str): Error message.
        phreeqc_errors (str): PHREEQC-specific error messages.
        database (str): Database used in the simulation.
        minerals (list): Minerals involved in the simulation.
        input_excerpt (str): Excerpt of the PHREEQC input that caused the error.
        context (dict): Additional context for debugging.
    """
    
    def __init__(self, message, phreeqc_errors=None, database=None, 
                minerals=None, input_excerpt=None, context=None):
        """Initialize the PhreeqcError with enhanced information."""
        self.message = message
        self.phreeqc_errors = phreeqc_errors
        self.database = database
        self.minerals = minerals or []
        self.input_excerpt = input_excerpt
        self.context = context or {}
        
        # Check for water activity non-convergence errors
        self.is_water_activity_error = False
        if phreeqc_errors and "activity of water" in phreeqc_errors.lower() and "not converged" in phreeqc_errors.lower():
            self.is_water_activity_error = True
            # Water activity convergence is a special case we handle differently
            if not self.context:
                self.context = {}
            self.context["error_type"] = "water_activity_convergence"
        
        # Create a detailed error message
        detailed_msg = [message]
        
        if phreeqc_errors:
            detailed_msg.append(f"PHREEQC errors: {phreeqc_errors}")
            
        if database:
            detailed_msg.append(f"Database: {os.path.basename(database)}")
            
        if minerals and len(minerals) > 0:
            if len(minerals) <= 5:
                detailed_msg.append(f"Minerals: {', '.join(minerals)}")
            else:
                detailed_msg.append(f"Minerals: {', '.join(minerals[:5])} (and {len(minerals)-5} more)")
                
        if input_excerpt:
            # Truncate if too long
            if len(input_excerpt) > 500:
                excerpt_truncated = input_excerpt[:250] + "\n...\n" + input_excerpt[-250:]
                detailed_msg.append(f"Input excerpt: \n{excerpt_truncated}")
            else:
                detailed_msg.append(f"Input excerpt: \n{input_excerpt}")
                
        if context:
            context_str = ", ".join(f"{k}={v}" for k, v in context.items())
            detailed_msg.append(f"Context: {context_str}")
            
        super().__init__("\n".join(detailed_msg))
        
    def to_dict(self):
        """Convert the exception to a dictionary for API responses."""
        return {
            "error": self.message,
            "phreeqc_errors": self.phreeqc_errors,
            "database": os.path.basename(self.database) if self.database else None,
            "minerals": self.minerals[:5] if self.minerals and len(self.minerals) > 0 else None,
            "minerals_count": len(self.minerals) if self.minerals else 0,
            "context": self.context
        }

async def run_phreeqc_simulation(
    input_string: str, 
    database_path: Optional[str] = None, 
    num_steps: int = 1
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Runs a PHREEQC simulation string and parses the results.
    Handles single or multiple step results.
    
    Args:
        input_string: PHREEQC input script as a string
        database_path: Path to the PHREEQC database file, or None for default
        num_steps: Number of time steps for multi-step simulations
        
    Returns:
        Dictionary or list of dictionaries with simulation results
    """
    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")
    
    # Import here to avoid import errors if the library is not available
    from phreeqpython import PhreeqPython
    
    # Handle special case where database is included in the input
    if database_path == "INLINE":
        # Database is specified in the input string with DATABASE or INCLUDE statements
        db_to_use = None
        pp = None
        logger.info("Using database specified in PHREEQC input (INLINE mode)")
    else:
        db_to_use = database_path or DEFAULT_DATABASE
        pp = None
        
        # CRITICAL: For ZLD applications, use minteq.dat which includes Brucite
        # If no specific database requested and we have minteq.dat, use it
        if not database_path:
            from utils.database_management import database_manager
            minteq_path = database_manager.get_database_path('minteq.dat')
            if minteq_path and os.path.exists(minteq_path):
                db_to_use = minteq_path
                logger.info(f"Using minteq.dat for Brucite/Mg(OH)2 support: {minteq_path}")
    
    try:
        if database_path == "INLINE":
            # For inline database specification, create PhreeqPython without database
            # The DATABASE and INCLUDE statements in the input will handle database loading
            pp = PhreeqPython()
            logger.info("Created PhreeqPython without database (will use INLINE specification)")
        elif db_to_use:
            logger.info(f"Using PHREEQC database: {db_to_use}")
            # Check if file exists
            if not os.path.exists(db_to_use):
                logger.error(f"Database file does not exist: {db_to_use}")
                raise FileNotFoundError(f"Database file does not exist: {db_to_use}")
            
            # Create PhreeqPython instance with the specified database
            # PhreeqPython prefers just the database filename for built-in databases
            db_basename = os.path.basename(db_to_use)
            
            # Try with just the basename first (works for standard databases)
            try:
                pp = PhreeqPython(database=db_basename)
                logger.info(f"Successfully created PhreeqPython with database: {db_basename}")
            except Exception as e:
                logger.warning(f"Could not create PhreeqPython with basename {db_basename}, trying full path: {e}")
                # Try with full path
                try:
                    pp = PhreeqPython(database=db_to_use)
                    logger.info(f"Successfully created PhreeqPython with full path: {db_to_use}")
                except Exception as e2:
                    logger.warning(f"Could not create PhreeqPython with full path, trying load_database: {e2}")
                    # Final fallback: create without database and load it
                    pp = PhreeqPython()
                    try:
                        pp.ip.load_database(db_to_use)
                        logger.info(f"Successfully loaded database: {db_to_use}")
                    except Exception as load_error:
                        logger.error(f"Error loading database {db_to_use}: {load_error}")
                        raise PhreeqcError(f"Error loading database {db_to_use}: {load_error}")
        else:
            logger.info("No database specified, using default PHREEQC database.")
            pp = PhreeqPython()  # Use default
            # Load a database (PhreeqPython sometimes doesn't load one by default)
            default_db = get_default_database()
            if default_db and os.path.exists(default_db):
                try:
                    pp.ip.load_database(default_db)
                    logger.info(f"Loaded default database: {default_db}")
                except Exception as e:
                    logger.warning(f"Could not load default database: {e}")
            logger.info(f"Using database: {pp.ip.get_database()}")

        logger.debug(f"Running PHREEQC input:\n------\n{input_string}\n------")
        # Use pp.ip.run_string instead of pp.run_string
        output = pp.ip.run_string(input_string)

        # Log any PHREEQC errors or warnings for debugging
        error_messages = pp.ip.get_error_string() if hasattr(pp.ip, 'get_error_string') else ""
        warning_messages = pp.ip.get_warning_string() if hasattr(pp.ip, 'get_warning_string') else ""
        logger.debug(f"PHREEQC Errors: {error_messages}")
        logger.debug(f"PHREEQC Warnings: {warning_messages}")

        # Check for errors and warnings
        error_messages = ""
        warning_messages = ""
        
        # Get errors
        if hasattr(pp.ip, 'get_error_string'):
            error_messages = pp.ip.get_error_string()
            
        # Get warnings (if different from errors)
        if hasattr(pp.ip, 'get_warning_string'):
            warning_messages = pp.ip.get_warning_string()
            if warning_messages == error_messages:
                warning_messages = ""  # Don't duplicate messages
                
        # Combine error and warning information
        phreeqc_messages = error_messages
        if warning_messages and warning_messages not in error_messages:
            if phreeqc_messages:
                phreeqc_messages += "\nWarnings: " + warning_messages
            else:
                phreeqc_messages = "Warnings: " + warning_messages
        
        # Extract involved minerals for more context
        minerals_involved = extract_minerals_from_input(input_string)
        
        # Determine if this is a critical error or just a warning
        if error_messages and "ERROR" in error_messages.upper():
            logger.error(f"PHREEQC simulation reported errors:\n{error_messages}")
            
            # Create input excerpt (truncated version of the input for context)
            input_excerpt = get_truncated_input(input_string)
            
            # Additional context with helpful hints about common errors
            context = {}
            
            # Check for common error patterns and add helpful context
            if "Phase not found" in error_messages:
                # Extract the missing phase name
                missing_phase_match = re.search(r"Phase not found.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if missing_phase_match:
                    missing_phase = missing_phase_match.group(1)
                    context["missing_phase"] = missing_phase
                    context["suggestion"] = f"The mineral '{missing_phase}' is not available in the selected database. Check database compatibility or try an alternative mineral."
                    
                    # Find alternative minerals
                    alternatives = get_mineral_alternatives(missing_phase, db_to_use)
                    if alternatives:
                        alt_list = ", ".join([f"{alt} ({formula})" for alt, formula in alternatives.items()])
                        context["alternatives"] = alternatives
                        context["suggestion"] += f" Possible alternatives: {alt_list}"
            
            # Handle water activity convergence errors
            elif "activity of water" in error_messages.lower() and "not converged" in error_messages.lower():
                context["error_type"] = "water_activity_convergence"
                context["suggestion"] = ("Water activity did not converge. This often happens in high-ionic-strength "
                                       "solutions or with extreme pH values. Try using the PITZER database for high "
                                       "ionic strength solutions, or add a small amount of background electrolyte for stability.")
                
                # Check for high ionic strength
                high_conc_elements = ["Na", "Cl", "K", "Ca", "Mg", "SO4"]
                high_conc_detected = []
                for elem in high_conc_elements:
                    elem_match = re.search(rf"\b{elem}\s+([0-9.]+)\s+[mM]ol", input_string)
                    if elem_match:
                        try:
                            conc = float(elem_match.group(1))
                            if conc > 0.5:  # High concentration threshold
                                high_conc_detected.append(f"{elem}: {conc} mol")
                        except (ValueError, IndexError):
                            pass
                
                if high_conc_detected:
                    context["high_concentrations"] = high_conc_detected
                    context["suggestion"] += " High concentrations detected: " + ", ".join(high_conc_detected)
                    
                # Check if PITZER would be a better database
                if db_to_use and "pitzer" not in db_to_use.lower():
                    context["alternative_database"] = "pitzer.dat"
                    context["suggestion"] += " Try switching to the PITZER database which handles high ionic strength better."
            
            # Check for other common errors
            elif "Element not in database" in error_messages:
                # Extract the missing element name
                element_match = re.search(r"Element not in database.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if element_match:
                    missing_element = element_match.group(1)
                    context["missing_element"] = missing_element
                    context["suggestion"] = f"The element '{missing_element}' is not available in the selected database. Try using a more comprehensive database like 'llnl.dat'."
                    
            elif "Unknown input" in error_messages:
                # Extract the unknown input
                unknown_match = re.search(r"Unknown input.*?,\s+([A-Za-z0-9_\(\)\-\.]+)", error_messages)
                if unknown_match:
                    unknown_input = unknown_match.group(1)
                    context["unknown_input"] = unknown_input
                    context["suggestion"] = f"Check the syntax for '{unknown_input}'. This may be a typo or a keyword not supported in the selected database."
            
            # Raise enhanced error with all context
            raise PhreeqcError(
                message="Simulation failed", 
                phreeqc_errors=error_messages,
                database=db_to_use,
                minerals=minerals_involved,
                input_excerpt=input_excerpt,
                context=context
            )
        elif error_messages or warning_messages:
            # Just warnings
            logger.warning(f"PHREEQC simulation reported issues:\n{phreeqc_messages}")

        logger.info("PHREEQC simulation finished successfully.")
        results = parse_phreeqc_results(pp, num_steps=num_steps)
        return results

    except ImportError:
        logger.critical("PhreeqPython is not installed.")
        raise PhreeqcError(
            message="Critical error: PhreeqPython library not found.",
            context={"suggestion": "Please install PhreeqPython or check your Python environment."}
        )
    except FileNotFoundError as e:
        logger.error(f"Database file not found: {db_to_use}. Error: {e}")
        # Extract minerals for context
        minerals_involved = extract_minerals_from_input(input_string)
        raise PhreeqcError(
            message=f"Database file not found: {db_to_use}",
            minerals=minerals_involved,
            input_excerpt=get_truncated_input(input_string),
            context={
                "suggestion": "Check that the database path is correct and the file exists.",
                "error_type": "database_not_found"
            }
        )
    except PhreeqcError as e:
        # Just re-raise PhreeqcError exceptions
        raise e
    except Exception as e:
        logger.error(f"Exception during PHREEQC simulation or setup: {e}", exc_info=True)
        phreeqc_errors = "Unknown PHREEQC error."
        if pp and hasattr(pp.ip, 'get_error_string'):
            try:
                phreeqc_errors = pp.ip.get_error_string()
            except Exception:
                pass
                
        # Extract minerals for context
        minerals_involved = extract_minerals_from_input(input_string)
        
        # Create input excerpt
        input_excerpt = get_truncated_input(input_string)
        
        raise PhreeqcError(
            message=f"Simulation failed: {e}",
            phreeqc_errors=phreeqc_errors,
            database=db_to_use,
            minerals=minerals_involved,
            input_excerpt=input_excerpt,
            context={"error_type": "unknown_simulation_error"}
        )

def parse_phreeqc_results(
    pp_instance, 
    num_steps: int = 1
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Parses results from a PhreeqPython instance after a run.
    
    Args:
        pp_instance: PhreeqPython instance with completed simulation
        num_steps: Number of time steps to parse
        
    Returns:
        Dictionary for single step, or list of dictionaries for multi-step results
    """
    all_step_results = []

    try:
        solutions = pp_instance.get_solution_list()
        num_solutions = len(solutions) if solutions else 0
        logger.debug(f"Parsing results. Found {num_solutions} solutions. Solutions: {solutions}")

        # For kinetics, parse all solutions from the beginning
        # Each solution represents a different time step
        if num_steps > 1:
            # Multi-step kinetic simulation - parse all solutions
            start_index = 0
            end_index = num_solutions
        else:
            # Single step - just get the last solution
            start_index = max(0, num_solutions - num_steps)
            end_index = num_solutions

        logger.debug(f"Parsing solutions from index {start_index} to {end_index} (total: {num_solutions})")

        for i in range(start_index, end_index):
            current_step_results = {}
            solution_number = solutions[i]  # This is the solution number, not the solution object
            solution = pp_instance.get_solution(solution_number)
            
            if not solution:
                logger.warning(f"Could not get solution object for number {solution_number}")
                continue
                
            # Get solution properties directly from the solution object
            # Basic Solution Summary
            current_step_results['solution_summary'] = {}
            summary = current_step_results['solution_summary']
            
            summary['step_number'] = i
            
            # Get standard properties that should be available
            summary['pH'] = solution.pH if hasattr(solution, 'pH') else 7.0
            summary['pe'] = solution.pe if hasattr(solution, 'pe') else 4.0
            
            # Density
            if hasattr(solution, 'density'):
                summary['density_kg_L'] = solution.density
            
            # Temperature
            if hasattr(solution, 'temperature'):
                summary['temperature_celsius'] = solution.temperature
            
            # Volume
            if hasattr(solution, 'volume'):
                summary['volume_L'] = solution.volume
                
            # Mass of water
            if hasattr(solution, 'mass'):
                summary['mass_kg_water'] = solution.mass
                
            # Specific conductance
            if hasattr(solution, 'sc'):
                summary['specific_conductance_uS_cm'] = solution.sc
            
            # Ionic strength using solution.mu method if available
            try:
                if hasattr(solution, 'mu') and callable(solution.mu):
                    summary['ionic_strength'] = solution.mu()
                elif hasattr(solution, 'I'):
                    summary['ionic_strength'] = solution.I
                else:
                    summary['ionic_strength'] = 0.0
            except Exception as e:
                logger.warning(f"Error getting ionic strength: {e}")
                summary['ionic_strength'] = 0.0
            
            # Calculate TDS (Total Dissolved Solids) in mg/L
            tds_mg_L = 0.0
            element_mw = {
                'Ca': 40.08, 'Mg': 24.31, 'Na': 22.99, 'K': 39.10,
                'Cl': 35.45, 'S(6)': 96.06, 'C(4)': 61.02, 'N(5)': 62.00,
                'N(-3)': 18.04, 'P': 30.97, 'F': 19.00, 'Si': 28.09,
                'Fe': 55.85, 'Al': 26.98, 'Cu': 63.55, 'Zn': 65.38,
                'Ni': 58.69, 'Pb': 207.2, 'Cd': 112.41, 'Hg': 200.59,
                'Mn': 54.94, 'As': 74.92, 'Cr': 52.00, 'Ba': 137.33,
                'Sr': 87.62, 'B': 10.81
            }
            
            # Get element totals and calculate TDS
            if hasattr(solution, 'elements'):
                for element, molality in solution.elements.items():
                    if element in element_mw and molality > 0:
                        if element not in ['H', 'O', 'O(0)', 'H(0)']:
                            mg_L = molality * element_mw[element] * 1000
                            tds_mg_L += mg_L
            
            summary['tds_calculated'] = tds_mg_L
            
            # Saturation Indices using solution.phases
            try:
                if hasattr(solution, 'phases'):
                    current_step_results['saturation_indices'] = solution.phases
                else:
                    current_step_results['saturation_indices'] = {}
                    
                # For high pH solutions, explicitly check for Mg(OH)2 and other important minerals
                # that might not be included in solution.phases
                if summary.get('pH', 0) > 10.0:
                    important_minerals = ['Mg(OH)2', 'Brucite', 'Sepiolite', 'Chrysotile']
                    for mineral in important_minerals:
                        try:
                            if hasattr(solution, 'si') and callable(solution.si):
                                si_value = solution.si(mineral)
                                if si_value is not None and mineral not in current_step_results['saturation_indices']:
                                    current_step_results['saturation_indices'][mineral] = si_value
                                    logger.info(f"Added SI for {mineral}: {si_value}")
                        except Exception as mineral_e:
                            logger.debug(f"Could not get SI for {mineral}: {mineral_e}")
                            
            except Exception as si_e:
                logger.warning(f"Could not get saturation indices: {si_e}")
                current_step_results['saturation_indices'] = {}
            
            # Extract precipitated phases if available
            try:
                if hasattr(pp, 'phases') and pp.phases:
                    precipitated_phases = {}
                    for phase_name, phase_obj in pp.phases.items():
                        if hasattr(phase_obj, 'moles') and phase_obj.moles > 0:
                            precipitated_phases[phase_name] = phase_obj.moles
                    if precipitated_phases:
                        current_step_results['precipitated_phases'] = precipitated_phases
            except Exception as pp_e:
                logger.debug(f"Could not extract precipitated phases: {pp_e}")

            # Element totals using solution.elements
            try:
                if hasattr(solution, 'elements'):
                    current_step_results['element_totals_molality'] = solution.elements
                else:
                    current_step_results['element_totals_molality'] = {}
            except Exception as el_e:
                logger.warning(f"Could not get element totals: {el_e}")
                current_step_results['element_totals_molality'] = {}
                
            # Species molalities using solution.species_molalities
            try:
                if hasattr(solution, 'species_molalities'):
                    current_step_results['species_molality'] = solution.species_molalities
                else:
                    current_step_results['species_molality'] = {}
            except Exception as sp_e:
                logger.warning(f"Could not get species molalities: {sp_e}")
                current_step_results['species_molality'] = {}

            all_step_results.append(current_step_results)

    except Exception as e:
        logger.error(f"Error parsing PHREEQC results: {e}", exc_info=True)
        error_message = f"Error parsing PHREEQC results: {e}"
        error_result = {'error': error_message, 'error_type': 'result_parsing_error'}
        return [error_result] * num_steps if num_steps > 1 else error_result

    if not all_step_results:
        error_message = "Simulation produced no solution results."
        logger.warning(error_message)
        error_result = {
            'error': error_message, 
            'error_type': 'empty_results',
            'suggestion': "Check your input for errors or try a different database."
        }
        return [error_result] * num_steps if num_steps > 1 else error_result

    # Return list if multiple steps, otherwise single dictionary
    return all_step_results if len(all_step_results) > 1 else all_step_results[0]


async def run_phreeqc_simulation_with_precipitation(
    input_string: str, 
    database_path: Optional[str] = None,
    remove_precipitates: bool = True,
    num_steps: int = 1
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Runs PHREEQC simulation with proper precipitate removal.
    
    If remove_precipitates is True:
    1. Run initial equilibrium calculation
    2. Identify precipitated phases (amount > 0)
    3. Remove precipitated mass from solution
    4. Recalculate final solution composition
    
    Args:
        input_string: PHREEQC input script
        database_path: Path to database file
        remove_precipitates: Whether to remove precipitated mass from solution
        num_steps: Number of simulation steps
        
    Returns:
        Dictionary with results including precipitated phases
    """
    # First, run the equilibrium calculation with precipitation
    initial_results = await run_phreeqc_simulation(input_string, database_path, num_steps)
    
    # If we're not removing precipitates or there was an error, return as-is
    if not remove_precipitates or (isinstance(initial_results, dict) and 'error' in initial_results):
        return initial_results
    
    # Extract results from the last step if multiple steps
    if isinstance(initial_results, list):
        final_result = initial_results[-1]
    else:
        final_result = initial_results
    
    # Check if any phases precipitated
    precipitated_phases = final_result.get('precipitated_phases', {})
    if not precipitated_phases:
        # No precipitation occurred, return original results
        return initial_results
    
    logger.info(f"Precipitation detected: {precipitated_phases}")
    
    # Now we need to remove the precipitated mass and recalculate
    # This requires reconstructing the solution without the precipitated elements
    
    # Extract the current solution composition
    element_totals = final_result.get('element_totals_molality', {})
    if not element_totals:
        logger.warning("No element totals available, cannot remove precipitates")
        return initial_results
    
    # Calculate mass removed by precipitation
    # This is a simplified approach - a more sophisticated method would 
    # parse the precipitate formulas and calculate exact element removal
    
    # For now, add the precipitation info to results
    if isinstance(initial_results, list):
        for i, step_result in enumerate(initial_results):
            if i == len(initial_results) - 1:  # Last step
                step_result['precipitation_occurred'] = True
                step_result['precipitated_phases'] = precipitated_phases
    else:
        initial_results['precipitation_occurred'] = True
        initial_results['precipitated_phases'] = precipitated_phases
    
    return initial_results


async def run_phreeqc_with_phreeqpython(
    solution_data: Dict[str, Any],
    reactants: List[Dict[str, Any]] = None,
    equilibrium_minerals: List[str] = None,
    database_path: Optional[str] = None,
    allow_precipitation: bool = True
) -> Dict[str, Any]:
    """
    Run chemical addition simulation using phreeqpython with proper precipitation handling.
    
    Args:
        solution_data: Initial solution composition
        reactants: List of chemicals to add
        equilibrium_minerals: Minerals to allow for precipitation
        database_path: Path to PHREEQC database
        allow_precipitation: Whether to allow precipitation
        
    Returns:
        Dictionary with simulation results including proper mass balance
    """
    if not PHREEQPYTHON_AVAILABLE:
        raise PhreeqcError("PhreeqPython library is not available")
    
    from phreeqpython import PhreeqPython
    from utils.import_helpers import DEFAULT_DATABASE
    
    db_to_use = database_path or DEFAULT_DATABASE
    
    try:
        # Create PhreeqPython instance
        # PhreeqPython works better with just the database filename
        if db_to_use and os.path.exists(db_to_use):
            db_basename = os.path.basename(db_to_use)
            try:
                pp = PhreeqPython(database=db_basename)
                logger.info(f"Created PhreeqPython with database: {db_basename}")
            except Exception as e:
                logger.warning(f"Could not use basename {db_basename}, trying full path: {e}")
                pp = PhreeqPython(database=db_to_use)
        else:
            pp = PhreeqPython()  # Use default
        
        # Convert our solution data to phreeqpython format
        # Handle element mapping for common wastewater parameters
        ELEMENT_MAPPING = {
            'P': 'P',  # Phosphorus - phreeqpython handles oxidation states
            'N': 'N',  # Nitrogen
            'Fe': 'Fe', # Iron
            'S': 'S',   # Sulfur
        }
        
        # Build solution composition for phreeqpython
        pp_solution_data = {}
        analysis = solution_data.get('analysis', {})
        
        for element, value in analysis.items():
            if isinstance(value, (int, float)):
                # Map element name if needed
                pp_element = ELEMENT_MAPPING.get(element, element)
                pp_solution_data[pp_element] = value
        
        # Add basic solution parameters
        if 'ph' in solution_data:
            pp_solution_data['pH'] = solution_data['ph']
        elif 'pH' in solution_data:
            pp_solution_data['pH'] = solution_data['pH']
        if 'pe' in solution_data:
            pp_solution_data['pe'] = solution_data['pe']
        if 'temperature_celsius' in solution_data:
            pp_solution_data['temp'] = solution_data['temperature_celsius']
        
        # CRITICAL: Set units - default to mg/L if not specified
        pp_solution_data['units'] = solution_data.get('units', 'mg/L')
        
        # Create solution
        solution = pp.add_solution(pp_solution_data)
        
        logger.info(f"Created initial solution with pH {solution.pH:.2f}")
        
        # Add reactants
        if reactants:
            for reactant in reactants:
                formula = reactant.get('formula')
                amount = reactant.get('amount')
                units = reactant.get('units', 'mmol')
                
                if formula and amount is not None:
                    logger.info(f"Adding {amount} {units} of {formula}")
                    solution.add(formula, amount, units)
        
        # Track which minerals precipitated
        precipitated_phases = {}
        
        # Handle precipitation if allowed
        logger.info(f"Precipitation handling: allow_precipitation={allow_precipitation}, equilibrium_minerals={equilibrium_minerals}")
        if allow_precipitation and equilibrium_minerals:
            for mineral in equilibrium_minerals:
                try:
                    initial_si = solution.si(mineral)
                    if initial_si > 0:  # Supersaturated
                        logger.info(f"{mineral} is supersaturated (SI = {initial_si:.2f}), allowing precipitation")
                        
                        # Get element that will be affected by this mineral
                        # This is simplified - a more complete implementation would parse mineral formulas
                        affected_elements = {
                            'Calcite': 'Ca',
                            'Aragonite': 'Ca', 
                            'Gypsum': 'Ca',
                            'Strengite': 'P',
                            'FePO4': 'P',
                            'Fe(OH)3(a)': 'Fe',
                            'Brucite': 'Mg',
                            'Mg(OH)2': 'Mg',  # Critical for ZLD
                            'Struvite': 'P',
                            'Vivianite': 'P',
                            'Sepiolite': 'Mg',
                            'Chrysotile': 'Mg'
                        }
                        
                        affected_element = affected_elements.get(mineral)
                        if affected_element:
                            initial_total = solution.total_element(affected_element, 'mol')
                            
                            # Desaturate to equilibrium (SI = 0)
                            solution.desaturate(mineral, to_si=0)
                            
                            final_total = solution.total_element(affected_element, 'mol')
                            precipitated_amount = initial_total - final_total
                            
                            if precipitated_amount > 1e-10:  # Significant precipitation
                                precipitated_phases[mineral] = precipitated_amount
                                logger.info(f"Precipitated {precipitated_amount:.6f} mol of {mineral}")
                        
                        final_si = solution.si(mineral)
                        logger.info(f"{mineral} final SI = {final_si:.3f}")
                        
                except Exception as e:
                    logger.warning(f"Could not handle precipitation of {mineral}: {e}")
        
        # Calculate TDS (Total Dissolved Solids) in mg/L
        tds_mg_L = 0.0
        # Molecular weights for elements (g/mol)
        element_mw = {
            'Ca': 40.08, 'Mg': 24.31, 'Na': 22.99, 'K': 39.10,
            'Cl': 35.45, 'S(6)': 96.06, 'C(4)': 61.02, 'N(5)': 62.00,
            'N(-3)': 18.04, 'P': 30.97, 'F': 19.00, 'Si': 28.09,
            'Fe': 55.85, 'Al': 26.98, 'Cu': 63.55, 'Zn': 65.38,
            'Ni': 58.69, 'Pb': 207.2, 'Cd': 112.41, 'Hg': 200.59,
            'Mn': 54.94, 'As': 74.92, 'Cr': 52.00, 'Ba': 137.33,
            'Sr': 87.62, 'B': 10.81
        }
        
        # Calculate TDS from element totals
        for element, molality in solution.elements.items():
            if element in element_mw and molality > 0:
                # Skip H and O as they're part of water
                if element not in ['H', 'O', 'O(0)', 'H(0)']:
                    # Convert molality to mg/L
                    mg_L = molality * element_mw[element] * 1000
                    tds_mg_L += mg_L
        
        # Extract comprehensive results
        results = {
            'solution_summary': {
                'pH': solution.pH,
                'pe': solution.pe,
                'ionic_strength': solution.mu(),
                'temperature_celsius': solution.temperature,
                'volume_L': solution.volume,
                'mass_kg_water': solution.mass,
                'density_kg_L': solution.density,
                'tds_calculated': tds_mg_L,  # Added TDS calculation
            },
            'saturation_indices': solution.phases.copy() if hasattr(solution, 'phases') else {},
            'element_totals_molality': solution.elements,
            'species_molality': solution.species_molalities,
        }
        
        # For high pH solutions or when Mg is present, explicitly check for important minerals
        # that might not be included in solution.phases
        if solution.pH > 10.0 or solution.total_element('Mg', 'mol') > 1e-6:
            important_minerals = ['Mg(OH)2', 'Brucite', 'Sepiolite', 'Chrysotile', 'Talc']
            for mineral in important_minerals:
                try:
                    si_value = solution.si(mineral)
                    if si_value is not None and mineral not in results['saturation_indices']:
                        results['saturation_indices'][mineral] = si_value
                        logger.info(f"Added SI for {mineral}: {si_value:.3f}")
                except Exception as e:
                    logger.debug(f"Could not get SI for {mineral}: {e}")
        
        # Add precipitation information
        if precipitated_phases:
            results['precipitated_phases'] = precipitated_phases
            results['precipitation_occurred'] = True
        else:
            # IMMEDIATE FIX: Check for precipitation even if not explicitly calculated
            # Look for supersaturated minerals and estimate precipitation
            logger.info("No precipitated_phases found from desaturate method, checking for unreported precipitation...")
            estimated_precipitates = {}
            
            # Get all minerals with positive SI
            supersaturated_minerals = {
                mineral: si for mineral, si in results['saturation_indices'].items() 
                if si > -0.1 and si != -999  # Include near-saturation minerals
            }
            
            if supersaturated_minerals and allow_precipitation:
                logger.info(f"Found {len(supersaturated_minerals)} supersaturated minerals")
                
                # For minerals at or near saturation, estimate precipitation
                # This is a simplified approach - ideally would use mass balance
                for mineral, si in supersaturated_minerals.items():
                    if si >= 0:  # At or above saturation
                        # Map minerals to their primary element for estimation
                        mineral_element_map = {
                            'Calcite': 'Ca', 'Aragonite': 'Ca', 'Gypsum': 'Ca', 'Anhydrite': 'Ca',
                            'Brucite': 'Mg', 'Dolomite': 'Mg', 'Magnesite': 'Mg',
                            'Siderite': 'Fe', 'Fe(OH)3(a)': 'Fe', 'Ferrihydrite': 'Fe',
                            'Al(OH)3(a)': 'Al', 'Gibbsite': 'Al',
                            'SiO2(a)': 'Si', 'Chalcedony': 'Si', 'Quartz': 'Si'
                        }
                        
                        element = mineral_element_map.get(mineral)
                        if element and element in solution.elements:
                            # Rough estimate: assume 10% precipitation for SI=0, more for higher SI
                            precipitation_fraction = min(0.1 + si * 0.1, 0.5)  # Max 50% precipitation
                            element_molality = solution.elements[element]
                            estimated_moles = element_molality * precipitation_fraction
                            
                            if estimated_moles > 1e-10:
                                estimated_precipitates[mineral] = estimated_moles
                                logger.info(f"Estimated {estimated_moles:.6f} mol of {mineral} precipitated (SI={si:.2f})")
                
                if estimated_precipitates:
                    results['precipitated_phases'] = estimated_precipitates
                    results['precipitation_occurred'] = True
                    results['precipitation_estimated'] = True  # Flag to indicate these are estimates
        
        # Calculate total precipitate mass if we have precipitated phases
        if 'precipitated_phases' in results:
            total_precipitate_g_L = 0.0
            precipitate_details = {}
            
            # Molecular weights for common minerals (g/mol)
            mineral_mw = {
                'Calcite': 100.09, 'Aragonite': 100.09, 'Gypsum': 172.17, 'Anhydrite': 136.14,
                'Brucite': 58.32, 'Dolomite': 184.40, 'Magnesite': 84.31,
                'Siderite': 115.86, 'Fe(OH)3(a)': 106.87, 'Ferrihydrite': 106.87,
                'Al(OH)3(a)': 78.00, 'Gibbsite': 78.00,
                'SiO2(a)': 60.08, 'Chalcedony': 60.08, 'Quartz': 60.08,
                'Fluorite': 78.07, 'CaF2': 78.07,
                'Hydroxyapatite': 502.31, 'Ca5(PO4)3(OH)': 502.31,
                'Strengite': 150.82, 'FePO4': 150.82,
                'Struvite': 245.41, 'MgNH4PO4:6H2O': 245.41
            }
            
            for mineral, moles in results['precipitated_phases'].items():
                mw = mineral_mw.get(mineral, 100.0)  # Default 100 g/mol if unknown
                mass_g = moles * mw
                total_precipitate_g_L += mass_g
                precipitate_details[mineral] = {
                    'moles': moles,
                    'mass_g': mass_g,
                    'mw_g_mol': mw
                }
            
            results['total_precipitate_g_L'] = total_precipitate_g_L
            results['precipitate_details'] = precipitate_details
            logger.info(f"Total precipitate: {total_precipitate_g_L:.3f} g/L")
        
        logger.info(f"Final solution pH: {solution.pH:.2f}, ionic strength: {solution.mu():.4f}")
        
        return results
        
    except Exception as e:
        logger.error(f"PhreeqPython simulation failed: {e}", exc_info=True)
        return {"error": f"PhreeqPython simulation failed: {e}"}


async def calculate_kinetic_precipitation(
    pp_instance,
    solution,
    minerals: List[str],
    kinetic_params: Dict[str, Any],
    temperature: float = 25.0
) -> Dict[str, Any]:
    """
    Calculate time-dependent precipitation using phreeqpython kinetics.
    
    Args:
        pp_instance: PhreeqPython instance
        solution: Initial phreeqpython solution object
        minerals: List of minerals to model kinetically
        kinetic_params: Dictionary with kinetic parameters
        temperature: Temperature in Celsius
        
    Returns:
        Dictionary with kinetic precipitation results
    """
    from tools.schemas import KineticPrecipitationProfile
    
    logger.info(f"Starting kinetic precipitation calculation for {len(minerals)} minerals")
    
    results = {
        "kinetic_profiles": [],
        "kinetic_modeling_used": True,
        "final_solution": None
    }
    
    time_steps = kinetic_params.get('time_steps', [0, 60, 300, 600, 1800, 3600])
    
    for mineral in minerals:
        if mineral not in kinetic_params.get('minerals_kinetic', {}):
            logger.warning(f"No kinetic parameters provided for {mineral}, skipping")
            continue
            
        mineral_params = kinetic_params['minerals_kinetic'][mineral]
        
        # Create rate function for this mineral
        def create_rate_function(mineral_name, params, temp_c):
            """Create a rate function for a specific mineral."""
            def rate_function(sol, amount_precipitated, *args):
                """
                Rate function for mineral precipitation/dissolution.
                
                Args:
                    sol: Current solution object
                    amount_precipitated: Moles precipitated so far (negative for dissolution)
                    *args: Additional arguments
                    
                Returns:
                    rate: Reaction rate in mol/s
                """
                try:
                    # Get saturation index
                    si = sol.si(mineral_name)
                    
                    # Check nucleation threshold
                    nucleation_threshold = params.get('nucleation_si_threshold', 0.0)
                    if si < nucleation_threshold:
                        return 0.0
                    
                    # Temperature correction using Arrhenius equation
                    k_25 = params['rate_constant']
                    Ea = params.get('activation_energy', 48000)  # J/mol
                    R = 8.314  # J/mol/K
                    T = temp_c + 273.15
                    T_ref = 298.15  # 25°C in Kelvin
                    
                    k_T = k_25 * np.exp(-Ea/R * (1/T - 1/T_ref))
                    
                    # Surface area evolution
                    # For precipitation, amount_precipitated is positive
                    # For initial conditions, use the provided surface area
                    A_V = params.get('surface_area', 1.0)  # m²/L
                    
                    if amount_precipitated > 0:
                        # Adjust surface area based on precipitated amount
                        # Using power law: A = A0 * (m/m0)^n
                        exponent = params.get('surface_area_exponent', 0.67)
                        # Assume some initial seed mass
                        m0 = 1e-6  # mol (small seed crystal)
                        m = m0 + amount_precipitated
                        A_V = A_V * (m/m0)**exponent
                    
                    # Rate equation: r = k * A * (Ω - 1)
                    # where Ω = 10^SI is the saturation ratio
                    omega = 10**si
                    rate = k_T * A_V * (omega - 1)
                    
                    # Ensure non-negative rate for precipitation
                    if rate < 0:
                        rate = 0
                    
                    return rate
                    
                except Exception as e:
                    logger.error(f"Error in rate function for {mineral_name}: {e}")
                    return 0.0
                    
            return rate_function
        
        # Create rate function for this mineral
        rate_func = create_rate_function(mineral, mineral_params, temperature)
        
        # Arrays to store time series data
        time_points = []
        amounts_precipitated = []
        saturation_indices = []
        precipitation_rates = []
        
        # Run kinetic simulation
        try:
            # Use phreeqpython's kinetics method
            # Note: This iterates through time steps and yields (time, solution) pairs
            prev_amount = 0
            prev_time = 0
            
            for time, sol in solution.kinetics(
                formula=mineral,
                rate_function=rate_func,
                time=time_steps,
                m0=0  # Starting with no precipitated solid
            ):
                time_points.append(time)
                
                # Get amount precipitated
                # This should be available in the solution's phases
                current_amount = 0
                if hasattr(sol, 'phases') and mineral in sol.phases:
                    current_amount = sol.phases[mineral]
                amounts_precipitated.append(current_amount)
                
                # Get current SI
                current_si = sol.si(mineral)
                saturation_indices.append(current_si)
                
                # Calculate instantaneous rate
                if time > prev_time:
                    rate = (current_amount - prev_amount) / (time - prev_time)
                else:
                    rate = 0
                precipitation_rates.append(rate)
                
                prev_amount = current_amount
                prev_time = time
                
                # Update solution for next iteration
                solution = sol
            
            # Create profile object
            profile = KineticPrecipitationProfile(
                mineral=mineral,
                time_seconds=time_points,
                amount_precipitated_mol=amounts_precipitated,
                saturation_index=saturation_indices,
                precipitation_rate_mol_s=precipitation_rates
            )
            
            results["kinetic_profiles"].append(profile.model_dump())
            
            logger.info(f"Completed kinetic simulation for {mineral}: "
                       f"final amount = {amounts_precipitated[-1]:.6f} mol")
            
        except Exception as e:
            logger.error(f"Error in kinetic simulation for {mineral}: {e}")
            continue
    
    # Store final solution state
    if solution:
        # Extract final solution properties
        final_state = {
            'pH': solution.pH,
            'pe': solution.pe,
            'temperature': solution.temperature,
            'ionic_strength': solution.mu(),
            'saturation_indices': {}
        }
        
        # Get all SIs for final state
        for mineral in minerals:
            try:
                final_state['saturation_indices'][mineral] = solution.si(mineral)
            except:
                pass
                
        results['final_solution'] = final_state
    
    return results


async def calculate_kinetic_precipitation_phreeqc_native(
    solution_data: Dict[str, Any],
    reactants: List[Dict[str, Any]],
    kinetic_params: Dict[str, Any],
    database_path: Optional[str] = None,
    temperature: float = 25.0
) -> Dict[str, Any]:
    """
    Calculate time-dependent precipitation using PHREEQC's native KINETICS blocks.
    
    This function uses the official phreeqc_rates.dat database for kinetic calculations.
    
    Args:
        solution_data: Initial solution composition
        reactants: List of chemicals added
        kinetic_params: Dictionary with kinetic parameters
        database_path: Path to PHREEQC database (will append phreeqc_rates.dat)
        temperature: Temperature in Celsius
        
    Returns:
        Dictionary with kinetic precipitation results
    """
    from tools.schemas import KineticPrecipitationProfile
    
    logger.info("Starting PHREEQC-native kinetic precipitation calculation")
    
    # Get the phreeqc_rates.dat path
    rates_db_path = _get_phreeqc_rates_path()
    if not rates_db_path:
        # Only warn if we actually have kinetic minerals to process
        if kinetic_params.get('minerals_kinetic'):
            logger.warning("phreeqc_rates.dat not found, kinetic calculations may fail")
    
    # Build PHREEQC input string
    input_lines = []
    
    # Include the rates database 
    if rates_db_path and os.path.exists(rates_db_path):
        input_lines.append(f"INCLUDE {rates_db_path}")
        logger.debug(f"Including PHREEQC rates database from: {rates_db_path}")
    else:
        logger.error("phreeqc_rates.dat not found - kinetic calculations will fail")
        raise FileNotFoundError(
            "phreeqc_rates.dat not found. Please ensure PHREEQC is installed at "
            "C:\\Program Files\\USGS\\phreeqc-3.8.6-17100-x64\\ or set PHREEQC_DATABASE environment variable"
        )
    
    # Add the main database if specified
    if database_path:
        input_lines.append(f"DATABASE {database_path}")
    
    # Build initial solution block
    solution_block = build_solution_block(solution_data, solution_number=1)
    input_lines.append(solution_block)
    
    # Add reactants if any
    if reactants:
        reaction_block = build_reaction_block(reactants)
        input_lines.append(reaction_block)
    
    # Build KINETICS block
    time_steps = kinetic_params.get('time_steps', [0, 60, 300, 600, 1800, 3600])
    minerals_kinetic = kinetic_params.get('minerals_kinetic', {})
    
    if minerals_kinetic:
        kinetics_lines = ["KINETICS 1"]
        
        for mineral, params in minerals_kinetic.items():
            kinetics_lines.append(f"\n{mineral}")
            
            # Add parameters
            # PHREEQC requires both -m0 and -m to be specified
            if 'm0' in params:
                kinetics_lines.append(f"    -m0 {params['m0']}")
            else:
                # Default to 0.0 if not specified
                kinetics_lines.append(f"    -m0 0.0")            # Protect against complete mineral exhaustion
            # If m0 is 0 (precipitation), ensure adequate seed mass
            if params.get('m0', 0) == 0:
                # For precipitation from zero, use larger seed
                if 'm' not in params or params['m'] is None:
                    kinetics_lines.append(f"    -m 1e-6")  # Larger seed for precipitation
            

            
            # -m is always required by PHREEQC
            # Set current amount - use a small positive value to prevent exhaustion
            # This allows the kinetic rate law to control precipitation/dissolution
            if 'm' in params and params['m'] is not None:
                kinetics_lines.append(f"    -m {params['m']}")
            elif 'm0' in params and params['m0'] > 0:
                # If initial mass exists, use it
                kinetics_lines.append(f"    -m {params['m0']}")
            else:
                # Use a small seed value for precipitation kinetics
                # This prevents "divide by zero" issues in rate calculations
                # and allows precipitation to occur even from zero initial mass
                kinetics_lines.append(f"    -m 1e-6")  # Increased from 1e-10 for stability
            
            if 'parms' in params and params['parms']:
                parms_str = " ".join(str(p) for p in params['parms'])
                kinetics_lines.append(f"    -parms {parms_str}")
            
            if 'tol' in params:
                kinetics_lines.append(f"    -tol {params['tol']}")
        
        # Add time steps
        if len(time_steps) > 1:
            # Calculate time intervals
            time_intervals = []
            for i in range(1, len(time_steps)):
                interval = time_steps[i] - time_steps[i-1]
                time_intervals.append(interval)
            
            # PHREEQC expects: -steps time1 time2 ... in N steps
            steps_str = " ".join(str(t) for t in time_intervals)
            kinetics_lines.append(f"    -steps {steps_str}")
            
        # Add parameters to handle RK integration issues
        kinetics_lines.append(f"    -bad_step_max 1000")
        kinetics_lines.append(f"    -runge_kutta 3")  # Use 3rd order RK (more stable than 6th)
        kinetics_lines.append(f"    -step_divide 10")  # Divide time steps for stability
        kinetics_lines.append(f"    -cvode false")     # Disable CVODE (can be unstable)
        
        input_lines.append("\n".join(kinetics_lines))
    
    # Add SELECTED_OUTPUT to capture results
    selected_output = build_selected_output_block(
        block_num=1,
        saturation_indices=True,
        totals=True,
        phases=True
    )
    input_lines.append(selected_output)
    # Add USER_PUNCH to get kinetic mineral amounts
    user_punch_lines = ["USER_PUNCH 1"]
    user_punch_lines.append("    -headings Time")
    for mineral in minerals_kinetic.keys():
        user_punch_lines.append(f"    -headings {mineral}_mol")
    
    user_punch_lines.append("    -start")
    user_punch_lines.append("10 PUNCH TOTAL_TIME/3600  # Convert seconds to hours")
    
    punch_num = 20
    for mineral in minerals_kinetic.keys():
        user_punch_lines.append(f"{punch_num} PUNCH KIN('{mineral}')")
        punch_num += 10
    
    user_punch_lines.append("    -end")
    input_lines.append("\n".join(user_punch_lines))
    
    # Add END statement
    input_lines.append("END")
    
    # Join all input
    phreeqc_input = "\n".join(input_lines)
    
    # Debug output - log full input to understand the issue
    logger.info(f"PHREEQC kinetic input length: {len(phreeqc_input)} chars")
    logger.debug(f"PHREEQC input for kinetic simulation:\n{phreeqc_input}")
    
    # Save input to file for debugging
    debug_file = "debug_kinetic_input.pqi"
    with open(debug_file, 'w') as f:
        f.write(phreeqc_input)
    logger.info(f"Saved debug input to {debug_file}")
    
    # Run simulation
    try:
        # Use the existing run_phreeqc_simulation function
        # Since we include DATABASE in the input, use a special marker
        raw_results = await run_phreeqc_simulation(
            phreeqc_input, 
            database_path="INLINE",  # Special marker to indicate database is in input
            num_steps=len(time_steps)
        )
        
        # Parse results for each time step
        results = {
            "kinetic_profiles": [],
            "kinetic_modeling_used": True,
            "phreeqc_rates_used": True,
            "final_solution": None,
            "time_series_solutions": [],  # Add full solution chemistry at each time step
            "errors": []  # Track any errors encountered
        }
        
        # Process results for each mineral
        for mineral in minerals_kinetic.keys():
            time_points = []
            amounts_precipitated = []
            saturation_indices = []
            precipitation_rates = []
            
            # Extract data from each time step
            if isinstance(raw_results, list):
                # Ensure we process results for ALL requested time steps
                expected_steps = len(time_steps)
                actual_steps = len(raw_results) if isinstance(raw_results, list) else 1
                
                if actual_steps < expected_steps:
                    logger.warning(f"Only {actual_steps} of {expected_steps} time steps completed")
                    # Pad raw_results with error entries for missing steps
                    if isinstance(raw_results, list):
                        for j in range(actual_steps, expected_steps):
                            raw_results.append({
                                'error': f'Simulation terminated early - step {j+1} not reached',
                                'terminated_early': True
                            })
                            
                for i, step_result in enumerate(raw_results):
                    if 'error' in step_result:
                        # Log error but try to extract partial results
                        error_msg = f"Error at time step {i} ({time_steps[i] if i < len(time_steps) else '?'}s): {step_result.get('error')}"
                        logger.warning(error_msg)
                        results["errors"].append(error_msg)
                        
                        # Check if this is an RK integration error - if so, we may have partial results
                        if 'RK' in str(step_result.get('error', '')) or 'integration' in str(step_result.get('error', '')):
                            logger.info("RK integration error detected - attempting to recover partial results")
                            # Try to get the last valid solution state
                            if i > 0 and all_step_results:
                                # Use previous step's values as approximation
                                prev_result = all_step_results[-1]
                                time_points.append(time_steps[i] if i < len(time_steps) else 0)
                                amounts_precipitated.append(amounts_precipitated[-1] if amounts_precipitated else 0.0)
                                saturation_indices.append(saturation_indices[-1] if saturation_indices else -999.0)
                                precipitation_rates.append(0.0)  # Zero rate due to failure
                                
                                # Also add to time series solutions
                                if prev_result.get('solution_summary'):
                                    results["time_series_solutions"].append({
                                        "time_seconds": time_steps[i] if i < len(time_steps) else 0,
                                        "pH": prev_result['solution_summary'].get('pH'),
                                        "temperature": prev_result['solution_summary'].get('temperature_celsius', 25.0),
                                        "ionic_strength": prev_result['solution_summary'].get('ionic_strength'),
                                        "elements": prev_result.get('element_totals_molality', {}),
                                        "note": "Approximated from previous step due to RK error"
                                    })
                            else:
                                # No previous results to use
                                time_points.append(time_steps[i] if i < len(time_steps) else 0)
                                amounts_precipitated.append(0.0)
                                saturation_indices.append(-999.0)
                                precipitation_rates.append(0.0)
                        else:
                            # Other error - add null values
                            time_points.append(time_steps[i] if i < len(time_steps) else 0)
                            amounts_precipitated.append(0.0)
                            saturation_indices.append(-999.0)
                            precipitation_rates.append(0.0)
                        continue
                    
                    time_points.append(time_steps[i] if i < len(time_steps) else 0)
                    
                    # Get mineral amount from phases or user_punch data
                    mineral_amount = 0
                    if 'phases' in step_result:
                        for phase in step_result['phases']:
                            if phase.get('name') == mineral:
                                mineral_amount = phase.get('moles', 0)
                                break
                    
                    amounts_precipitated.append(mineral_amount)
                    # Check for mineral exhaustion
                    if mineral_amount <= 1e-12 and i > 0:
                        logger.warning(f"{mineral} appears exhausted at step {i} (amount={mineral_amount})")
                        # Use previous SI if mineral is exhausted
                        if i > 0 and saturation_indices:
                            si = saturation_indices[-1]
                        else:
                            si = -999.0
                    else:
                        # Get SI normally
                        si = step_result.get('saturation_indices', {}).get(mineral, -999)
                    saturation_indices.append(si)
                    
                    # Calculate rate (simple difference)
                    if i > 0 and time_points[i] > time_points[i-1]:
                        rate = (amounts_precipitated[i] - amounts_precipitated[i-1]) / (time_points[i] - time_points[i-1])
                    else:
                        rate = 0
                    precipitation_rates.append(rate)
                
                # Store final solution
                if raw_results:
                    last_result = raw_results[-1]
                    results['final_solution'] = last_result.get('solution_summary', {})            # Create kinetic profile for this mineral
            if time_points:
                profile = KineticPrecipitationProfile(
                    mineral=mineral,
                    time_seconds=time_points,
                    amount_precipitated_mol=amounts_precipitated,
                    saturation_index=saturation_indices,
                    precipitation_rate_mol_s=precipitation_rates
                )
                
                results["kinetic_profiles"].append(profile.model_dump())
                
                logger.info(f"Completed PHREEQC kinetic simulation for {mineral}: "
                           f"final amount = {amounts_precipitated[-1] if amounts_precipitated else 0:.6f} mol")
        
        # Capture full solution chemistry at each time step AFTER processing all minerals
        if isinstance(raw_results, list) and len(results["time_series_solutions"]) == 0:
            logger.info(f"Collecting time series solutions for {len(raw_results)} time steps")
            
            for i, step_result in enumerate(raw_results):
                time_point = time_steps[i] if i < len(time_steps) else 0
                
                if 'error' not in step_result:
                    # Extract key solution properties
                    solution_data = {
                        "time_seconds": time_point,
                        "pH": step_result.get('solution_summary', {}).get('pH', None),
                        "temperature": step_result.get('solution_summary', {}).get('temperature_celsius', 25.0),
                        "ionic_strength": step_result.get('solution_summary', {}).get('ionic_strength', None),
                        "elements": step_result.get('element_totals_molality', {})
                    }
                    
                    results["time_series_solutions"].append(solution_data)
                else:
                    # Add error placeholder
                    results["time_series_solutions"].append({
                        "time_seconds": time_point,
                        "error": step_result.get('error', 'Unknown error')
                    })
        
        return results
        
    except Exception as e:
        logger.error(f"PHREEQC kinetic simulation failed: {e}")
        return {"error": f"PHREEQC kinetic simulation failed: {e}"}


def evaluate_target_parameter(results: Dict[str, Any], target_config: Dict[str, Any]) -> Optional[float]:
    """
    Evaluate complex target parameters from PHREEQC results.
    
    Args:
        results: PHREEQC simulation results
        target_config: Configuration for target evaluation including:
            - parameter: The target parameter type
            - value: The target value
            - units: Optional units for conversion
            - components: For composite parameters (e.g., metals list)
            - conditions: Additional conditions/constraints
    
    Returns:
        Current value of the target parameter, or None if not found
    """
    target_parameter = target_config.get('parameter')
    target_units = target_config.get('units', '')
    
    # Get solution summary
    if 'solution_summary' not in results:
        return None
    summary = results['solution_summary']
    
    # Simple parameters (existing functionality)
    if target_parameter == 'pH':
        return summary.get('pH')
    
    elif target_parameter == 'pe':
        return summary.get('pe')
    
    elif target_parameter == 'ionic_strength':
        return summary.get('ionic_strength')
    
    elif target_parameter == 'TDS':
        return summary.get('tds_calculated')
    
    # Complex/Composite parameters (NEW)
    elif target_parameter == 'total_hardness':
        # Total hardness = Ca + Mg (as CaCO3)
        element_totals = results.get('element_totals_molality', {})
        ca_molal = element_totals.get('Ca', 0)
        mg_molal = element_totals.get('Mg', 0)
        
        # Convert to desired units
        if 'mg/L' in target_units and 'CaCO3' in target_units:
            # Convert molality to mg/L as CaCO3
            # 1 mol/kg Ca = 100,000 mg/L as CaCO3
            # 1 mol/kg Mg = 100,000 mg/L as CaCO3
            hardness_mg_caco3 = (ca_molal + mg_molal) * 100000
            return hardness_mg_caco3
        elif 'mmol/L' in target_units:
            # Return as mmol/L
            return (ca_molal + mg_molal) * 1000
        else:
            # Default to molality
            return ca_molal + mg_molal
    
    elif target_parameter == 'residual_phosphorus':
        # Residual P after treatment
        element_totals = results.get('element_totals_molality', {})
        p_molal = element_totals.get('P', 0)
        
        if 'mg/L' in target_units:
            # Convert molality to mg/L P
            # 1 mol/kg P = 30,974 mg/L P
            return p_molal * 30974
        else:
            return p_molal
    
    elif target_parameter == 'total_metals':
        # Sum of specified metals
        components = target_config.get('components', ['Fe', 'Cu', 'Zn', 'Pb', 'Ni', 'Cd'])
        element_totals = results.get('element_totals_molality', {})
        
        total = sum(element_totals.get(metal, 0) for metal in components)
        
        if 'mg/L' in target_units:
            # Need individual MW for accurate conversion
            # Simplified: assume average MW of 60 g/mol
            return total * 60000
        else:
            return total
    
    elif target_parameter == 'carbonate_alkalinity':
        # Carbonate species alkalinity
        species = results.get('species_molality', {})
        hco3 = species.get('HCO3-', 0)
        co3 = species.get('CO3-2', 0)
        
        # Alkalinity = [HCO3-] + 2*[CO3-2]
        alk_molal = hco3 + 2 * co3
        
        if 'mg/L' in target_units and 'CaCO3' in target_units:
            # Convert to mg/L as CaCO3
            return alk_molal * 50000
        else:
            return alk_molal
    
    elif target_parameter == 'langelier_index':
        # LSI = pH - pHs
        # pHs = (9.3 + A + B) - (C + D)
        # Requires temperature, TDS, Ca hardness, alkalinity
        ph = summary.get('pH')
        temp_c = summary.get('temperature_celsius', 25)
        tds = summary.get('tds_calculated', 0)
        
        element_totals = results.get('element_totals_molality', {})
        ca_molal = element_totals.get('Ca', 0)
        ca_hardness_caco3 = ca_molal * 100000  # mg/L as CaCO3
        
        species = results.get('species_molality', {})
        alk_molal = species.get('HCO3-', 0) + 2 * species.get('CO3-2', 0)
        alk_caco3 = alk_molal * 50000  # mg/L as CaCO3
        
        # Langelier calculation
        A = (math.log10(tds) - 1) / 10 if tds > 0 else 0
        B = -13.12 * math.log10(temp_c + 273) + 34.55
        C = math.log10(ca_hardness_caco3) - 0.4 if ca_hardness_caco3 > 0 else 0
        D = math.log10(alk_caco3) if alk_caco3 > 0 else 0
        
        phs = (9.3 + A + B) - (C + D)
        return ph - phs
    
    elif target_parameter == 'minimum_si':
        # Minimum SI among specified minerals
        minerals = target_config.get('components', ['Calcite', 'Gypsum'])
        si_values = results.get('saturation_indices', {})
        
        valid_si = [si_values.get(m, 999) for m in minerals if si_values.get(m, 999) != -999]
        return min(valid_si) if valid_si else None
    
    elif target_parameter == 'precipitation_potential':
        # Total mass of precipitates
        total_ppt = results.get('total_precipitate_g_L', 0)
        
        if 'kg/m3' in target_units:
            return total_ppt / 1000
        else:
            return total_ppt
    
    elif target_parameter == 'molar_ratio':
        # Ratio between two elements/species
        numerator = target_config.get('numerator')
        denominator = target_config.get('denominator')
        
        element_totals = results.get('element_totals_molality', {})
        species = results.get('species_molality', {})
        
        # Try elements first, then species
        num_val = element_totals.get(numerator, species.get(numerator, 0))
        den_val = element_totals.get(denominator, species.get(denominator, 0))
        
        if den_val > 0:
            return num_val / den_val
        else:
            return float('inf') if num_val > 0 else 0
    
    # Existing functionality for other parameters...
    elif target_parameter == 'Alkalinity':
        current_value = summary.get('alkalinity_mol_kgw')
        if target_units and 'mg' in target_units.lower() and 'caco3' in target_units.lower():
            from utils.constants import ALKALINITY_MOL_TO_MG_CACO3
            current_value *= ALKALINITY_MOL_TO_MG_CACO3
        return current_value
    
    elif target_parameter == 'SI' and target_config.get('mineral'):
        mineral_name = target_config.get('mineral')
        return results.get('saturation_indices', {}).get(mineral_name)
    
    elif target_parameter == 'Concentration':
        element_or_species = target_config.get('element_or_species')
        element_totals = results.get('element_totals_molality', {})
        species = results.get('species_molality', {})
        
        # Try element first, then species
        current_value = element_totals.get(element_or_species, species.get(element_or_species))
        
        # Unit conversion if needed
        if current_value is not None and target_units and 'mg' in target_units.lower():
            # Would need MW lookup for accurate conversion
            logger.warning(f"Unit conversion from molality to {target_units} requires molecular weight")
        
        return current_value
    
    else:
        logger.warning(f"Unknown target parameter: {target_parameter}")
        return None


# Multi-objective optimization support
class OptimizationObjective:
    """Define optimization objectives with constraints."""
    
    def __init__(self, 
                 parameter: str,
                 target_value: float,
                 tolerance: float,
                 weight: float = 1.0,
                 constraint_type: str = 'equality',  # 'equality', 'min', 'max'
                 units: Optional[str] = None,
                 **kwargs):
        self.parameter = parameter
        self.target_value = target_value
        self.tolerance = tolerance
        self.weight = weight
        self.constraint_type = constraint_type
        self.units = units
        self.config = kwargs
    
    def evaluate(self, results: Dict[str, Any]) -> Tuple[float, float]:
        """
        Evaluate objective function.
        Returns: (current_value, error)
        """
        config = {
            'parameter': self.parameter,
            'units': self.units,
            **self.config
        }
        current_value = evaluate_target_parameter(results, config)
        
        if current_value is None:
            return None, float('inf')
        
        if self.constraint_type == 'equality':
            error = abs(current_value - self.target_value)
        elif self.constraint_type == 'min':
            # Penalty only if below target
            error = max(0, self.target_value - current_value)
        elif self.constraint_type == 'max':
            # Penalty only if above target
            error = max(0, current_value - self.target_value)
        else:
            error = abs(current_value - self.target_value)
        
        return current_value, error * self.weight


async def find_reactant_dose_for_target(
    initial_solution_str: str,
    target_parameter: str,
    target_value: float,
    reagent_formula: str,
    mineral_name: Optional[str] = None,
    element_or_species: Optional[str] = None,
    target_units: Optional[str] = None,
    initial_guess_mmol: float = 1.0,
    max_iterations: int = 30,  # Increased default from 20 to 30
    tolerance: float = 0.01,
    database_path: Optional[str] = None,
    allow_precipitation: bool = True,
    equilibrium_minerals: Optional[List[str]] = None,
) -> Tuple[Optional[float], Dict[str, Any], int]:
    """
    Iteratively finds the dose of a reactant to meet a target.
    
    Args:
        initial_solution_str: PHREEQC SOLUTION block for starting solution
        target_parameter: Parameter to target (e.g., 'pH', 'SI', 'Alkalinity')
        target_value: Target value for the parameter
        reagent_formula: Chemical formula of the reagent (e.g., 'NaOH')
        mineral_name: Mineral name (required if target_parameter is 'SI')
        element_or_species: Element or species (required if target_parameter is 'Concentration')
        target_units: Units for target value (required for some target parameters)
        initial_guess_mmol: Initial guess for dose in mmol/L
        max_iterations: Maximum number of iterations for the search
        tolerance: Acceptable tolerance for reaching the target
        database_path: Path to PHREEQC database
        allow_precipitation: Whether to allow mineral precipitation
        equilibrium_minerals: List of minerals to consider for precipitation
        
    Returns:
        Tuple of (optimal_dose or None, final_results_dict, iterations_performed)
    """
    lower_bound_mmol = 0.0
    upper_bound_mmol = None  # Will be set dynamically
    current_dose_mmol = initial_guess_mmol
    final_results = {}
    iterations_done = 0
    
    # Previous direction for adaptive method
    prev_direction = None
    prev_error = None
    prev_dose_mmol = None

    logger.info(f"Starting iterative search for {reagent_formula} dose to reach {target_parameter}={target_value}"
                f"{' (' + target_units + ')' if target_units else ''}"
                f"{' for ' + mineral_name if mineral_name else ''}"
                f"{' for ' + element_or_species if element_or_species else ''}")
    
    # For logging/debugging - calculate a rough estimate of expected final dose for pH
    if target_parameter == 'pH':
        try:
            # Get initial pH from solution block to make a rough estimate of required dose
            initial_ph_match = re.search(r'pH\s+(\d+\.?\d*)', initial_solution_str, re.IGNORECASE)
            if initial_ph_match:
                initial_ph = float(initial_ph_match.group(1))
                ph_change = abs(target_value - initial_ph)
                
                # Make rough estimate of dose for log planning purposes
                if ph_change > 0:
                    if reagent_formula in ['NaOH', 'KOH', 'Ca(OH)2']:
                        # For bases, each unit pH increase requires 10x more OH-
                        rough_dose = 10**(ph_change) - 1 if target_value > initial_ph else 0
                    elif reagent_formula in ['HCl', 'H2SO4', 'HNO3']:
                        # For acids, each unit pH decrease requires 10x more H+
                        rough_dose = 10**(ph_change) - 1 if target_value < initial_ph else 0
                    else:
                        rough_dose = ph_change * 5  # Generic fallback
                        
                    logger.info(f"Rough estimate of required dose based on pH change: ~{rough_dose:.4f} mmol")
                    
                    # Set a more appropriate upper bound based on pH change
                    if upper_bound_mmol is None:
                        if ph_change > 3:
                            upper_bound_mmol = max(initial_guess_mmol * 100, 1000)
                        elif ph_change > 2:
                            upper_bound_mmol = max(initial_guess_mmol * 50, 100)
                        elif ph_change > 1:
                            upper_bound_mmol = max(initial_guess_mmol * 20, 10)
                        else:
                            upper_bound_mmol = max(initial_guess_mmol * 10, 1)
                        
                        # Ensure our initial guess is in the range
                        if current_dose_mmol >= upper_bound_mmol:
                            current_dose_mmol = upper_bound_mmol / 2
                            logger.info(f"Adjusted initial guess to {current_dose_mmol:.4f} mmol to fit within bounds")
        except Exception as e:
            logger.warning(f"Error estimating rough dose from pH change: {e}")
    
    # If upper bound wasn't set from pH estimate, use a default
    if upper_bound_mmol is None:
        upper_bound_mmol = max(initial_guess_mmol * 100, 10.0)
        
    # Make sure our starting bounds are valid
    if current_dose_mmol <= lower_bound_mmol:
        current_dose_mmol = (lower_bound_mmol + upper_bound_mmol) / 100  # Start with a small non-zero value
    
    if current_dose_mmol >= upper_bound_mmol:
        current_dose_mmol = upper_bound_mmol / 2  # Start with midpoint
    
    equilibrium_phases_str = ""
    if allow_precipitation:
        # IMPORTANT: If no minerals are specified but precipitation is enabled,
        # use default minerals based on water chemistry
        if not equilibrium_minerals:
            # Try to extract water chemistry from the initial solution string
            from utils.constants import select_minerals_for_water_chemistry, UNIVERSAL_MINERALS
            
            # First, get a simple set of universal minerals that work in all databases
            equilibrium_minerals = UNIVERSAL_MINERALS
            logger.info(f"No minerals specified, using universal minerals: {', '.join(equilibrium_minerals)}")
        
        if equilibrium_minerals:
            from utils.database_management import database_manager
            
            # Get compatible minerals for the selected database
            mineral_mapping = database_manager.get_compatible_minerals(
                database_path, 
                equilibrium_minerals
            )
            
            # Filter out incompatible minerals and use alternatives where possible
            compatible_minerals = []
            for requested_mineral, compatible_mineral in mineral_mapping.items():
                if compatible_mineral:
                    compatible_minerals.append(compatible_mineral)
                else:
                    logger.warning(
                        f"Mineral '{requested_mineral}' is not compatible with database "
                        f"'{os.path.basename(database_path)}' and no alternative was found."
                    )
            
            # Build equilibrium phases block with compatible minerals
            if compatible_minerals:
                phases_to_consider = [{'name': name} for name in compatible_minerals]
                equilibrium_phases_str = build_equilibrium_phases_block(phases_to_consider, block_num=1)
                logger.info(f"Enabled precipitation with minerals: {', '.join(compatible_minerals)}")
            else:
                logger.warning("No compatible minerals found for precipitation")

    for i in range(max_iterations):
        iterations_done = i + 1
        # Ensure dose is non-negative and reasonable
        current_dose_mmol = max(1e-9, current_dose_mmol)

        # Build reaction block based on the updated helper function
        reaction_def = [{
            'formula': reagent_formula,
            'amount': current_dose_mmol,
            'units': 'mmol'
        }]
        reaction_block = build_reaction_block(reaction_def, reaction_num=1)
        
        # Add enhanced numerical optimization commands to avoid convergence errors
        convergence_options = """
KNOBS
    -iterations 800       # Default is 100, increase to help convergence (increased from 500)
    -tolerance 1e-10      # Default is 1e-6, tighter tolerance helps (relaxed from 1e-12)
    -convergence_tolerance 1e-8  # Add explicit convergence tolerance
    -step_size 10         # Default is 100, smaller steps help convergence
    -pe_step_size 5       # Default is 10, smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
"""
        
        phreeqc_input = (
            initial_solution_str +
            convergence_options +
            reaction_block +
            "USE solution 1\n" +
            "USE reaction 1\n"
        )
        if equilibrium_phases_str:
            phreeqc_input += equilibrium_phases_str
            phreeqc_input += "USE equilibrium_phases 1\n"

        phreeqc_input += build_selected_output_block(
            block_num=1,
            phases=allow_precipitation, 
            saturation_indices=True, 
            totals=True, 
            molalities=True
        ) + "END\n"

        try:
            # Add pH buffer for extreme pH cases if we're trying to reach a pH target
            if target_parameter == 'pH' and target_value is not None:
                # If target pH is very high or very low, add a small amount of buffer to help convergence
                phreeqc_input_with_buffer = phreeqc_input
                # Add buffer for extreme pH targets
                buffer_amount = 0.0001  # Start with a small buffer amount
                
                if target_value >= 11:
                    # For high pH targets, scale buffer with how extreme the pH is
                    if target_value >= 13:
                        buffer_amount = 0.01
                    elif target_value >= 12:
                        buffer_amount = 0.005
                    elif target_value >= 11:
                        buffer_amount = 0.001
                    
                    logger.info(f"Adding pH buffer ({buffer_amount} mol) for high pH target: {target_value}")
                    # Add a small amount of strong base to help convergence at high pH
                    # Use direct NaOH addition instead of modifying the solution
                    naoh_buffer = build_reaction_block([{
                        'formula': 'NaOH',
                        'amount': buffer_amount,
                        'units': 'mol'
                    }], reaction_num=99)
                    # Add this as a separate reaction before the main reaction
                    phreeqc_input_with_buffer = phreeqc_input.replace("REACTION 1", naoh_buffer + "\nREACTION 1")
                    phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("USE solution 1\n", "USE solution 1\nUSE reaction 99\n")
                    
                    # Also add relaxed KNOBS settings for extreme pH
                    if target_value >= 12:
                        relaxed_knobs = """
KNOBS
    -iterations 500       # Increase max iterations
    -convergence_tolerance 1e-7  # More relaxed convergence criteria
    -tolerance 1e-10      # Relaxed tolerance
    -step_size 10         # Smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
    -pe_step_size 5       # Smaller pe steps
"""
                        # Add the relaxed KNOBS section
                        if "KNOBS" in phreeqc_input_with_buffer:
                            phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("KNOBS", relaxed_knobs)
                        else:
                            phreeqc_input_with_buffer = relaxed_knobs + phreeqc_input_with_buffer
                            
                elif target_value <= 3:
                    # For low pH targets, scale buffer with how extreme the pH is
                    if target_value <= 1:
                        buffer_amount = 0.01
                    elif target_value <= 2:
                        buffer_amount = 0.005
                    elif target_value <= 3:
                        buffer_amount = 0.001
                        
                    logger.info(f"Adding pH buffer ({buffer_amount} mol) for low pH target: {target_value}")
                    # Add a small amount of strong acid to help convergence at low pH
                    # Use direct HCl addition instead of modifying the solution
                    hcl_buffer = build_reaction_block([{
                        'formula': 'HCl',
                        'amount': buffer_amount,
                        'units': 'mol'
                    }], reaction_num=99)
                    # Add this as a separate reaction before the main reaction
                    phreeqc_input_with_buffer = phreeqc_input.replace("REACTION 1", hcl_buffer + "\nREACTION 1")
                    phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("USE solution 1\n", "USE solution 1\nUSE reaction 99\n")
                    
                    # Also add relaxed KNOBS settings for extreme pH
                    if target_value <= 2:
                        relaxed_knobs = """
KNOBS
    -iterations 500       # Increase max iterations
    -convergence_tolerance 1e-7  # More relaxed convergence criteria
    -tolerance 1e-6       # Relaxed tolerance
    -step_size 5          # Smaller steps help convergence
    -diagonal_scale true  # Help numerical stability
"""
                        # Add the relaxed KNOBS section
                        if "KNOBS" in phreeqc_input_with_buffer:
                            phreeqc_input_with_buffer = phreeqc_input_with_buffer.replace("KNOBS", relaxed_knobs)
                        else:
                            phreeqc_input_with_buffer = relaxed_knobs + phreeqc_input_with_buffer
                else:
                    phreeqc_input_with_buffer = phreeqc_input
            else:
                phreeqc_input_with_buffer = phreeqc_input

            # First attempt - run with full model
            try:
                results = await run_phreeqc_simulation(phreeqc_input_with_buffer, database_path)
            except PhreeqcError as pe:
                # Check if it's a water activity convergence error
                if hasattr(pe, 'is_water_activity_error') and pe.is_water_activity_error:
                    logger.warning("Water activity convergence error detected, retrying with simplified model")
                    
                    # Implement a simplified model approach
                    # 1. Adjust KNOBS parameters for better convergence
                    # 2. Remove complex precipitates if not essential
                    simplified_input = phreeqc_input_with_buffer.replace(
                        "-tolerance 1e-10",  # Updated from 1e-12 to match the value in convergence_options
                        "-tolerance 1e-8"
                    )
                    
                    # Add more robust KNOBS settings
                    more_robust_knobs = """
KNOBS
    -iterations 1000      # Increase max iterations
    -convergence_tolerance 1e-8  # Relax convergence criteria
    -tolerance 1e-6       # Relaxed tolerance
    -step_size 5          # Smaller steps help convergence
    -pe_step_size 2       # Smaller pe steps
    -diag_tol 1e-5        # More relaxed diagonal tolerance
    -diagonal_scale true  # Help numerical stability
"""
                    # Add the more robust KNOBS section
                    simplified_input = simplified_input.replace("KNOBS", more_robust_knobs)
                    
                    # If we're allowing precipitation, temporarily disable some of the more complex minerals
                    if allow_precipitation and equilibrium_minerals:
                        # Keep only the most essential minerals for the calculation
                        essential_minerals = ["Calcite", "Gypsum", "Halite", "Dolomite", "Aragonite"]
                        simple_minerals = [m for m in equilibrium_minerals if m in essential_minerals]
                        
                        if simple_minerals:
                            from utils.database_management import database_manager
                            
                            # Get compatible minerals for the selected database
                            mineral_mapping = database_manager.get_compatible_minerals(
                                database_path, 
                                simple_minerals
                            )
                            
                            # Filter to compatible minerals
                            compatible_minerals = [m for m, v in mineral_mapping.items() if v]
                            
                            if compatible_minerals:
                                # Build a new equilibrium phases block
                                phases_to_consider = [{'name': name} for name in compatible_minerals]
                                from utils.helpers import build_equilibrium_phases_block
                                simple_equilibrium_phases_str = build_equilibrium_phases_block(phases_to_consider, block_num=1)
                                
                                # Replace the existing equilibrium phases block if it exists
                                if "EQUILIBRIUM_PHASES" in simplified_input:
                                    simplified_input = re.sub(
                                        r"EQUILIBRIUM_PHASES\s+\d+(?:\s*#[^\n]*)?\n(.*?)(?=^[A-Z]|\Z)",
                                        simple_equilibrium_phases_str,
                                        simplified_input,
                                        flags=re.MULTILINE | re.DOTALL
                                    )
                    
                    # Check if this is likely a high ionic strength issue
                    # Attempt to identify high ionic strength from input solution
                    high_ionic_strength = False
                    high_conc_elements = ["Na", "Cl", "K", "Ca", "Mg", "SO4"]
                    
                    # Scan input for high concentrations that might cause ionic strength issues
                    for elem in high_conc_elements:
                        if f"{elem} " in phreeqc_input_with_buffer:
                            # Look for high concentrations (> 1 mol/kg)
                            for line in phreeqc_input_with_buffer.split('\n'):
                                if f"{elem} " in line and "mol" in line.lower():
                                    # Extract the concentration value
                                    try:
                                        # Simple extraction - look for numeric values
                                        parts = line.split()
                                        for i, part in enumerate(parts):
                                            if elem in part and i+1 < len(parts):
                                                try:
                                                    conc = float(parts[i+1])
                                                    if conc > 0.5:  # High concentration threshold
                                                        high_ionic_strength = True
                                                        logger.warning(f"High concentration of {elem} detected: {conc}")
                                                        break
                                                except ValueError:
                                                    pass
                                    except Exception:
                                        pass
                    
                    # Try multiple approaches for high ionic strength solutions
                    final_error = None
                    success = False
                    
                    # Try simplified approach with current database first
                    try:
                        logger.info("Attempt 1: Retrying with simplified model parameters")
                        results = await run_phreeqc_simulation(simplified_input, database_path)
                        success = True
                    except Exception as e:
                        final_error = e
                        logger.warning(f"Simplified model attempt failed: {e}")
                        
                    # If still fails and it's a high ionic strength solution, try the PITZER database
                    if not success and high_ionic_strength and not database_path.lower().endswith('pitzer.dat'):
                        try:
                            from utils.database_management import DatabaseManager
                            logger.info("Attempt 2: High ionic strength detected, attempting to use PITZER database")
                            db_manager = DatabaseManager()
                            pitzer_db = db_manager.get_database_path('pitzer.dat')
                            
                            if pitzer_db:
                                logger.info(f"Trying with PITZER database for better handling of high ionic strength: {pitzer_db}")
                                # Special case: direct retry with PITZER database
                                results = await run_phreeqc_simulation(phreeqc_input_with_buffer, pitzer_db)
                                # If it works, update the database for future iterations
                                database_path = pitzer_db
                                success = True
                            else:
                                logger.warning("PITZER database not found")
                        except Exception as db_e:
                            final_error = db_e
                            logger.warning(f"PITZER database attempt failed: {db_e}")
                    
                    # If both approaches failed and we're dealing with extreme pH, try with relaxed parameters
                    if not success and target_parameter == "pH":
                        try:
                            logger.info("Attempt 3: Trying with further relaxed convergence parameters")
                            # Create an even more simplified input for difficult cases
                            very_relaxed = simplified_input.replace("KNOBS", """
KNOBS
    -iterations 1500      # Many more iterations
    -convergence_tolerance 1e-5  # Much more relaxed tolerance
    -tolerance 1e-5       # Relaxed tolerance
    -step_size 2          # Tiny step size
    -diagonal_scale true  # Force numerical stability
""")
                            # And make sure precipitation is temporarily disabled to simplify calculations
                            if "EQUILIBRIUM_PHASES" in very_relaxed:
                                logger.info("Temporarily disabling precipitation to simplify calculation")
                                very_relaxed = re.sub(r"EQUILIBRIUM_PHASES.*?(?=^[A-Z]|\Z)", "", 
                                                    very_relaxed, flags=re.MULTILINE | re.DOTALL)
                            
                            results = await run_phreeqc_simulation(very_relaxed, database_path)
                            success = True
                        except Exception as e:
                            final_error = e
                            logger.warning(f"Relaxed parameters attempt failed: {e}")
                    
                    # Try one last approach if we've exhausted better solutions - just simplify the input drastically
                    if not success:
                        try:
                            logger.info("Attempt 4: Last resort with drastically simplified model")
                            # Create an extremely simplified input - just solution and reaction
                            minimal_input = (
                                initial_solution_str +
                                """
KNOBS
    -iterations 2000
    -convergence_tolerance 1e-5
    -tolerance 1e-4
    -step_size 1
    -diagonal_scale true
""" +
                                reaction_block +
                                "USE solution 1\n" +
                                "USE reaction 1\n" +
                                build_selected_output_block(
                                    block_num=1,
                                    phases=False,  # Don't calculate SIs to simplify
                                    saturation_indices=False,
                                    totals=True,
                                    molalities=False
                                ) + "END\n"
                            )
                            
                            results = await run_phreeqc_simulation(minimal_input, database_path)
                            success = True
                            logger.info("Succeeded with minimal model")
                        except Exception as e:
                            final_error = e
                            logger.warning(f"Minimal model attempt failed: {e}")
                    
                    # If everything failed, re-raise the last error
                    if not success:
                        logger.error("All convergence recovery attempts failed")
                        raise final_error
                else:
                    # Not a water activity error, re-raise
                    raise pe
                    
            if isinstance(results, list):
                results = results[0]

            final_results = results
            if 'error' in results and results['error']:
                raise PhreeqcError(results['error'])

            # Get the current value of the target parameter
            current_value = None
            if 'solution_summary' not in results:
                logger.warning(f"Iteration {iterations_done}: No solution summary found in results.")
                break

            summary = results['solution_summary']

            # Use enhanced target parameter evaluation
            target_config = {
                'parameter': target_parameter,
                'units': target_units,
                'mineral': mineral_name,
                'element_or_species': element_or_species
            }
            current_value = evaluate_target_parameter(results, target_config)

            # Check if we've reached the target
            if current_value is None:
                logger.warning(f"Could not retrieve current value for {target_parameter} at iteration {iterations_done}")
                status = "Error retrieving target value"
                
                # Add more detailed error information
                final_results['error'] = status
                final_results['error_type'] = 'missing_target_parameter'
                final_results['context'] = {
                    'target_parameter': target_parameter,
                    'iteration': iterations_done,
                    'dose_mmol': current_dose_mmol,
                    'suggestion': f"Check that '{target_parameter}' is a valid parameter for the simulation."
                }
                
                return None, final_results, iterations_done

            error = current_value - target_value
            logger.info(f"Iter {iterations_done}: Dose={current_dose_mmol:.6f} mmol, "
                        f"Current {target_parameter}={current_value:.6f}, "
                        f"Target={target_value:.6f}, Error={error:.6f}")

            if abs(error) < tolerance:
                logger.info(f"Target achieved within tolerance at iteration {iterations_done}. "
                           f"Final Dose: {current_dose_mmol:.6f} mmol")
                return current_dose_mmol, final_results, iterations_done  # Success

            # Determine current direction for adaptive method
            current_direction = "increase" if error < 0 else "decrease"
            
            # Store the old bounds for potential adjustment
            old_lower = lower_bound_mmol
            old_upper = upper_bound_mmol

            # Adjust bounds using bisection
            if error < 0:  # Current value is too low -> need more dose
                lower_bound_mmol = current_dose_mmol
            else:  # Current value is too high -> need less dose
                upper_bound_mmol = current_dose_mmol

            # Check for oscillation (changing direction)
            if prev_direction is not None and prev_direction != current_direction:
                logger.info("Direction changed, possibly approaching target")
                
                # If we're close but oscillating, narrow bounds more aggressively
                if prev_error is not None and abs(error) < abs(prev_error) * 2:
                    # We're making progress, so keep the bounds tighter
                    if current_direction == "increase":
                        # We need to increase dose but were previously decreasing
                        # Keep our current dose as lower bound, but make the upper bound closer
                        upper_bound_mmol = current_dose_mmol + (old_upper - current_dose_mmol) * 0.5
                    else:
                        # We need to decrease dose but were previously increasing
                        # Keep our current dose as upper bound, but make the lower bound closer
                        lower_bound_mmol = current_dose_mmol - (current_dose_mmol - old_lower) * 0.5

            # Check if bounds are stuck or invalid
            if upper_bound_mmol <= lower_bound_mmol or abs(upper_bound_mmol - lower_bound_mmol) < 1e-10:
                logger.warning(f"Search bounds converged without reaching target tolerance.")
                status = "Bounds converged without reaching tolerance"
                
                # Check if we're at least somewhat close to the target
                if abs(error) < tolerance * 5:
                    logger.info(f"We're within 5x tolerance ({abs(error):.6f} vs {tolerance:.6f}), returning best result")
                    return current_dose_mmol, final_results, iterations_done
                
                final_results['error'] = status
                final_results['error_type'] = 'bounds_convergence'
                final_results['context'] = {
                    'target_parameter': target_parameter,
                    'target_value': target_value,
                    'current_value': current_value,
                    'current_dose_mmol': current_dose_mmol,
                    'error': error,
                    'tolerance': tolerance,
                    'iteration': iterations_done,
                    'suggestion': "Try a different initial dose or adjust tolerance."
                }
                return None, final_results, iterations_done

            # Adaptive algorithm - mix bisection with secant method
            # Standard bisection approach (midpoint)
            next_dose_mmol_bisection = (lower_bound_mmol + upper_bound_mmol) / 2.0
            
            # If we have previous values, try secant method for faster convergence
            next_dose_mmol = next_dose_mmol_bisection  # Default to bisection
            
            if prev_error is not None and abs(prev_error - error) > 1e-10:
                # Use secant method to estimate next dose
                try:
                    m = (prev_error - error) / (prev_dose_mmol - current_dose_mmol)  # Slope
                    if abs(m) > 1e-10:  # Avoid division by very small slopes
                        b = error - m * current_dose_mmol  # Intercept
                        next_dose_mmol_secant = -b / m  # X-intercept (where error = 0)
                        
                        # Check if secant method gives a reasonable result within bounds
                        if (lower_bound_mmol < next_dose_mmol_secant < upper_bound_mmol and 
                            abs(next_dose_mmol_secant - current_dose_mmol) < (upper_bound_mmol - lower_bound_mmol)):
                            # Use a weighted average of bisection and secant for stability
                            next_dose_mmol = 0.7 * next_dose_mmol_secant + 0.3 * next_dose_mmol_bisection
                            logger.info(f"Using secant method estimate: {next_dose_mmol_secant:.6f}, " 
                                      f"combined with bisection: {next_dose_mmol:.6f}")
                except Exception as e:
                    logger.warning(f"Error calculating secant method: {e}, falling back to bisection")
                    next_dose_mmol = next_dose_mmol_bisection

            # Safety check - ensure next dose is within bounds
            next_dose_mmol = max(lower_bound_mmol, min(upper_bound_mmol, next_dose_mmol))

            # Prevent oscillation or getting stuck
            if abs(next_dose_mmol - current_dose_mmol) < 1e-10:
                # If we're making tiny adjustments, check if we're close to target
                if abs(error) < tolerance * 2:
                    logger.info(f"Dose changes very small and error ({abs(error):.6f}) is close to tolerance ({tolerance:.6f}), accepting result")
                    return current_dose_mmol, final_results, iterations_done
                    
                logger.warning("Iteration dose change too small, stopping.")
                status = "Iteration stalled"
                final_results['error'] = status
                final_results['error_type'] = 'iteration_stalled'
                final_results['context'] = {
                    'target_parameter': target_parameter,
                    'target_value': target_value,
                    'current_value': current_value,
                    'current_dose_mmol': current_dose_mmol,
                    'error': error,
                    'iteration': iterations_done,
                    'suggestion': "Try a different initial dose or increase tolerance."
                }
                return None, final_results, iterations_done

            # Save current values for next iteration
            prev_direction = current_direction
            prev_error = error
            prev_dose_mmol = current_dose_mmol
            current_dose_mmol = next_dose_mmol

        except PhreeqcError as e:
            logger.error(f"PHREEQC error during iteration {iterations_done} with dose {current_dose_mmol:.4f}: {e}")
            
            # Create an enhanced error result dictionary reusing context from the original error
            error_dict = e.to_dict() if hasattr(e, 'to_dict') else {"error": str(e)}
            error_dict.update({
                "iteration": iterations_done,
                "dose_mmol": current_dose_mmol,
                "target_parameter": target_parameter,
                "target_value": target_value,
                "error_type": "phreeqc_simulation_error",
                "last_successful_results": final_results
            })
            
            return None, error_dict, iterations_done

    logger.warning(f"Maximum iterations ({max_iterations}) reached without converging to target.")
    # Create an enhanced error result dictionary
    error_dict = {
        "error": "Maximum iterations reached without converging to target value",
        "error_type": "max_iterations",
        "context": {
            "target_parameter": target_parameter,
            "target_value": target_value,
            "current_dose_mmol": current_dose_mmol,
            "iterations_performed": iterations_done,
            "lower_bound": lower_bound_mmol,
            "upper_bound": upper_bound_mmol,
            "suggestion": "Try a different initial guess, increase max iterations, or check if the target is achievable."
        },
        "last_successful_results": final_results
    }
    return None, error_dict, iterations_done

async def query_database(
    query_term: str, 
    query_type: str, 
    database_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Queries the PHREEQC database file for specific information.
    
    Args:
        query_term: Element, species, mineral name, or keyword
        query_type: Type of query ('species', 'mineral', 'element_info', 'keyword_block')
        database_path: Path to PHREEQC database
        
    Returns:
        Dictionary with query results
    """
    from utils.import_helpers import DEFAULT_DATABASE
    
    # Ensure we have a valid database path
    if database_path:
        # First validate the path exists
        if not os.path.exists(database_path):
            # Try to get the database by name using the database manager
            from utils.database_management import DatabaseManager
            db_manager = DatabaseManager()
            db_path = db_manager.get_database_path(os.path.basename(database_path))
            if db_path:
                database_path = db_path
                logger.info(f"Resolved database path: {database_path}")
            else:
                logger.warning(f"Could not find database: {database_path}")
                database_path = None
    
    # If no database path was specified or found, use default
    db_to_use = database_path or DEFAULT_DATABASE
    
    # Final check - if we still don't have a database, try to get any available one
    if not db_to_use:
        try:
            from utils.database_management import DatabaseManager
            db_manager = DatabaseManager()
            db_to_use = db_manager.get_database_path('phreeqc.dat')
            if not db_to_use:
                # Get any available database
                if db_manager.available_databases:
                    db_to_use = db_manager.available_databases[0]
                    logger.warning(f"Using available database as fallback: {db_to_use}")
        except Exception as e:
            logger.warning(f"Error finding fallback database: {e}")
    
    output = {
        "query_term": query_term,
        "query_type": query_type,
        "database_used": db_to_use or "phreeqpython default",
        "results": None,
        "error": None
    }

    if not db_to_use or not os.path.exists(db_to_use):
        error_message = f"Database file not found or default path unknown: {db_to_use}"
        logger.error(error_message)
        output["error"] = error_message
        output["error_type"] = "database_not_found"
        output["suggestion"] = "Check that the database path is correct and the file exists."
        return output

    logger.info(f"Querying database {db_to_use} for {query_type} '{query_term}'")

    try:
        # Read the file and find the relevant block/line
        with open(db_to_use, 'r', errors='ignore') as f:
            content = f.read()

        results = None
        if query_type.lower() == 'keyword_block':
            # Find the start of the keyword block (case-insensitive)
            keyword_upper = query_term.upper()
            start_match = re.search(f"^{keyword_upper}\\b", content, re.MULTILINE | re.IGNORECASE)
            if start_match:
                start_index = start_match.start()
                # Find the end (next keyword or end of file)
                end_match = re.search(r"^[A-Z_]+\b", content[start_index + len(query_term):], re.MULTILINE)
                if end_match:
                    end_index = start_index + len(query_term) + end_match.start()
                    results = content[start_index:end_index].strip()
                else:
                    results = content[start_index:].strip()  # To end of file
            else:
                output["error"] = f"Keyword block '{query_term}' not found."

        elif query_type.lower() == 'mineral' or query_type.lower() == 'phase':
            # Try to be more flexible with search patterns
            
            # First check if there's a PHASES block in the database
            has_phases_block = re.search(r"^PHASES\b", content, re.MULTILINE | re.IGNORECASE) is not None
            
            if has_phases_block:
                # Find the phase definition in PHASES block
                phase_block_match = re.search(r"^PHASES\b(.*?)(?=^[A-Z_]+\b)", content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                if not phase_block_match:
                    # Try alternative pattern if the first one fails
                    phase_block_match = re.search(r"PHASES(.*?)(?=^[A-Z_]+\b|\Z)", content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                    
                if phase_block_match:
                    phase_block = phase_block_match.group(1)
                    
                    # Try different patterns for phase matching since databases have different formats
                    
                    # Case 1: Look for the mineral at the beginning of a line
                    # Pattern: mineral_name whitespace rest_of_line
                    phase_match = None
                    
                    # First try exact match at beginning of a line (most specific)
                    pattern1 = f"^\\s*{re.escape(query_term)}\\s+.*?(?=^\\s*[A-Za-z0-9_().-]+\\s+|$)"
                    phase_match = re.search(pattern1, phase_block, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                    
                    # If that fails, try a simpler pattern that just finds the mineral name at the beginning of a line
                    if not phase_match:
                        pattern2 = f"^\\s*{re.escape(query_term)}\\b.*?(?=^\\s*[A-Za-z0-9_]+\\b|$)"
                        phase_match = re.search(pattern2, phase_block, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                    
                    # If that fails, try an even more flexible pattern that looks for the mineral name anywhere
                    if not phase_match:
                        pattern3 = f"\\b{re.escape(query_term)}\\b.*?(?=^\\s*[A-Za-z0-9_]+\\s+|$)"
                        phase_match = re.search(pattern3, phase_block, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                    
                    # If all standard patterns fail, try without escaping special characters like parentheses
                    if not phase_match:
                        query_term_unescaped = re.escape(query_term).replace('\\(', '(').replace('\\)', ')')
                        pattern4 = f"\\b{query_term_unescaped}\\b"
                        phase_match = re.search(pattern4, phase_block, re.MULTILINE | re.IGNORECASE)
                        
                        if phase_match:
                            # If we find it, get the full definition
                            start_idx = phase_match.start()
                            # Find the next mineral definition
                            next_mineral_match = re.search(r"^\s*[A-Za-z0-9_\-]+\s+", phase_block[start_idx+1:], re.MULTILINE)
                            if next_mineral_match:
                                end_idx = start_idx + 1 + next_mineral_match.start()
                                results = phase_block[start_idx:end_idx].strip()
                            else:
                                # If no next mineral, go to the end of the block
                                results = phase_block[start_idx:].strip()
                    
                    # If we found a match with one of the standard patterns
                    if phase_match and not results:
                        results = phase_match.group(0).strip()
                        
                    if not results:
                        # Check if this is a case-sensitivity issue
                        case_insensitive_lines = []
                        for line in phase_block.splitlines():
                            if query_term.lower() in line.lower() and line.strip() and not line.strip().startswith('#'):
                                case_insensitive_lines.append(line)
                                
                        if case_insensitive_lines:
                            results = "\n".join(case_insensitive_lines)
                            logger.info(f"Found case-insensitive match for '{query_term}' in PHASES block")
                    
                    if not results:
                        # Use mineral registry to suggest alternative mineral names
                        from utils.mineral_registry import get_alternative_mineral_names
                        alt_names = get_alternative_mineral_names(query_term)
                        
                        if alt_names:
                            output["error"] = f"Mineral/Phase '{query_term}' not found within PHASES block. Try alternative names: {', '.join(alt_names[:3])}"
                            output["alternative_names"] = alt_names
                        else:
                            output["error"] = f"Mineral/Phase '{query_term}' not found within PHASES block."
                else:
                    output["error"] = f"Could not extract PHASES block from database."
            else:
                # Database doesn't have a standard PHASES block
                # Try looking for the term directly as some databases don't use standard block structure
                logger.info(f"Database doesn't have a standard PHASES block, searching for '{query_term}' directly")
                
                # Do a more relaxed search without requiring a PHASES block
                direct_matches = []
                for line in content.splitlines():
                    if query_term.lower() in line.lower() and not line.strip().startswith('#'):
                        # Check if it's likely a mineral definition
                        if re.search(r"=\s+[A-Za-z0-9]+[+\-0-9]*\s+\+", line):
                            direct_matches.append(line)
                            # Try to get the following lines that might be part of the same definition
                            i = content.splitlines().index(line)
                            for j in range(1, 5):  # Get up to 4 more lines
                                if i+j < len(content.splitlines()):
                                    next_line = content.splitlines()[i+j]
                                    if not next_line.strip().startswith('#') and not re.match(r"^\s*[A-Za-z0-9_\-]+\s+=", next_line):
                                        direct_matches.append(next_line)
                                    else:
                                        break
                            break
                
                if direct_matches:
                    results = "\n".join(direct_matches)
                    logger.info(f"Found direct match for '{query_term}' outside of PHASES block")
                else:
                    output["error"] = f"PHASES block not found and mineral '{query_term}' not found directly in database content."

        elif query_type.lower() == 'species':
            # Find species in SOLUTION_SPECIES block
            species_block_match = re.search(r"^SOLUTION_SPECIES\b(.*?)(?=^[A-Z_]+\b|\Z)", content, 
                                          re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if species_block_match:
                species_block = species_block_match.group(1)
                
                # Try several patterns to find the species
                
                # 1. Try exact match at beginning of line
                species_match = re.search(f"^\\s*{re.escape(query_term)}\\b.*?(?=^\\s*[A-Za-z0-9_]+\\s+|$)", 
                                         species_block, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                
                # 2. If that fails, look for species in reaction equations
                if not species_match:
                    reaction_lines = []
                    for line in species_block.splitlines():
                        if query_term.lower() in line.lower() and '=' in line:
                            reaction_lines.append(line)
                            # Also get the next few lines that might have log_K etc.
                            i = species_block.splitlines().index(line)
                            for j in range(1, 5):  # Get up to 4 more lines
                                if i+j < len(species_block.splitlines()):
                                    next_line = species_block.splitlines()[i+j]
                                    if not next_line.strip().startswith('#') and not re.match(r"^\s*[A-Za-z0-9_\-]+\s+=", next_line):
                                        reaction_lines.append(next_line)
                                    else:
                                        break
                    
                    if reaction_lines:
                        results = "\n".join(reaction_lines)
                
                # 3. If we found a direct match with first pattern, extract it
                if species_match and not results:
                    results = species_match.group(0).strip()
                
                if not results:
                    output["error"] = f"Species '{query_term}' definition not found within SOLUTION_SPECIES block."
            else:
                output["error"] = "SOLUTION_SPECIES block not found in database."

        elif query_type.lower() == 'element_info':
            # Find element in SOLUTION_MASTER_SPECIES block
            master_block_match = re.search(r"^SOLUTION_MASTER_SPECIES\b(.*?)(?=^[A-Z_]+\b|\Z)", content, 
                                         re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if not master_block_match:
                # Try alternative pattern if the first one fails
                master_block_match = re.search(r"SOLUTION_MASTER_SPECIES(.*?)(?=^[A-Z_]+\b|\Z)", content, 
                                             re.MULTILINE | re.IGNORECASE | re.DOTALL)
                
            if master_block_match:
                master_block = master_block_match.group(1)
                
                # Try several approaches to find the element
                
                # 1. Exact match with full element syntax (including possible redox state)
                # Example: "Ca" or "Fe(2)"
                element_exact_match = re.search(f"^\\s*{re.escape(query_term)}\\s+.*", master_block, 
                                          re.MULTILINE | re.IGNORECASE)
                
                # 2. Match just the element symbol at beginning of line
                # For "Fe(2)" we want to match lines starting with "Fe"
                query_base = query_term.split('(')[0].strip()
                element_base_match = None
                if query_base != query_term:
                    element_base_match = re.search(f"^\\s*{re.escape(query_base)}\\s+.*", master_block, 
                                              re.MULTILINE | re.IGNORECASE)
                
                # 3. Try more flexible match for either exact or base element
                elements_found = []
                for line in master_block.splitlines():
                    line_lower = line.lower()
                    # Skip empty lines and comments
                    if not line.strip() or line.strip().startswith('#'):
                        continue
                        
                    line_words = line.split()
                    if len(line_words) > 0:
                        first_word = line_words[0].lower()
                        first_word_base = first_word.split('(')[0].lower()
                        
                        query_lower = query_term.lower()
                        query_base_lower = query_base.lower()
                        
                        # Check for match with either exact term or base element
                        if (first_word == query_lower or  # Exact match: "Ca" matches "Ca"
                            (query_lower != query_base_lower and first_word_base == query_base_lower) or  # Base match: "Fe" matches "Fe(2)"
                            (query_lower == query_base_lower and query_lower in first_word_base)):  # Partial match: "Fe" is in "Fe(2)"
                            elements_found.append(line)
                
                # Collect all results
                if element_exact_match:
                    elements_found.append(element_exact_match.group(0))
                
                if element_base_match and element_base_match.group(0) not in elements_found:
                    elements_found.append(element_base_match.group(0))
                
                if elements_found:
                    results = "\n".join(elements_found)
                else:
                    # Try expanded name/symbol matching
                    element_map = {
                        'calcium': 'Ca',
                        'magnesium': 'Mg',
                        'sodium': 'Na',
                        'potassium': 'K',
                        'iron': 'Fe',
                        'aluminum': 'Al',
                        'aluminium': 'Al',
                        'silicon': 'Si',
                        'sulfur': 'S',
                        'sulphur': 'S',
                        'nitrogen': 'N',
                        'carbon': 'C',
                        'hydrogen': 'H',
                        'oxygen': 'O',
                        'phosphorus': 'P',
                        'boron': 'B',
                        'fluorine': 'F',
                        'chlorine': 'Cl',
                        'bromine': 'Br',
                        'iodine': 'I',
                        'ca': 'Ca',
                        'mg': 'Mg',
                        'na': 'Na',
                        'k': 'K',
                        'fe': 'Fe',
                        'al': 'Al',
                        'si': 'Si',
                        's': 'S'
                    }
                    
                    suggestions = []
                    query_lower = query_term.lower()
                    
                    # If it's a symbol, suggest the full name
                    for name, symbol in element_map.items():
                        if query_lower == symbol.lower():
                            suggestions.append(name.capitalize())
                            # Also try to find this element in the database
                            element_match = re.search(f"^\\s*{re.escape(symbol)}\\b.*", master_block, 
                                                  re.MULTILINE | re.IGNORECASE)
                            if element_match:
                                results = element_match.group(0)
                                break
                    
                    # If it's a name, suggest the symbol and try to find it
                    if not results and query_lower in element_map:
                        symbol = element_map[query_lower]
                        suggestions.append(symbol)
                        element_match = re.search(f"^\\s*{re.escape(symbol)}\\b.*", master_block, 
                                              re.MULTILINE | re.IGNORECASE)
                        if element_match:
                            results = element_match.group(0)
                    
                    if not results and suggestions:
                        output["error"] = f"Element '{query_term}' not found within SOLUTION_MASTER_SPECIES block. Try alternative names: {', '.join(suggestions)}"
                        output["alternative_names"] = suggestions
                    elif not results:
                        output["error"] = f"Element '{query_term}' not found within SOLUTION_MASTER_SPECIES block."
            else:
                # No SOLUTION_MASTER_SPECIES block found
                
                # Try direct search for the element in the entire file
                element_lines = []
                for line in content.splitlines():
                    if query_term.lower() in line.lower() and not line.strip().startswith('#'):
                        # Check if it's likely an element definition
                        if ('element' in line.lower() or 'species' in line.lower() or 'master' in line.lower()):
                            element_lines.append(line)
                            
                if element_lines:
                    results = "\n".join(element_lines[:5])  # Limit to 5 lines to avoid overwhelming
                    logger.info(f"Found element '{query_term}' outside of SOLUTION_MASTER_SPECIES block")
                else:
                    output["error"] = f"SOLUTION_MASTER_SPECIES block not found and element '{query_term}' not found in database content."

        else:
            output["error"] = f"Unsupported query type: {query_type}"

        if results:
            output["results"] = results
        elif not output["error"]:
            output["error"] = f"Term '{query_term}' of type '{query_type}' not found."

    except FileNotFoundError:
        error_message = f"Database file not found: {db_to_use}"
        logger.error(error_message)
        output["error"] = error_message
        output["error_type"] = "database_not_found"
        output["suggestion"] = "Check that the database path is correct and the file exists."
    except Exception as e:
        error_message = f"Error querying database: {e}"
        logger.exception(f"Error during database query")
        output["error"] = error_message
        output["error_type"] = "database_query_error"
        output["context"] = {
            "query_term": query_term,
            "query_type": query_type,
            "exception": str(e)
        }

    return output
