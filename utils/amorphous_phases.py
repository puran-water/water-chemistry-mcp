"""
Amorphous phases commonly found in water treatment systems.
These phases are important for accurate precipitation modeling.

Enhanced to address silicate precipitation issues:
- Lowered silica threshold from 50 to 10 mmol/L
- Added Chrysotile, Tremolite, Talc, and Quartz phases
- Improved Mg-Si coprecipitation logic for ZLD applications
- Better phase selection based on Mg:Si ratios
"""

# Common amorphous phases in water treatment
AMORPHOUS_PHASES = {
    # Iron phases
    "Ferrihydrite": {
        "formula": "Fe(OH)3",
        "description": "Amorphous iron hydroxide",
        "conditions": "Forms at pH > 3, especially with rapid oxidation",
        "databases": ["minteq.dat", "wateq4f.dat"]
    },
    "Fe(OH)3(a)": {
        "formula": "Fe(OH)3",
        "description": "Amorphous ferric hydroxide",
        "conditions": "Precursor to goethite and hematite",
        "databases": ["phreeqc.dat", "minteq.dat"]
    },
    
    # Aluminum phases
    "Al(OH)3(a)": {
        "formula": "Al(OH)3",
        "description": "Amorphous aluminum hydroxide",
        "conditions": "Forms at pH 4-9, precursor to gibbsite",
        "databases": ["phreeqc.dat", "minteq.dat", "wateq4f.dat"]
    },
    "Basaluminite": {
        "formula": "Al4(OH)10SO4",
        "description": "Basic aluminum sulfate",
        "conditions": "Forms in acidic waters with sulfate",
        "databases": ["minteq.dat"]
    },
    
    # Silica phases
    "SiO2(a)": {
        "formula": "SiO2",
        "description": "Amorphous silica",
        "conditions": "Forms when Si > 100-120 mg/L",
        "databases": ["phreeqc.dat", "minteq.dat", "wateq4f.dat", "llnl.dat"]
    },
    "Chalcedony": {
        "formula": "SiO2",
        "description": "Microcrystalline silica",
        "conditions": "More stable than amorphous silica",
        "databases": ["phreeqc.dat", "minteq.dat", "llnl.dat"]
    },
    "Quartz": {
        "formula": "SiO2",
        "description": "Crystalline silica",
        "conditions": "Most stable silica form, slow kinetics",
        "databases": ["phreeqc.dat", "minteq.dat", "wateq4f.dat", "llnl.dat"]
    },
    
    # Magnesium-silicate phases (important for silica removal)
    "Sepiolite": {
        "formula": "Mg4Si6O15(OH)2:6H2O",
        "description": "Magnesium silicate clay",
        "conditions": "Forms at high pH with Mg and Si present",
        "databases": ["minteq.dat", "llnl.dat"]
    },
    "Antigorite": {
        "formula": "Mg48Si34O85(OH)62",
        "description": "Serpentine group mineral",
        "conditions": "Mg-Si phase at high pH",
        "databases": ["llnl.dat"]
    },
    "Chrysotile": {
        "formula": "Mg3Si2O5(OH)4",
        "description": "Serpentine group mineral (asbestos form)",
        "conditions": "Forms at pH > 9 with high Mg and Si",
        "databases": ["llnl.dat", "minteq.dat"]
    },
    "Tremolite": {
        "formula": "Ca2Mg5Si8O22(OH)2",
        "description": "Amphibole group mineral",
        "conditions": "Ca-Mg-Si phase at high pH",
        "databases": ["llnl.dat"]
    },
    "Talc": {
        "formula": "Mg3Si4O10(OH)2",
        "description": "Magnesium silicate hydroxide",
        "conditions": "Forms in Mg-rich, high pH systems",
        "databases": ["llnl.dat", "minteq.dat"]
    },
    "M-S-H": {
        "formula": "MgSiO3·H2O",
        "description": "Magnesium silicate hydrate (synthetic)",
        "conditions": "Forms during Mg(OH)2 + silica interaction",
        "databases": []  # May need custom PHASES data
    },
    
    # Calcium phases
    "CSH": {
        "formula": "CaSiO3·H2O",
        "description": "Calcium silicate hydrate",
        "conditions": "Forms in high Ca, high pH systems",
        "databases": ["cemdata18.dat"]  # Cement database
    },
    
    # Phosphate phases
    "Strengite": {
        "formula": "FePO4:2H2O",
        "description": "Ferric phosphate",
        "conditions": "Forms in Fe-P systems at pH < 5",
        "databases": ["minteq.dat"]
    },
    "MgNH4PO4:6H2O(s)": {
        "formula": "MgNH4PO4:6H2O",
        "description": "Struvite",
        "conditions": "Forms in wastewater with Mg, N, P",
        "databases": ["minteq.dat"]
    }
}

def get_recommended_amorphous_phases(solution_composition, database="minteq.dat"):
    """
    Recommend amorphous phases to include based on solution composition.
    Enhanced to address silicate precipitation issues from MCP client feedback.
    
    Args:
        solution_composition: Dict of element concentrations and pH
        database: Database being used
        
    Returns:
        List of amorphous phase names to include
    """
    pH = solution_composition.get('pH', 7.0)
    return get_amorphous_phases_for_system(solution_composition, pH, database)


def get_amorphous_phases_for_system(solution_composition, pH, database="minteq.dat"):
    """
    Recommend amorphous phases to include based on solution composition.
    
    Args:
        solution_composition: Dict of element concentrations
        pH: Solution pH
        database: Database being used
        
    Returns:
        List of amorphous phase names to include
    """
    recommended_phases = []
    
    # Check for iron
    if solution_composition.get('Fe', 0) > 0.1:  # mg/L
        if pH > 3:
            recommended_phases.append("Ferrihydrite")
            recommended_phases.append("Fe(OH)3(a)")
    
    # Check for aluminum
    if solution_composition.get('Al', 0) > 0.1:
        if 4 < pH < 9:
            recommended_phases.append("Al(OH)3(a)")
        if solution_composition.get('S(6)', 0) > 10 and pH < 5:
            recommended_phases.append("Basaluminite")
    
    # Check for silica (enhanced support for high-Si wastewaters)
    si_concentration = solution_composition.get('Si', 0)
    
    # Assume input is in mmol/L unless it's very large (>1000 suggests mg/L)
    if si_concentration > 1000:
        # Convert from mg/L to mmol/L (Si atomic weight = 28.09 g/mol)
        si_mmol = si_concentration / 28.09
    else:
        # Assume already in mmol/L
        si_mmol = si_concentration
    
    # Lower threshold to 10 mmol/L (280 mg/L) for better silicate precipitation
    if si_mmol > 10:  # 10 mmol/L threshold
        # Always include amorphous silica phases
        recommended_phases.extend(["SiO2(a)", "Chalcedony"])
        
        if si_mmol > 20:  # Medium silica systems
            recommended_phases.append("Quartz")
        
        # Enhanced Mg-Si coprecipitation logic for ZLD applications
        mg_concentration = solution_composition.get('Mg', 0)
        
        # Handle Mg concentration units (assume mmol/L unless very large)
        if mg_concentration > 1000:
            # Convert from mg/L to mmol/L (Mg atomic weight = 24.31 g/mol)
            mg_mmol = mg_concentration / 24.31
        else:
            # Assume already in mmol/L
            mg_mmol = mg_concentration
        
        # Lower Mg threshold and pH for better coprecipitation
        if mg_mmol > 2 and pH > 8.0:
            # Primary Mg-silicate phases
            recommended_phases.extend(["Sepiolite", "Talc"])
            
            # Add database-specific phases
            if "llnl.dat" in database.lower():
                recommended_phases.extend(["Antigorite", "Chrysotile"])
                
                # Add tremolite if Ca is also present
                ca_concentration = solution_composition.get('Ca', 0)
                if ca_concentration > 40:  # 1 mmol/L Ca
                    recommended_phases.append("Tremolite")
            
            # For high Mg systems, add all serpentine minerals
            if mg_mmol > 5:
                recommended_phases.extend(["Chrysotile", "Antigorite"])
                
        # Check Mg:Si ratio for optimal phase selection
        if mg_mmol > 0 and si_mmol > 0:
            mg_si_ratio = mg_mmol / si_mmol
            
            # Sepiolite favored at Mg:Si ~ 0.67
            if 0.5 < mg_si_ratio < 1.0:
                recommended_phases.append("Sepiolite")
            
            # Talc favored at Mg:Si ~ 0.75
            elif 0.6 < mg_si_ratio < 0.9:
                recommended_phases.append("Talc")
            
            # Chrysotile favored at Mg:Si ~ 1.5
            elif 1.2 < mg_si_ratio < 2.0:
                recommended_phases.append("Chrysotile")
        
        # Ca-Si phases with lower threshold
        ca_concentration = solution_composition.get('Ca', 0)
        if ca_concentration > 40 and pH > 9:  # 1 mmol/L Ca
            recommended_phases.append("CSH")
    
    # Check for phosphate systems
    if solution_composition.get('P', 0) > 0.5:
        if solution_composition.get('Fe', 0) > 1 and pH < 5:
            recommended_phases.append("Strengite")
        if solution_composition.get('Mg', 0) > 5 and solution_composition.get('N(-3)', 0) > 5:
            recommended_phases.append("MgNH4PO4:6H2O(s)")
    
    # Filter by database availability
    available_phases = []
    for phase in recommended_phases:
        if phase in AMORPHOUS_PHASES:
            phase_info = AMORPHOUS_PHASES[phase]
            # Check if phase is in the current database
            if not phase_info["databases"] or any(db in database for db in phase_info["databases"]):
                available_phases.append(phase)
    
    return list(set(available_phases))  # Remove duplicates


def should_include_silicate_phases(saturation_indices):
    """
    Check if silicate phases should be included based on SI values.
    
    Args:
        saturation_indices: Dict of phase names to SI values
        
    Returns:
        bool: True if any silicate phase has positive SI
    """
    silicate_phases = [
        "SiO2(a)", "Chalcedony", "Quartz", "Sepiolite", 
        "Antigorite", "Chrysotile", "Tremolite", "Talc"
    ]
    
    for phase in silicate_phases:
        if phase in saturation_indices and saturation_indices[phase] > 0:
            return True
    
    return False


def get_precipitating_phases(saturation_indices, si_threshold=0.0):
    """
    Get list of phases that are supersaturated and likely to precipitate.
    
    Args:
        saturation_indices: Dict of phase names to SI values
        si_threshold: Minimum SI value to consider for precipitation
        
    Returns:
        List of phase names with SI above threshold
    """
    precipitating = []
    
    for phase, si in saturation_indices.items():
        if si > si_threshold:
            precipitating.append(phase)
    
    # Sort by SI value (highest first)
    precipitating.sort(key=lambda p: saturation_indices[p], reverse=True)
    
    return precipitating
