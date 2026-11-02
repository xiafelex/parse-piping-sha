"""Inventory SHA resources and same-drawing PDF image placements; no PDF edits."""
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


def sha_inventory(path):
    streams=read_sha_streams(path)
    references=[];images=[];fields={}
    signatures={'BMP':b'BM','PNG':b'\x89PNG\r\n\x1a\n','JPEG':b'\xff\xd8\xff',
                'TIFF_LE':b'II\x2a\x00','TIFF_BE':b'MM\x00\x2a'}
    for name,data in streams.items():
        for encoding,pattern in [('utf-16le',rb'(?:[ -~]\x00){5,}'),('ascii',rb'[ -~]{7,}')]:
            for match in re.finditer(pattern,data):
                value=match.group().decode(encoding)
                if re.search(r'\.(?:bmp|png|jpe?g|tiff?|wmf|emf|sym)\b',value,re.I):
                    references.append({'stream':name,'offset':match.start(),'text':value})
        for kind,magic in signatures.items():
            if data.startswith(magic):
                images.append({'stream':name,'type':kind,'bytes':len(data),
                               'sha256':hashlib.sha256(data).hexdigest()})
        if name=='TaggedTxtData/SignatureArea':
            root=ET.fromstring(data.rstrip(b'\0'))
            fields={child.tag:child.text or '' for child in root}
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'stream_count':len(streams),'signature_area':fields,
            'image_streams':images,'file_references':references,
            'embedded_resources':[{'stream':k,'bytes':len(v)} for k,v in streams.items()
                                  if k.endswith(('/CONTENTS','/PSMcluster0'))]}


def pdf_inventory(path):
    instances=[]
    with fitz.open(path) as document:
        for page in document:
            for item in page.get_image_info(xrefs=True):
                rect=fitz.Rect(item['bbox'])*page.rotation_matrix
                box=[v*25.4/72 for v in rect]
                # Known title-block regions in this A3 corpus, not identity
                # recognition. Keep other images in the report as well.
                region='other'
                if 387<box[0]<414 and 205<box[1]<213:region='revision_signatures'
                elif 254<box[0]<278 and 246<box[1]<254:region='discipline_signatures'
                elif 369<box[0]<378 and 225<box[1]<239:region='ADNOC_logo'
                instances.append({'page':page.number,'xref':item['xref'],
                                  'pixel_width':item['width'],'pixel_height':item['height'],
                                  'bbox_display_mm':box,'region':region,
                                  'pixel_digest':item['digest'].hex()})
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'image_instances':instances}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--input-dir',required=True,type=Path)
    cli.add_argument('--report',required=True,type=Path)
    args=cli.parse_args()
    drawings=[]
    for sha in sorted(args.input_dir.glob('*.sha')):
        if sha.stem.endswith(('-template','-usergraphics')):continue
        pdf=sha.with_name(sha.stem+'_0.pdf')
        if not pdf.exists():continue
        sources=[sha]+[p for suffix in ('-template.sha','-usergraphics.sha')
                       if (p:=sha.with_name(sha.stem+suffix)).exists()]
        drawings.append({'drawing':sha.stem,'sha_files':[sha_inventory(p) for p in sources],
                         'reference_pdf':pdf_inventory(pdf)})
    if not drawings:cli.error('No SHA/PDF pairs found')
    report={'drawings':drawings,'limitations':[
        'Image search checks stream-start signatures only; absence does not prove no alternate encoding.',
        'PDF image-region classification uses this corpus layout, not biometric or signer identification.',
        'No reference PDF image is inserted into SHA output; this is evidence only.']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in drawings:
        print(row['drawing'],'SHA files',len(row['sha_files']),'PDF signature-region images',
              sum(i['region'].endswith('signatures') for i in row['reference_pdf']['image_instances']))


if __name__=='__main__':main()
