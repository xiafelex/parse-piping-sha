import sys,unittest,xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'workflow'))
from annotate_received_tables import overlay,format_target


class AnnotationStyleTests(unittest.TestCase):
    def test_chengda_markers_and_single_straight_leaders(self):
        targets=[]
        for i,kind in enumerate(['weld','support','valve','flange','spool']):
            targets.append(format_target({'kind':kind,'number':['FW-123','SP12','V2','FL3','A'][i],
                'weld_type':'FF','anchor':[10.,20.],'center':[30.+i*12,35.],
                'leader_end':[27.+i*12,32.], 'route':[[10,20],[15,40],[27,32]]}))
        svg=overlay('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>',targets)
        root=ET.fromstring(svg);ns={'s':'http://www.w3.org/2000/svg'}
        group=root.find('s:g[@id="received-table-callouts"]',ns)
        self.assertEqual(len(group.findall('s:polyline',ns)),0)
        leaders=[v for v in group.findall('s:line',ns) if v.get('data-layer')=='number-leader']
        self.assertEqual(len(leaders),5)
        self.assertEqual((leaders[0].get('x1'),leaders[0].get('x2')),('10.0','27.0'))
        circle=group.find('s:circle',ns)
        self.assertEqual((circle.get('r'),circle.get('stroke'),circle.get('fill')),('4.5','#ff0000','none'))
        rectangles=group.findall('s:rect',ns)
        self.assertEqual(len(rectangles),5)
        self.assertEqual(sum(v.get('stroke')=='#0066ff' for v in rectangles),3)
        self.assertEqual(sum(v.get('stroke')=='#ff0000' for v in rectangles),2)
        self.assertTrue(all(v.get('rx') is None for v in rectangles))
        self.assertEqual([v.text for v in group.findall('s:text',ns)],['FW-123','SP12','V2','FL3','A'])
        self.assertTrue(all(v.get('stroke') not in ('white','#ffffff','#fff') for v in group))
        self.assertTrue(all(v.get('fill')=='none' for v in group if v.tag.rsplit('}',1)[-1] in ('circle','rect')))
        texts=group.findall('s:text',ns)
        self.assertAlmostEqual(float(texts[0].get('font-size')),2.2*1.5*4/6)
        self.assertAlmostEqual(float(texts[1].get('font-size')),3.3)
        self.assertAlmostEqual(float(group.find('s:rect',ns).get('height')),4.2)
        self.assertAlmostEqual(float(texts[-1].get('font-size')),2.4)
        self.assertAlmostEqual(float(rectangles[-2].get('height')),4.35)
        self.assertTrue(float(rectangles[-1].get('width'))<float(rectangles[-2].get('width')))

if __name__=='__main__':unittest.main()
