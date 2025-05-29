# Water Chemistry MCP Server

[![Test Suite](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Test%20Suite/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/test.yml)
[![Code Quality](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Code%20Quality/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/quality.yml)
[![Integration Tests](https://github.com/orgs/puran-water/water-chemistry-mcp/workflows/Integration%20Tests/badge.svg)](https://github.com/orgs/puran-water/water-chemistry-mcp/actions/workflows/integration.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Advanced water chemistry modeling MCP server powered by PHREEQC, designed for industrial wastewater treatment applications. This server provides sophisticated chemical equilibrium and kinetic modeling capabilities through a modern Model Context Protocol (MCP) interface.

## 🌟 Features

### Core Water Chemistry Tools

1. **🧪 Solution Speciation** - Complete water quality analysis including pH, ionic strength, saturation indices, and species distribution
2. **⚗️ Chemical Addition** - Simulate treatment processes with chemical dosing and equilibrium calculations
3. **📊 Dosing Requirement** - Calculate optimal chemical doses using advanced convergence algorithms
4. **🌊 Solution Mixing** - Analyze blending of multiple water streams with precipitation modeling
5. **⚖️ Scaling Potential** - Predict mineral scaling risks for membrane systems (RO/NF)
6. **⏱️ Kinetic Modeling** - Time-dependent precipitation and dissolution kinetics
7. **📋 Engineering Calculation Sheets** - Professional documentation generation

### Advanced Capabilities

- **Multi-database Support**: phreeqc.dat, minteq.dat, llnl.dat, wateq4f.dat, pitzer.dat, and more
- **Cross-platform Compatibility**: Windows, Linux, macOS, and WSL environments
- **Kinetic & Equilibrium Modeling**: Both instantaneous and time-dependent processes
- **Industrial Focus**: Optimized for wastewater treatment design
- **Professional Documentation**: Client-ready calculation sheets and reports

## 🚀 Quick Start

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
from tools.chemical_addition import simulate_chemical_addition
from tools.schemas import ChemicalAdditionInput, InitialSolution, ChemicalReactant

# Simulate lime softening
input_data = ChemicalAdditionInput(
    database="minteq.dat",
    reactants=[
        ChemicalReactant(amount=5, formula="Ca(OH)2")
    ],
    initial_solution=InitialSolution(
        pH=7.5,
        analysis={
            "Ca": 120,    # mg/L as Ca
            "Mg": 40,     # mg/L as Mg  
            "HCO3": 200,  # mg/L as HCO3
            "SO4": 150    # mg/L as SO4
        }
    ),
    allow_precipitation=True,
    equilibrium_minerals=["Calcite", "Brucite"]
)

result = await simulate_chemical_addition(input_data)
```

## 🔬 Kinetic Modeling

Advanced kinetic precipitation modeling with time-dependent behavior:

```python
# Kinetic calcite precipitation
kinetic_params = KineticParameters(
    time_steps=[0, 60, 300, 600, 1800, 3600],  # seconds
    enable_kinetics=True,
    minerals_kinetic={
        "Calcite": MineralKineticParams(
            m0=0,           # Initial moles
            m=1e-6,         # Seed amount (prevents exhaustion)
            tol=1e-6,       # Tolerance
            parms=[10, 0.6] # Rate parameters [k, n]
        )
    },
    use_phreeqc_rates=True
)
```

**Recent Kinetic Improvements (Jan 2025):**
- Robust handling of RK integration failures
- Complete time series output with solution chemistry
- Enhanced numerical stability
- Prevention of mineral exhaustion issues

## 📊 Engineering Calculation Sheets

Generate professional documentation for engineering projects:

```python
from tools.calculation_sheet_generator import generate_calculation_sheet

sheet_data = {
    "project_info": {
        "project_name": "Industrial WWT Lime Softening",
        "project_number": "MWTP-2024-001",
        "engineer": "John Smith, P.E.",
        "date": "2024-01-15"
    },
    "calculation_type": "lime_softening",
    "input_data": input_data,
    "results": result
}

# Generate professional calculation sheet
notebook_path, html_path = await generate_calculation_sheet(sheet_data)
```

## 🗃️ Database Support

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

## 🧪 Testing

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

## 📁 Project Structure

```
water-chemistry-mcp/
├── tools/                    # Core MCP tools
│   ├── chemical_addition.py  # Chemical dosing simulations
│   ├── solution_speciation.py # Water quality analysis
│   ├── scaling_potential.py  # Membrane scaling prediction
│   ├── dosing_requirement.py # Optimal dose calculations
│   ├── solution_mixing.py    # Stream blending analysis
│   └── calculation_sheet_generator.py # Engineering docs
├── utils/                    # Utility modules
│   ├── database_management.py # Database handling
│   ├── phreeqc_wrapper.py   # PHREEQC integration
│   ├── amorphous_phases.py  # Advanced precipitation
│   └── helpers.py           # Common functions
├── tests/                   # Comprehensive test suite
├── templates/               # Calculation sheet templates
├── databases/               # PHREEQC databases
└── .github/workflows/       # CI/CD workflows
```

## 🔧 Configuration

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

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

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

## 📖 Documentation

- **[AI Agent System Prompt](AI_AGENT_SYSTEM_PROMPT.md)** - Comprehensive usage guide
- **[Contributing Guidelines](CONTRIBUTING.md)** - Development guidelines
- **[Test Documentation](tests/README.md)** - Testing setup and troubleshooting

## 🏗️ Architecture

The server is built using:

- **MCP Protocol**: Modern context protocol for AI integration
- **PHREEQC**: Industry-standard geochemical modeling (USGS)
- **PhreeqPython**: Python interface to PHREEQC
- **Pydantic**: Data validation and serialization
- **AsyncIO**: Asynchronous processing for performance

## 📋 Requirements

- Python 3.9+
- PhreeqPython 1.5.2+
- PHREEQC databases
- See [requirements.txt](requirements.txt) for full dependencies

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🏢 About Puran Water

Puran Water specializes in advanced water treatment technologies and engineering solutions. This MCP server represents our commitment to open-source tools for the water treatment industry.

**Contact**: info@puranwater.com

---

*Built with ❤️ for the water treatment engineering community*