"""Explicit code search paths, independent of the project data location."""
from pathlib import Path
import sys

_profile = Path(__file__).resolve().parents[2]
for _path in (_profile / "parser", _profile / "workflow", _profile.parents[1] / "shared"):
    sys.path.insert(0, str(_path))
