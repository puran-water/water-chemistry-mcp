"""
Amorphous phases commonly found in water treatment systems.
These phases are important for accurate precipitation modeling.

Enhanced to address silicate precipitation issues:
- Lowered silica threshold from 50 to 10 mmol/L
- Added Chrysotile, Tremolite, Talc, and Quartz phases
- Improved Mg-Si coprecipitation logic for ZLD applications
- Better phase selection based on Mg:Si ratios
"""
AMORPHOUS_PHASES = {'Ferrihydrite': {'formula': 'Fe(OH)3', 'description': 'Amorphous iron hydroxide', 'conditions': 'Forms at pH > 3, especially with rapid oxidation', 'databases': ['minteq.dat', 'wateq4f.dat']}, 'Fe(OH)3(a)': {'formula': 'Fe(OH)3', 'description': 'Amorphous ferric hydroxide', 'conditions': 'Precursor to goethite and hematite', 'databases': ['phreeqc.dat', 'minteq.dat']}, 'Al(OH)3(a)': {'formula': 'Al(OH)3', 'description': 'Amorphous aluminum hydroxide', 'conditions': 'Forms at pH 4-9, precursor to gibbsite', 'databases': ['phreeqc.dat', 'minteq.dat', 'wateq4f.dat']}, 'Basaluminite': {'formula': 'Al4(OH)10SO4', 'description': 'Basic aluminum sulfate', 'conditions': 'Forms in acidic waters with sulfate', 'databases': ['minteq.dat']}, 'SiO2(a)': {'formula': 'SiO2', 'description': 'Amorphous silica', 'conditions': 'Forms when Si > 100-120 mg/L', 'databases': ['phreeqc.dat', 'minteq.dat', 'wateq4f.dat', 'llnl.dat']}, 'Chalcedony': {'formula': 'SiO2', 'description': 'Microcrystalline silica', 'conditions': 'More stable than amorphous silica', 'databases': ['phreeqc.dat', 'minteq.dat', 'llnl.dat']}, 'Quartz': {'formula': 'SiO2', 'description': 'Crystalline silica', 'conditions': 'Most stable silica form, slow kinetics', 'databases': ['phreeqc.dat', 'minteq.dat', 'wateq4f.dat', 'llnl.dat']}, 'Sepiolite': {'formula': 'Mg4Si6O15(OH)2:6H2O', 'description': 'Magnesium silicate clay', 'conditions': 'Forms at high pH with Mg and Si present', 'databases': ['minteq.dat', 'llnl.dat']}, 'Antigorite': {'formula': 'Mg48Si34O85(OH)62', 'description': 'Serpentine group mineral', 'conditions': 'Mg-Si phase at high pH', 'databases': ['llnl.dat']}, 'Chrysotile': {'formula': 'Mg3Si2O5(OH)4', 'description': 'Serpentine group mineral (asbestos form)', 'conditions': 'Forms at pH > 9 with high Mg and Si', 'databases': ['llnl.dat', 'minteq.dat']}, 'Tremolite': {'formula': 'Ca2Mg5Si8O22(OH)2', 'description': 'Amphibole group mineral', 'conditions': 'Ca-Mg-Si phase at high pH', 'databases': ['llnl.dat']}, 'Talc': {'formula': 'Mg3Si4O10(OH)2', 'description': 'Magnesium silicate hydroxide', 'conditions': 'Forms in Mg-rich, high pH systems', 'databases': ['llnl.dat', 'minteq.dat']}, 'M-S-H': {'formula': 'MgSiO3·H2O', 'description': 'Magnesium silicate hydrate (synthetic)', 'conditions': 'Forms during Mg(OH)2 + silica interaction', 'databases': []}, 'CSH': {'formula': 'CaSiO3·H2O', 'description': 'Calcium silicate hydrate', 'conditions': 'Forms in high Ca, high pH systems', 'databases': ['cemdata18.dat']}, 'Strengite': {'formula': 'FePO4:2H2O', 'description': 'Ferric phosphate', 'conditions': 'Forms in Fe-P systems at pH < 5', 'databases': ['minteq.dat']}, 'Vivianite': {'formula': 'Fe3(PO4)2:8H2O', 'description': 'Ferrous phosphate octahydrate', 'conditions': 'Forms under anaerobic conditions with Fe(II) and P', 'databases': ['minteq.dat', 'phreeqc.dat', 'wateq4f.dat']}, 'MgNH4PO4:6H2O(s)': {'formula': 'MgNH4PO4:6H2O', 'description': 'Struvite', 'conditions': 'Forms in wastewater with Mg, N, P', 'databases': ['minteq.dat']}, 'FeS(ppt)': {'formula': 'FeS', 'description': 'Amorphous iron sulfide (precipitate)', 'conditions': 'Forms under sulfidic, anaerobic conditions', 'databases': ['minteq.dat', 'wateq4f.dat']}, 'Mackinawite': {'formula': 'FeS', 'description': 'Tetragonal iron sulfide', 'conditions': 'Precursor to pyrite under anaerobic conditions', 'databases': ['phreeqc.dat', 'wateq4f.dat', 'minteq.dat']}, 'Siderite': {'formula': 'FeCO3', 'description': 'Iron carbonate', 'conditions': 'Forms under anaerobic conditions with high alkalinity', 'databases': ['phreeqc.dat', 'minteq.dat', 'wateq4f.dat']}}

def get_recommended_amorphous_phases(solution_composition, database='minteq.dat'):
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

def get_amorphous_phases_for_system(solution_composition, pH, database='minteq.dat'):
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
    if solution_composition.get('Fe', 0) > 0.1:
        if pH > 3:
            recommended_phases.append('Ferrihydrite')
            recommended_phases.append('Fe(OH)3(a)')
    if solution_composition.get('Al', 0) > 0.1:
        if 4 < pH < 9:
            recommended_phases.append('Al(OH)3(a)')
        if solution_composition.get('S(6)', 0) > 10 and pH < 5:
            recommended_phases.append('Basaluminite')
    si_concentration = solution_composition.get('Si', 0)
    if si_concentration > 1000:
        si_mmol = si_concentration / 28.09
    else:
        si_mmol = si_concentration
    if si_mmol > 10:
        recommended_phases.extend(['SiO2(a)', 'Chalcedony'])
        if si_mmol > 20:
            recommended_phases.append('Quartz')
        mg_concentration = solution_composition.get('Mg', 0)
        if mg_concentration > 1000:
            mg_mmol = mg_concentration / 24.31
        else:
            mg_mmol = mg_concentration
        if mg_mmol > 2 and pH > 8.0:
            recommended_phases.extend(['Sepiolite', 'Talc'])
            if 'llnl.dat' in database.lower():
                recommended_phases.extend(['Antigorite', 'Chrysotile'])
                ca_concentration = solution_composition.get('Ca', 0)
                if ca_concentration > 40:
                    recommended_phases.append('Tremolite')
            if mg_mmol > 5:
                recommended_phases.extend(['Chrysotile', 'Antigorite'])
        if mg_mmol > 0 and si_mmol > 0:
            mg_si_ratio = mg_mmol / si_mmol
            if 0.5 < mg_si_ratio < 1.0:
                recommended_phases.append('Sepiolite')
            elif 0.6 < mg_si_ratio < 0.9:
                recommended_phases.append('Talc')
            elif 1.2 < mg_si_ratio < 2.0:
                recommended_phases.append('Chrysotile')
        ca_concentration = solution_composition.get('Ca', 0)
        if ca_concentration > 40 and pH > 9:
            recommended_phases.append('CSH')
    p_concentration = solution_composition.get('P', solution_composition.get('P(5)', 0))
    if p_concentration > 0.5:
        fe_concentration = solution_composition.get('Fe', 0)
        if fe_concentration > 1 and pH < 5:
            recommended_phases.append('Strengite')
        pe = solution_composition.get('pe', 4.0)
        if fe_concentration > 1 and pe < 0:
            recommended_phases.append('Vivianite')
        if solution_composition.get('Mg', 0) > 5 and solution_composition.get('N(-3)', 0) > 5:
            recommended_phases.append('MgNH4PO4:6H2O(s)')
    sulfide_concentration = solution_composition.get('S(-2)', 0)
    fe_concentration = solution_composition.get('Fe', 0)
    pe = solution_composition.get('pe', 4.0)
    if sulfide_concentration > 0.1 and fe_concentration > 0.1 and (pe < 0):
        recommended_phases.extend(['FeS(ppt)', 'Mackinawite'])
    alkalinity = solution_composition.get('Alkalinity', solution_composition.get('C(4)', 0))
    if alkalinity > 100 and fe_concentration > 1 and (pe < 0):
        recommended_phases.append('Siderite')
    available_phases = []
    for phase in recommended_phases:
        if phase in AMORPHOUS_PHASES:
            phase_info = AMORPHOUS_PHASES[phase]
            if not phase_info['databases'] or any((db in database for db in phase_info['databases'])):
                available_phases.append(phase)
    return list(set(available_phases))

def should_include_silicate_phases(saturation_indices):
    """
    Check if silicate phases should be included based on SI values.

    Args:
        saturation_indices: Dict of phase names to SI values

    Returns:
        bool: True if any silicate phase has positive SI
    """
    silicate_phases = ['SiO2(a)', 'Chalcedony', 'Quartz', 'Sepiolite', 'Antigorite', 'Chrysotile', 'Tremolite', 'Talc']
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
    precipitating.sort(key=lambda p: saturation_indices[p], reverse=True)
    return precipitating
