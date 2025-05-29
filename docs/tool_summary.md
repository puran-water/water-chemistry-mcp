# Water Chemistry MCP Tools Summary

## Tool Overview

| Tool Name | Main Function | Purpose | Key Inputs | Key Outputs | pH Adjustment | Precipitation |
|-----------|---------------|---------|------------|-------------|---------------|---------------|
| **calculate_solution_speciation** | `calculate_solution_speciation()` | Calculates full speciation of a water sample including pH, pe, saturation indices, species distribution | - analysis (element/species concentrations)<br>- pH, pe, temperature, pressure<br>- units, charge_balance<br>- database | - solution_summary (pH, pe, ionic strength)<br>- saturation_indices<br>- species concentrations<br>- elemental totals | ❌ (only calculates) | ❌ (only calculates SI) |
| **simulate_chemical_addition** | `simulate_chemical_addition()` | Simulates adding chemicals to a solution and calculates resulting equilibrium | - initial_solution<br>- reactants (list of chemicals with amounts)<br>- allow_precipitation<br>- equilibrium_minerals<br>- database | - solution_summary<br>- saturation_indices<br>- precipitated_phases<br>- species concentrations | ✅ (through chemical addition) | ✅ (configurable) |
| **calculate_dosing_requirement** | `calculate_dosing_requirement()` | Calculates required dose of a reagent to meet a target condition (e.g., target pH) | - initial_solution<br>- target_condition (parameter, value)<br>- reagent (formula)<br>- max_iterations, tolerance<br>- allow_precipitation | - required_dose_mmol_per_L<br>- final_state<br>- iterations_taken<br>- convergence_status | ✅ (target-based) | ✅ (configurable) |
| **predict_scaling_potential** | `predict_scaling_potential()` | Predicts mineral scaling potential and optionally calculates precipitation amounts | - water analysis<br>- force_equilibrium_minerals<br>- database<br>- recovery parameters (for membrane systems) | - saturation_indices<br>- precipitated_phases<br>- solution_summary | ❌ (only predicts) | ✅ (optional equilibrium) |
| **simulate_solution_mixing** | `simulate_solution_mixing()` | Simulates mixing multiple solutions and calculates resulting equilibrium | - solutions_to_mix (list with fractions/volumes)<br>- allow_precipitation<br>- database | - mixed_solution properties<br>- saturation_indices<br>- precipitated_phases | ❌ (only from mixing) | ✅ (automatic based on chemistry) |
| **simulate_redox_adjustment** | `simulate_redox_adjustment()` | Adjusts solution redox state via pe, Eh, or equilibrium with redox couple | - initial_solution<br>- target_redox (pe, Eh_mV, or couple)<br>- database | - solution with adjusted redox<br>- saturation_indices<br>- species distribution | ❌ | ❌ |
| **simulate_gas_phase_interaction** | `simulate_gas_phase_interaction()` | Simulates equilibration of solution with gas phase (e.g., CO2, O2) | - initial_solution<br>- gas_phase (composition, pressure)<br>- database | - equilibrated solution<br>- gas_phase composition<br>- saturation_indices | ✅ (indirect via CO2) | ❌ (disabled to avoid errors) |
| **simulate_kinetic_reaction** | `simulate_kinetic_reaction()` | Simulates reactions governed by kinetic rates over time | - initial_solution<br>- kinetic_reactions (RATES and KINETICS)<br>- time_steps<br>- allow_precipitation | - final_state after kinetics<br>- time-dependent results | ❌ | ✅ (configurable) |
| **query_thermodynamic_database** | `query_thermodynamic_database()` | Queries PHREEQC database for species, minerals, elements info | - query_term<br>- query_type<br>- database | - query results<br>- database information | ❌ | ❌ |

## Overlapping Functionality Analysis

### pH Adjustment Capabilities
1. **simulate_chemical_addition** - Can adjust pH by adding acids/bases (HCl, NaOH, etc.)
2. **calculate_dosing_requirement** - Specifically designed to calculate exact dose needed for target pH
3. **simulate_gas_phase_interaction** - Can indirectly affect pH through CO2 equilibration

### Precipitation Calculations
1. **simulate_chemical_addition** - Full precipitation with user-specified or auto-selected minerals
2. **calculate_dosing_requirement** - Includes precipitation during dosing calculations
3. **predict_scaling_potential** - Focused on scaling prediction, can force equilibrium
4. **simulate_solution_mixing** - Automatic precipitation based on mixed water chemistry
5. **simulate_kinetic_reaction** - Time-dependent precipitation with kinetic control

### Key Distinctions
- **Speciation vs. Reaction**: `calculate_solution_speciation` only analyzes existing water, while others modify it
- **Target-based vs. Amount-based**: `calculate_dosing_requirement` finds the dose for a target, while `simulate_chemical_addition` uses known amounts
- **Equilibrium vs. Kinetic**: Most tools assume equilibrium, but `simulate_kinetic_reaction` models time-dependent processes
- **Single vs. Multiple Solutions**: `simulate_solution_mixing` is unique in handling multiple input solutions

### Recommended Tool Selection
- **For pH adjustment**: Use `calculate_dosing_requirement` to find dose, then `simulate_chemical_addition` to verify
- **For precipitation analysis**: Use `predict_scaling_potential` for assessment, `simulate_chemical_addition` for treatment simulation
- **For complex scenarios**: Combine tools (e.g., mixing → chemical addition → scaling prediction)