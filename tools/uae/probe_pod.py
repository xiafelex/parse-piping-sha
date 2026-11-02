"""Read-only POD probe; bounded hex and object summaries for further research."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'profiles/uae/parser'))
import olefile
from pod_binary import parse_tree,length_strings

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('pod',type=Path)
    cli.add_argument('--offset',type=lambda v:int(v,0),default=0)
    cli.add_argument('--length',type=int,default=128)
    cli.add_argument('--depth',type=int,default=2)
    args=cli.parse_args()
    with olefile.OleFileIO(args.pod) as ole:data=ole.openstream('PODFile').read()
    if args.offset<0 or not 0<=args.length<=4096:cli.error('offset>=0 and length in 0..4096 required')
    root,nodes=parse_tree(data);depths={}
    for n in nodes:
        depths[n.start]=depths[n.parent]+1 if n.parent is not None else 0
        if depths[n.start]<=args.depth:
            print(n.start,n.end,'tag',n.tag,'children',len(n.children),length_strings(n.head(data),n.start+8)[:3])
    print('HEX',args.offset,data[args.offset:args.offset+args.length].hex(' '))

if __name__=='__main__':main()
