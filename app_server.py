# Compatibility entry for the frozen Lanxing application.
from pathlib import Path as _Path
import sys as _sys
_source = _Path(__file__).resolve().parent / 'profiles' / 'lanxing' / 'parser' / 'app_server.py'
_sys.path.insert(0, str(_source.parent))
__file__ = str(_source)
exec(compile(_source.read_bytes(), str(_source), 'exec'), globals())
