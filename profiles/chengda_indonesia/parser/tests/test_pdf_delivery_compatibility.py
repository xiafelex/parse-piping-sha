from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import uuid
import hashlib
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

from pypdf import PdfReader, PdfWriter


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
CONVERTER = CODE_PROGRAM_DIR / "convert_chengda_sha_to_pdf.py"
OUTPUT_DIR = PROJECT_DIR / "SHA文件转换"


def load_converter_module():
    spec = importlib.util.spec_from_file_location("chengda_converter", CONVERTER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载转换入口：{CONVERTER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PdfDeliveryCompatibilityTests(unittest.TestCase):
    def test_normalized_delivery_pdf_is_strict_single_page_pdf_13(self) -> None:
        """正式交付文件应由PDF库重写，避免依赖Chromium的直接输出结构。"""

        converter = load_converter_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "browser.pdf"
            delivery = root / "delivery.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=1684.08, height=1191.12)
            with source.open("wb") as handle:
                writer.write(handle)

            converter.normalize_delivery_pdf(source, delivery)

            self.assertTrue(delivery.is_file())
            self.assertEqual(delivery.read_bytes()[:8], b"%PDF-1.3")
            reader = PdfReader(str(delivery), strict=True)
            self.assertEqual(len(reader.pages), 1)
            page = reader.pages[0]
            self.assertAlmostEqual(float(page.mediabox.width), 1684.08, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), 1191.12, places=2)
            self.assertEqual(delivery.read_bytes().count(b"%%EOF"), 1)

    @unittest.skipUnless(sys.platform == "win32", "ACL回归测试仅适用于Windows")
    def test_published_pdf_inherits_readable_output_directory_acl(self) -> None:
        """发布动作不能把沙箱临时文件的受保护ACL带入正式输出目录。"""

        converter = load_converter_module()
        target = OUTPUT_DIR / f"._acl_test_{uuid.uuid4().hex}.pdf"
        try:
            with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as temporary:
                source = Path(temporary) / "delivery.pdf"
                writer = PdfWriter()
                writer.add_blank_page(width=1684.08, height=1191.12)
                with source.open("wb") as handle:
                    writer.write(handle)

                converter.publish_delivery_pdf(source, target)

            acl = subprocess.run(
                ["icacls", str(target)],
                capture_output=True,
                text=True,
                encoding="mbcs",
                check=True,
            )
            self.assertIn("(I)", acl.stdout)
            self.assertEqual(len(PdfReader(str(target), strict=True).pages), 1)
        finally:
            target.unlink(missing_ok=True)

    def test_publish_preserves_browser_pdf_bytes(self) -> None:
        """发布过程不能重写含位图/字体资源的浏览器PDF对象结构。"""

        converter = load_converter_module()
        target = OUTPUT_DIR / f"._byte_test_{uuid.uuid4().hex}.pdf"
        try:
            with tempfile.TemporaryDirectory(dir=OUTPUT_DIR) as temporary:
                source = Path(temporary) / "browser.pdf"
                writer = PdfWriter()
                writer.add_blank_page(width=1684.08, height=1191.12)
                with source.open("wb") as handle:
                    writer.write(handle)
                expected = hashlib.sha256(source.read_bytes()).hexdigest()

                converter.publish_delivery_pdf(source, target)

            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(actual, expected)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
