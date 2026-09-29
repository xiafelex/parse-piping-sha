"""Run a frozen Chengda unit entry point from the unified code repository."""
from pathlib import Path
import argparse
import subprocess
import sys


def main():
    units_dir = Path(__file__).resolve().parent / 'profiles' / 'chengda_indonesia' / 'units'
    entries = {path.name.split('_')[1].lower(): path for path in units_dir.glob('convert_*.py')}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', type=str.lower, choices=sorted(entries))
    parser.add_argument('--list-units', action='store_true')
    args, remaining = parser.parse_known_args()
    if args.list_units:
        print('\n'.join(sorted(entries)))
        return 0
    if not args.unit:
        parser.error('--unit is required unless --list-units is used')
    if not any(arg == '--workers' or arg.startswith('--workers=') for arg in remaining):
        remaining += ['--workers', '8']
    return subprocess.call([sys.executable, str(entries[args.unit]), *remaining])


if __name__ == '__main__':
    raise SystemExit(main())
