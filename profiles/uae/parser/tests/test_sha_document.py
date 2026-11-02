import struct,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sha_document import document_pages
from sha_contours import embedded_instances,records


class DocumentTests(unittest.TestCase):
    def test_page_order_uses_title_fields_not_stream_names(self):
        def text(data,_):return [{'text':str(data[0]),'x':.388,'y':.010},{'text':'3','x':.4,'y':.010}]
        streams={'Sheet39817':b'frame','Sheet6':b'\x01','Sheet95000':b'\x02','Sheet32':b'\x03'}
        with patch('sha_document.native_texts',side_effect=text):
            self.assertEqual([p['sheet'] for p in document_pages(streams)],['Sheet6','Sheet95000','Sheet32'])
        streams['Sheet32']=b'\x02'
        with patch('sha_document.native_texts',side_effect=text),self.assertRaises(ValueError):document_pages(streams)

    def test_embedded_instance_selects_only_referenced_owner_tree(self):
        def node(kind,ref,owner):return struct.pack('<HIII',kind,8,ref,owner)
        cluster=b'\0'*8+node(0x114,151,0)+node(0x4d,129,151)+node(0x114,177,0)+node(0x13,166,177)
        for length in (119,127):
            instance=bytearray(length);struct.pack_into('<HI',instance,0,0xce,length-6)
            struct.pack_into('<7d',instance,43,1,0,0,1,.2,.1,1)
            struct.pack_into('<II',instance,length-8,151,100)
            streams={'JSite100/PSMcluster0':cluster}
            selected=list(embedded_instances(b'\0'*8+instance,streams))[0]
            refs=[struct.unpack_from('<I',r,6)[0] for _,_,r in records(selected['cluster'])]
            self.assertEqual(refs,[151,129])
            struct.pack_into('<I',instance,length-8,999)
            with self.assertRaises(ValueError):list(embedded_instances(b'\0'*8+instance,streams))

if __name__=='__main__':unittest.main()
