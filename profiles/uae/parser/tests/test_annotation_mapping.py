import csv,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from uae_annotation_mapping import read_tables,Drawing


class TableTests(unittest.TestCase):
    def test_duplicate_headers_and_extra_unit_column_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            with (p/'焊口表.csv').open('w',encoding='gb18030',newline='') as f:
                writer=csv.writer(f);writer.writerow(['管线号','数量','元件编号','数量','元件编号'])
                writer.writerow([' X ',' 1 ',' PIPE-1 ',' 2 ',' ELBOW-2 ',' 213 '])
            for name in ('支架表.csv','阀门表.csv','法兰表.csv'):
                (p/name).write_text('管线号,UCI\n',encoding='gb18030')
            row=read_tables(p)['weld'][0]
            self.assertEqual((row['数量'],row['数量2'],row['元件编号'],row['元件编号2'],row['单元号']),('1','2','PIPE-1','ELBOW-2','213'))

    def test_pipe_interpolation_uses_weld_orientation_and_rejects_off_axis(self):
        d=Drawing.__new__(Drawing)
        d.components=[{'type':'PIPE','omitted':False,'uci':'pipe','points':[('END-POINT',(0.,0.,0.,4.)),('END-POINT',(1000.,0.,0.,4.))]}]
        d.geometries=lambda u:[{'sheet':'Sheet6','root':123,'points':[(90.,100.),(10.,100.)]}]
        d.endpoint_anchor=lambda xyz,sheet:(10.,100.) if xyz[0]==0 else (90.,100.)
        self.assertEqual(d.pipe_point((250.,0.,0.))[0]['anchor'],(30.,100.))
        self.assertEqual(d.pipe_point((250.,1.,0.)),[])
        self.assertEqual(d.pipe_point((1200.,0.,0.)),[])

    def test_supplied_spool_uses_pipe_between_real_weld_endpoints(self):
        d=Drawing.__new__(Drawing);d.pageby={'Sheet6':1}
        d.pipe_point=lambda xyz:[{'sheet':'Sheet6','anchor':(xyz[0],20.),'root':123}]
        rows=[{'焊点编号':'FW-01','管段编号':'','焊点位置':'0,0,0','元件编号':'P1','元件编号2':'F1','描述1':'PIPE,A106','描述2':'FLANGE','_row':2},
              {'焊点编号':'S-02','管段编号':'A','焊点位置':'50,0,0','元件编号':'P1','元件编号2':'E1','描述1':'PIPE,A106','描述2':'ELBOW','_row':3}]
        welds=[{'kind':'weld','number':'S-02','sheet':'Sheet6','anchor':(50.,20.),'root':456}]
        targets,audit=d.map_spools('line',rows,welds)
        self.assertEqual(len(targets),1)
        self.assertEqual((targets[0]['number'],targets[0]['anchor']),('A',(25.,20.)))
        self.assertEqual(targets[0]['component_ids'],['E1','P1'])
        self.assertEqual(audit[0]['所属焊口'],'S-02')

    def test_cross_page_spool_retains_same_number_on_each_page(self):
        d=Drawing.__new__(Drawing);d.pageby={'Sheet6':1,'Sheet8':2};d.pipe_point=lambda xyz:[]
        rows=[{'焊点编号':'S-01','管段编号':'A','焊点位置':'0,0,0','元件编号':'E1','元件编号2':'F1','描述1':'ELBOW','描述2':'FLANGE','_row':2},
              {'焊点编号':'S-02','管段编号':'A','焊点位置':'50,0,0','元件编号':'E2','元件编号2':'F2','描述1':'ELBOW','描述2':'FLANGE','_row':3}]
        welds=[{'kind':'weld','number':r['焊点编号'],'sheet':s,'anchor':(10.,20.),'root':i} for i,(r,s) in enumerate(zip(rows,['Sheet6','Sheet8']))]
        targets,audit=d.map_spools('line',rows,welds)
        self.assertEqual([(t['number'],t['page']) for t in targets],[('A',1),('A',2)])
        for r in rows:r['管段编号']=''
        self.assertEqual(d.map_spools('line',rows,welds),([],[]))

if __name__=='__main__':unittest.main()
