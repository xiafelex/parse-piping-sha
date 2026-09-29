# Compatibility entry; implementation lives in the project profile.
from pathlib import Path as _Path
import sys as _sys
_source = _Path(__file__).resolve().parents[1] / 'units' / 'convert_201600_iso_review.py'
_sys.path.insert(0, str(_source.parent))
__file__ = str(_source)
exec(compile(_source.read_bytes(), str(_source), 'exec'), globals())