"""Export UAE SHA native metadata, uint32 line references and partial previews."""
import argparse
import json
from pathlib import Path
import _bootstrap
from sha_native import inspect, preview
from analyze_iso_split import read_sha_streams

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('input',type=Path)
    cli.add_argument('--out',type=Path,required=True)
    args=cli.parse_args()
    files=[args.input] if args.input.is_file() else sorted(p for p in args.input.glob('*.sha') if not p.stem.endswith(('-template','-usergraphics')))
    if not files:cli.error('No SHA files')
    for path in files:
        out=args.out/path.stem;out.mkdir(parents=True,exist_ok=True)
        info=inspect(path);streams=read_sha_streams(path)
        for name in info['sheets']:
            svg,coverage=preview(streams[name],streams);info['sheets'][name]['preview']=coverage
            (out/(name+'-native-preview.svg')).write_text(svg,encoding='utf-8')
        (out/'sha-evidence.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        print(path.name, {name:s['preview'] for name,s in info['sheets'].items()})
    return 0

if __name__=='__main__':raise SystemExit(main())
