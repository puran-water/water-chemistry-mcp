"""Dependency-free separation of immutable databases and writable service state.

Set WATER_CHEMISTRY_STATE_DIR to a private absolute directory per service identity.
Importing this module does not create directories or migrate existing assets.
"""
import os
from pathlib import Path
BUNDLED_DATABASE_DIR = Path(__file__).resolve().parents[1] / 'databases'
_configured = os.environ.get('WATER_CHEMISTRY_STATE_DIR')
if _configured:
    _state = Path(_configured).expanduser()
    if not _state.is_absolute():
        raise ValueError('WATER_CHEMISTRY_STATE_DIR must be absolute')
else:
    _xdg = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))).expanduser()
    if not _xdg.is_absolute():
        raise ValueError('XDG_DATA_HOME must be absolute')
    _state = _xdg / 'puran/water-chemistry'
_state = _state.resolve()
if _state == BUNDLED_DATABASE_DIR.parent or BUNDLED_DATABASE_DIR.parent in _state.parents:
    raise ValueError('Writable chemistry state must be outside installed database assets')
DATABASE_DIR = str(_state / 'databases')
OFFICIAL_DIR = str(Path(DATABASE_DIR) / 'official')
CUSTOM_DIR = str(Path(DATABASE_DIR) / 'custom')
CACHED_DIR = str(Path(DATABASE_DIR) / 'cached')
METADATA_DIR = str(Path(DATABASE_DIR) / 'metadata')
