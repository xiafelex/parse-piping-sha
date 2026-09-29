"""Bootstrap Chengda diagnostic tools from the unified repository."""
from pathlib import Path
import sys

_repo = Path(__file__).resolve().parents[2]
_profile = _repo / 'profiles' / 'chengda_indonesia'
for _path in (_profile / 'parser', _profile / 'workflow', _repo / 'shared'):
    sys.path.insert(0, str(_path))
from chengda_paths import PROJECT_DIR, PARSER_DIR, PROGRAM_DIR, SHARED_DIR
