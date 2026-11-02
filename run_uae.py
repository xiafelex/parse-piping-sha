"""Isolated UAE entry: txt, pod or sha. Existing project entry points unchanged."""
from pathlib import Path
import argparse
import subprocess
import sys

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('command',choices=['txt','pod','sha','pdf'])
    args,remaining=cli.parse_known_args()
    root=Path(__file__).resolve().parent/'profiles'/'uae'
    entry={'txt':root/'parser/parse_uae_isogen_neutral.py','pod':root/'workflow/decode_pod.py','sha':root/'workflow/inspect_sha.py','pdf':root/'workflow/convert_uae_sha_to_pdf.py'}[args.command]
    return subprocess.call([sys.executable,str(entry),*remaining])

if __name__=='__main__':raise SystemExit(main())
