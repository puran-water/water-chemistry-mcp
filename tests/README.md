# Water Chemistry MCP Server Tests

## Test Runners

This project uses **two test runners**:

### 1. pytest (recommended)
```bash
pytest tests/ -v
```
pytest collects tests from files using standard pytest patterns (`class Test*`, `def test_*` with fixtures/marks). The `conftest.py` excludes old-style test files via `collect_ignore`.

### 2. Legacy script runner
```bash
python tests/run_all_tests.py
```
Some test files use a custom `TestResults` class and `async def test_*(results: TestResults)` pattern. These are excluded from pytest collection but can be run individually:
```bash
python tests/test_batch_processing.py
python tests/test_chemical_addition.py
python tests/test_scaling_potential.py
python tests/test_solution_speciation.py
python tests/test_solution_mixing.py
```

## Test Categories

| File | Runner | Description |
|------|--------|-------------|
| `test_chemical_dosing_comprehensive.py` | pytest | Dosing optimization tests |
| `test_dosing_convergence.py` | pytest | Dosing convergence tests |
| `test_ferric_helpers.py` | pytest | Ferric P-removal helpers |
| `test_kinetic_reaction.py` | pytest | Kinetic reaction tests |
| `test_lime_softening_comprehensive.py` | pytest | Lime softening tests |
| `test_phosphorus_removal.py` | pytest | P removal tool tests |
| `test_phreeqc_parser.py` | pytest | PHREEQC output parser |
| `test_schemas_ferric.py` | pytest | Ferric schema validation |
| `test_thermodynamic_database.py` | pytest | Database query tests |
| `test_batch_processing.py` | legacy | Batch processing tests |
| `test_chemical_addition.py` | legacy | Chemical addition tests |
| `test_scaling_potential.py` | legacy | Scaling potential tests |
| `test_solution_speciation.py` | legacy | Speciation tests |
| `test_solution_mixing.py` | legacy | Solution mixing tests |

## Common Issues

### Database Loading
```
ERROR: RunString: No database is loaded
```
Ensure PhreeqPython is installed with scipy: `pip install phreeqpython scipy`

### Convergence
Increase `max_iterations` or adjust `tolerance` in test cases for complex chemistry scenarios.
