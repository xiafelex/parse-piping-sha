"""Read-only diagnostics for unresolved support UCI and coordinate projection."""
import json
import math
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profiles/chengda_indonesia/units'))
from _bootstrap import PROJECT_DIR
from sha_to_svg_prototype import read_sha_streams
from chengda_management_callouts import _pcf_pipe_projection, _pcf_component_centre


def main():
    results = []
    for unit in ('301200', '301300'):
        base = PROJECT_DIR / 'SHA文件转换' / unit
        for page in (base / '水印及编号标注审计/pages').iterdir():
            audit_path = page / 'management-callouts.json'
            if not audit_path.is_file():
                continue
            audit = json.loads(audit_path.read_text(encoding='utf-8'))
            rows = [r for r in audit.get('unresolved', []) if r.get('reason') == 'support UCI unavailable and coordinate projection failed']
            if not rows:
                continue
            weld = json.loads((page / 'weld-map.json').read_text(encoding='utf-8'))
            trace = json.loads((page / 'trace.json').read_text(encoding='utf-8'))
            pcf = Path(weld['pcf'])
            sha = Path(weld['sha'])
            text = pcf.read_text(encoding='utf-8', errors='replace')
            blocks = re.split(r'(?m)(?=^[A-Z][A-Z0-9-]*(?:\s|$))', text)
            streams = read_sha_streams(sha)
            for row in rows:
                point = row['coordinate']
                support_blocks = [b for b in blocks if b.startswith('SUPPORT') and any(u in b.upper() for u in row['ucis'])]
                support_points = [tuple(map(float, m)) for b in support_blocks for m in re.findall(r'(?:CO-ORDS|CENTRE-POINT|END-POINT)\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)', b)]
                hits = {}
                for u in row['ucis']:
                    guid = uuid.UUID(u.strip('{}'))
                    variants = [u.encode(), u.encode('utf-16le'), guid.bytes, guid.bytes_le]
                    hits[u] = [k for k, v in streams.items() if any(x in v for x in variants)]
                pipes = []
                for b in filter(str.strip, blocks):
                    if b.splitlines()[0].strip() not in ('PIPE', 'PIPE-FIXED'):
                        continue
                    pts = [tuple(map(float, m)) for m in re.findall(r'END-POINT\s+([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)', b)]
                    u = re.search(r'\bUCI\s+(\{[^}]+\})', b)
                    if len(pts) < 2 or not u:
                        continue
                    a, z = pts[:2]
                    delta = [z[i]-a[i] for i in range(3)]
                    length2 = sum(v*v for v in delta)
                    if not length2:
                        continue
                    t = max(0, min(1, sum((point[i]-a[i])*delta[i] for i in range(3))/length2))
                    distance = math.dist(point, [a[i]+t*delta[i] for i in range(3)])
                    pipes.append((distance, u.group(1)))
                projection = _pcf_pipe_projection(pcf, tuple(point))
                pipe_uci = projection[0] if projection else None
                results.append(dict(unit=unit, sheet=sha.stem, number=row['number'], ucis=row['ucis'],
                    coordinate=point, pcf_support_points=support_points,
                    support_distance=min((math.dist(point,p) for p in support_points), default=None),
                    sha_uci_hits=hits,
                    visible_regions=[r for r in trace.get('uci_regions',[]) if str(r.get('uci','')).upper() in row['ucis']],
                    nearest_pipe=sorted(pipes)[:1], projection=projection,
                    projected_pipe_segments=sum(pipe_uci in r.get('uci_candidates',[]) for r in trace.get('segments',[])) if pipe_uci else 0,
                    component_centre=_pcf_component_centre(pcf, tuple(point))))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
