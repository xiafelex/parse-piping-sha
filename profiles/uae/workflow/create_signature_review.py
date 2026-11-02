"""Create separately labelled review PDFs with same-version PDF signature assets.

This is an external-resource supplement, NOT SHA signature decoding. Native SHA
PDFs and original reference PDFs remain unchanged. The review directory is PDF-only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import fitz
from convert_uae_sha_to_pdf import validate_pdf,publish_delivery_pdf


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature_region(box):
    x,y,_,_=box
    if 387<x<414 and 205<y<213:return 'revision'
    if 254<x<278 and 246<y<254:return 'discipline'
    return None


def create_review(native,reference,sha,assets,output,force=False):
    target=output/(native.stem+'_签字补充核对.pdf')
    if target.exists() and not force:raise FileExistsError(f'{target}; use --force')
    hashes={str(p.resolve()):digest(p) for p in (native,reference,sha)}
    placements=[]
    with fitz.open(reference) as source:
        if len(source)!=1:raise ValueError('Expected one reference page')
        page=source[0]
        masks={item[0]:item[1] for item in page.get_images(full=True)}
        for item in page.get_image_info(xrefs=True):
            rect=fitz.Rect(item['bbox'])*page.rotation_matrix
            box=[v*25.4/72 for v in rect]
            region=signature_region(box)
            if region is None:continue
            matrix=fitz.Matrix(item['transform'])*page.rotation_matrix
            if abs(matrix.b)>1e-4 or abs(matrix.c)>1e-4 or matrix.a<=0 or matrix.d<=0:
                raise ValueError('Unverified image rotation, reflection or shear')
            pix=fitz.Pixmap(source,item['xref'])
            if masks.get(item['xref']):pix=fitz.Pixmap(pix,fitz.Pixmap(source,masks[item['xref']]))
            if pix.colorspace and pix.colorspace.n!=3:pix=fitz.Pixmap(fitz.csRGB,pix)
            payload=pix.tobytes('png');asset_hash=hashlib.sha256(payload).hexdigest()
            asset=assets/(asset_hash+'.png')
            if not asset.exists():asset.write_bytes(payload)
            if digest(asset)!=asset_hash:raise ValueError('Asset hash mismatch')
            placements.append({'source_kind':'reference_pdf_image','xref':item['xref'],
                               'soft_mask_xref':masks.get(item['xref'],0),'region':region,
                               'bbox_display_mm':box,'asset':str(asset.resolve()),'asset_sha256':asset_hash})
    if len(placements)!=5 or sum(p['region']=='revision' for p in placements)!=3:
        raise ValueError('Expected three revision and two discipline signature images')
    with tempfile.TemporaryDirectory(prefix='._signature_review_',dir=output) as tmp:
        staged=Path(tmp)/'review.pdf'
        with fitz.open(native) as document:
            if len(document)!=1 or document[0].rotation!=0:raise ValueError('Expected unrotated native A3 PDF')
            page=document[0]
            for placement in placements:
                rect=fitz.Rect(*(v*72/25.4 for v in placement['bbox_display_mm']))
                if not page.rect.contains(rect):raise ValueError('Signature falls outside native page')
                # Match the source placement, including its non-uniform scaling.
                page.insert_image(rect,filename=placement['asset'],keep_proportion=False,overlay=True)
            metadata=document.metadata
            metadata['title']=native.stem+' - signature supplement review'
            metadata['subject']='SHA rendering plus five images from same-version reference PDF; signatures are external assets, not SHA decoded.'
            metadata['keywords']='reference_pdf_sha256='+hashes[str(reference.resolve())]
            document.set_metadata(metadata)
            document.save(staged,garbage=4,deflate=True)
        validate_pdf(staged)
        if any(digest(Path(p))!=h for p,h in hashes.items()):raise ValueError('Input changed during review creation')
        publish_delivery_pdf(staged,target)
    validate_pdf(target)
    return {'drawing':sha.stem,'source_hashes':hashes,'placements':placements,
            'output':str(target.resolve()),'output_sha256':digest(target),'pure_sha_decode':False}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-dir',required=True,type=Path)
    cli.add_argument('--native-dir',required=True,type=Path)
    cli.add_argument('--asset-dir',required=True,type=Path)
    cli.add_argument('--output-dir',required=True,type=Path)
    cli.add_argument('--force',action='store_true')
    args=cli.parse_args()
    reference,native,assets,output=[p.resolve() for p in (args.reference_dir,args.native_dir,args.asset_dir,args.output_dir)]
    for folder in (assets,output):
        if any(folder==p or p in folder.parents or folder in p.parents for p in (reference,native)):
            cli.error('Assets and review outputs must be separate from both input directories')
    if assets==output or assets in output.parents or output in assets.parents:
        cli.error('Keep assets and manifests outside the PDF-only review directory')
    files=sorted(native.glob('*_SHA.pdf'))
    if not files:cli.error('No native SHA PDFs found')
    assets.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for path in files:
        base=path.stem.removesuffix('_SHA')
        rows.append(create_review(path,reference/(base+'_0.pdf'),reference/(base+'.sha'),assets,output,args.force))
        print(base,'signature supplement review created')
    (assets/'manifest.json').write_text(json.dumps({'mode':'external_signature_supplement_review','drawings':rows},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
