import struct
import unittest

from analyze_composite_primitives import sheet_summary


class CompositePrimitiveRegressionTests(unittest.TestCase):
    def test_type_0_can_be_a_text_group_range_companion(self) -> None:
        text = "E 123"
        text_record_length = 60 + 2 * len(text)
        text_record = bytearray(
            b"\x4d\x00" + text_record_length.to_bytes(4, "little")
            + text_record_length * b"\x00"
        )
        text_record[6:10] = (100).to_bytes(4, "little")
        text_record[10:14] = (200).to_bytes(4, "little")
        text_record[14:18] = (1).to_bytes(4, "little")
        text_record[20:24] = (1).to_bytes(4, "little")
        text_record[28:30] = len(text).to_bytes(2, "little")
        text_record[30:30 + 2 * len(text)] = text.encode("utf-16le")
        struct.pack_into("<4d", text_record, 30 + 2 * len(text), 0.2, 0.3, 1.0, 0.0)

        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (200).to_bytes(4, "little") + b"\x00" * 42)
        composite[22:26] = (1).to_bytes(4, "little")
        struct.pack_into("<I5H", composite, 34, 100, 1, 1, 2, 2, 0)

        result = sheet_summary("Sheet6", bytes(text_record + composite), set())
        self.assertEqual(result["type_0_role_counts"], {"text-group-range-companion": 1})

    def test_type_2_can_alias_a_sheet_circle(self) -> None:
        circle = bytearray(
            b"\x59\x00\x2b\x00\x00\x00" + (100).to_bytes(4, "little")
            + (200).to_bytes(4, "little") + b"\x00" * 39
        )
        circle[14:18] = (1).to_bytes(4, "little")
        circle[20:24] = (1).to_bytes(4, "little")
        struct.pack_into("<3d", circle, 24, 0.2, 0.3, 0.01)

        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (200).to_bytes(4, "little") + b"\x00" * 42)
        composite[22:26] = (1).to_bytes(4, "little")
        struct.pack_into("<I5H", composite, 34, 100, 1, 1, 2, 2, 2)

        result = sheet_summary("Sheet6", bytes(circle + b"\x00" + composite), set())
        links = result["auxiliary_types_to_18_32_reference_links"]
        self.assertEqual(links["type_2_child_ref_matches_59_circle"], 1)
        self.assertEqual(links["type_2_linked_ref_matches_59_circle_graphic_ref"], 1)

    def test_type_16_can_alias_a_pipe_arc(self) -> None:
        arc = bytearray(b"\x61\x00\x3b\x00\x00\x00" + (100).to_bytes(4, "little") + (200).to_bytes(4, "little") + b"\x00" * 51)
        arc[14:18] = (1).to_bytes(4, "little")
        arc[20:24] = (1).to_bytes(4, "little")
        struct.pack_into("<5d", arc, 24, 0.2, 0.3, 0.01, 0.0, 1.57)

        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (200).to_bytes(4, "little") + b"\x00" * 42)
        composite[22:26] = (1).to_bytes(4, "little")
        struct.pack_into("<I5H", composite, 34, 100, 1, 1, 2, 2, 16)

        # Composite headers are uint16-aligned in physical Sheet streams.
        result = sheet_summary("Sheet6", bytes(arc + b"\x00" + composite), set())
        links = result["auxiliary_types_to_18_32_reference_links"]
        self.assertEqual(links["type_16_child_ref_matches_61_pipe_arc"], 1)
        self.assertEqual(links["type_16_linked_ref_matches_61_pipe_arc_graphic_ref"], 1)

    def test_type_10_can_alias_a_sheet_line(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (100).to_bytes(4, "little") + b"\x00" * 46)
        line[10:14] = (200).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)

        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (200).to_bytes(4, "little") + b"\x00" * 42)
        composite[22:26] = (1).to_bytes(4, "little")
        struct.pack_into("<I5H", composite, 34, 100, 1, 1, 2, 2, 10)

        result = sheet_summary("Sheet6", bytes(line + composite), set())
        links = result["auxiliary_types_to_18_32_reference_links"]
        self.assertEqual(links["type_10_child_ref_matches_18_32"], 1)
        self.assertEqual(links["type_10_linked_ref_matches_18_32_object_ref"], 1)


if __name__ == "__main__":
    unittest.main()
