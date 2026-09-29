"""Make the isolated Chengda parser and shared PCF utilities importable."""
from pathlib import Path
import sys

_profile = Path(__file__).resolve().parent.parent
for _path in (_profile / "parser", _profile.parents[1] / "shared"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from chengda_paths import PROJECT_DIR, PARSER_DIR, PROGRAM_DIR, SHARED_DIR
