# Water Chemistry MCP Server

[![Test Suite](https://github.com/puran-water/water-chemistry-mcp/workflows/Test%20Suite/badge.svg)](https://github.com/puran-water/water-chemistry-mcp/actions/workflows/test.yml)
[![Code Quality](https://github.com/puran-water/water-chemistry-mcp/workflows/Code%20Quality/badge.svg)](https://github.com/puran-water/water-chemistry-mcp/actions/workflows/quality.yml)
[![Integration Tests](https://github.com/puran-water/water-chemistry-mcp/workflows/Integration%20Tests/badge.svg)](https://github.com/puran-water/water-chemistry-mcp/actions/workflows/integration.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Advanced water chemistry modeling MCP server powered by PHREEQC, designed for industrial wastewater treatment applications. This server provides sophisticated chemical equilibrium and kinetic modeling capabilities through a modern Model Context Protocol (MCP) interface.


> **⚠️ DEVELOPMENT STATUS: This project is under active development and is not yet production-ready. APIs, interfaces, and functionality may change without notice. Use at your own risk for evaluation and testing purposes only. Not recommended for production deployments.**

## Features

### 17 Registered MCP Tools

#### Core Analysis Tools (5)
1. **calculate_solution_speciation** - Complete water quality analysis including pH, ionic strength, saturation indices
2. **simulate_chemical_addition** - Treatment simulation with precipitation modeling
3. **simulate_solution_mixing** - Stream blending analysis with precipitation
4. **predict_scaling_potential** - Mineral scaling risk assessment
5. **batch_process_scenarios** - Parallel scenario processing and optimization

#### Advanced PHREEQC Tools (6)
6. **calculate_dosing_requirement** - Binary search for target pH/hardness/SI
7. **query_thermodynamic_database** - Query minerals, elements, species from databases
8. **simulate_kinetic_reaction** - Time-dependent reaction modeling
9. **simulate_gas_phase_interaction** - Gas-water equilibration (CO2 stripping, O2 transfer)
10. **simulate_redox_adjustment** - pe/Eh/redox couple adjustment
11. **simulate_surface_interaction** - Surface complexation modeling

#### Optimization Tools (5)
12. **generate_lime_softening_curve** - Complete dose-response curves in single call
13. **calculate_lime_softening_dose** - Optimal lime dose for target hardness
14. **calculate_dosing_requirement_enhanced** - Multi-objective dosing optimization
15. **optimize_multi_reagent_treatment** - Multi-reagent with 4 strategies (weighted_sum, pareto_front, sequential, robust)
16. **calculate_phosphorus_removal_dose** - Unified P removal with 4 strategies (iron, aluminum, struvite, calcium phosphate)

#### Diagnostics (1)
17. **get_engine_status** - Engine health check and database availability

### Phosphorus Removal Strategies

The unified `calculate_phosphorus_removal_dose` tool supports multiple coagulant/precipitation strategies:

| Strategy | Reagents | Mechanism | Typical Metal:P |
|----------|----------|-----------|-----------------|
| `iron` | FeCl3, FeSO4, FeCl2 | HFO adsorption + Strengite/Vivianite | 2.0-3.5 |
| `aluminum` | AlCl3, Al2(SO4)3 | HAO adsorption + Variscite | 2.5-4.0 |
| `struvite` | MgCl2, MgO, Mg(OH)2 | Struvite crystallization | 1.0 (stoich) |
| `calcium_phosphate` | Ca(OH)2, CaCl2 | Brushite/HAP precipitation | 1.5-2.0 |

Features:
- **Inline PHREEQC blocks** for phases not in standard databases (Struvite, Variscite, HAO surface)
- **SI triggers** for metastability control (slow-precipitation phases)
- **Sulfide sensitivity sweep** for anaerobic iron (runs [0, 20, 50, 100] mg/L scenarios)
- **HFO/HAO surface complexation** with phase-linked site scaling
- **Convergence tracking** with `converged`, `target_met`, and `residual_error_mg_l` fields
- **Status semantics**: `success`, `success_with_warning`, `infeasible`, `input_error`
- **P partitioning outputs**: `phase_moles_mmol_per_L`, `p_adsorbed_mg_L`, `p_dissolved_mg_L`
- **Redox diagnostics**: `redox_control_variable`, `target_pO2_atm` for O2 equilibrium mode
- **Chemistry validations**: Ca competition warning for struvite, alkalinity check for Ca-P

### Advanced Capabilities

- **Multi-database Support**: phreeqc.dat, minteq.dat, minteq.v4.dat, llnl.dat, wateq4f.dat, pitzer.dat
- **USGS PHREEQC Support**: Subprocess mode for full USGS database compatibility
- **Cross-platform Compatibility**: Windows, Linux, macOS, and WSL environments
- **Kinetic & Equilibrium Modeling**: Both instantaneous and time-dependent processes
- **Multi-objective Optimization**: Pareto front, weighted sum, sequential, and robust strategies
- **FAIL LOUDLY Error Handling**: Typed exceptions instead of silent failures

## Quick Start

### Installation

```bash
git clone https://github.com/puran-water/water-chemistry-mcp.git
cd water-chemistry-mcp
pip install -r requirements.txt
```

### Start the Server

```bash
python server.py
```

### Example Usage

```python
# Lime softening simulation
from tools.chemical_addition import simulate_chemical_addition

input_data = {
    "initial_solution": {
        "units": "mmol/L",
        "analysis": {
            "Ca": 3.0,
            "Mg": 1.6,
            "Alkalinity": 3.3,
            "Cl": 1.0
        },
        "database": "minteq.v4.dat",
        "temperature_celsius": 25.0
    },
    "reactants": [{"formula": "Ca(OH)2", "amount": 5.0, "units": "mmol"}],
    "allow_precipitation": True
}

result = await simulate_chemical_addition(input_data)
```

### Phosphorus Removal Example

```python
# Iron coagulation for P removal
from tools.phosphorus_removal import calculate_phosphorus_removal_dose

input_data = {
    "initial_solution": {
        "ph": 7.0,
        "analysis": {
            "P": 5.0,
            "Ca": 50,
            "Mg": 10,
            "Alkalinity": "as CaCO3 100"
        },
        "units": "mg/L"
    },
    "target_residual_p_mg_l": 0.5,
    "strategy": {
        "strategy": "iron",
        "reagent": "FeCl3"
    },
    "database": "minteq.v4.dat"
}

result = await calculate_phosphorus_removal_dose(input_data)
```

### Optimization Example

```python
# Multi-reagent optimization with Pareto front
from tools.optimization_tools import optimize_multi_reagent_treatment

input_data = {
    "initial_water": {
        "units": "mmol/L",
        "analysis": {"Ca": 2.0, "Mg": 1.0, "Alkalinity": 2.5},
        "pH": 7.0,
        "database": "minteq.v4.dat"
    },
    "reagents": [
        {"formula": "Ca(OH)2", "min_dose": 0.5, "max_dose": 5.0}
    ],
    "objectives": [
        {"parameter": "pH", "value": 10.5, "weight": 0.5},
        {"parameter": "total_hardness", "value": 80, "weight": 0.5}
    ],
    "optimization_strategy": "pareto_front",
    "grid_points": 10
}

result = await optimize_multi_reagent_treatment(input_data)
```

## Scientific Integrity Features

- **PHREEQC-Only Results**: All user-facing results use pure PHREEQC thermodynamic calculations
- **Comprehensive Mineral Lists**: Default precipitation modeling includes full database minerals
- **Accurate TDS Calculations**: Based on individual species concentrations
- **Smart Optimization Bounds**: Stoichiometry provides efficient search ranges internally
- **FAIL LOUDLY**: All errors raise typed exceptions (DosingConvergenceError, TermNotFoundError, etc.)

## Database Support

| Database | Purpose | Elements | Minerals |
|----------|---------|----------|----------|
| **minteq.v4.dat** | Recommended for softening & P removal (has Brucite) | 50+ | 300+ |
| **minteq.dat** | General purpose | 50+ | 300+ |
| **phreeqc.dat** | Standard PHREEQC | 40+ | 200+ |
| **llnl.dat** | Comprehensive elements | 80+ | 500+ |
| **wateq4f.dat** | Natural waters | 60+ | 400+ |

## Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=tools --cov=utils --cov-report=html

# Specific test files
pytest tests/test_phosphorus_removal.py -v
```

## Project Structure

```
water-chemistry-mcp/
├── tools/                        # MCP tools (17 total)
│   ├── solution_speciation.py    # Water quality analysis
│   ├── chemical_addition.py      # Chemical dosing
│   ├── solution_mixing.py        # Stream blending
│   ├── scaling_potential.py      # Scaling assessment
│   ├── batch_processing.py       # Parallel processing
│   ├── dosing_requirement.py     # Dosing optimization
│   ├── optimization_tools.py     # Advanced optimization (4 tools)
│   ├── phosphorus_removal.py     # Unified P removal (4 strategies)
│   ├── thermodynamic_database.py # Database queries
│   ├── kinetic_reaction.py       # Kinetic modeling
│   ├── gas_phase.py              # Gas-water equilibria
│   ├── redox_adjustment.py       # Redox control
│   ├── surface_interaction.py    # Surface complexation
│   ├── phreeqc_wrapper.py        # PHREEQC integration + engine status
│   ├── schemas.py                # Core Pydantic schemas
│   └── schemas_ferric.py         # P removal specific schemas
├── utils/                        # Utility modules
│   ├── exceptions.py             # Typed exceptions (FAIL LOUDLY)
│   ├── database_management.py    # Database handling
│   ├── database_registry.py      # Database path registry
│   ├── constants.py              # Mineral mappings
│   ├── helpers.py                # PHREEQC block builders
│   ├── ferric_phases.py          # Fe/Al phase definitions
│   ├── inline_phases.py          # Inline PHREEQC blocks (Struvite, Variscite, HAO)
│   ├── amorphous_phases.py       # Amorphous phase handling
│   ├── convergence_strategies.py # Binary search strategies
│   └── import_helpers.py         # PhreeqPython detection
├── tests/                        # Test suite
├── server.py                     # MCP server entry point
└── CLAUDE.md                     # AI agent documentation
```

## Configuration

### Environment Variables

```bash
USGS_PHREEQC_DATABASE_PATH=/path/to/usgs/databases/
USE_PHREEQC_SUBPROCESS=1  # Enable USGS subprocess mode
WATER_CHEMISTRY_DEBUG=1   # Enable debug logging
```

### MCP Client Configuration

For Claude Desktop:

```json
{
  "mcpServers": {
    "water-chemistry": {
      "command": "python",
      "args": ["/path/to/water-chemistry-mcp/server.py"],
      "env": {
        "USGS_PHREEQC_DATABASE_PATH": "/path/to/databases/"
      }
    }
  }
}
```

## Current Status

**Server Version: 3.1**

- 17 registered MCP tools
- Unified phosphorus removal with 4 strategies (Fe/Al/Mg/Ca)
- Inline PHREEQC blocks for Struvite, Variscite, HAO surface
- **NEW**: Sulfide sensitivity sweep for anaerobic iron dosing
- **NEW**: Convergence tracking and status semantics (`success_with_warning`)
- **NEW**: P partitioning outputs (phase moles, adsorbed P, dissolved P)
- **NEW**: Enhanced redox diagnostics with control variable and pO2 fields
- FAIL LOUDLY error handling with typed exceptions
- USGS PHREEQC subprocess support
- Multi-objective optimization with 4 strategies
- Comprehensive test coverage (390+ tests)

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - AI agent system prompt with usage examples
- **[tests/README.md](tests/README.md)** - Testing documentation

## Requirements

- Python 3.9+
- PhreeqPython 1.5.2+
- PHREEQC databases (bundled or USGS)
- See [requirements.txt](requirements.txt) for full dependencies

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
