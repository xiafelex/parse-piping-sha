"""Regional ink coverage with physical tolerance; background is not scored."""
import argparse
import json
from pathlib import Path
import fitz
import numpy as np
from PIL import Image,ImageFilter

REGIONS={
    'pipe_drawing':(6,5,301,245),
    'materials':(302,5,415,201),
    'general_notes':(7,255,183,281),
    'title_block':(183,255,415,297),
    'wood_logo':(307,239,333,248),
    'adnoc_logo':(338,226,378,238),
    'revision_signatures':(387,206,414,212),
    'discipline_signatures':(255,247,278,252),
}


def render(path,dpi):
    with fitz.open(path) as document:
        if len(document)!=1:raise ValueError('Expected a single-page drawing')
        pix=document[0].get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),colorspace=fitz.csRGB,alpha=False)
        return Image.frombytes('RGB',(pix.width,pix.height),pix.samples)


def compare(reference,output,dpi,overlay_dir=None):
    old,new=render(reference,dpi),render(output,dpi)
    scale=dpi/25.4;rows={}
    for name,box in REGIONS.items():
        crop=tuple(round(v*scale) for v in box)
        a=np.min(np.asarray(old.crop(crop)),axis=2)<180
        b=np.min(np.asarray(new.crop(crop)),axis=2)<180
        # Two pixels at 200 dpi = 0.254 mm in either coordinate direction.
        radius=max(1,round(.254*scale))
        def dilate(mask):
            return np.asarray(Image.fromarray(mask.astype('uint8')*255).filter(ImageFilter.MaxFilter(2*radius+1)))>0
        missing=a & ~dilate(b);extra=b & ~dilate(a)
        rows[name]={'bbox_mm':box,'reference_ink_pixels':int(a.sum()),'output_ink_pixels':int(b.sum()),
                    'reference_ink_covered_fraction':float(1-missing.sum()/a.sum()) if a.any() else None,
                    'output_ink_supported_fraction':float(1-extra.sum()/b.sum()) if b.any() else None}
        if overlay_dir is not None:
            overlay_dir.mkdir(parents=True,exist_ok=True)
            rgb=np.full((*a.shape,3),255,dtype='uint8')
            rgb[a|b]=(100,100,100);rgb[missing]=(220,30,30);rgb[extra]=(30,90,220)
            Image.fromarray(rgb).save(overlay_dir/(name+'.png'))
    return {'reference':str(reference),'output':str(output),'regions':rows}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-dir',required=True,type=Path)
    cli.add_argument('--output-dir',required=True,type=Path)
    cli.add_argument('--report',required=True,type=Path)
    cli.add_argument('--overlay-dir',type=Path)
    args=cli.parse_args();dpi=200
    rows=[compare(args.reference_dir/(p.stem.removesuffix('_SHA')+'_0.pdf'),p,dpi,
                  args.overlay_dir/p.stem if args.overlay_dir else None)
          for p in sorted(args.output_dir.glob('*_SHA.pdf'))]
    if not rows:cli.error('No output PDFs found')
    report={'dpi':dpi,'ink_threshold':180,'coordinate_tolerance_mm':.254,'drawings':rows,
            'limitations':['Ink coverage only, not semantic correctness, color or exact shape similarity.',
                           'Tolerance uses a square neighborhood; nearby wrong ink may match.',
                           'Both directions are reported; blank background is never counted.',
                           'Regions are fixed for this A3 corpus and are not a whole-page completeness test.'],
            'overlay_legend':{'red':'reference ink without nearby output ink','blue':'output ink without nearby reference ink','gray':'ink within tolerance'}}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        print(Path(row['output']).name)
        for name,value in row['regions'].items():
            print(' ',name,round(value['reference_ink_covered_fraction'] or 0,4),round(value['output_ink_supported_fraction'] or 0,4))


if __name__=='__main__':main()
