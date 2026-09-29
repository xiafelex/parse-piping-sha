from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


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


class ChengdaPageGeometryTests(unittest.TestCase):
    def test_a2_landscape_extent_is_read_from_template_stream(self) -> None:
        """缺少Sheet页幅时，模板中的594×420 mm声明不能丢失。"""

        analyze = load_module("analyze_iso_split.py", "chengda_analyze_iso_split")
        sys.modules["analyze_iso_split"] = analyze
        parser = load_module("sha_to_svg_prototype.py", "chengda_sha_to_svg")
        streams = analyze.read_sha_streams(SOURCE_SHA)

        actual = parser.declared_sheet_viewbox(streams["Sheet221"])

        expected = (0.0, 4824.136246177718, 9978.200058876533, 7055.863753822282)
        self.assertIsNotNone(actual)
        assert actual is not None
        for observed, wanted in zip(actual, expected):
            self.assertAlmostEqual(observed, wanted, places=6)

    def test_rendered_svg_declares_detected_a2_physical_size(self) -> None:
        """PDF渲染器必须能从SVG读取594×420 mm，而不是固定使用A1。"""

        analyze = load_module("analyze_iso_split.py", "chengda_analyze_iso_split_svg")
        sys.modules["analyze_iso_split"] = analyze
        parser = load_module("sha_to_svg_prototype.py", "chengda_sha_to_svg_size")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, output, 1, False, None, None, "Sheet6")
            root = ET.parse(output).getroot()

        self.assertEqual(root.attrib["width"], "594mm")
        self.assertEqual(root.attrib["height"], "420mm")
        self.assertEqual(root.attrib["data-page-width-mm"], "594")
        self.assertEqual(root.attrib["data-page-height-mm"], "420")


if __name__ == "__main__":
    unittest.main()
