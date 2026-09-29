from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR

from pypdf import PdfReader


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
SOURCE_SHA = PROJECT_DIR / "SHA文件转换" / "00-42-0203-26-00EE00002-01.sha"
sys.path.insert(0, str(PARSER_DIR))


def load_module(filename: str, module_name: str):
    path = PARSER_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ChengdaPdfRendererTests(unittest.TestCase):
    def test_pdf_uses_physical_size_declared_by_svg(self) -> None:
        """A2 SHA不能被PDF渲染器强制输出为841×594 mm的A1页面。"""

        parser = load_module("sha_to_svg_prototype.py", "chengda_sha_to_svg_pdf")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            svg = temporary_path / "page.svg"
            pdf = temporary_path / "page.pdf"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            subprocess.run(
                ["node", str(PARSER_DIR / "render_svg_pdf.mjs"), str(svg), str(pdf)],
                cwd=PARSER_DIR,
                check=True,
                capture_output=True,
                text=True,
            )
            document = PdfReader(str(pdf))
            width = float(document.pages[0].mediabox.width)
            height = float(document.pages[0].mediabox.height)

        self.assertEqual(len(document.pages), 1)
        self.assertAlmostEqual(width, 594 * 72 / 25.4, delta=1.0)
        self.assertAlmostEqual(height, 420 * 72 / 25.4, delta=1.0)


if __name__ == "__main__":
    unittest.main()
