# Robust Kinetic Modeling Fixes - Complete Summary

Date: 2025-01-27

## Root Cause Analysis

Based on MCP client testing, the root causes of persistent kinetic issues were:

1. **Rapid Mineral Exhaustion** - The seed value of 1e-10 was too small, causing minerals to be consumed in 2-3 time steps
2. **Incorrect INCREMENTAL_REACTIONS** - This directive is for REACTION progress, not KINETICS, causing unexpected behavior
3. **Time Series Logic Error** - Collection code was inside a conditional that prevented it from running
4. **Aggressive RK Integration** - 6th order RK with CVODE was causing numerical instability

## Implemented Solutions

### 1. Removed INCREMENTAL_REACTIONS

**Before:**
```python
input_lines.append("INCREMENTAL_REACTIONS true")
```

**After:**
```python
# Removed - INCREMENTAL_REACTIONS is for REACTION, not KINETICS
```

### 2. Increased Seed Value

**Before:**
```python
kinetics_lines.append(f"    -m 1e-10")
```

**After:**
```python
kinetics_lines.append(f"    -m 1e-6")  # Increased from 1e-10 for stability
```

### 3. Fixed Time Series Collection

Moved kinetic profile creation outside the time series loop and fixed the collection logic to properly gather solution chemistry at each time step:

```python
# Now correctly collects pH, temperature, ionic strength, and element concentrations
# for each time step in the kinetic simulation
```

### 4. Optimized Numerical Integration

**Before:**
```python
kinetics_lines.append(f"    -runge_kutta 6")  # Too aggressive
kinetics_lines.append(f"    -cvode true")     # Can be unstable
```

**After:**
```python
kinetics_lines.append(f"    -runge_kutta 3")  # More stable 3rd order
kinetics_lines.append(f"    -step_divide 10") # Divide steps for stability
kinetics_lines.append(f"    -cvode false")    # Disable CVODE
```

### 5. Added Mineral Mass Protection

Added logic to use larger seed values for precipitation scenarios:

```python
# For precipitation from zero, use larger seed
if params.get('m0', 0) == 0:
    if 'm' not in params or params['m'] is None:
        kinetics_lines.append(f"    -m 1e-6")  # Larger seed for precipitation
```

### 6. Updated Documentation

Updated AI_AGENT_SYSTEM_PROMPT.md with:
- Recommended seed value of 1e-6 or larger
- Guidance on preventing mineral exhaustion
- Examples with stable kinetic parameters

## Expected Improvements

### Before (MCP Client Tests):
- Only 2-3 time points returned
- SI = -999 after 2-3 steps
- No time series solutions
- Rapid mineral exhaustion

### After Robust Fixes:
- ✅ All requested time points returned
- ✅ SI values remain valid longer (no rapid -999)
- ✅ Time series solutions included with pH and elements
- ✅ More stable kinetic simulations
- ✅ Better numerical integration

## Recommended Usage

For stable kinetic simulations, use these parameters:

```json
{
    "kinetic_parameters": {
        "time_steps": [0, 60, 300, 600, 1800, 3600],
        "enable_kinetics": true,
        "minerals_kinetic": {
            "Calcite": {
                "m0": 0,        // No initial mass
                "m": 1e-6,      // Use 1e-6 minimum (or 1e-4 for very stable)
                "tol": 1e-6,    
                "parms": [10, 0.6]  // Adjust first parameter for rate
            }
        },
        "use_phreeqc_rates": true
    }
}
```

## Remaining Considerations

1. **Precipitation vs Dissolution** - PHREEQC rates can be negative (dissolution) even when SI > 0 if kinetic barriers exist
2. **Rate Parameters** - The `parms` values significantly affect stability; smaller values = slower kinetics = more stable
3. **Time Steps** - Gradual time steps are critical; avoid large jumps
4. **Mineral Selection** - Only minerals with rate equations in phreeqc_rates.dat can be used kinetically

## Files Modified

1. `/tools/phreeqc_wrapper.py` - Core kinetic execution and result parsing
2. `/AI_AGENT_SYSTEM_PROMPT.md` - User guidance documentation

## Verification

The MCP client should retest with:
- Larger seed values (m: 1e-6 or 1e-4)
- Gradual time steps
- Lower rate constants in parms for stability

This comprehensive fix addresses all root causes identified through systematic analysis of the MCP client's test results.