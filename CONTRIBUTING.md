# Contributing to Water Chemistry MCP Server

Thank you for your interest in contributing to the Water Chemistry MCP Server! This document provides guidelines for contributing to the project.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Issue Reporting](#issue-reporting)

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/your-username/water-chemistry-mcp.git
   cd water-chemistry-mcp
   ```

3. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

4. Install development dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -e ".[dev]"
   ```

## Development Setup

### Required Software

- Python 3.9 or higher
- PHREEQC (for chemistry calculations)
- Git

### Environment Variables

Create a `.env` file in the project root:
```
PHREEQC_DATABASE_PATH=./databases/official/
DEBUG=true
```

### Database Setup

The project requires PHREEQC databases. Download them to `databases/official/`:
- `minteq.dat` (recommended for most applications)
- `phreeqc.dat` (standard PHREEQC database)
- `llnl.dat` (comprehensive database)

## Code Style

We use several tools to maintain code quality:

### Formatting
- **Black**: Code formatting
- **isort**: Import sorting

```bash
black tools/ utils/ server.py
isort tools/ utils/ server.py
```

### Linting
- **flake8**: Code linting
- **mypy**: Type checking

```bash
flake8 tools/ utils/ server.py --max-line-length=120
mypy tools/ utils/ server.py --ignore-missing-imports
```

### Security
- **bandit**: Security analysis
- **safety**: Dependency security check

```bash
bandit -r tools/ utils/ server.py
safety check
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=tools --cov=utils --cov-report=html

# Run specific test categories
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests only
pytest -m "not slow"    # Skip slow tests
```

### Test Structure

- `tests/` - Main test directory
- Unit tests: Fast tests that don't require external dependencies
- Integration tests: Tests that require PHREEQC databases and external resources
- Test files follow the pattern `test_*.py`

### Writing Tests

```python
import pytest
from tools.chemical_addition import simulate_chemical_addition

@pytest.mark.asyncio
async def test_chemical_addition():
    # Test implementation
    pass

@pytest.mark.integration
async def test_phreeqc_integration():
    # Integration test implementation
    pass
```

## Submitting Changes

### Workflow

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and commit:
   ```bash
   git add .
   git commit -m "Add feature: your feature description"
   ```

3. Run tests and quality checks:
   ```bash
   pytest
   black tools/ utils/ server.py
   flake8 tools/ utils/ server.py
   ```

4. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

5. Create a Pull Request on GitHub

### Commit Messages

Use clear, descriptive commit messages:
- `feat: add kinetic modeling support`
- `fix: resolve database path issue`
- `docs: update API documentation`
- `test: add integration tests for scaling prediction`

### Pull Request Guidelines

- Include a clear description of changes
- Reference any related issues
- Ensure all tests pass
- Update documentation if needed
- Add tests for new features

## Issue Reporting

### Bug Reports

Include:
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Error messages/stack traces
- Sample input data (if applicable)

### Feature Requests

Include:
- Use case description
- Proposed API/interface
- Examples of expected usage
- Any relevant scientific background

### Performance Issues

Include:
- Performance measurements
- Input data size/complexity
- System specifications
- Profiling information (if available)

## Development Guidelines

### Water Chemistry Modeling

- Follow established chemical engineering principles
- Validate calculations against known references
- Include units in variable names and documentation
- Consider numerical stability in calculations

### PHREEQC Integration

- Handle PHREEQC errors gracefully
- Provide meaningful error messages
- Support multiple database formats
- Test with various water compositions

### API Design

- Use Pydantic models for input validation
- Provide comprehensive error responses
- Include units in all numerical outputs
- Document all parameters thoroughly

### Documentation

- Update docstrings for all public functions
- Include examples in docstrings
- Update README.md for new features
- Add type hints to all functions

## Getting Help

- Open an issue for questions
- Check existing issues for similar problems
- Review the documentation in `AI_AGENT_SYSTEM_PROMPT.md`
- Contact the maintainers at info@puranwater.com

## License

By contributing, you agree that your contributions will be licensed under the MIT License.