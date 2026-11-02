import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'workflow'))
from convert_uae_sha_to_pdf import publish_delivery_pdf, validate_pdf


class DeliveryTests(unittest.TestCase):
    def test_publish_preserves_bytes_and_restores_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root/'browser.pdf', root/'delivery.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=420*72/25.4, height=297*72/25.4)
            writer.write(source)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if os.name == 'nt':
                subprocess.run(['icacls', str(source), '/inheritance:d'], check=True, capture_output=True)
            publish_delivery_pdf(source, target)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)
            validate_pdf(target)
            self.assertEqual([p.name for p in root.iterdir()], ['delivery.pdf'])
            if os.name == 'nt':
                acl = subprocess.run(['icacls', str(target)], check=True, capture_output=True)
                self.assertIn(b'(I)', acl.stdout)


if __name__ == '__main__': unittest.main()
