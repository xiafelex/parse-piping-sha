import math
import struct
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pod_binary import START,END,Reader,parse_tree,lossless_tree,rebuild_tree
from pod_decode import variant,decode
from cross_format import compare
from sha_native import lines,inspect
from sha_bindings import bound_text


class BinaryTests(unittest.TestCase):
    def test_revision_binding_last_three_and_property_transform(self):
        expression='/Revision/RevisionRecord[((last()&gt;3) and position()=(last()-1)) or ((last() &lt;=3) and position()=2)]/MinorRev_ForRevise'
        xml=('<?xml version="1.0"?><body><intstgxml stream="Revision" select="'+expression+'"/></body>').encode('utf-16le')
        data=xml+bytes(64)+struct.pack('<4d',.31,.08,1,0)
        for versions,expected in [('A0','0'),('A012','1')]:
            source=('<Revision>'+''.join('<RevisionRecord><MinorRev_ForRevise>'+v+'</MinorRev_ForRevise></RevisionRecord>' for v in versions)+'</Revision>').encode()
            values=bound_text(data,{'TaggedTxtData/Revision':source})
            self.assertEqual(len(values),1)
            self.assertEqual(values[0]['text'],expected)
            self.assertEqual(values[0]['x'],.31)

    def test_nested_roundtrip_unknown_bytes(self):
        data=START+struct.pack('<I',5)+b'unknown'+START+struct.pack('<I',9)+b'\x81\xff\x7f\x00'+END+b'tail'+END
        root,nodes=parse_tree(data)
        self.assertEqual(len(nodes),2)
        self.assertEqual(rebuild_tree(lossless_tree(root,data)),data)

    def test_truncated_or_unbalanced_rejected(self):
        for value in (b'',START,START+struct.pack('<I',1),END,START+struct.pack('<I',1)+END+b'tail'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_tree(value)

    def test_length_prefixed_utf16(self):
        raw='测试'.encode('utf-16le')+b'\0\0'
        self.assertEqual(Reader(struct.pack('<I',len(raw))+raw).string(),'测试')
        with self.assertRaises(ValueError):Reader(struct.pack('<I',100)+raw).string()

    def test_typed_values(self):
        self.assertEqual(variant(Reader(struct.pack('<HiB',3,-12,1)))['value'],-12)
        self.assertAlmostEqual(variant(Reader(struct.pack('<HdB',5,107.95,1)))['value'],107.95)
        self.assertIsNone(variant(Reader(struct.pack('<HIHB',0,2,0,0)))['value'])
        with self.assertRaises(ValueError):variant(Reader(struct.pack('<HdB',5,math.nan,1)))

    def test_sha_uint32_ids(self):
        raw=struct.pack('<HHHIIIHI4d',24,50,0,74500,74427,74563,0,192,.26899,.17905,.271335,.183075)
        result=lines(raw)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['owner_ref'],74427)
        self.assertEqual(result[0]['xy_metres'][0],.26899)
        self.assertEqual(lines(raw[:-1]),[])
        flagged=bytearray(raw)
        struct.pack_into('<H',flagged,18,0x10)
        self.assertEqual(lines(flagged)[0]['record_flags'],0x10)
        struct.pack_into('<H',flagged,18,0x80)
        self.assertEqual(lines(flagged),[])

    def test_polyline_boundary_validation(self):
        from sha_graphics import polyline_primitives
        record=struct.pack('<HI',0x84,56)+bytes(18)+struct.pack('<I',2)+b'\x01\x02'+struct.pack('<4d',.3,0,.02,0)
        self.assertEqual(len(polyline_primitives(bytes(8)+record)),1)
        bad=bytearray(record)
        struct.pack_into('<I',bad,24,3)
        self.assertEqual(polyline_primitives(bytes(8)+bad),[])


ROOT=Path(__file__).resolve().parents[5]
INPUT=next((p for p in (
    ROOT/'阿联酋项目/项目文件资料/30201_50150_LT_HE-H4-000-13-61-023_NATIVE/3. NativeFiles',
    ROOT/'阿联酋项目/30201_50150_LT_HE-H4-000-13-61-023_NATIVE/3. NativeFiles') if p.exists()),
    ROOT/'阿联酋项目/项目文件资料/30201_50150_LT_HE-H4-000-13-61-023_NATIVE/3. NativeFiles')

@unittest.skipUnless(INPUT.exists(),'local UAE source corpus unavailable')
class CorpusTests(unittest.TestCase):
    def test_four_files_binary_and_geometry(self):
        import olefile
        expected={'0212':(3581,49,100,416),'0937':(1581,17,33,152),'0939':(1856,21,39,159),'0511':(2535,30,60,247)}
        for path in sorted(INPUT.glob('*.pod')):
            with self.subTest(path=path.name):
                with olefile.OleFileIO(path) as ole:data=ole.openstream('PODFile').read()
                root,_=parse_tree(data)
                self.assertEqual(rebuild_tree(lossless_tree(root,data)),data)
                result=decode(data)
                e=next(v for k,v in expected.items() if k in path.name)
                self.assertEqual((result['object_count'],len(result['components']),sum(len(c['ports']) for c in result['components']),result['primitive_counts'][3]),e)
                self.assertEqual(result['decode_errors'],[])
                self.assertFalse(result['coverage']['complete_semantic_decode'])
                base=path.name.removesuffix('-pod.pod')
                checks=compare(result,path.with_name(base+'-pcf.pcf'),inspect(path.with_name(base+'.sha')))
                self.assertEqual(checks['pcf']['missing_in_pod'],[])
                self.assertTrue(all(v['matches_pcf'] for v in checks['pcf']['attribute_checks']))
                self.assertEqual(checks['sha']['exact_geometry_matches'],e[3])
                self.assertEqual(checks['sha']['exact_arc_matches'],result['primitive_counts'].get(0,0))
                from analyze_iso_split import read_sha_streams
                from sha_native import preview
                streams=read_sha_streams(path.with_name(base+'.sha'))
                svg,coverage=preview(streams['Sheet39817'],streams)
                self.assertEqual(coverage['bound_text_count'],13)
                latest=[v for v in bound_text(streams['Sheet39817'],streams) if v['latest_revision']]
                self.assertEqual(len(latest),1)
                self.assertEqual(latest[0]['text'],'0')
                self.assertGreater(latest[0]['x'],.405)
                self.assertLess(latest[0]['y'],.02)
                dates=[v for v in bound_text(streams['Sheet39817'],streams) if v['field']=='RevisedDate']
                self.assertEqual(len(dates),2)
                self.assertTrue(all(14<v['text_box_width_mm']<16 for v in dates))
                self.assertIn('ISSUED FOR CONSTRUCTION (IFC)',svg)
                self.assertNotIn('&lt;?xml',svg)
                self.assertGreaterEqual(coverage['frame_text_records'],70)
                self.assertEqual(coverage['embedded_bitmap_instances'],1)
                self.assertEqual(coverage['line_count'],95)
                self.assertEqual(coverage['polyline_count'],2)
                self.assertEqual(coverage['embedded_vector_contour_count'],5)
                self.assertIn('PROJECT 5.0 (P5)',svg)
                self.assertIn('AMEC INTERNATIONAL LTD.',svg)
                self.assertIn('>ADNOC Gas</tspan>',svg)
                self.assertIn('(°C)',svg)
                self.assertIn('fill="#1859a9"',svg)
                from sha_graphics import text_formats, circle_primitives, native_texts
                main_texts=native_texts(streams['Sheet6'],None)
                boxed=[t for t in native_texts(streams['Sheet39817'],None) if t['text_box_border']]
                self.assertEqual([t['text'] for t in boxed],['BILL OF MATERIALS'])
                self.assertEqual(boxed[0]['text_box_height_mm'],4)
                expected_texts={'0212':226,'0937':162,'0939':180,'0511':225}
                self.assertEqual(len(main_texts),next(v for k,v in expected_texts.items() if k in path.name))
                self.assertFalse(any(v['text']=='û' for v in main_texts))
                coordinate=next(v for v in main_texts if v['text'].startswith('E '))
                self.assertEqual(text_formats(streams['StyleCluster'])[coordinate['format_ref']]['weight'],'normal')
                drawing=next(v for v in main_texts if v['text']==base)
                self.assertAlmostEqual(text_formats(streams['StyleCluster'])[drawing['format_ref']]['width_scale'],.66)
                main_svg,_=preview(streams['Sheet6'],streams)
                self.assertIn('scale(0.66 1)',main_svg)
                title=next(v for v in native_texts(streams['Sheet39817'],None) if v['text'].startswith('PROJECT 5.0'))
                self.assertEqual(text_formats(streams['StyleCluster'])[title['format_ref']]['paragraph_alignment'],0x100)
                self.assertEqual(len(circle_primitives(streams['Sheet6'])),result['primitive_counts'].get(1,0))
                from sha_contours import filled_contours
                fills=filled_contours(streams['Sheet6'],streams['StyleCluster'])
                expected_fills={'0212':80,'0937':32,'0939':34,'0511':52}
                self.assertEqual(len(fills),next(v for k,v in expected_fills.items() if k in path.name))
                self.assertTrue(all(v['color']=='#000000' for v in fills))
                self.assertIn('9. REINFORCING PAD',svg)
                self.assertIn('data:image/bmp;base64,',svg)
                from sha_graphics import line_styles, native_texts
                styles=line_styles(streams['StyleCluster'])
                self.assertAlmostEqual(styles[39]['width'],.35)
                self.assertAlmostEqual(styles[84]['width'],.35)
                self.assertTrue(all(line['flags'] in styles for line in lines(streams['Sheet39817'])))
                note_boxes=[t for t in main_texts if t['text_box_border']]
                if any(k in path.name for k in ('0937','0939')):
                    self.assertEqual(len(note_boxes),1)
                    self.assertEqual(note_boxes[0]['alignment_flags'],1)
                    import xml.etree.ElementTree as ET
                    rectangles=ET.fromstring(main_svg).findall('.//{http://www.w3.org/2000/svg}rect')
                    note_box=next(r for r in rectangles if abs(float(r.get('width'))-note_boxes[0]['text_box_width_mm'])<1e-6)
                    self.assertAlmostEqual(float(note_box.get('y')),-1000*note_boxes[0]['y'])
                self.assertTrue(any(v['color']=='#ff0000' for v in styles.values()))
                note=next(v for v in native_texts(streams['Sheet39817'],39817) if v['text'].startswith('1. ALL'))
                self.assertEqual((note['direction_x'],note['direction_y']),(1.0,0.0))
                self.assertGreater(note['text_box_width_mm'],100)
                frame_records=native_texts(streams['Sheet39817'],39817)
                centered=next(v for v in frame_records if v['text'].strip()=='SUPPORT SCHEDULE NO.')
                self.assertEqual(centered['alignment_flags'],0x17)
                self.assertAlmostEqual(text_formats(streams['StyleCluster'])[centered['format_ref']]['width_scale'],.92)
                self.assertIn('text-anchor="middle"',svg)
                from sha_contours import embedded_contours
                vectors=embedded_contours(streams['Sheet39817'],streams)
                self.assertEqual(len(vectors),5)
                points=[p for v in vectors for contour in v['paths'] for p in contour]
                self.assertTrue(all(307<x<333 and -58<y<-49 for x,y in points))
                self.assertEqual(len({v['ref'] for v in vectors}),5)
                from sha_contours import embedded_strokes
                strokes=embedded_strokes(streams['Sheet39817'],streams)
                self.assertEqual(len(strokes),11)
                self.assertTrue(all(abs(v['width']-.25)<1e-8 and v['color']=='#000000' for v in strokes))
                self.assertEqual(svg.count('data-embedded-stroke-ref='),11)
                self.assertTrue(all(307<x<333 and -58<y<-49 for v in strokes for x,y in v['points']))

if __name__=='__main__':unittest.main()
