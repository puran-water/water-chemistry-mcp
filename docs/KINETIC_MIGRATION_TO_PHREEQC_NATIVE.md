# Migration Guide: From Custom Kinetics to PHREEQC Native Rates

## Overview

The Water Chemistry MCP Server has been updated to use PHREEQC's official kinetic rates database (`phreeqc_rates.dat`) instead of custom Python rate functions. This migration provides access to peer-reviewed, temperature-dependent rate equations for mineral precipitation and dissolution.

## Key Benefits of Migration

1. **Official USGS Rate Equations**: Uses peer-reviewed kinetics from the literature
2. **Temperature Dependencies**: Built-in Arrhenius corrections
3. **Complex Rate Laws**: Multi-mechanism rates (H+, OH-, CO2 catalysis)
4. **Tested Integration**: Native PHREEQC solver for kinetic differential equations
5. **No Custom Code**: Reduces maintenance and improves reliability

## What Changed

### Previous Approach (Custom Rate Functions)
```python
# Old approach - custom Python rate functions
kinetic_params = {
    "enable_kinetics": True,
    "time_steps": [0, 60, 300, 600, 1800, 3600],
    "minerals_kinetic": {
        "Calcite": {
            "rate_constant": 1.55e-6,      # mol/m²/s
            "surface_area": 1.0,           # m²/L
            "activation_energy": 41800,    # J/mol
            "surface_area_exponent": 0.67,
            "nucleation_si_threshold": 0.5
        }
    }
}
```

### New Approach (PHREEQC Native)
```python
# New approach - PHREEQC native KINETICS blocks
kinetic_params = {
    "enable_kinetics": True,
    "use_phreeqc_rates": True,  # NEW - uses phreeqc_rates.dat
    "time_steps": [0, 60, 300, 600, 1800, 3600],
    "minerals_kinetic": {
        "Calcite": {
            "m0": 0.0,              # Initial moles
            "parms": [1.67e5, 0.6], # Surface area (cm²/mol), exponent
            "tol": 1e-8             # Integration tolerance
        }
    }
}
```

## Migration Steps

### Step 1: Update Your Input Parameters

Replace custom kinetic parameters with PHREEQC-style parameters:

| Old Parameter | New Parameter | Notes |
|--------------|---------------|-------|
| `rate_constant` | Built into RATES | Temperature-dependent in phreeqc_rates.dat |
| `surface_area` | `parms[0]` | Units vary by mineral (see below) |
| `activation_energy` | Built into RATES | Handled by PHREEQC |
| `surface_area_exponent` | `parms[1]` | Usually 0.6-0.67 |
| `nucleation_si_threshold` | Built into RATES | Part of rate equation |

### Step 2: Convert Surface Area Units

Different minerals use different surface area units in phreeqc_rates.dat:

- **Calcite**: cm²/mol (multiply m²/L by ~1.67e5)
- **Quartz**: m²/mol
- **K-feldspar**: m²/mol
- **Pyrite**: log10(m²/mol) as parms[0]

### Step 3: Use Helper Functions

```python
from utils.phreeqc_rates_info import format_for_mcp_input, get_mineral_info

# Get information about a mineral
info = get_mineral_info("Calcite")
print(info['description'])  # CaCO3 - Calcium carbonate
print(info['parameters'])   # Parameter descriptions

# Format parameters for MCP input
calcite_params = format_for_mcp_input("Calcite", surface_area=1.0)
# Returns: {"m0": 0.0, "parms": [1.67e5, 0.6], "tol": 1e-8}
```

## Example Migrations

### Example 1: Simple Calcite Precipitation

**Before (Custom):**
```python
result = await simulate_chemical_addition({
    "initial_solution": solution,
    "reactants": reactants,
    "kinetic_parameters": {
        "enable_kinetics": True,
        "minerals_kinetic": {
            "Calcite": {
                "rate_constant": 1.55e-6,
                "surface_area": 1.0,
                "activation_energy": 41800
            }
        }
    }
})
```

**After (PHREEQC Native):**
```python
result = await simulate_chemical_addition({
    "initial_solution": solution,
    "reactants": reactants,
    "kinetic_parameters": {
        "enable_kinetics": True,
        "use_phreeqc_rates": True,  # Use native rates
        "minerals_kinetic": {
            "Calcite": {
                "m0": 0.0,
                "parms": [1.67e5, 0.6],  # Convert surface area
                "tol": 1e-8
            }
        }
    }
})
```

### Example 2: Multiple Minerals

**Before:**
```python
minerals_kinetic = {
    "Calcite": {
        "rate_constant": 1.55e-6,
        "surface_area": 1.0
    },
    "Gypsum": {
        "rate_constant": 2.5e-8,
        "surface_area": 0.5
    }
}
```

**After:**
```python
minerals_kinetic = {
    "Calcite": {
        "m0": 0.0,
        "parms": [1.67e5, 0.6]
    }
    # Note: Gypsum not in phreeqc_rates.dat
    # Would need to use equilibrium for Gypsum
}
```

## Available Minerals in phreeqc_rates.dat

| Mineral | Description | Key Parameters |
|---------|-------------|----------------|
| Calcite | CaCO3 | parms: [surface_area_cm2/mol, M/M0_exp] |
| Quartz | SiO2 | parms: [surface_area_m2/mol, salt_factor] |
| K-feldspar | KAlSi3O8 | parms: [surface_area_m2/mol, field_factor] |
| Albite | NaAlSi3O8 | parms: [surface_area_m2/mol, field_factor] |
| Pyrite | FeS2 | parms: [log10_area, M/M0_exp, O2_exp, H+_exp] |
| Dolomite | CaMg(CO3)2 | Similar to calcite |
| Pyrolusite | MnO2 | parms: [surface_area_m2/g, M/M0_exp] |
| Organic_C | Organic matter | parms: [C0_mol/kgw, rate_const_1/s] |

## Backward Compatibility

The server maintains backward compatibility. To use the old custom rate functions:

```python
kinetic_params = {
    "enable_kinetics": True,
    "use_phreeqc_rates": False,  # Force custom functions
    "minerals_kinetic": {
        # Old-style parameters
    }
}
```

## Troubleshooting

### Issue: Mineral not found in phreeqc_rates.dat
**Solution**: Check available minerals with:
```python
from utils.phreeqc_rates_info import get_available_minerals
print(get_available_minerals())
```

### Issue: Different precipitation rates than before
**Cause**: PHREEQC uses different (often more accurate) rate equations
**Solution**: This is expected. PHREEQC rates are peer-reviewed and include more mechanisms.

### Issue: Surface area units confusion
**Solution**: Use the helper functions:
```python
from utils.phreeqc_rates_info import get_example_kinetics_block
print(get_example_kinetics_block("Calcite"))
```

## Best Practices

1. **Always use PHREEQC native rates when available** - They're more accurate and tested
2. **Check mineral availability first** - Not all minerals have kinetic rates
3. **Use appropriate surface areas** - Industrial systems typically 0.1-10 m²/L
4. **Consider field vs lab rates** - Field rates often 10-100x slower
5. **Set appropriate tolerances** - Default 1e-8 is usually good

## Summary

The migration to PHREEQC native kinetics provides:
- ✓ More accurate rate equations
- ✓ Built-in temperature dependencies  
- ✓ Peer-reviewed kinetic parameters
- ✓ Better integration with PHREEQC solver
- ✓ Reduced maintenance burden

While the input format has changed, the functionality remains the same with improved accuracy and reliability.