#!/usr/bin/env python3
"""Report match audits invalidated by a DXF continuation mark; never overwrite them."""
from __future__ import annotations
import argparse,json
from pathlib import Path
def main():
 ap=argparse.ArgumentParser();ap.add_argument('inventory',type=Path);ap.add_argument('output_root',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 inv=json.loads(a.inventory.read_text())['lines'];rows=[]
 for x in inv:
  if x.get('continuation_in_single_page'):
   for page in x['dxf_pages']:
    audit=a.output_root/x['line_key']/'match-audit.json'
    if audit.exists(): rows.append({'line_key':x['line_key'],'audit':str(audit),'status':'invalidated_by_continuation','reason':'DXF contains CONT. ON/CONT. FROM; closed single-page algorithm is ineligible'})
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({'algorithm':'INVALIDATE_NONCLOSED_AUDITS_V1','rows':rows},ensure_ascii=False,indent=2));print(json.dumps({'invalidated':len(rows)}))
if __name__=='__main__':main()
