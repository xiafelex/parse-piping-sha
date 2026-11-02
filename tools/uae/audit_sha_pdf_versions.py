"""Read-only revision/extraction-time audit against supplied original ISO PDFs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import fitz

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'profiles/uae/parser'))
from analyze_iso_split import read_sha_streams
from sha_bindings import bound_text


def audit(sha,pdf):
    streams=read_sha_streams(sha)
    root=ET.fromstring(streams['TaggedTxtData/Revision'])
    revisions=[{c.tag:c.text or '' for c in r} for r in root.findall('RevisionRecord')]
    drawing=ET.fromstring(streams['TaggedTxtData/TitleArea']).findtext('DrawingNumber')
    with fitz.open(pdf) as doc:
        text='\n'.join(p.get_text() for p in doc)
        # Restrict revision checks to physical revision-table rectangle; account for PDF rotation.
        revision_lines=[]
        for page in doc:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        point=fitz.Point(span['origin'])*page.rotation_matrix
                        x,y=point.x/page.rect.width,point.y/page.rect.height
                        if .715<x<.99 and .67<y<.77:
                            revision_lines.append(span['text'].strip())
        revision_text=' '.join(revision_lines)
        stamps=sorted(set(re.findall(r'20\d\d-\d\d-\d\d \d\d:\d\d:\d\d',text)))
        fields=('MinorRev_ForRevise','RevisedDate','RevisionDescription','RevisedBy','CheckedBy','ApprovedBy')
        checks=[{'revision':r['MinorRev_ForRevise'],'fields':{k:{'sha':r[k],'pdf_revision_table_contains':r[k] in revision_text} for k in fields}} for r in revisions]
        stamp_checks=[{'value':s,'present_in_sha_sheet6':s.encode('utf-16le') in streams['Sheet6']} for s in stamps]
        bindings=bound_text(streams['Sheet39817'],streams)
        return {'sha':str(sha),'pdf':str(pdf),'sha256':hashlib.sha256(sha.read_bytes()).hexdigest(),
                'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'drawing':drawing,
                'drawing_matches':drawing in text,'revision_checks':checks,'pdf_revision_table_text':revision_lines,
                'extraction_time_checks':stamp_checks,'pdf_metadata':doc.metadata,
                'pdf_annotations':sum(len(list(p.annots() or [])) for p in doc),
                'resolved_sha_revision_bindings':len(bindings),
                'same_design_revision_supported':bool(checks and stamps and drawing in text and
                    all(all(v['pdf_revision_table_contains'] for v in c['fields'].values()) for c in checks) and
                    all(c['present_in_sha_sheet6'] for c in stamp_checks)),
                'scope':'same design revision/extraction supported; does not prove identical signatures, flattened markups or all visual content'}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('input',type=Path)
    cli.add_argument('--out',type=Path,required=True)
    args=cli.parse_args()
    results=[]
    for sha in sorted(args.input.glob('*.sha')):
        if sha.stem.endswith(('-template','-usergraphics')):continue
        results.append(audit(sha,sha.with_name(sha.stem+'_0.pdf')))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    for r in results:print(r['drawing'],r['same_design_revision_supported'],r['extraction_time_checks'])


if __name__=='__main__':main()
