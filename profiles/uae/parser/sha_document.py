"""Order UAE Shape2D pages by native title-block page fields, never stream IDs."""
import re
from sha_graphics import native_texts


def document_pages(streams):
    names=[n for n in streams if re.fullmatch(r'Sheet\d+',n)]
    frame='Sheet39817'
    if frame not in names:
        raise ValueError('Shared UAE title-block sheet Sheet39817 is missing')
    pages=[]
    for name in names:
        if name==frame:continue
        texts=native_texts(streams[name],None)
        number={int(t['text']) for t in texts if t['text'].isdigit() and .386<t['x']<.390 and .008<t['y']<.012}
        total={int(t['text']) for t in texts if t['text'].isdigit() and .398<t['x']<.402 and .008<t['y']<.012}
        if len(number)!=1 or len(total)!=1:
            raise ValueError(f'{name}: ambiguous or missing native SHEET/OF fields: {number}/{total}')
        pages.append({'sheet':name,'number':number.pop(),'total':total.pop(),'frame':frame})
    pages.sort(key=lambda p:p['number'])
    if not pages or [p['number'] for p in pages]!=list(range(1,len(pages)+1)) or any(p['total']!=len(pages) for p in pages):
        raise ValueError(f'Incomplete or conflicting native page sequence: {pages}')
    return pages
