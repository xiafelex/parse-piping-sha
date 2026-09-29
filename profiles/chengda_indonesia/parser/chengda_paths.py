"""Code and project-data locations; no rendering or matching policy lives here."""
from pathlib import Path
import os

PARSER_DIR = Path(__file__).resolve().parent
PROFILE_DIR = PARSER_DIR.parent
REPOSITORY_DIR = PROFILE_DIR.parents[1]
PROGRAM_DIR = PROFILE_DIR / "workflow"
SHARED_DIR = REPOSITORY_DIR / "shared"
PROJECT_DIR = Path(os.environ.get(
    "CHENGDA_PROJECT_DIR", str(REPOSITORY_DIR.parent / "成达印尼项目")
)).expanduser().resolve()
ASSET_DIR = Path(os.environ.get(
    "CHENGDA_ASSET_DIR", str(PROJECT_DIR / "本地资源" / "parser-assets")
)).expanduser().resolve()
