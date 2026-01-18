# Water Chemistry MCP Server

[![Test Suite](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Test%20Suite/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/test.yml)
[![Code Quality](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Code%20Quality/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/quality.yml)
[![Integration Tests](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Integration%20Tests/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/integration.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Advanced water chemistry modeling MCP server powered by PHREEQC, designed for industrial wastewater treatment applications. This server provides sophisticated chemical equilibrium and kinetic modeling capabilities through a modern Model Context Protocol (MCP) interface.

## Features

### 16 Registered MCP Tools

#### Core Analysis Tools (5)
1. **calculate_solution_speciation** - Complete water quality analysis including pH, ionic strength, saturation indices
2. **simulate_chemical_addition** - Treatment simulation with precipitation modeling
3. **simulate_solution_mixing** - Stream blending analysis with precipitation
4. **predict_scaling_potential** - Mineral scaling risk assessment
5. **batch_process_scenarios** - Parallel scenario processing and optimization

#### Advanced PHREEQC Tools (7)
6. **calculate_dosing_requirement** - Binary search for target pH/hardness/SI
7. **query_thermodynamic_database** - Query minerals, elements, species from databases
8. **simulate_kinetic_reaction** - Time-dependent reaction modeling
9. **simulate_gas_phase_interaction** - Gas-water equilibration (CO2 stripping, O2 transfer)
10. **simulate_redox_adjustment** - pe/Eh/redox couple adjustment
11. **simulate_surface_interaction** - Surface complexation modeling
12. **calculate_ferric_dose_for_tp** - Optimal ferric/ferrous dose for target P removal with HFO surface complexation

#### Optimization Tools (4)
13. **generate_lime_softening_curve** - Complete dose-response curves in single call
14. **calculate_lime_softening_dose** - Optimal lime dose for target hardness
15. **calculate_dosing_requirement_enhanced** - Multi-objective dosing optimization
16. **optimize_multi_reagent_treatment** - Multi-reagent with 4 strategies (weighted_sum, pareto_front, sequential, robust)

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
git clone https://github.com/orgs/puran-water/water-chemistry-mcp.git
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
| **minteq.v4.dat** | Recommended for softening (has Brucite) | 50+ | 300+ |
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
pytest tests/test_dosing_convergence.py tests/test_thermodynamic_database.py -v
```

## Project Structure

```
water-chemistry-mcp/
├── tools/                        # MCP tools (16 total)
│   ├── solution_speciation.py    # Water quality analysis
│   ├── chemical_addition.py      # Chemical dosing
│   ├── solution_mixing.py        # Stream blending
│   ├── scaling_potential.py      # Scaling assessment
│   ├── batch_processing.py       # Parallel processing
│   ├── dosing_requirement.py     # Dosing optimization
│   ├── optimization_tools.py     # Advanced optimization (5 tools)
│   ├── thermodynamic_database.py # Database queries
│   ├── kinetic_reaction.py       # Kinetic modeling
│   ├── gas_phase.py              # Gas-water equilibria
│   ├── redox_adjustment.py       # Redox control
│   ├── surface_interaction.py    # Surface complexation
│   ├── phreeqc_wrapper.py        # PHREEQC integration
│   └── schemas.py                # Pydantic schemas
├── utils/                        # Utility modules
│   ├── exceptions.py             # Typed exceptions (FAIL LOUDLY)
│   ├── database_management.py    # Database handling
│   ├── constants.py              # Mineral mappings
│   ├── helpers.py                # PHREEQC block builders
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

**Server Version: 2.1**

- 16 registered MCP tools
- FAIL LOUDLY error handling with typed exceptions
- USGS PHREEQC subprocess support
- Multi-objective optimization with 4 strategies
- Comprehensive test coverage

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
