"""Concurrent UAE SHA review PDFs; isolated from the other project profiles."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import _bootstrap
from analyze_iso_split import read_sha_streams
from sha_native import preview
from sha_document import document_pages
from pypdf import PdfReader, PdfWriter

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)


def compose(streams, sheet=None):
    pages=document_pages(streams)
    if sheet is None:
        if len(pages)!=1:raise ValueError('Multipage SHA requires an explicit drawing sheet')
        sheet=pages[0]['sheet']
    if sheet not in {p['sheet'] for p in pages}:raise ValueError(f'Not a drawing sheet: {sheet}')
    root = ET.Element(f'{{{NS}}}svg', {
        'viewBox': '0 -297 420 297', 'width': '420mm', 'height': '297mm',
        'data-page-width-mm': '420', 'data-page-height-mm': '297'})
    ET.SubElement(root, f'{{{NS}}}rect', {'x': '0', 'y': '-297', 'width': '420', 'height': '297', 'fill': 'white'})
    coverage = {}
    for name in ('Sheet39817', sheet):
        svg, coverage[name] = preview(streams[name], streams)
        group = ET.SubElement(root, f'{{{NS}}}g', {'id': name})
        for child in ET.fromstring(svg):
            if child.tag == f'{{{NS}}}g':
                group.append(child)
    return ET.tostring(root, encoding='unicode'), coverage


def validate_pdf(path, expected_pages=1):
    payload = path.read_bytes()
    if not payload.startswith(b'%PDF-') or payload.count(b'%%EOF') != 1:
        raise ValueError('Invalid PDF header or EOF count')
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted or len(reader.pages) != expected_pages:
        raise ValueError(f'Expected {expected_pages} unencrypted PDF pages, got {len(reader.pages)}')
    for page in reader.pages:
        box=page.mediabox
        size = [float(box.width), float(box.height)]
        if any(abs(a-b*72/25.4)>2 for a,b in zip(size,(420,297))):
            raise ValueError(f'Unexpected A3 page size: {size}')
    return size


def publish_delivery_pdf(source, target):
    """Match Chengda delivery: preserve browser bytes and inherit directory ACL."""
    os.replace(source, target)
    if os.name == 'nt':
        result = subprocess.run(['icacls', str(target), '/inheritance:e'], capture_output=True)
        if result.returncode:
            raise RuntimeError('Failed to restore PDF inherited permissions: ' +
                               (result.stdout + result.stderr).decode(errors='replace'))


def convert(path, input_dir, output_dir, force):
    out = output_dir / path.parent.relative_to(input_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / (path.stem + '_SHA.pdf')
    if target.exists() and not force:
        raise FileExistsError(f'{target}; use --force to replace generated results')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    streams=read_sha_streams(path)
    pages=document_pages(streams)
    coverage=[]
    with tempfile.TemporaryDirectory(prefix='._uae_pdf_', dir=out) as directory:
        work = Path(directory)
        pdf_path = work/'page.pdf'
        jobs=[]
        for page in pages:
            svg,counts=compose(streams,page['sheet'])
            svg_path=work/f'page_{page["number"]:03}.svg'
            page_pdf=svg_path.with_suffix('.pdf')
            svg_path.write_text(svg,encoding='utf-8')
            jobs.append({'input':str(svg_path),'output':str(page_pdf)})
            coverage.append({**page,'coverage':counts})
        manifest=work/'manifest.json';manifest.write_text(json.dumps(jobs),encoding='utf-8')
        result = subprocess.run(['node', str(_bootstrap.PARSER/'render_svg_pdf.mjs'), '--manifest',str(manifest)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180+30*len(pages))
        if result.returncode:
            raise RuntimeError(result.stdout+'\n'+result.stderr)
        if len(jobs)==1:
            os.replace(jobs[0]['output'],pdf_path)
        else:
            writer=PdfWriter()
            for job in jobs:
                validate_pdf(Path(job['output']))
                writer.append(job['output'])
            writer.add_metadata({'/Title':path.stem,'/Subject':'Rendered from SHA native pages; no reference PDF used.'})
            writer.write(pdf_path);writer.close()
        size = validate_pdf(pdf_path,len(pages))
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Source SHA changed during conversion')
        publish_delivery_pdf(pdf_path, target)
    validate_pdf(target,len(pages))
    return {'source':str(path), 'pdf':str(target), 'status':'ok','page_count':len(pages),'pages':coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--report-file',type=Path,help='Optional diagnostics JSON, outside the PDF-only output directory')
    args = parser.parse_args()
    if args.workers < 1: parser.error('--workers must be positive')
    source, output = args.input_dir.resolve(), args.output_dir.resolve()
    if not source.is_dir(): parser.error('Input directory does not exist')
    if source == output or source in output.parents:
        parser.error('Keep generated PDFs outside the source directory')
    if args.report_file and (args.report_file.resolve().parent==output or output in args.report_file.resolve().parents):
        parser.error('Keep diagnostics outside the PDF-only output directory')
    files = sorted(p for p in source.rglob('*.sha') if not p.stem.lower().endswith(('-template','-usergraphics','-titleblock')))
    if not files: parser.error('No main SHA files found')
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(convert,p,source,output,args.force):p for p in files}
        for task in as_completed(tasks):
            try: row = task.result()
            except Exception as exc: row = {'source':str(tasks[task]),'status':'error','error':str(exc)}
            results.append(row)
            print(json.dumps({k:v for k,v in row.items() if k!='pages'},ensure_ascii=False),flush=True)
    if args.report_file:
        args.report_file.parent.mkdir(parents=True,exist_ok=True)
        args.report_file.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return int(any(r['status'] != 'ok' for r in results))


if __name__ == '__main__': raise SystemExit(main())
