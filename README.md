# Water Chemistry MCP Server

[![Test Suite](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Test%20Suite/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/test.yml)
[![Code Quality](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Code%20Quality/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/quality.yml)
[![Integration Tests](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Integration%20Tests/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/integration.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Advanced water chemistry modeling MCP server powered by PHREEQC, designed for industrial wastewater treatment applications. This server provides sophisticated chemical equilibrium and kinetic modeling capabilities through a modern Model Context Protocol (MCP) interface.

## Features

### Core Water Chemistry Tools (5 Working Tools)

1. **Solution Speciation** - Complete water quality analysis including pH, ionic strength, saturation indices, and species distribution
2. **Chemical Addition** - Simulate treatment processes with chemical dosing and precipitation modeling
3. **Solution Mixing** - Analyze blending of multiple water streams with precipitation modeling
4. **Scaling Potential** - Predict mineral scaling risks using thermodynamic calculations
5. **Batch Processing** - Parameter sweeps, optimization, and parallel scenario evaluation

### Removed Tools (Based on Testing)
- **Dosing Requirement** - Removed due to database compatibility issues
- **Engineering Calculation Sheets** - Removed as requested
- **Enhanced Optimization Tools** - Replaced by batch_process_scenarios

### Advanced Capabilities

- **Multi-database Support**: phreeqc.dat, minteq.dat, llnl.dat, wateq4f.dat, pitzer.dat, and more
- **Cross-platform Compatibility**: Windows, Linux, macOS, and WSL environments
- **Kinetic & Equilibrium Modeling**: Both instantaneous and time-dependent processes
- **Industrial Focus**: Optimized for wastewater treatment design
- **Professional Documentation**: Client-ready calculation sheets and reports

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
# MCP Server usage - 5 working tools available
from tools.chemical_addition import simulate_chemical_addition

# Simulate lime softening
input_data = {
    "initial_solution": {
        "units": "mmol/L",
        "analysis": {
            "Ca": 3.0,     # mmol/L
            "Mg": 1.6,     # mmol/L  
            "C(4)": 3.3,   # mmol/L (use C(4) not HCO3)
            "S(6)": 1.6    # mmol/L (use S(6) not SO4)
        },
        "database": "minteq.dat",
        "temperature_celsius": 25.0
    },
    "reactants": [{"formula": "Ca(OH)2", "amount": 5.0, "units": "mmol"}],
    "allow_precipitation": True,
    "equilibrium_minerals": ["Calcite", "Brucite"]
}

# Call via MCP server
result = await simulate_chemical_addition(input_data)
```

## Batch Processing & Optimization

Use `batch_process_scenarios` for all optimization tasks including dose finding:

```python
# Parameter sweep for lime softening optimization
input_data = {
    "base_scenario": {
        "initial_solution": {
            "units": "mmol/L",
            "analysis": {"Ca": 3.0, "Mg": 1.6, "C(4)": 3.3},
            "database": "minteq.dat"
        },
        "reactants": [{"formula": "Ca(OH)2", "amount": 0, "units": "mmol"}],
        "allow_precipitation": True
    },
    "parameter_sweeps": [{
        "parameter_path": "reactants.0.amount",
        "values": [2.0, 3.0, 4.0, 5.0, 6.0]
    }],
    "parallel_limit": 5
}

# Returns results for all doses with hardness, pH, and precipitation data
results = await batch_process_scenarios(input_data)
```

## Scientific Integrity Features

**PHREEQC-Only Results**: All user-facing results use pure PHREEQC thermodynamic calculations with no heuristics or approximations.

**Comprehensive Mineral Lists**: Default precipitation modeling includes 50-200 minerals from database rather than limited selections.

**Accurate TDS Calculations**: Based on individual species concentrations, not approximations.

**Smart Optimization**: Internal stoichiometry provides efficient search bounds while final results remain purely thermodynamic.

## Database Support

Comprehensive PHREEQC database support with intelligent path resolution:

| Database | Purpose | Elements | Minerals |
|----------|---------|----------|----------|
| **minteq.dat** | General purpose, recommended | 50+ | 300+ |
| **phreeqc.dat** | Standard PHREEQC | 40+ | 200+ |
| **llnl.dat** | Comprehensive elements | 80+ | 500+ |
| **wateq4f.dat** | Natural waters | 60+ | 400+ |
| **pitzer.dat** | High ionic strength | 30+ | 100+ |

```python
from utils.database_management import database_manager

# Automatic database recommendation
recommended_db = database_manager.recommend_database('general')

# Check mineral compatibility  
compatible = database_manager.get_compatible_minerals(
    ['Calcite', 'Gypsum', 'Brucite'], 
    'minteq.dat'
)
```

## Testing

### Run All Tests

```bash
# Basic tests
pytest

# With coverage
pytest --cov=tools --cov=utils --cov-report=html

# Integration tests only
pytest -m integration

# Skip slow tests
pytest -m "not slow"
```

### Continuous Integration

The project includes comprehensive CI/CD workflows:

- **Test Suite**: Multi-platform testing (Ubuntu, Windows, macOS)
- **Code Quality**: Black, isort, flake8, mypy, bandit
- **Integration Tests**: Real-world scenarios with PHREEQC
- **Release Automation**: Automated versioning and releases

## Project Structure

```
water-chemistry-mcp/
├── tools/                    # Core MCP tools (5 working)
│   ├── solution_speciation.py # Water quality analysis
│   ├── chemical_addition.py  # Chemical dosing simulations
│   ├── solution_mixing.py    # Stream blending analysis
│   ├── scaling_potential.py  # Scaling risk assessment
│   ├── batch_processing.py   # Optimization & parameter sweeps
│   ├── phreeqc_wrapper.py   # PHREEQC integration core
│   └── schemas.py           # Data validation schemas
├── utils/                    # Utility modules
│   ├── database_management.py # Database handling
│   ├── amorphous_phases.py  # Advanced precipitation
│   ├── import_helpers.py    # PhreeqPython detection
│   └── helpers.py           # Common functions
├── tests/                   # Comprehensive test suite (10 tests)
├── templates/               # Calculation templates (7 notebooks)
├── databases/               # PHREEQC databases (5 databases)
└── .github/workflows/       # CI/CD workflows
```

## Configuration

### Environment Variables

```bash
# Optional configuration
PHREEQC_DATABASE_PATH=/path/to/databases/
DEBUG=true
LOG_LEVEL=INFO
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
        "PHREEQC_DATABASE_PATH": "/path/to/databases/"
      }
    }
  }
}
```

## Current Status

**✅ 5 Working Tools** - Tested and validated with PHREEQC thermodynamics
**❌ Broken Tools Removed** - Dosing requirement and calculation sheets removed
**🔬 Scientific Integrity** - All results use pure PHREEQC calculations

### Development Setup

```bash
# Clone and setup
git clone https://github.com/orgs/puran-water/water-chemistry-mcp.git
cd water-chemistry-mcp

# Install development dependencies
pip install -e ".[dev]"

# Run quality checks
black tools/ utils/ server.py
flake8 tools/ utils/ server.py
pytest
```

## Documentation

- **[AI Agent System Prompt](AI_AGENT_SYSTEM_PROMPT.md)** - Complete usage guide for all 5 working tools
- **[Implementation Guide](CLAUDE.md)** - Project roadmap and implementation details
- **[Test Documentation](tests/README.md)** - Testing setup and troubleshooting

## Architecture

The server is built using:

- **MCP Protocol**: Modern context protocol for AI integration
- **PHREEQC**: Industry-standard geochemical modeling (USGS)
- **PhreeqPython**: Python interface to PHREEQC
- **Pydantic**: Data validation and serialization
- **AsyncIO**: Asynchronous processing for performance

## Requirements

- Python 3.9+
- PhreeqPython 1.5.2+
- PHREEQC databases
- See [requirements.txt](requirements.txt) for full dependencies

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

