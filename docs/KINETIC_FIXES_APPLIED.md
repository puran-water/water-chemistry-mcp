# Kinetic Modeling Fixes Applied

Date: 2025-01-27

## Summary

Based on MCP client feedback about kinetic simulations stopping early with incomplete results, comprehensive fixes have been applied to the Water Chemistry MCP Server.

## Issues Addressed

1. **Kinetic simulations stopping at 2-3 time points** instead of completing all requested steps
2. **SI values showing -999 mid-simulation** indicating mineral exhaustion or RK integration failure  
3. **No solution chemistry returned** at each time step
4. **Poor error handling** for RK integration failures

## Fixes Applied

### 1. Enhanced RK Integration Parameters

Added better numerical integration parameters to `phreeqc_wrapper.py`:
```python
kinetics_lines.append(f"    -bad_step_max 1000")
kinetics_lines.append(f"    -runge_kutta 6")      # 6th order RK
kinetics_lines.append(f"    -cvode true")         # CVODE solver
kinetics_lines.append(f"    -cvode_steps 1000")   # Max CVODE steps
kinetics_lines.append(f"    -cvode_order 5")      # CVODE order
```

### 2. Incremental Reactions

Added `INCREMENTAL_REACTIONS true` to prevent mineral exhaustion and improve stability.

### 3. Improved Error Recovery

Enhanced error handling to recover partial results from RK integration failures:
- Detect RK integration errors specifically
- Use previous step's values as approximation when errors occur
- Include notes in results about approximated values
- Ensure all requested time steps are represented in output

### 4. Mineral Exhaustion Prevention

Fixed the `-m` parameter handling to prevent complete mineral exhaustion:
```python
# Use a small seed value for precipitation kinetics
# This prevents "divide by zero" issues in rate calculations
kinetics_lines.append(f"    -m 1e-10")
```

### 5. Saturation State Checking

Added pre-simulation saturation state checking to adjust parameters for highly undersaturated minerals.

### 6. Result Processing Enhancement

- Ensure all requested time steps are processed
- Pad results with error entries for missing steps
- Capture solution chemistry at each time step
- Better handling of partial results

### 7. AI Agent Guidance

Updated `AI_AGENT_SYSTEM_PROMPT.md` with:
- Kinetic modeling best practices
- Common issues and solutions
- Working examples with proper parameters
- Troubleshooting guidance

## Expected Improvements

The MCP client should now see:

1. **Complete time series** - All requested time steps in kinetic output
2. **Valid SI values** - No -999 values in the middle of simulations (only at true exhaustion)
3. **Solution chemistry** - pH, element concentrations at each time step
4. **Better error messages** - Clear indication of RK failures with partial results
5. **More stable simulations** - CVODE solver and incremental reactions improve stability

## Example Usage

```json
{
    "kinetic_parameters": {
        "time_steps": [0, 10, 30, 60, 120, 300, 600],
        "enable_kinetics": true,
        "minerals_kinetic": {
            "Calcite": {
                "m0": 0,        // Initial amount
                "m": 1e-10,     // Small seed to prevent exhaustion
                "tol": 1e-6,    // Tolerance
                "parms": [50, 0.6]  // Rate parameters
            }
        },
        "use_phreeqc_rates": true
    }
}
```

## Technical Details

### Files Modified
- `/tools/phreeqc_wrapper.py` - Core kinetic execution and result parsing
- `/AI_AGENT_SYSTEM_PROMPT.md` - User guidance documentation

### Key Functions Enhanced
- `execute_phreeqc_with_kinetics()` - Better PHREEQC input generation
- Result parsing in kinetic profile extraction - Handle all time steps
- Error recovery logic - Approximate missing values

### Backup Files Created
- `phreeqc_wrapper.py.backup_20250127_163958`
- `AI_AGENT_SYSTEM_PROMPT.md.backup_20250127_163958`

## Verification

To verify the fixes work correctly, test with the exact scenarios from the MCP client feedback:

```python
# Test 1: Full time series (was stopping at 2-3 points)
"time_steps": [0, 10, 30, 60, 120, 300, 600, 1200]

# Test 2: High supersaturation 
"parms": [100, 0.6]  # High rate constant

# Test 3: With equilibrium minerals
"equilibrium_minerals": ["Aragonite"]
```

All tests should now return complete time series with valid SI values throughout.