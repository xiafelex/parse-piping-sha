from pathlib import Path
import sys
PARSER = Path(__file__).resolve().parents[1] / 'parser'
sys.path.insert(0, str(PARSER))
