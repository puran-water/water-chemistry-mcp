'Public engineering calculation interface.'
from __future__ import annotations
import contextlib
import functools
import hashlib
import os
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Tuple
NAMES_ITS_DATABASE = '__names_its_database__'
_RUNS: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar('waterchem_database_runs', default=None)
_SHA256: Dict[Tuple[str, float, int], str] = {}
_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _sha256(path: str) -> Optional[str]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.path.realpath(path), stat.st_mtime, stat.st_size)
    if key not in _SHA256:
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
        _SHA256[key] = digest.hexdigest()
    return _SHA256[key]

def database_source(path: str) -> str:
    """Where a database file comes from: the repository's copy, a dependency's bundled copy, or a caller's path."""
    real = os.path.realpath(path)
    if real.startswith(os.path.join(os.path.realpath(_SERVER_ROOT), 'databases') + os.sep):
        return 'repo-local'
    try:
        import phreeqpython
        if real.startswith(os.path.realpath(os.path.dirname(phreeqpython.__file__)) + os.sep):
            return 'phreeqpython-bundled'
    except ImportError:
        pass
    return 'caller-path'

def database_record(path: str) -> Dict[str, Any]:
    """The database file a run loaded: its name, source and sha256."""
    return {'database': os.path.basename(path), 'source': database_source(path), 'sha256': _sha256(path)}

def record_run(database_path: str, engine: str, model: Optional[Dict[str, Any]]=None, *, declared_by: Optional[str]=None) -> None:
    'Public engineering calculation interface.'
    runs = _RUNS.get()
    if runs is not None:
        runs.append({'path': database_path, 'engine': engine, 'model': model, 'declared_by': declared_by})

@contextlib.contextmanager
def collecting_runs() -> Iterator[List[Dict[str, Any]]]:
    """Collect the runs made inside the block; they are also added to an enclosing collection (a nested call's runs
    belong to its caller's answer too)."""
    outer = _RUNS.get()
    runs: List[Dict[str, Any]] = []
    token = _RUNS.set(runs)
    try:
        yield runs
    finally:
        _RUNS.reset(token)
        if outer is not None:
            outer.extend(runs)

def summarise(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per database file and engine, in the order they first ran."""
    entries: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for run in runs:
        key = (os.path.realpath(run['path']), run['engine'])
        if key not in entries:
            entries[key] = {**database_record(run['path']), 'engine': run['engine'], 'runs': 0, 'chemical_models': []}
            if run.get('declared_by'):
                entries[key]['engine_declared_by'] = run['declared_by']
        entries[key]['runs'] += 1
        model = run.get('model')
        if model:
            known = next((m for m in entries[key]['chemical_models'] if m['fingerprint'] == model['fingerprint']), None)
            if known is None:
                entries[key]['chemical_models'].append({**model, 'runs': 1})
            else:
                known['runs'] += 1
    return list(entries.values())

def names_its_database(fn):
    """Wrap a tool so its result names the databases that answered it (``answered_by``); the signature is unchanged."""
    from .basis_defaults import collecting_basis_defaults
    from .phase_choice import collecting_phase_substitutions

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with collecting_runs() as runs, collecting_phase_substitutions() as substitutions, collecting_basis_defaults() as defaults:
            result = await fn(*args, **kwargs)
        if isinstance(result, dict):
            from .chemical_model import CHEMICAL_MODEL_RULING_ID
            result = {**result, 'answered_by': summarise(runs), 'chemical_model_ruling_id': CHEMICAL_MODEL_RULING_ID}
            if substitutions:
                result['phase_substitutions'] = substitutions
            if defaults:
                result['solution_basis_defaults'] = defaults
        return result
    setattr(wrapper, NAMES_ITS_DATABASE, True)
    return wrapper
