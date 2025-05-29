# Tool Migration Guide

## Overview

The Water Chemistry MCP Server has been streamlined from 9 tools to 5 core tools focused on industrial wastewater applications. This guide helps users migrate from archived tools to the core tool set.

## Archived Tools and Alternatives

### 1. **simulate_gas_phase_interaction** → Use **simulate_chemical_addition**

**Previous usage:**
```python
# Old way - gas phase equilibration
result = await simulate_gas_phase_interaction({
    "initial_solution": water_sample,
    "gas_phase": {
        "composition": {"CO2(g)": 0.0003},  # 300 ppm
        "pressure": 1.0
    }
})
```

**New approach:**
```python
# New way - add dissolved gas directly
result = await simulate_chemical_addition({
    "initial_solution": water_sample,
    "reactants": [{"formula": "CO2", "amount": 0.01, "units": "mol"}],
    "allow_precipitation": True
})
```

### 2. **simulate_redox_adjustment** → Use **simulate_chemical_addition**

**Previous usage:**
```python
# Old way - adjust redox state
result = await simulate_redox_adjustment({
    "initial_solution": water_sample,
    "target_redox": {"pe": 8.0}
})
```

**New approach:**
```python
# New way - add oxidant/reductant
result = await simulate_chemical_addition({
    "initial_solution": water_sample,
    "reactants": [{"formula": "O2", "amount": 0.001, "units": "mol"}],  # Oxidant
    # Or use H2O2, Cl2, etc. for oxidation
    # Or use Na2S2O3, FeSO4, etc. for reduction
})
```

### 3. **simulate_kinetic_reaction** → Use **simulate_chemical_addition** (equilibrium assumed)

**Previous usage:**
```python
# Old way - kinetic precipitation
result = await simulate_kinetic_reaction({
    "initial_solution": water_sample,
    "kinetic_reactions": {...},
    "time_steps": [...]
})
```

**New approach:**
```python
# New way - assume equilibrium (standard for industrial design)
result = await simulate_chemical_addition({
    "initial_solution": water_sample,
    "reactants": [...],
    "allow_precipitation": True,
    "equilibrium_minerals": ["Calcite", "Gypsum", ...]
})
```

### 4. **query_thermodynamic_database** → Not needed for typical operations

The database query functionality was rarely used. If you need to check mineral availability:
- Use the mineral in your calculation - if SI returns -999, it's not available
- Check the recommended database (minteq.dat) documentation

## Core Tools Quick Reference

| Task | Tool to Use |
|------|------------|
| Analyze water quality | `calculate_solution_speciation` |
| Simulate treatment (known dose) | `simulate_chemical_addition` |
| Find optimal dose | `calculate_dosing_requirement` |
| Blend water streams | `simulate_solution_mixing` |
| Check scaling risk | `predict_scaling_potential` |

## Common Workflows

### pH Adjustment
1. Use `calculate_dosing_requirement` to find NaOH/HCl dose
2. Verify with `simulate_chemical_addition`

### Precipitation Treatment
1. Use `calculate_solution_speciation` to analyze initial water
2. Use `simulate_chemical_addition` to model treatment
3. Use `predict_scaling_potential` to check for unwanted scaling

### Membrane Pretreatment
1. Use `predict_scaling_potential` with recovery parameters
2. Use `simulate_chemical_addition` to model antiscalant or softening
3. Re-check with `predict_scaling_potential`

## Benefits of Consolidation

- **Clearer tool selection** - Each tool has a distinct purpose
- **Faster execution** - Less overhead from unused tools
- **Easier maintenance** - Focused on core industrial needs
- **Better documentation** - More focused on actual use cases

## Need Help?

If you have a use case that seems to require an archived tool, consider:
1. Can it be modeled with equilibrium assumptions? (usually yes)
2. Is it a common industrial application? (if yes, it's supported)
3. Contact support for guidance on complex scenarios

## Archived Tools Location

The archived tools are preserved in `_archived_tools/` directory if you need to reference their implementation.