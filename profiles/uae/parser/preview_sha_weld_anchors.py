"""Small diagnostic drawing from PCF+SHA only. Labels are PCF IDs, not weld numbers."""
import re
import fitz
from benchmark_pod_incremental_value import PROJECT, OUT, sha_weld_baseline
from analyze_iso_split import read_sha_streams
from sha_native import preview


def main():
    base='2-27-52-0605-CD-LAA'
    sha=next(PROJECT.glob('30201*/**/'+base+'.sha'))
    rows,_=sha_weld_baseline(sha.with_name(base+'-pcf.pcf'),sha)
    streams=read_sha_streams(sha)
    output=fitz.open()
    for sheet,data in streams.items():
        if not re.fullmatch(r'Sheet\d+',sheet) or sheet=='Sheet39817':continue
        svg,_=preview(data,streams)
        overlays=[]
        for row in rows:
            candidates=row['sha_circle_candidates']
            if len(candidates)!=1 or candidates[0]['sheet']!=sheet:continue
            point=candidates[0];x=point['x_mm'];y=-point['y_mm']
            overlays.append(f'<circle cx="{x}" cy="{y}" r="1.6" fill="none" stroke="#d22128" stroke-width="0.3"/>'
                f'<text x="{x+2}" y="{y-2}" font-family="Arial" font-size="2.2" fill="#d22128">PCF {row["pcf_id"]}</text>')
        overlays.append(f'<text x="12" y="-10" font-family="Arial" font-size="3" fill="#d22128">PCF + SHA ONLY | {base} | {sheet} | Labels = PCF component IDs</text>')
        svg=svg.replace('</svg>',''.join(overlays)+'</svg>')
        (OUT/(base+'_'+sheet+'_SHA独立定位.svg')).write_text(svg,encoding='utf-8')
        with fitz.open(stream=svg.encode('utf-8'),filetype='svg') as doc:
            with fitz.open(stream=doc.convert_to_pdf(),filetype='pdf') as pdf:output.insert_pdf(pdf)
    output.save(OUT/(base+'_PCF_SHA独立定位验证.pdf'))
    output[0].get_pixmap(matrix=fitz.Matrix(.9,.9)).save(OUT/'定位预览.png')
    output.close()
    print('PCF/SHA-only preview written; no POD file opened.')


if __name__=='__main__':main()
