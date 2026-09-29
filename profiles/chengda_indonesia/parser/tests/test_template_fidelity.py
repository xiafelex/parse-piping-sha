from __future__ import annotations

import importlib.util
import re
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from chengda_paths import PROJECT_DIR as DATA_DIR, PARSER_DIR as CODE_PARSER_DIR, PROGRAM_DIR as CODE_PROGRAM_DIR, SHARED_DIR, ASSET_DIR


PARSER_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DATA_DIR
SOURCE_SHA = PROJECT_DIR / "SHA文件转换" / "00-42-0203-26-00EE00002-01.sha"
SOURCE_0200_SHA = (
    PROJECT_DIR
    / "SHA文件转换"
    / "000200FW"
    / "00-42-0200-26-10FW41001-03.sha"
)
SOURCE_0200_01_SHA = (
    PROJECT_DIR
    / "SHA文件转换"
    / "000200FW"
    / "00-42-0200-26-10FW41001-01.sha"
)
SOURCE_200701_CLOUD_SHA = (
    PROJECT_DIR
    / "SHA文件转换"
    / "200701"
    / "200701 SHA"
    / "20-42-0701-26-AI-7103-01.sha"
)
SOURCE_201600_GBK_TITLE_SHA = (
    PROJECT_DIR
    / "SHA文件转换"
    / "201600"
    / "201600 SHA"
    / "20-42-1600-26-HCL-8890-02.sha"
)
SOURCE_201600_SHA_DIR = (
    PROJECT_DIR / "SHA文件转换" / "201600" / "201600 SHA"
)
SOURCE_201600_CLOUD_FALSE_POSITIVES = tuple(
    SOURCE_201600_SHA_DIR / f"{sheet_id}.sha"
    for sheet_id in (
        "20-42-1600-26-AI-8553-01",
        "20-42-1600-26-CL2-3404-02",
        "20-42-1600-26-CL-9004-01",
        "20-42-1600-26-CL-9004-03",
        "20-42-1600-26-EC-3737-01",
        "20-42-1600-26-EC-6056-01",
    )
)
SOURCE_201600_DEGREE_TEXT_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-ARH-1167-02.sha"
)
SOURCE_201600_DIRECT_REVISION_TEXT_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-AI-8553-01.sha"
)
SOURCE_201600_CUT_PIPE_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-EC-8087-01.sha"
)
SOURCE_201600_CUT_PIPE_PCF = (
    PROJECT_DIR
    / "SHA文件转换"
    / "201600"
    / "201600 0A PCF"
    / "20-42-1600-26-EC-8087-01.pcf"
)
SOURCE_201600_CONN_CLOUD_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-HTF-1155-02.sha"
)
SOURCE_201600_CONN_CLOUD_PCF = (
    PROJECT_DIR
    / "SHA文件转换"
    / "201600"
    / "201600 0A PCF"
    / "20-42-1600-26-HTF-1155-02.pcf"
)
SOURCE_201600_COMPASS_NOTE_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-WR-7012B-01.sha"
)
SOURCE_201600_PARTIAL_CLOUD_NOTE_SHA = (
    SOURCE_201600_SHA_DIR / "20-42-1600-26-WW-8887-04.sha"
)
sys.path.insert(0, str(PARSER_DIR))


def load_parser():
    spec = importlib.util.spec_from_file_location(
        "chengda_sha_template_fidelity", PARSER_DIR / "sha_to_svg_prototype.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载成达SHA解析器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChengdaTemplateFidelityTests(unittest.TestCase):
    def test_cut_pipe_table_uses_visible_piece_markers_and_pcf_rows(self) -> None:
        """复合表残留格不得生成虚假切管序号或一毫米切管。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_CUT_PIPE_SHA)
        records = parser.text_records(streams["Sheet6"])
        offsets, suppressed = parser.cut_pipe_table_text_adjustments(
            records,
            streams["PSMcluster0"],
            SOURCE_201600_CUT_PIPE_PCF,
        )
        kept = {
            str(record["text"]).strip()
            for record in records
            if (key := (int(record["graphic_ref"]), int(record["text_start"]))) in offsets
        }
        removed = {
            str(record["text"]).strip()
            for record in records
            if (int(record["graphic_ref"]), int(record["text_start"])) in suppressed
        }

        self.assertEqual({"<1>", "<2>", "<3>", "115", "24159", "1271", "3", "3/4"}, kept)
        self.assertIn("<4>", removed)
        self.assertIn("1", removed)

        adjusted = {
            str(record["text"]).strip(): (
                float(record["x"]) * parser.SHEET_UNIT + offsets[key][0],
                float(record["y"]) * parser.SHEET_UNIT + offsets[key][1],
            )
            for record in records
            if (key := (int(record["graphic_ref"]), int(record["text_start"]))) in offsets
            and str(record["text"]).strip() in {"<1>", "<2>", "<3>", "24159", "115", "1271"}
        }
        # The current SHA revision numbers the long main pipe as <1> and the
        # short upper branch as <2>; the older reference PDF used the reverse
        # numbering.  Drawing geometry, not that older row order, must win.
        self.assertAlmostEqual(adjusted["<1>"][1], adjusted["24159"][1], delta=0.01)
        self.assertAlmostEqual(adjusted["<2>"][1], adjusted["115"][1], delta=0.01)
        self.assertAlmostEqual(adjusted["<3>"][1], adjusted["1271"][1], delta=0.01)
        self.assertLess(adjusted["<1>"][0], adjusted["<2>"][0])

    def test_conn_to_cloud_block_recovers_horizontal_and_vertical_parent_offset(self) -> None:
        """CONN TO 云线文字要作为一个块恢复二维父容器位移。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_CONN_CLOUD_SHA)
        sheet = streams["Sheet6"]
        records = parser.text_records(sheet)
        groups = parser.revision_cloud_arc_groups(parser.standalone_arcs(sheet))
        offsets = parser.revision_cloud_connection_text_offsets(
            records,
            groups,
            streams["PSMcluster0"],
        )
        moved = [
            (str(record["text"]).strip(), offsets[key])
            for record in records
            if (key := (int(record["graphic_ref"]), int(record["text_start"]))) in offsets
        ]

        self.assertEqual(
            {
                "CONN TO",
                '28"-LATERAL/N1(2)',
                "28 in BE Undefined",
                "E 85280",
                "N 87520",
                "EL +7727",
            },
            {text for text, _ in moved},
        )
        self.assertEqual(1, len({offset for _, offset in moved}))
        dx, dy = moved[0][1]
        self.assertGreater(dx, 0)
        self.assertLess(dy, 0)

    def test_normal_cut_pipe_table_is_not_reflowed(self) -> None:
        """表格行与图中切管标记一致时必须保持SHA原布局。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_CONN_CLOUD_SHA)
        offsets, suppressed = parser.cut_pipe_table_text_adjustments(
            parser.text_records(streams["Sheet6"]),
            streams["PSMcluster0"],
            SOURCE_201600_CONN_CLOUD_PCF,
        )
        self.assertEqual({}, offsets)
        self.assertEqual(set(), suppressed)

    def test_partial_clouded_connection_line_vetoes_whole_block_relocation(self) -> None:
        """已有一行落在小云线内时，不得把整块连接信息吸到另一朵云。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_PARTIAL_CLOUD_NOTE_SHA)
        sheet = streams["Sheet6"]
        records = parser.text_records(sheet)
        groups = parser.revision_cloud_arc_groups(parser.standalone_arcs(sheet))
        offsets = parser.revision_cloud_text_offsets(
            records, groups, streams["PSMcluster0"]
        )

        self.assertEqual({}, offsets)
        line_record = next(
            record
            for record in records
            if str(record["text"]).strip() == "20-42-1600-26-WW-8887-05"
        )
        x = float(line_record["x"]) * parser.SHEET_UNIT
        y = float(line_record["y"]) * parser.SHEET_UNIT
        self.assertTrue(
            any(
                min(float(arc["center_x"]) - float(arc["radius"]) for arc in group)
                <= x
                <= max(float(arc["center_x"]) + float(arc["radius"]) for arc in group)
                and min(float(arc["center_y"]) - float(arc["radius"]) for arc in group)
                <= y
                <= max(float(arc["center_y"]) + float(arc["radius"]) for arc in group)
                for group in groups
            )
        )

    def test_compass_collision_moves_connection_block_and_plain_leader(self) -> None:
        """左上连接信息与建北图碰撞时，整块文字及无箭头引线端同步移动。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_COMPASS_NOTE_SHA)
        sheet = streams["Sheet6"]
        records = parser.text_records(sheet)
        offsets = parser.compass_collision_connection_text_offsets(
            records,
            streams["PSMcluster0"],
            parser.template_circle_primitives(streams["Sheet221"]),
        )
        moved = [
            (str(record["text"]).strip(), offsets[key])
            for record in records
            if (key := (int(record["graphic_ref"]), int(record["text_start"])))
            in offsets
        ]

        self.assertEqual(6, len(moved))
        self.assertEqual(1, len({offset for _, offset in moved}))
        dx, dy = moved[0][1]
        self.assertGreater(dx, 0)
        self.assertLess(dy, 0)
        segments = parser.unique_line_segments(
            parser.line_segments(sheet) + parser.template_line_segments(sheet)
        )
        self.assertEqual(
            {0x16AA: (1, dx, dy)},
            parser.plain_connection_leader_endpoint_offsets(
                records, offsets, segments, streams["PSMcluster0"]
            ),
        )

    def test_title_area_with_gb18030_text_keeps_all_bound_fields(self) -> None:
        """TitleArea含中文字节时，不能让整个图签绑定区失效。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_GBK_TITLE_SHA)
        records = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        title_fields = {
            str(record["field"]): str(record["text"])
            for record in records
            if record.get("stream") == "TitleArea"
        }

        self.assertEqual(
            title_fields["DrawingNumber"], "20-42-1600-26-HCL-8890-02"
        )
        self.assertEqual(
            title_fields["ChargeTitle"],
            "PIPING ISOMETRIC DRAWING FOR EDC PLANT PIPE RACK",
        )
        self.assertEqual(
            [title_fields[f"Desc{index}"] for index in range(1, 5)],
            ["CAA", "CE", "1", "ED"],
        )
        self.assertEqual(
            [title_fields[f"Title{index}"] for index in range(1, 4)],
            ["20", "PIP", "ISO"],
        )
        self.assertEqual(title_fields["CompanyName"], "E")

    def test_revision_cloud_connection_note_uses_one_source_derived_offset(self) -> None:
        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_200701_CLOUD_SHA)
        sheet = streams["Sheet6"]
        records = parser.text_records(sheet)
        groups = parser.revision_cloud_arc_groups(parser.standalone_arcs(sheet))

        offsets = parser.revision_cloud_text_offsets(records, groups)
        moved = [
            (str(record["text"]).strip(), offsets[key])
            for record in records
            if (key := (int(record["graphic_ref"]), int(record["text_start"])))
            in offsets
        ]

        self.assertEqual(
            {text for text, _ in moved},
            {
                "SEE ISO",
                "201600",
                "20-42-1600-26-AI-0005-06",
                "E 82639",
                "N 115190",
                "EL +12344",
            },
        )
        self.assertEqual(len({offset for _, offset in moved}), 1)
        self.assertGreater(moved[0][1][1], 0)

        segments = parser.unique_line_segments(
            parser.line_segments(sheet) + parser.template_line_segments(sheet)
        )
        arrowheads = parser.filled_line_arrowheads(sheet, segments)
        leaders = parser.revision_cloud_leader_endpoint_offsets(
            records,
            offsets,
            arrowheads,
            segments,
            streams["PSMcluster0"],
        )
        self.assertEqual(set(leaders), {0x1646})
        endpoint_index, dx, dy = leaders[0x1646]
        self.assertEqual(endpoint_index, 1)
        self.assertEqual(dx, 0.0)
        self.assertGreater(dy, 0.0)

    def test_connection_notes_are_not_relocated_to_unrelated_revision_clouds(self) -> None:
        """小云线或无真实引线关系的云线不能吸附邻近 SEE ISO 文本组。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            for source in SOURCE_201600_CLOUD_FALSE_POSITIVES:
                manifest = Path(temporary) / f"{source.stem}.json"
                parser.render(
                    source,
                    Path(temporary) / f"{source.stem}.svg",
                    1,
                    False,
                    manifest,
                    None,
                    "Sheet6",
                    "100200",
                )
                trace = __import__("json").loads(
                    manifest.read_text(encoding="utf-8")
                )
                moved_connection_lines = [
                    record
                    for record in trace["text"]
                    if record["revision_cloud_text_adjustment_page_units"] is not None
                    and (
                        record["text"] == "SEE ISO"
                        or re.fullmatch(r"\d{6}", record["text"])
                        or re.fullmatch(
                            r"\d{2}(?:-[A-Za-z0-9_]+){3,}", record["text"]
                        )
                        or re.fullmatch(r"(?:E|N|EL)\s+[+-]?\d+", record["text"])
                    )
                ]
                self.assertEqual([], moved_connection_lines, source.name)

    def test_unicode_degree_text_record_is_decoded_and_rendered(self) -> None:
        """0x4D 计数字符串中的度符号不能截断整条 45° 标注。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_201600_DEGREE_TEXT_SHA)
        self.assertIn(
            "45°",
            {str(record["text"]).strip() for record in parser.text_records(streams["Sheet6"])},
        )
        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "degree.svg"
            parser.render(
                SOURCE_201600_DEGREE_TEXT_SHA,
                svg,
                1,
                False,
                None,
                None,
                "Sheet6",
                "100200",
            )
            root = ET.parse(svg).getroot()
            self.assertIn("45°", {"".join(node.itertext()) for node in root.iter()})

    def test_generic_text_scan_rejects_non_native_graphic_reference(self) -> None:
        """二进制片段即使恰好形似 UTF-16 文本，也不能生成虚假文字。"""

        parser = load_parser()

        def text_record(graphic_ref: int) -> bytes:
            data = bytearray(96)
            text_start = 24
            struct.pack_into("<I", data, 0, graphic_ref)
            struct.pack_into("<I", data, 8, 0x18D0)
            data[text_start : text_start + 2] = b"2\x00"
            struct.pack_into("<dddd", data, text_start + 2, 0.25, 0.35, 1.0, 0.0)
            return bytes(data)

        valid = parser.text_records(text_record(0x183A))
        self.assertTrue(
            any(record["text"] == "2" and record["graphic_ref"] == 0x183A for record in valid)
        )
        invalid = parser.text_records(text_record(0xDB8BAC71))
        self.assertFalse(
            any(
                record["text"] == "2" and record["graphic_ref"] == 0xDB8BAC71
                for record in invalid
            )
        )

    def test_direct_revision_digit_uses_its_psm_glyph_envelope(self) -> None:
        """未组成复合三角形的升版数字仍应使用SHA给出的PSM字形边界。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "revision.svg"
            parser.render(
                SOURCE_201600_DIRECT_REVISION_TEXT_SHA,
                svg,
                1,
                False,
                None,
                None,
                "Sheet6",
                "100200",
            )
            root = ET.parse(svg).getroot()
            revision = next(
                node
                for node in root.iter()
                if node.tag.endswith("text")
                and "".join(node.itertext()) == "0"
                and node.attrib.get("data-style") == "0x0008"
            )
            self.assertEqual(revision.attrib.get("dominant-baseline"), "text-after-edge")
            self.assertAlmostEqual(float(revision.attrib["font-size"]), 79.4678, delta=0.1)

    def render_0200_svg(self, temporary: str) -> ET.Element:
        parser = load_parser()
        svg = Path(temporary) / "0200.svg"
        parser.render(SOURCE_0200_SHA, svg, 1, False, None, None, "Sheet6")
        return ET.parse(svg).getroot()

    def render_0200_01_svg(self, temporary: str) -> ET.Element:
        parser = load_parser()
        svg = Path(temporary) / "0200-01.svg"
        parser.render(SOURCE_0200_01_SHA, svg, 1, False, None, None, "Sheet6")
        return ET.parse(svg).getroot()

    def test_sheet_circle_records_render_instrument_bubbles_and_filled_weld_points(self) -> None:
        """物理页圆形记录中的两个仪表圆和九个焊点圆均不得遗漏。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_0200_SHA)
        circles = parser.template_circle_primitives(streams["Sheet6"])
        self.assertEqual(
            sorted(round(float(circle["radius"]), 2) for circle in circles),
            [15.96, 15.96, 15.96, 19.32, 19.32, 19.32, 19.32, 19.32, 19.32, 113.4, 126.0],
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        instruments = [
            node for node in root.iter(ns + "circle")
            if node.attrib.get("data-layer") == "sha-instrument-bubble"
        ]
        welds = [
            node for node in root.iter(ns + "circle")
            if node.attrib.get("data-layer") == "sha-weld-point"
        ]
        self.assertEqual(len(instruments), 2)
        self.assertEqual(len(welds), 9)
        self.assertTrue(all(node.attrib.get("fill") == "#17202a" for node in welds))

    def test_instrument_bubble_multiline_tags_are_complete(self) -> None:
        """仪表圆内分行保存的位号前缀和编号必须同时显示。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        tags = [
            "".join(node.itertext()) for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "sha-instrument-tag"
        ]
        self.assertEqual(tags, ["10PG", "41003", "10PCV", "41002"])

    def test_material_description_lines_with_equals_are_not_filtered(self) -> None:
        """材料表的L = 100 MM续行是合法SHA文字，五张问题图均不得漏行。"""

        parser = load_parser()
        cases = {
            "00-42-0200-26-10FW41006-01": "A106-B ; L = 100 MM ; GALV.,S-80",
            "00-42-0200-26-10FW41007-01": "A106-B ; L = 100 MM ; GALV.,S-80",
            "00-42-0200-26-10IA41102-01": "A312-TP304 ; L = 100 MM ; SMLS,S-80S",
            "00-42-0200-26-10IA41102-06": "A312-TP304 ; L = 100 MM ; SMLS,S-80S",
            "00-42-0200-26-20FW41001-07": "A106-B ; L = 100 MM ; SMLS,S-160",
        }
        source_dir = PROJECT_DIR / "SHA文件转换" / "000200FW"
        with tempfile.TemporaryDirectory() as temporary:
            for stem, expected in cases.items():
                svg = Path(temporary) / f"{stem}.svg"
                parser.render(
                    source_dir / f"{stem}.sha",
                    svg,
                    1,
                    False,
                    None,
                    None,
                    "Sheet6",
                )
                root = ET.parse(svg).getroot()
                texts = {
                    "".join(node.itertext())
                    for node in root.iter("{http://www.w3.org/2000/svg}text")
                }
                self.assertIn(expected, texts, stem)

    def test_instrument_tag_uses_bubble_scale_and_is_vertically_centred(self) -> None:
        """仪表位号字号应匹配圆径，两行文字应围绕圆心对称排布。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_01_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        bubble = next(
            node for node in root.iter(ns + "circle")
            if node.attrib.get("data-layer") == "sha-instrument-bubble"
            and abs(float(node.attrib["r"]) - 126.0) < 0.1
        )
        labels = [
            node for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "sha-instrument-tag"
            and node.attrib.get("data-bubble") == bubble.attrib["data-child"]
        ]
        self.assertEqual(["".join(node.itertext()) for node in labels], ["10PG", "41001"])
        expected_size = float(bubble.attrib["r"]) * 0.45
        self.assertTrue(
            all(abs(float(node.attrib["font-size"]) - expected_size) < 0.05 for node in labels)
        )
        self.assertAlmostEqual(
            sum(float(node.attrib["y"]) for node in labels) / len(labels),
            float(bubble.attrib["cy"]),
            delta=0.06,
        )

    def test_revision_cloud_uses_chengda_thin_plot_pen(self) -> None:
        """升版云线应使用成达参考图的0.25 mm细笔，不回退为默认粗线。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_01_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        clouds = [
            node for node in root.iter(ns + "path")
            if node.attrib.get("data-layer") == "sha-revision-cloud"
        ]
        self.assertEqual(len(clouds), 62)
        expected_width = 0.00025 * parser.SHEET_UNIT
        self.assertTrue(
            all(
                node.attrib.get("stroke-width") is not None
                and abs(float(node.attrib["stroke-width"]) - expected_width) < 0.001
                for node in clouds
            )
        )

    def test_latest_revision_people_select_three_reference_signature_images(self) -> None:
        """最新版次人员驱动签名图，但签名应落在0版上方的独立行。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_0200_SHA)
        revisions = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        signatures_data = parser.revision_signature_records(
            revisions, parser.template_line_segments(streams["Sheet221"])
        )
        latest_centres = [
            (float(record["psm_bbox"][1]) + float(record["psm_bbox"][3])) / 2
            for record in revisions
            if record.get("stream") == "Revision"
            and int(record.get("record_index", -1)) == 2
        ]
        self.assertEqual(len(signatures_data), 3)
        self.assertTrue(
            all(
                sum(map(float, record["signature_row"])) / 2 > max(latest_centres)
                for record in signatures_data
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        signatures = [
            node for node in root.iter(ns + "image")
            if node.attrib.get("data-layer") == "sha-revision-signature"
        ]
        self.assertEqual([node.attrib.get("data-person") for node in signatures], ["XZS", "PJ", "ML"])
        self.assertTrue(all(node.attrib.get("href", "").startswith("data:image/png;base64,") for node in signatures))
        self.assertTrue(
            all(
                node.attrib.get("data-mapping")
                == "sha-latest-revision-person-plus-dedicated-signature-row-and-project-asset"
                for node in signatures
            )
        )
        # The project signature PNGs are stored 180 degrees from the final
        # plot orientation. SVG must flip both axes inside the original cell.
        for node in signatures:
            x = float(node.attrib["x"])
            width = float(node.attrib["width"])
            y = float(node.attrib["y"])
            height = float(node.attrib["height"])
            match = re.fullmatch(
                r"translate\(([-\d.]+) ([-\d.]+)\) scale\((-?\d+) (-?\d+)\)",
                node.attrib.get("transform", ""),
            )
            self.assertIsNotNone(match)
            translate_x, translate_y, scale_x, scale_y = match.groups()
            self.assertAlmostEqual(float(translate_x), 2 * x + width, delta=0.2)
            self.assertAlmostEqual(float(translate_y), 2 * y + height, delta=0.2)
            self.assertEqual((int(scale_x), int(scale_y)), (-1, -1))

        revision_people = [
            "".join(node.itertext())
            for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "sha-template-revision"
            and node.attrib.get("data-field") in {"RevisedBy", "CheckedBy", "ApprovedBy"}
        ]
        self.assertEqual(revision_people.count("XZS"), 3)
        self.assertEqual(revision_people.count("PJ"), 3)
        self.assertEqual(revision_people.count("ML"), 3)

        from PIL import Image

        signature_dir = ASSET_DIR / "signatures"
        with Image.open(signature_dir / "PJ.png") as pj_image:
            self.assertGreaterEqual(pj_image.width, 200)
        with Image.open(signature_dir / "ML.png") as ml_image:
            self.assertGreaterEqual(ml_image.width, 300)

    def test_grid_zone_labels_use_cell_centres_and_declared_size(self) -> None:
        """四边分区字符应统一居中、使用SHA声明字高且不得压缩字形。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_svg(temporary)
        ns = "{http://www.w3.org/2000/svg}"
        labels = [
            node for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "sha-grid-zone-label"
        ]
        by_side = {
            side: [node for node in labels if node.attrib.get("data-side") == side]
            for side in ("left", "right", "bottom", "top")
        }
        self.assertEqual(["".join(node.itertext()) for node in by_side["left"]], list("123456"))
        self.assertEqual(["".join(node.itertext()) for node in by_side["right"]], list("123456"))
        self.assertEqual(["".join(node.itertext()) for node in by_side["bottom"]], list("ABCDEFGHI"))
        self.assertEqual(["".join(node.itertext()) for node in by_side["top"]], list("ABCDEFGHI"))
        expected_y = [11152.15, 10034.95, 8914.96, 7792.18, 6672.19, 5552.17]
        for node, y in zip(by_side["left"], expected_y):
            self.assertAlmostEqual(float(node.attrib["x"]), 335.07, delta=0.15)
            self.assertAlmostEqual(float(node.attrib["y"]), y, delta=0.15)
        for node in labels:
            self.assertAlmostEqual(float(node.attrib["font-size"]), 56.20, delta=0.05)
            self.assertEqual(node.attrib.get("text-anchor"), "middle")
            self.assertEqual(node.attrib.get("dominant-baseline"), "central")
            self.assertIn("Arial", node.attrib.get("font-family", ""))
            self.assertNotIn("Arial Narrow", node.attrib.get("font-family", ""))
            self.assertNotIn("textLength", node.attrib)
            self.assertNotIn("lengthAdjust", node.attrib)
            self.assertIn("scale(1 1.2200)", node.attrib.get("transform", ""))

    def test_template_stream_is_discovered_structurally_after_renumbering(self) -> None:
        """模板Sheet编号变化后仍应依靠其几何内容识别。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        renamed = {
            "Sheet6": streams["Sheet6"],
            # A drawing stream can legitimately contain more generic 0x84
            # paths than the template. It must not win without compass
            # circle/closed-triangle topology.
            "Sheet777": streams["Sheet6"] + streams["Sheet221"][:270_000] * 2,
            "Sheet999": streams["Sheet221"],
            "StyleCluster": streams["StyleCluster"],
        }
        self.assertGreater(
            len(parser.template_polyline_paths(renamed["Sheet777"])),
            len(parser.template_polyline_paths(renamed["Sheet999"])),
        )
        name, data = parser.discover_template_stream(renamed)
        self.assertEqual(name, "Sheet999")
        self.assertIs(data, streams["Sheet221"])

    def test_physical_sheet_is_not_used_as_template_when_signature_is_absent(self) -> None:
        """没有模板拓扑签名时，普通物理页不能被重复当作模板解析。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        name, data = parser.discover_template_stream({"Sheet6": streams["Sheet6"]})
        self.assertEqual(name, "")
        self.assertEqual(data, b"")

    def test_compass_circle_and_direction_labels_come_from_template_records(self) -> None:
        """建北图应恢复SHA圆形记录和无局部PSM框的六个方向文字。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        circles = parser.template_circle_primitives(streams["Sheet221"])
        compass = next(
            record
            for record in circles
            if abs(float(record["center_x"]) - 692.0) < 1.0
            and abs(float(record["center_y"]) - 6566.0) < 1.0
        )
        self.assertAlmostEqual(float(compass["radius"]), 183.7, delta=0.2)
        triangles = parser.template_closed_triangles(streams["Sheet221"], circles)
        self.assertEqual(len(triangles), 6)

        records = parser.template_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        compass_records = {
            str(record["text"]): record
            for record in records
            if record.get("mapping") == "sha-text-anchor-plus-inherited-template-format"
        }
        self.assertEqual(set(compass_records), {"UP", "DOWN", "N", "E", "S", "W"})
        self.assertTrue(
            all(float(record["font_size"]) > 50.0 for record in compass_records.values())
        )

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        rendered_circles = [
            node
            for node in root.iter(ns + "circle")
            if node.attrib.get("data-layer") == "sha-template-circle"
        ]
        self.assertEqual(len(rendered_circles), len(circles))
        rendered_arrowheads = [
            node
            for node in root.iter(ns + "polygon")
            if node.attrib.get("data-layer") == "sha-template-filled-arrowhead"
        ]
        self.assertEqual(len(rendered_arrowheads), 6)
        self.assertTrue(all(node.attrib.get("fill") == "#17202a" for node in rendered_arrowheads))
        rendered_text = {
            "".join(node.itertext())
            for node in root.iter(ns + "text")
            if node.attrib.get("data-mapping")
            == "sha-text-anchor-plus-inherited-template-format"
        }
        self.assertEqual(rendered_text, {"UP", "DOWN", "N", "E", "S", "W"})

    def test_legend_polyline_strokes_are_decoded_from_counted_point_records(self) -> None:
        """焊缝图例完整字形必须来自全部0x84计数折线记录。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        paths = parser.template_polyline_paths(streams["Sheet221"])
        self.assertEqual(len(paths), 73)
        clusters: dict[tuple[int, int, int], list[dict[str, object]]] = {}
        for record in paths:
            key = (
                int(record["owner_ref"]),
                int(record["object_group"]),
                int(record["style_ref"]),
            )
            clusters.setdefault(key, []).append(record)
        legend = max(clusters.values(), key=len)
        self.assertEqual(len(legend), 67)
        self.assertEqual({int(record["point_count"]) for record in legend}, {
            3, 4, 5, 7, 8, 11, 12, 15, 18, 20, 23, 24
        })
        points = [point for record in legend for point in record["points"]]
        bbox = (
            min(point[0] for point in points),
            min(point[1] for point in points),
            max(point[0] for point in points),
            max(point[1] for point in points),
        )
        expected = (5032.7, 298.7, 6182.3, 427.0)
        for value, target in zip(bbox, expected):
            self.assertAlmostEqual(value, target, delta=0.2)
        style_by_child = parser.template_line_style_refs(streams["Sheet221"])
        straight_strokes = [
            segment
            for segment in parser.template_line_segments(streams["Sheet221"])
            if style_by_child.get(segment[5]) == int(legend[0]["style_ref"])
            and min(segment[0], segment[2]) * parser.SHEET_UNIT >= bbox[0] - 1
            and max(segment[0], segment[2]) * parser.SHEET_UNIT <= bbox[2] + 1
            and min(segment[1], segment[3]) * parser.SHEET_UNIT >= bbox[1] - 1
            and max(segment[1], segment[3]) * parser.SHEET_UNIT <= bbox[3] + 1
        ]
        self.assertGreaterEqual(len(straight_strokes), 80)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        rendered = [
            node
            for node in root.iter(ns + "polyline")
            if node.attrib.get("data-layer") == "sha-template-polyline"
        ]
        self.assertEqual(len(rendered), 73)
        self.assertTrue(all(len(node.attrib["points"].split()) >= 3 for node in rendered))

    def test_bom_semicolon_text_and_source_template_font_faces_are_preserved(self) -> None:
        """图框样式应按参考PDF的真实Arial字形分类，而非统一替换为窄体。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        text_nodes = list(root.iter(ns + "text"))
        by_text = {"".join(node.itertext()): node for node in text_nodes}
        description = "PIPE ; ASME B36.10M ; BE ; A106-B ;"
        self.assertIn(description, by_text)
        self.assertAlmostEqual(float(by_text[description].attrib["x"]), 6667.9, delta=1.0)

        for value in (
            "CAA-1 PROJECT",
            "Cilegon, Banten, Indonesia",
            "PT CHANDRA ASRI ALKALI",
        ):
            node = by_text[value]
            self.assertIn("Arial Narrow", node.attrib["font-family"])
            self.assertEqual(node.attrib.get("font-weight"), "400")

        self.assertAlmostEqual(float(by_text["CAA-1 PROJECT"].attrib["font-size"]), 112.8, delta=0.2)
        self.assertAlmostEqual(
            float(by_text["PT CHANDRA ASRI ALKALI"].attrib["font-size"]),
            103.8,
            delta=0.2,
        )

        review = by_text["Document Review Result"]
        self.assertIn("Arial Narrow", review.attrib["font-family"])
        self.assertAlmostEqual(float(review.attrib["font-size"]), 67.1, delta=0.2)
        self.assertEqual(review.attrib.get("text-anchor"), "middle")
        self.assertEqual(review.attrib.get("font-weight"), "400")

        reviewed = by_text["Reviewed with Comment"]
        self.assertEqual(reviewed.attrib.get("text-anchor"), "start")
        self.assertEqual(reviewed.attrib.get("lengthAdjust"), "spacingAndGlyphs")
        self.assertAlmostEqual(float(reviewed.attrib["textLength"]), 575.3, delta=0.2)
        self.assertEqual(
            reviewed.attrib.get("data-mapping"),
            "sha-review-panel-right-column-psm-left-plus-two-em",
        )

        hold = by_text["No Comment with Hold"]
        self.assertEqual(hold.attrib.get("lengthAdjust"), "spacingAndGlyphs")
        self.assertAlmostEqual(float(hold.attrib["textLength"]), 527.1, delta=0.2)

        client = by_text["PT CHANDRA ASRI ALKALI"]
        self.assertNotIn("letter-spacing", client.attrib)
        self.assertNotIn("textLength", client.attrib)
        self.assertNotIn("lengthAdjust", client.attrib)

        for value in ("INSUL. THICK.", "PAINT SYSTEM", "PWHT"):
            node = by_text[value]
            self.assertIn("Arial", node.attrib["font-family"])
            self.assertNotIn("Arial Narrow", node.attrib["font-family"])

        company = by_text["China Chengda Engineering Co.,Ltd"]
        self.assertIn("Arial", company.attrib["font-family"])
        self.assertNotIn("Arial Narrow", company.attrib["font-family"])
        self.assertEqual(company.attrib.get("font-style"), "italic")

        issue = next(
            node for node in text_nodes
            if "".join(node.itertext()) == "ISSUE FOR REVIEW"
            and node.attrib.get("data-layer") == "sha-template-text"
        )
        self.assertIn("Arial", issue.attrib["font-family"])
        self.assertNotIn("Arial Narrow", issue.attrib["font-family"])
        self.assertEqual(issue.attrib.get("font-style"), "italic")

    def test_multiline_notice_uses_equal_source_cell_rows(self) -> None:
        """三行声明应按SHA段落框等分行高，不使用固定基线补偿。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        records = parser.template_multiline_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        notice = next(
            record
            for record in records
            if str(record["text"]).startswith("THE INFORMATION CONTAINED")
        )
        self.assertNotIn("baseline_offset", notice)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        node = next(
            item
            for item in root.iter(ns + "text")
            if "".join(item.itertext()).startswith("THE INFORMATION CONTAINED")
        )
        tspans = list(node.findall(ns + "tspan"))
        self.assertEqual(len(tspans), 3)
        self.assertTrue(
            all(tspan.attrib.get("dominant-baseline") == "central" for tspan in tspans)
        )
        actual = [float(tspan.attrib["y"]) for tspan in tspans]
        left, bottom, right, top = notice["bbox"]
        row_height = (top - bottom) / 3
        expected = [
            parser.svg_y(top - (index + 0.5) * row_height) for index in range(3)
        ]
        for value, target in zip(actual, expected):
            self.assertAlmostEqual(value, target, delta=0.11)

    def test_multiline_transform_cannot_be_borrowed_from_the_next_record(self) -> None:
        """多行文字只能使用本条0x4D记录范围内的变换。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        text = "FIRST\rSECOND"
        payload_end = 34 + len(text) * 2
        record = bytearray(payload_end)
        struct.pack_into("<HI", record, 0, 0x4D, payload_end - 6)
        struct.pack_into("<I", record, 6, 123)
        struct.pack_into("<I", record, 18, (0x14C << 16) | 0x0100)
        struct.pack_into("<H", record, 32, len(text))
        record[34:payload_end] = text.encode("utf-16le")
        data = bytes(record) + struct.pack("<4d", 0.1, 0.1, 1.0, 0.0) + b"\0\0"
        psm = struct.pack("<I5H", 123, 3000, 3000, 3500, 3500, 0)
        self.assertEqual(
            parser.template_multiline_text_records(data, psm, streams["StyleCluster"]),
            [],
        )

    def test_uncounted_text_that_starts_with_a_count_like_character_is_not_truncated(self) -> None:
        """无0x4D计数头时，首字符碰巧等于后续长度也不能被删除。"""

        parser = load_parser()
        value = "!" + "A" * 33
        data = bytearray(24 + len(value) * 2 + 32)
        struct.pack_into("<I", data, 0, 321)
        struct.pack_into("<I", data, 8, 654)
        data[24 : 24 + len(value) * 2] = value.encode("utf-16le")
        struct.pack_into("<4d", data, 24 + len(value) * 2, 0.1, 0.1, 1.0, 0.0)
        texts = {str(record["text"]) for record in parser.text_records(bytes(data))}
        self.assertIn(value, texts)

    def test_line_styles_inherit_width_and_boundary_uses_printable_pen_weight(self) -> None:
        """图框应保留0.20–0.35 mm笔宽层级，不把边界画成6.34 mm粗线。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        widths = parser.resolved_line_style_widths(streams["StyleCluster"])

        self.assertAlmostEqual(widths[265], 4.2, places=6)
        self.assertAlmostEqual(widths[268], 4.2, places=6)
        self.assertAlmostEqual(widths[284], 3.36, places=6)
        self.assertAlmostEqual(widths[231], 5.88, places=6)
        self.assertAlmostEqual(widths[292], 4.2, places=6)
        self.assertAlmostEqual(widths[285], 4.2, places=6)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        template_lines = [
            node
            for node in root.iter(ns + "line")
            if node.attrib.get("data-layer") == "sha-template"
        ]
        self.assertIn("0x011D", {node.attrib["data-style"] for node in template_lines})
        self.assertLessEqual(
            max(float(node.attrib["stroke-width"]) for node in template_lines), 5.9
        )

    def test_static_template_text_and_embedded_logo_are_recovered(self) -> None:
        """低层模板记录中的标题栏文字和JSite位图不能被蓝星筛选条件丢弃。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        records = parser.template_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        texts = {str(record["text"]) for record in records}

        self.assertTrue(
            {
                "ISOMETRIC NOTE:",
                "Project Doc Number :",
                "Isometric drawing No:",
                "Contractor/Licensor Doc No:",
                "Confidential",
                "ISSUE FOR REVIEW",
                "China Chengda Engineering Co.,Ltd",
                "PT CHANDRA ASRI ALKALI",
                "LEGEND",
            }
            <= texts
        )
        images = parser.template_images(streams)
        self.assertEqual([int(image["resource_id"]) for image in images], [8169])

    def test_multiline_legal_notice_and_tagged_fields_are_resolved(self) -> None:
        """图签长文本及TitleArea/Revision绑定值必须从SHA数据流恢复。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        notices = parser.template_multiline_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        multiline = {str(record["text"]) for record in notices}
        legal_notice = next(
            text for text in multiline if text.startswith("THE INFORMATION CONTAINED")
        )
        self.assertEqual(len(legal_notice.splitlines()), 3)
        self.assertIn("Scale\n1/1\n@A3", multiline)
        self.assertIn("Sheet\n1  of  1", multiline)

        bound = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        bound_texts = [str(record["text"]) for record in bound]
        self.assertIn("00-42-0203-26-00EE00002-01", bound_texts)
        self.assertIn("0C", bound_texts)
        self.assertIn("9/9/2026", bound_texts)
        self.assertIn("ISSUE FOR CONSTRUCTION", bound_texts)
        self.assertGreaterEqual(len(bound_texts), 40)

    def test_all_visible_revision_rows_are_completed_from_tagged_data(self) -> None:
        """模板漏绑单元格时，A、B、0行仍应由Revision XML补全完整字段。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_0200_SHA)
        bound = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        required = {
            "MajorRev_ForRevise",
            "RevisedDate",
            "RevisionDescription",
            "RevisedBy",
            "CheckedBy",
            "ApprovedBy",
        }
        for record_index, revision in enumerate(("A", "B", "0")):
            row = {
                str(record["field"]): str(record["text"])
                for record in bound
                if record.get("stream") == "Revision"
                and int(record.get("record_index", -1)) == record_index
            }
            self.assertEqual(required - row.keys(), set())
            self.assertEqual(row["MajorRev_ForRevise"], revision)
            self.assertEqual(
                (row["RevisedBy"], row["CheckedBy"], row["ApprovedBy"]),
                ("XZS", "PJ", "ML"),
            )

    def test_title_block_text_uses_source_cell_metrics_without_duplicates(self) -> None:
        """标题栏字号应由PSM单元格和格式层级推导，不能统一压成约5 pt。"""

        parser = load_parser()
        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        text_nodes = list(root.iter(ns + "text"))

        def exact(value: str):
            return [node for node in text_nodes if "".join(node.itertext()) == value]

        self.assertEqual(len(exact("Scale")), 0)
        self.assertEqual(len(exact("Scale1/1@A3")), 1)

        expected_minimums = {
            "PT CHANDRA ASRI ALKALI": 80.0,
            "Confidential": 60.0,
            "ISSUE FOR REVIEW": 60.0,
            "China Chengda Engineering Co.,Ltd": 52.0,
            "Project Doc Number :": 52.0,
            "Isometric drawing No:": 42.0,
            "Contractor/Licensor Doc No:": 42.0,
        }
        for value, minimum in expected_minimums.items():
            nodes = [
                node
                for node in exact(value)
                if node.attrib.get("data-layer") == "sha-template-text"
            ]
            self.assertEqual(len(nodes), 1, value)
            self.assertGreaterEqual(float(nodes[0].attrib["font-size"]), minimum, value)

        company = exact("China Chengda Engineering Co.,Ltd")[0]
        self.assertAlmostEqual(float(company.attrib["font-size"]), 59.69, delta=0.2)

        self.assertEqual(exact("Confidential")[0].attrib.get("font-style"), "italic")
        issue_static = next(
            node
            for node in exact("ISSUE FOR REVIEW")
            if node.attrib.get("data-layer") == "sha-template-text"
        )
        self.assertEqual(issue_static.attrib.get("font-style"), "italic")

        designed = exact("Designed")[0]
        self.assertLessEqual(float(designed.attrib["font-size"]), 52.0)
        iso_note = exact("ISOMETRIC NOTE:")[0]
        self.assertLessEqual(float(iso_note.attrib["font-size"]), 46.0)

        legal = next(
            node
            for node in text_nodes
            if "".join(node.itertext()).startswith("THE INFORMATION CONTAINED")
        )
        self.assertGreaterEqual(float(legal.attrib["font-size"]), 48.0)
        self.assertIn("Calibri", legal.attrib["font-family"])

        drawing_number = exact("00-42-0203-26-00EE00002-01")[0]
        self.assertGreaterEqual(float(drawing_number.attrib["font-size"]), 70.0)
        self.assertIn("Arial Narrow", drawing_number.attrib["font-family"])
        revision = exact("0C")[0]
        self.assertGreaterEqual(float(revision.attrib["font-size"]), 70.0)
        self.assertIn("Arial Narrow", revision.attrib["font-family"])

        project_label = exact("Project Doc Number :")[0]
        self.assertEqual(project_label.attrib["text-anchor"], "start")
        self.assertAlmostEqual(float(project_label.attrib["x"]), 7073.1, delta=1.0)
        contractor_label = exact("Contractor/Licensor Doc No:")[0]
        self.assertEqual(contractor_label.attrib["text-anchor"], "start")
        self.assertAlmostEqual(float(contractor_label.attrib["x"]), 6392.3, delta=1.0)
        for footer_label in (
            exact("Isometric drawing No:")[0],
            contractor_label,
        ):
            self.assertIn("Arial", footer_label.attrib.get("font-family", ""))
            self.assertNotIn("Arial Narrow", footer_label.attrib.get("font-family", ""))
            self.assertIn("scale(1.0000 1.1000)", footer_label.attrib.get("transform", ""))

        authorized = exact("Authorized")[0]
        self.assertEqual(authorized.attrib.get("text-anchor"), "middle")
        self.assertIn(
            "scale(1.0000 1.1800)", authorized.attrib.get("transform", "")
        )
        legend = exact("LEGEND")[0]
        self.assertIn("scale(1.0000 1.2800)", legend.attrib.get("transform", ""))
        approved_by = exact("Approved by :")[0]
        approval_date = exact("Date :")[0]
        self.assertAlmostEqual(float(approved_by.attrib["y"]), 11113.7, delta=0.2)
        self.assertAlmostEqual(float(approval_date.attrib["y"]), 11199.0, delta=0.2)
        self.assertIn(
            "scale(1.1650 1.0000)", project_label.attrib.get("transform", "")
        )

        sheet_range = exact("13001~13464")
        self.assertEqual(len(sheet_range), 1)
        self.assertGreaterEqual(float(sheet_range[0].attrib["font-size"]), 59.0)
        self.assertLessEqual(float(sheet_range[0].attrib["font-size"]), 62.0)
        streams = parser.read_sha_streams(SOURCE_SHA)
        bound = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        drawing_record = next(
            record
            for record in bound
            if record["text"] == "00-42-0203-26-00EE00002-01"
        )
        drawing_box = drawing_record["psm_bbox"]
        self.assertAlmostEqual(
            float(drawing_number.attrib["y"]),
            parser.svg_y((drawing_box[1] + drawing_box[3]) / 2),
            delta=0.11,
        )
        company_cells = [
            node
            for node in exact("E")
            if node.attrib.get("data-stream") == "TitleArea"
            and node.attrib.get("data-field") == "CompanyName"
        ]
        self.assertEqual(len(company_cells), 1)

    def test_feature_strip_values_use_reference_pdf_font_and_height(self) -> None:
        """下方特性栏动态值应使用原PDF的Arial Narrow字形和校准字高。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_01_svg(temporary)

        ns = "{http://www.w3.org/2000/svg}"
        nodes = {
            "".join(node.itertext()): node
            for node in root.iter(ns + "text")
        }
        values = (
            "10-PS-PID-E-4101",
            "CAA-CE-1-ED-00-PIP-LAY-E-1802",
            "Water",
            "P22",
            "AMB",
        )
        for value in values:
            node = nodes[value]
            family = node.attrib.get("font-family", "")
            self.assertIn("Arial Narrow", family, value)
            self.assertNotIn("Courier", family, value)
            self.assertAlmostEqual(float(node.attrib["font-size"]), 48.28, delta=0.15)
            self.assertEqual(node.attrib.get("lengthAdjust"), "spacing", value)
            self.assertEqual(
                node.attrib.get("data-mapping"),
                "sha-feature-strip-value-anchor-plus-psm-size",
            )
            self.assertIn("scale(1 1.2300)", node.attrib.get("transform", ""))

        static_feature = nodes["DESIGN TEMP."]
        self.assertIn(
            "scale(1.0000 1.2300)", static_feature.attrib.get("transform", "")
        )

        # 字宽仍由每条SHA记录的PSM包围框控制，不能因换字体而统一重排。
        self.assertAlmostEqual(
            float(nodes["10-PS-PID-E-4101"].attrib["textLength"]),
            319.921875,
            delta=0.01,
        )
        self.assertAlmostEqual(
            float(nodes["CAA-CE-1-ED-00-PIP-LAY-E-1802"].attrib["textLength"]),
            595.751953125,
            delta=0.01,
        )

    def test_fin_watermark_uses_reference_pdf_relative_geometry(self) -> None:
        """FIN水印应作为固定页面SVG层恢复，不应改写源SHA或事后重写PDF。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = self.render_0200_01_svg(temporary)

        ns = "{http://www.w3.org/2000/svg}"
        watermarks = [
            node
            for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "chengda-fin-watermark"
        ]
        self.assertEqual(len(watermarks), 1)
        node = watermarks[0]
        self.assertEqual("".join(node.itertext()), "ONLY FOR FIN")
        self.assertEqual(node.attrib.get("fill"), "#808080")
        self.assertEqual(node.attrib.get("fill-opacity"), "0.4")
        self.assertIn("Helvetica", node.attrib.get("font-family", ""))
        self.assertEqual(node.attrib.get("font-kerning"), "none")
        self.assertIn("font-feature-settings:'kern' 0", node.attrib.get("style", ""))
        self.assertNotIn("textLength", node.attrib)
        self.assertNotIn("lengthAdjust", node.attrib)

        view_x, view_y, view_width, view_height = map(
            float, root.attrib["viewBox"].split()
        )
        expected_x = view_x + view_width * 0.362688409040589
        expected_y = view_y + view_height * 0.7369893905486742
        expected_size = view_height * 0.08720022819640558
        self.assertAlmostEqual(float(node.attrib["x"]), expected_x, delta=0.02)
        self.assertAlmostEqual(float(node.attrib["y"]), expected_y, delta=0.02)
        self.assertAlmostEqual(float(node.attrib["font-size"]), expected_size, delta=0.02)
        self.assertEqual(
            node.attrib.get("transform"),
            f"rotate(-45.000 {node.attrib['x']} {node.attrib['y']})",
        )

    def test_angle_bracket_piece_and_revision_tokens_are_not_filtered(self) -> None:
        """切管序号与三角形版次均以尖括号文本存储，必须完整输出。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        source_tokens = [
            record
            for record in parser.text_records(streams["Sheet6"])
            if record["text"] in {"<1>", "<2>"}
        ]
        self.assertEqual(len(source_tokens), 4)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        rendered = [
            node
            for node in root.iter(ns + "text")
            if "".join(node.itertext()) in {"<1>", "<2>"}
        ]
        self.assertEqual(len(rendered), 4)
        self.assertEqual(sorted("".join(node.itertext()) for node in rendered), ["<1>", "<1>", "<2>", "<2>"])
        self.assertFalse(
            any("".join(node.itertext()) == "M>" for node in root.iter(ns + "text"))
        )

    def test_text_record_headers_are_not_emitted_as_md(self) -> None:
        """0x4D/0x44记录头不能被UTF-16扫描器误读成切管表文字MD。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        records = parser.text_records(streams["Sheet6"])
        self.assertFalse(
            any(
                record["text"] == "MD"
                and 0.8
                <= (float(record["direction_x"]) ** 2 + float(record["direction_y"]) ** 2) ** 0.5
                <= 1.2
                for record in records
            )
        )

    def test_large_revision_triangles_receive_their_internal_revision_text(self) -> None:
        """绘图区两个大三角形内的0C来自邻近SHA文本锚点，不应为空。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        segments = parser.unique_line_segments(
            parser.line_segments(streams["Sheet6"])
            + parser.template_line_segments(streams["Sheet6"])
        )
        triangles = parser.sheet_revision_triangles(segments)
        self.assertEqual(len(triangles), 2)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        labels = [
            node
            for node in root.iter(ns + "text")
            if node.attrib.get("data-layer") == "sha-revision-triangle-text"
        ]
        self.assertEqual(["".join(node.itertext()) for node in labels], ["0C", "0C"])
        self.assertEqual(
            sum(1 for node in root.iter(ns + "text") if "".join(node.itertext()) == "0C"),
            3,
        )
        rendered_triangles = [
            node
            for node in root.iter(ns + "polygon")
            if node.attrib.get("data-layer") == "sha-revision-triangle"
        ]
        self.assertEqual(len(rendered_triangles), 2)
        for node, triangle in zip(labels, rendered_triangles):
            points = [
                tuple(float(value) for value in pair.split(","))
                for pair in triangle.attrib["points"].split()
            ]
            apex, left_base, right_base = points
            self.assertAlmostEqual(left_base[1], right_base[1], delta=0.11)
            self.assertAlmostEqual(apex[0], (left_base[0] + right_base[0]) / 2, delta=0.11)
            self.assertAlmostEqual(float(node.attrib["x"]), sum(p[0] for p in points) / 3, delta=0.11)
            self.assertAlmostEqual(float(node.attrib["y"]), sum(p[1] for p in points) / 3, delta=0.11)
            triangle_height = left_base[1] - apex[1]
            self.assertAlmostEqual(
                float(node.attrib["font-size"]), triangle_height * 0.50, delta=0.1
            )
            self.assertIn("Arial Narrow", node.attrib.get("font-family", ""))
            self.assertEqual(node.attrib.get("text-anchor"), "middle")
            self.assertEqual(node.attrib.get("dominant-baseline"), "central")
        self.assertTrue(all("textLength" not in node.attrib for node in labels))

    def test_revision_triangles_can_share_one_sheet_parent(self) -> None:
        """同一Sheet父对象下的断开三角形应按连通分量分别识别。"""

        parser = load_parser()
        unit = parser.SHEET_UNIT
        segments = [
            (100 / unit, 100 / unit, 200 / unit, 100 / unit, 6, 101),
            (200 / unit, 100 / unit, 150 / unit, 220 / unit, 6, 102),
            (150 / unit, 220 / unit, 100 / unit, 100 / unit, 6, 103),
            (400 / unit, 300 / unit, 520 / unit, 300 / unit, 6, 201),
            (520 / unit, 300 / unit, 460 / unit, 440 / unit, 6, 202),
            (460 / unit, 440 / unit, 400 / unit, 300 / unit, 6, 203),
            # An unrelated line beneath the same parent must not merge the
            # disconnected revision-marker components.
            (800 / unit, 800 / unit, 900 / unit, 800 / unit, 6, 301),
        ]
        records = parser.sheet_revision_triangle_records(segments)
        self.assertEqual(len(records), 2)
        self.assertEqual(
            {tuple(record["triangle_key"]) for record in records},
            {(101, 102, 103), (201, 202, 203)},
        )

    def test_wide_revision_triangle_is_not_misclassified_as_decoration(self) -> None:
        """200301 EC-3121-02 stores a valid 275-unit-wide revision triangle."""

        parser = load_parser()
        source = (
            PROJECT_DIR
            / "SHA文件转换"
            / "200301"
            / "200301 SHA"
            / "20-42-0301-26-EC-3121-02.sha"
        )
        streams = parser.read_sha_streams(source)
        segments = parser.unique_line_segments(
            parser.line_segments(streams["Sheet6"])
            + parser.template_line_segments(streams["Sheet6"])
        )
        records = parser.sheet_revision_triangle_records(segments)

        assert len(records) == 2
        assert any(float(row["source_bbox"][2]) - float(row["source_bbox"][0]) > 270 for row in records)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(source, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()
        labels = [
            "".join(node.itertext())
            for node in root.iter("{http://www.w3.org/2000/svg}text")
            if node.attrib.get("data-layer") == "sha-revision-triangle-text"
        ]
        assert "0B" in labels

    def test_extra_wide_sheet_revision_triangles_receive_latest_revision_text(self) -> None:
        """EC-3120-02/03 use valid Sheet6 revision markers wider than 320 units."""

        parser = load_parser()
        source_dir = PROJECT_DIR / "SHA文件转换" / "200301" / "200301 SHA"
        expected_counts = {
            "20-42-0301-26-EC-3120-02": 1,
            "20-42-0301-26-EC-3120-03": 2,
        }
        for sheet_id, expected_count in expected_counts.items():
            source = source_dir / f"{sheet_id}.sha"
            streams = parser.read_sha_streams(source)
            segments = parser.unique_line_segments(
                parser.line_segments(streams["Sheet6"])
                + parser.template_line_segments(streams["Sheet6"])
            )
            records = parser.sheet_revision_triangle_records(segments)
            self.assertEqual(len(records), expected_count, sheet_id)
            self.assertTrue(
                all(
                    320
                    < float(row["source_bbox"][2]) - float(row["source_bbox"][0])
                    <= 400
                    for row in records
                ),
                sheet_id,
            )
            with tempfile.TemporaryDirectory() as temporary:
                svg = Path(temporary) / "page.svg"
                parser.render(source, svg, 1, False, None, None, "Sheet6")
                root = ET.parse(svg).getroot()
            labels = [
                "".join(node.itertext())
                for node in root.iter("{http://www.w3.org/2000/svg}text")
                if node.attrib.get("data-layer") == "sha-revision-triangle-text"
            ]
            self.assertEqual(labels.count("0B"), expected_count, sheet_id)

    def test_embedded_revision_triangle_text_is_rendered_on_20fw_pages(self) -> None:
        """0x4A composite labels supply the revision text when no free text exists."""

        parser = load_parser()
        sources = [
            PROJECT_DIR / "SHA文件转换" / "000200FW" / f"{stem}.sha"
            for stem in (
                "00-42-0200-26-20FW41001-01",
                "00-42-0200-26-20FW41001-02",
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for index, source in enumerate(sources):
                svg = Path(temporary) / f"revision-{index}.svg"
                parser.render(source, svg, 1, False, None, None, "Sheet6")
                root = ET.parse(svg).getroot()
                labels = [
                    "".join(node.itertext())
                    for node in root.iter("{http://www.w3.org/2000/svg}text")
                    if node.attrib.get("data-layer") == "sha-revision-triangle-text"
                ]
                self.assertEqual(labels, ["0"])

    def test_closed_multi_arc_groups_are_rendered_as_revision_clouds(self) -> None:
        """不接直线但自身首尾闭合的多圆弧组是升版云线，不是无效弯头。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        clouds = parser.revision_cloud_arc_groups(parser.standalone_arcs(streams["Sheet6"]))
        self.assertEqual(sorted(len(group) for group in clouds), [8, 15])

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        cloud_paths = [
            node
            for node in root.iter(ns + "path")
            if node.attrib.get("data-layer") == "sha-revision-cloud"
        ]
        self.assertEqual(len(cloud_paths), 23)
        self.assertEqual(
            {node.attrib.get("data-owner") for node in cloud_paths},
            {"0x1625", "0x1675"},
        )

    def test_bound_and_multiline_cell_text_use_true_cell_centres(self) -> None:
        """图签单元格文字应以PSM框中心排版，而不是把字体基线当作字形中心。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        bound = parser.template_bound_text(
            streams["Sheet221"], streams, streams["PSMcluster0"]
        )
        revision_record = next(record for record in bound if record["text"] == "0C")
        left, bottom, right, top = revision_record["psm_bbox"]
        self.assertAlmostEqual(
            float(revision_record["y"]), (bottom + top) / 2, places=6
        )

        multiline = parser.template_multiline_text_records(
            streams["Sheet221"], streams["PSMcluster0"], streams["StyleCluster"]
        )
        for value in ("Scale\n1/1\n@A3", "Sheet\n1  of  1"):
            record = next(item for item in multiline if item["text"] == value)
            self.assertEqual(record["alignment"], "middle")

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()

        ns = "{http://www.w3.org/2000/svg}"
        text_nodes = list(root.iter(ns + "text"))
        revision = next(node for node in text_nodes if "".join(node.itertext()) == "0C")
        self.assertEqual(revision.attrib.get("dominant-baseline"), "central")
        self.assertAlmostEqual(
            float(revision.attrib["y"]),
            parser.svg_y((bottom + top) / 2),
            delta=0.11,
        )

        for value in ("Scale1/1@A3", "Sheet1  of  1"):
            node = next(item for item in text_nodes if "".join(item.itertext()) == value)
            self.assertEqual(node.attrib.get("text-anchor"), "middle")
            tspans = list(node.findall(ns + "tspan"))
            self.assertTrue(tspans)
            self.assertTrue(
                all(tspan.attrib.get("dominant-baseline") == "central" for tspan in tspans)
            )
            ys = [float(tspan.attrib["y"]) for tspan in tspans]
            record = next(
                item for item in multiline if item["text"].replace("\n", "") == value
            )
            box = record["bbox"]
            self.assertAlmostEqual(
                sum(ys) / len(ys),
                parser.svg_y((box[1] + box[3]) / 2),
                delta=0.11,
            )

    def test_chengda_coloured_vector_logo_is_decoded_and_rendered(self) -> None:
        """成达红蓝标识应来自Sheet221自由轮廓，不应依赖外置图片或硬编码坐标。"""

        parser = load_parser()
        streams = parser.read_sha_streams(SOURCE_SHA)
        paths = parser.template_filled_paths(
            streams["Sheet221"], streams["StyleCluster"]
        )
        self.assertGreaterEqual(len(paths), 50)
        self.assertEqual(
            {str(record["fill"]) for record in paths},
            {"#000000", "#ff0000", "#0000ff"},
        )
        self.assertEqual(
            {int(record["style_ref"]) for record in paths if record["fill"] != "#000000"},
            {279, 280},
        )
        overall = (
            min(float(record["bbox"][0]) for record in paths),
            min(float(record["bbox"][1]) for record in paths),
            max(float(record["bbox"][2]) for record in paths),
            max(float(record["bbox"][3]) for record in paths),
        )
        self.assertAlmostEqual(overall[0], 6406.7, delta=0.2)
        self.assertAlmostEqual(overall[1], 1026.3, delta=0.2)
        self.assertAlmostEqual(overall[2], 7281.1, delta=0.2)
        self.assertAlmostEqual(overall[3], 1145.1, delta=0.2)

        with tempfile.TemporaryDirectory() as temporary:
            svg = Path(temporary) / "page.svg"
            parser.render(SOURCE_SHA, svg, 1, False, None, None, "Sheet6")
            root = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        logo_group = next(
            node for node in root.iter(ns + "g") if node.attrib.get("id") == "sha-template-filled-paths"
        )
        rendered = list(logo_group.findall(ns + "path"))
        self.assertEqual(len(rendered), len(paths))
        self.assertEqual({node.attrib.get("fill") for node in rendered}, {"#000000", "#ff0000", "#0000ff"})


if __name__ == "__main__":
    unittest.main()
