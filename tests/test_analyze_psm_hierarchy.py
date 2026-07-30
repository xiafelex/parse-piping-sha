import struct
import unittest

from analyze_psm_hierarchy import (
    parse_psmcluster_envelope_runs,
    classify_counted_zero_relation_anchor_lists,
    link_sheet221_bitmap_resource_descriptors,
    jsite_resource_inventory,
    index_sheet221_template_primitives,
    summarize_known_fixed_style_usage,
    summarize_known_dash_pattern_usage,
    summarize_stylecluster_2e_category_usage,
    parse_stylecluster_18_control_records,
    parse_stylecluster_2e_style_records,
    parse_stylecluster_zero_object_containers,
    parse_sheet221_template_special_records,
    parse_sheet_3d_placement_wrappers,
    link_sheet_3d_resource_descriptors,
    link_contentless_jsite_sheet_templates,
    summarize_dynamic_property_sheet_text_matches,
    summarize_element_tag_unique_text_candidates,
    summarize_dynamic_graphic_sheet_primitive_bindings,
    bounded_dynamic_graphics_by_uci,
    attach_4d_text_style_resources,
    summarize_4d_text_psm_envelope_bindings,
    parse_stylecluster_1b_named_internal_style_records,
    parse_stylecluster_61_local_arc_resources,
    parse_stylecluster_70_fixed_records,
    parse_stylecluster_59_local_ellipse_resources,
    parse_stylecluster_7c_polygon_groups,
    parse_stylecluster_84_polygon_resources,
    parse_stylecluster_named_style_catalog_entries,
    psm_envelope_tag_provenance,
    parse_sheet221_template_text_records,
    resolve_sheet221_revision_bindings,
    summarize_stylecluster_local_resource_sheet_references,
    parse_13_ac_layer_relations,
    validate_13_ac_reverse_line_aliases,
    parse_13_63_circle_geometry,
    parse_59_2b_page_layer_bindings,
    parse_18_32_layer_bindings,
    summarize_7b_composite_child_graphic_links,
    parse_4d_text_layer_bindings,
    parse_prefixed_tseg_nodes,
    summarize_prefixed_zero_relation_semantics,
    classify_prefixed_zero_relation_201_geometry_companions,
    classify_counted_relation_201_dynamic_graphic_bindings,
    parse_psmcluster_top_vfset_records,
    parse_psmcluster_88_page_default_records,
    validate_psmcluster_88_default_parent_refs,
    validate_psmcluster_88_page_container_links,
    parse_psmcluster_42_page_layer_containers,
    validate_psmcluster_42_member_anchor_refs,
    validate_psmcluster_42_default_object_refs,
    parse_psmcluster_57_page_linked_control_records,
    parse_psmcluster_75_root_catalog,
    parse_psmcluster_02_preference_index,
    parse_psmcluster_6c_default_style_bundles,
    parse_psmcluster_89_application_property_records,
    validate_psmcluster_89_parent_object_links,
    validate_psmcluster_89_pasted_graphic_jsite_links,
    parse_psmcluster_73_background_records,
    parse_psmcluster_64_zero_control_slots,
    parse_psmcluster_65_section_name_sites,
    parse_dynamic_attribute_property_records,
    validate_psmcluster_65_section_sheet_directory,
    validate_psmcluster_57_layer_state_profiles,
    link_psm_root_directory_entries,
    summarize_mixed_relation_parent_target_namespaces,
)


class PsmHierarchyRegressionTests(unittest.TestCase):
    def test_dynamic_attribute_record_distinguishes_schema_stub_from_component_instance(self) -> None:
        def property_block(key: str, value: str) -> bytes:
            payload = b"\x10\x80\x00\x00\x01" + key.encode() + b"\x00" + value.encode() + b"\x00"
            return payload[:2] + len(payload).to_bytes(2, "little") + payload[4:]

        def record(values: dict[str, str], graphic_ref: int) -> bytes:
            properties = b"".join(property_block(key, value) for key, value in values.items())
            prefix = b"\x00\x00\x13\x00\x00PipeLine Info\x00"
            footer_size = 26
            record_size = len(prefix) + len(properties) + footer_size - 6
            return (
                prefix
                + properties
                + b"\x89\x00"
                + record_size.to_bytes(4, "little")
                + (1).to_bytes(4, "little")
                + b"\x00" * 8
                + graphic_ref.to_bytes(4, "little")
                + b"\xff\xff\x00\x00"
            )

        stub = record(
            {
                "PipeLine Reference": "",
                "Fly Text": "",
                "Unique Component Identifier": "",
                "Element Tag": "",
            },
            100,
        )
        instance = record(
            {
                "PipeLine Reference": "LINE-1",
                "Fly Text": "DN50",
                "Unique Component Identifier": "{00000000-0000-0000-0000-000000000001}",
                "Element Tag": "TAG-1",
            },
            200,
        )
        parsed = parse_dynamic_attribute_property_records(stub + instance)
        self.assertEqual(
            [record["record_kind"] for record in parsed],
            ["empty-property-schema-stub", "component-instance"],
        )
        self.assertTrue(parsed[1]["has_component_uci"])
        self.assertEqual(parsed[1]["graphic_ref"], 200)

    def test_2e_category_usage_keeps_zero_use_as_a_limited_negative_result(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (10).to_bytes(4, "little") + b"\x00" * 46)
        line[10:14] = (77).to_bytes(4, "little")
        line[14:18] = (1).to_bytes(4, "little")
        line[20:24] = (100).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = summarize_stylecluster_2e_category_usage(
            {"Sheet6": bytes(line)},
            [
                {"category_raw": 1, "style_ref": 100},
                {"category_raw": 2, "style_ref": 200},
            ],
        )
        self.assertEqual(result["records"][0]["validated_primitive_use_count"], 1)
        self.assertEqual(result["records"][1]["validated_primitive_use_count"], 0)

    def test_page_container_tail_directly_backlinks_to_default_object(self) -> None:
        member_refs = [101, 102]
        record_length = 72 + 4 * len(member_refs)
        container = bytearray(b"\x42\x00" + record_length.to_bytes(4, "little") + record_length * b"\x00")
        container[6:10] = (77).to_bytes(4, "little")
        container[18:22] = (6).to_bytes(4, "little")
        container[22:26] = (101).to_bytes(4, "little")
        container[26:30] = len(member_refs).to_bytes(4, "little")
        struct.pack_into("<2I", container, 30, *member_refs)
        # The second u32 after the variable member vector is the 0x0088 Default id.
        struct.pack_into("<I", container, 30 + 4 * len(member_refs) + 4, 900)
        containers = parse_psmcluster_42_page_layer_containers(bytes(container))["records"]
        self.assertEqual(containers[0]["page_default_object_ref"], 900)
        validation = validate_psmcluster_42_default_object_refs(
            containers,
            [{"object_ref": 900, "name": "Default", "page_parent_ref": 101}],
        )
        self.assertTrue(validation["all_containers_have_matching_default_backlink"])
        self.assertEqual(validation["records"][0]["sheet_stream_id"], 6)

    def test_extended_4d_text_preserves_its_eight_byte_prefix(self) -> None:
        text = "TN481-003A"
        record_length = 68 + 2 * len(text)
        record = bytearray(b"\x4d\x00" + record_length.to_bytes(4, "little") + record_length * b"\x00")
        record[6:10] = (39045).to_bytes(4, "little")
        record[10:14] = (7591).to_bytes(4, "little")
        record[14:18] = (7684).to_bytes(4, "little")
        record[20:24] = (273).to_bytes(4, "little")
        record[28:30] = len(text).to_bytes(2, "little")
        record[30:38] = b"\x01\x00\x10\x01\x00\x00\x0a\x00"
        record[38:38 + 2 * len(text)] = text.encode("utf-16le")
        struct.pack_into("<4d", record, 38 + 2 * len(text), 0.3, 0.2, 1.0, 0.0)
        result = parse_4d_text_layer_bindings(bytes(record))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["layout"], "extended-0x004d-text-with-eight-byte-prefix")
        self.assertEqual(result[0]["text"], text)
        self.assertEqual(result[0]["text_prefix_extension_raw_hex"], "0100100100000a00")
        self.assertEqual(result[0]["text_prefix_constant_raw"], 1)
        self.assertEqual(result[0]["explicit_font_style_ref"], 272)
        self.assertTrue(result[0]["text_prefix_character_count_matches"])

    def test_application_property_parent_links_keep_schema_distinct_from_values(self) -> None:
        application_payload = (
            b"\x13\x00\x00PipeLine Info\x00"
            + b"\x10\x80\x0f\x00\x01Fly Text\x00"
            + b"\x10\x80\x12\x00\x01Element Tag\x00"
        )
        record_length = 149
        application = bytearray(b"\x89\x00" + record_length.to_bytes(4, "little") + record_length * b"\x00")
        application[6:10] = (901).to_bytes(4, "little")
        application[18:22] = (77).to_bytes(4, "little")
        application[22:24] = (0xFFFF).to_bytes(2, "little")
        application[24:28] = (129).to_bytes(4, "little")
        application[28:28 + len(application_payload)] = application_payload
        parsed = parse_psmcluster_89_application_property_records(bytes(application))
        self.assertEqual(parsed["record_count"], 1)
        record = parsed["records"][0]
        self.assertEqual(record["application_name"], "PipeLine Info")
        self.assertEqual(record["parent_object_ref"], 77)
        self.assertIn("Fly Text", record["property_label_candidates"])
        self.assertIn("Element Tag", record["property_label_candidates"])

        envelopes = b"".join(
            struct.pack("<I5H", ref, 1, 2, 3, 4, 5)
            for ref in (77, 78, 79)
        )
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (10).to_bytes(4, "little") + b"\x00" * 46)
        line[10:14] = (77).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        validation = validate_psmcluster_89_parent_object_links(
            {"PSMcluster0": envelopes, "Sheet6": bytes(line)}, parsed["records"]
        )
        linked = validation["records"][0]
        self.assertTrue(linked["parent_in_psm_envelope"])
        self.assertEqual(linked["parent_sheet_graphic_families"], ["18_32_line"])

    def test_microstation_global_application_property_extracts_only_the_dgn_source_path(self) -> None:
        record = bytearray(b"\x89\x00" + (245).to_bytes(4, "little") + 245 * b"\x00")
        record[6:10] = (715).to_bytes(4, "little")
        payload = b"\x12\x00\x00MSTN_GLOBALS\x00FileName\x00C:\\templates\\MYLARD.dgn\x00MuSuStr\x00INTH\x00"
        record[28:28 + len(payload)] = payload
        parsed = parse_psmcluster_89_application_property_records(bytes(record))
        self.assertEqual(parsed["record_count"], 1)
        global_record = parsed["records"][0]
        self.assertEqual(global_record["application_name"], "MSTN_GLOBALS")
        self.assertEqual(global_record["source_dgn_path"], "C:\\templates\\MYLARD.dgn")
        self.assertIn("MuSuStr", global_record["payload_ascii_tokens"])

    def test_pasted_graphic_links_only_an_exact_adjacent_jsite_id(self) -> None:
        result = validate_psmcluster_89_pasted_graphic_jsite_links(
            {
                "JSite690/JProperties": b"",
                "JSite690/CONTENTS": b"",
                "JSite559/JProperties": b"",
            },
            [
                {"subtype": "pasted-graphics-application-property", "object_ref": 691},
                {"subtype": "pasted-graphics-application-property", "object_ref": 1716},
            ],
        )
        self.assertEqual(result["exact_jsite_match_count"], 1)
        self.assertTrue(result["records"][0]["has_embedded_contents"])
        self.assertFalse(result["records"][1]["exact_jsite_match"])

    def test_dynamic_property_text_summary_keeps_occurrences_distinct_from_values(self) -> None:
        records = [
            {"properties": [{"key": "PipeLine Reference", "value": "LINE-1"}, {"key": "Fly Text", "value": "hidden"}]},
            {"properties": [{"key": "PipeLine Reference", "value": "LINE-1"}, {"key": "Element Tag", "value": "TAG-1"}]},
        ]
        result = summarize_dynamic_property_sheet_text_matches(records, {"LINE-1", "TAG-1"})
        self.assertEqual(result["PipeLine Reference"]["nonempty_occurrence_count"], 2)
        self.assertEqual(result["PipeLine Reference"]["nonempty_unique_value_count"], 1)
        self.assertEqual(result["PipeLine Reference"]["exact_physical_sheet_4d_text_match_occurrence_count"], 2)
        self.assertEqual(result["Fly Text"]["exact_physical_sheet_4d_text_match_count"], 0)

    def test_element_tag_only_returns_unique_sheet_text_candidate(self) -> None:
        records = [
            {"graphic_ref": 77, "properties": [{"key": "Element Tag", "value": "TAG-1"}]},
            {"graphic_ref": 78, "properties": [{"key": "Element Tag", "value": "TAG-2"}]},
        ]
        text = [
            {"text": "TAG-1", "sheet_stream": "Sheet6", "child_ref": 10, "secondary_ref": 20, "page_layer_ref": 30, "style_ref": 40, "font_name": "Arial", "font_size_ratio": 0.0035, "x": 0.1, "y": 0.2, "direction": [1.0, 0.0]},
            {"text": "TAG-2", "sheet_stream": "Sheet6", "child_ref": 11, "secondary_ref": 21, "page_layer_ref": 30, "style_ref": 40, "x": 0.3, "y": 0.4, "direction": [1.0, 0.0]},
            {"text": "TAG-2", "sheet_stream": "Sheet6", "child_ref": 12, "secondary_ref": 22, "page_layer_ref": 30, "style_ref": 40, "x": 0.5, "y": 0.6, "direction": [1.0, 0.0]},
        ]
        result = summarize_element_tag_unique_text_candidates(records, text)
        self.assertEqual(result["candidate_text_count_distribution"], {"1": 1, "2": 1})
        self.assertEqual(result["unique_candidate_count"], 1)
        self.assertEqual(result["unique_candidates"][0]["dynamic_graphic_ref"], 77)
        self.assertEqual(result["unique_candidates"][0]["text_font_name"], "Arial")

    def test_dynamic_uci_graphic_ref_resolves_to_equal_sheet_line_graphic(self) -> None:
        line = bytearray(
            b"\x18\x00\x32\x00\x00\x00"
            + (10).to_bytes(4, "little")
            + (77).to_bytes(4, "little")
            + b"\x00" * 42
        )
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = summarize_dynamic_graphic_sheet_primitive_bindings(
            {"Sheet6": bytes(line) + b"\x00" * 1024},
            {"{UCI}": [{"graphic_ref": 77}]},
        )
        self.assertEqual(result["direct_sheet_graphic_record_count"], 1)
        self.assertEqual(result["direct_sheet_family_occurrence_counts"], {"18_32_line": 1})

    def test_4d_nonzero_tail_flag_is_preserved_as_observed_template_marker(self) -> None:
        text = "中文".encode("utf-16le")
        record_length = 60 + len(text)
        record = bytearray(
            b"\x4d\x00" + record_length.to_bytes(4, "little") + (10).to_bytes(4, "little")
            + (77).to_bytes(4, "little") + b"\x00" * (record_length - 8)
        )
        struct.pack_into("<H", record, 28, 2)
        record[30:34] = text
        struct.pack_into("<4d", record, 34, 0.1, 0.2, 1.0, 0.0)
        struct.pack_into("<I", record, 66, 0x01000000)
        result = parse_4d_text_layer_bindings(bytes(record))
        self.assertEqual(result[0]["tail_flags_u32"], 0x01000000)
        self.assertTrue(result[0]["observed_non_ascii_template_text_flag"])

    def test_bounded_dynamic_uci_builder_excludes_unframed_placeholder_records(self) -> None:
        uci = "{00000000-0000-0000-0000-000000000001}"
        records = [{
            "offset": 12,
            "reference": 99,
            "graphic_ref": 77,
            "properties": [{"key": "Unique Component Identifier", "value": uci}],
        }]
        self.assertEqual(
            bounded_dynamic_graphics_by_uci(records),
            {uci: [{"record_offset": 12, "space_ref": 99, "graphic_ref": 77}]},
        )

    def test_text_style_resources_attach_font_and_ratio_by_style_ref(self) -> None:
        records = [{"style_ref": 217, "text": "中文"}]
        attach_4d_text_style_resources(
            records,
            {217: {"font_style_ref": 215, "font_name": "SimHei", "font_size_ratio": 0.0035}},
        )
        self.assertEqual(records[0]["font_style_ref"], 215)
        self.assertEqual(records[0]["font_name"], "SimHei")
        self.assertEqual(records[0]["font_size_ratio"], 0.0035)

    def test_88_page_default_record_uses_same_bounded_utf16_framing_without_joining_layer_catalog(self) -> None:
        name = "Default".encode("utf-16le")
        record = (
            b"\x88\x00"
            + (46).to_bytes(4, "little")
            + struct.pack("<6I", 0x1E56, 0, 0, 1, 0x1E55, 0)
            + (7).to_bytes(4, "little")
            + name
            + b"\x00\x00"
        )
        result = parse_psmcluster_88_page_default_records(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["default_name_record_count"], 1)
        self.assertEqual(result["records"][0]["object_ref"], 0x1E56)

    def test_42_page_layer_container_has_self_sized_member_vector(self) -> None:
        members = [100, 101, 102]
        record = (
            b"\x42\x00"
            + (84).to_bytes(4, "little")
            + struct.pack("<3I", 97, 0, 0)
            + (96).to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + (3).to_bytes(4, "little")
            + struct.pack("<3I", *members)
            + b"\x00" * 42
        )
        result = parse_psmcluster_42_page_layer_containers(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["object_ref"], 97)
        self.assertEqual(result["records"][0]["sheet_stream_id"], 96)
        self.assertEqual(result["records"][0]["member_refs"], members)

    def test_57_page_linked_control_record_keeps_sheet_backlink(self) -> None:
        record = bytearray(b"\x57\x00" + (148).to_bytes(4, "little") + (148 - 6) * b"\x00")
        struct.pack_into("<I", record, 6, 105)
        struct.pack_into("<I", record, 22, 100)
        struct.pack_into("<I", record, 26, 1)
        struct.pack_into("<I", record, 30, 1)
        result = parse_psmcluster_57_page_linked_control_records(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["object_ref"], 105)
        self.assertEqual(result["records"][0]["sheet_stream_id"], 100)

    def test_57_large_template_profile_keeps_its_sheet_backlink(self) -> None:
        record = bytearray(b"\x57\x00" + (1756).to_bytes(4, "little") + (1756 - 6) * b"\x00")
        struct.pack_into("<I", record, 6, 3708)
        struct.pack_into("<I", record, 22, 221)
        # 83 declared items mean 82 name/state records plus one trailer.
        # The character counts fill the exact bounded 1756-byte layout.
        struct.pack_into("<I", record, 138, 83)
        cursor = 142
        names = ["WELDS"] + ["LAYER07"] * 68 + ["LAYER6"] * 13
        for index, name in enumerate(names):
            struct.pack_into("<I", record, cursor, len(name))
            cursor += 4
            encoded = name.encode("utf-16le")
            record[cursor : cursor + len(encoded)] = encoded
            cursor += len(encoded)
            struct.pack_into("<H", record, cursor, 97 + index)
            cursor += 2
        self.assertEqual(cursor, 1752)
        struct.pack_into("<I", record, 1752, 2)
        result = parse_psmcluster_57_page_linked_control_records(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["profile_kind"], "template-layer-state-profile")
        self.assertEqual(result["records"][0]["sheet_stream_id"], 221)
        self.assertIn("WELDS", result["records"][0]["layer_state_names"])
        self.assertEqual(result["records"][0]["layer_state_entries"][0]["state_raw"], 97)
        self.assertEqual(result["records"][0]["layer_state_entries"][0]["entry_id_raw"], 97)

    def test_57_long_profile_matches_page_layers_except_reserved_05(self) -> None:
        result = validate_psmcluster_57_layer_state_profiles(
            [
                {"object_ref": 1, "name": "05", "entry_id": 8},
                {"object_ref": 2, "name": "PIPES", "entry_id": 1},
            ],
            [{"sheet_stream_id": 221, "member_refs": [1, 2]}],
            [
                {
                    "object_ref": 3708,
                    "sheet_stream_id": 221,
                    "record_length": 1756,
                    "layer_state_entries": [{"name": "PIPES", "state_raw": 1}],
                }
            ],
        )
        self.assertEqual(result["profile_count"], 1)
        self.assertTrue(result["profiles"][0]["matches_page_layers_except_stable_05"])
        self.assertTrue(result["profiles"][0]["state_raw_equals_page_layer_entry_id"])

    def test_88_page_default_parent_resolves_to_named_default(self) -> None:
        result = validate_psmcluster_88_default_parent_refs(
            [{"object_ref": 232, "page_parent_ref": 230}],
            [{"object_ref": 230, "entry_id": 0, "name": "Default"}],
        )
        self.assertTrue(result["all_parents_are_named_default"])
        self.assertEqual(result["records"][0]["parent_name"], "Default")

    def test_42_member_anchor_resolves_inside_its_named_layer_vector(self) -> None:
        result = validate_psmcluster_42_member_anchor_refs(
            [{"object_ref": 7, "sheet_stream_id": 6, "member_anchor_ref": 1404, "member_refs": [332, 1404]}],
            [{"object_ref": 1404, "entry_id": 98, "name": "NOZZLES"}],
        )
        self.assertTrue(result["all_anchors_are_members"])
        self.assertEqual(result["records"][0]["anchor_name"], "NOZZLES")
        self.assertEqual(result["records"][0]["anchor_member_index"], 1)

    def test_88_default_links_to_one_page_container_through_named_default(self) -> None:
        result = validate_psmcluster_88_page_container_links(
            [{"object_ref": 232, "page_parent_ref": 230}],
            [{"object_ref": 230, "entry_id": 0, "name": "Default"}],
            [{"object_ref": 238, "sheet_stream_id": 221, "member_refs": [332, 230]}],
        )
        self.assertTrue(result["all_records_have_unique_page_container"])
        self.assertEqual(result["records"][0]["sheet_stream_id"], 221)

    def test_75_root_catalog_keeps_document_collection_entry_ids(self) -> None:
        entries = [("SiteObjects", 2), ("PreferenceSet", 3), ("Sheets", 4)]
        payload = bytearray()
        for name, entry_id in entries:
            encoded = name.encode("utf-16le")
            payload += len(encoded).to_bytes(4, "little") + encoded + entry_id.to_bytes(4, "little")
        payload += b"\x00" * 4
        record = (
            b"\x75\x00"
            + (113).to_bytes(4, "little")
            + (1).to_bytes(4, "little")
            + b"\x00" * 8
            + struct.pack("<d", 1.0)
            + b"\x01"
            + (3).to_bytes(4, "little")
            + payload
        )
        result = parse_psmcluster_75_root_catalog(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["entries"], [
            {"name": "SiteObjects", "catalog_entry_id": 2},
            {"name": "PreferenceSet", "catalog_entry_id": 3},
            {"name": "Sheets", "catalog_entry_id": 4},
        ])

    def test_02_preferenceset_index_preserves_mixed_endian_raw_entries(self) -> None:
        record = bytearray(b"\x02\x00\x00\x03\x00\x00" + (768) * b"\x00")
        record[6:10] = b"\x00\x01\x00\x00"
        record[15] = 25
        record[16:24] = b"\x00\x01\x00\x15\x02\x01\x00\x00"
        result = parse_psmcluster_02_preference_index(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["entries"][0], {
            "group_id_raw": 1,
            "key_id_raw": 21,
            "target_raw": 258,
        })

    def test_6c_default_style_bundle_keeps_nested_default_and_style_raw(self) -> None:
        record = bytearray(b"\x6c\x00" + (116).to_bytes(4, "little") + (116) * b"\x00")
        record[12:18] = b"\x88\x00" + (46).to_bytes(4, "little")
        struct.pack_into("<I", record, 18, 9)
        struct.pack_into("<I", record, 34, 8)
        struct.pack_into("<I", record, 42, 7)
        record[46:60] = "Default".encode("utf-16le")
        record[64:70] = b"\x37\x00" + (50).to_bytes(4, "little")
        struct.pack_into("<I", record, 70, 10)
        result = parse_psmcluster_6c_default_style_bundles(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["default_object_ref"], 9)
        self.assertEqual(result["records"][0]["companion_style_ref"], 10)

    def test_89_application_property_uses_its_own_six_byte_length_basis(self) -> None:
        record = bytearray(b"\x89\x00" + (40).to_bytes(4, "little") + 40 * b"\x00")
        struct.pack_into("<I", record, 6, 691)
        record[31:46] = b"_PastedGraphic\x00"
        result = parse_psmcluster_89_application_property_records(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["subtype"], "pasted-graphics-application-property")

    def test_73_background_site_object_uses_full_name_prefix(self) -> None:
        record = bytearray(521)
        prefix = b"\x73\x00\x03\x02\x00\x00\x00\x01\x00\x00\x00\x0a\x00" + "Background".encode("utf-16le")
        record[:len(prefix)] = prefix
        # The nested 0x0076 record begins at an 8-byte-aligned offset inside
        # the fixed 515-byte Background container, not at one hard-coded slot.
        record[72:78] = b"\x76\x00" + (307).to_bytes(4, "little")
        record[130:134] = (6).to_bytes(4, "little")
        record[134:146] = "Sketch".encode("utf-16le")
        record[146:150] = (4).to_bytes(4, "little")
        record[150:158] = "ISO1".encode("utf-16le")
        record[100:112] = "Sketch".encode("utf-16le")
        result = parse_psmcluster_73_background_records(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["name"], "Background")
        self.assertEqual(result["records"][0]["sketch_name_offset"], 100)
        self.assertEqual(result["records"][0]["nested_76_offset"], 72)
        self.assertEqual(result["records"][0]["nested_76_relative_offset"], 72)
        self.assertEqual(result["records"][0]["nested_76_length"], 307)
        self.assertEqual(result["records"][0]["nested_76_sketch_name"], "Sketch")
        self.assertEqual(result["records"][0]["nested_76_document_identifier"], "ISO1")

    def test_64_zero_control_slot_requires_the_entire_zero_payload(self) -> None:
        record = b"\x64\x00" + (101).to_bytes(4, "little") + b"\x00" * 101
        result = parse_psmcluster_64_zero_control_slots(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["total_byte_length"], 107)
        self.assertEqual(parse_psmcluster_64_zero_control_slots(record[:-1] + b"\x01")["record_count"], 0)

    def test_65_section_directory_maps_ordinal_names_without_treating_them_as_stream_names(self) -> None:
        record = bytearray(180)
        record[:6] = b"\x65\x00" + (114).to_bytes(4, "little")
        record[91:107] = "Section1".encode("utf-16le")
        record[107:112] = b"\x02\x01\x00\x00\x00"
        record[112:116] = (1).to_bytes(4, "little")
        record[116:118] = (6).to_bytes(2, "little")
        record[118:130] = "Sheet1".encode("utf-16le")
        record[130] = 1
        record[131:133] = (11).to_bytes(2, "little")
        record[133:155] = "Backgrounds".encode("utf-16le")
        record[155:157] = b"\x03\x02"
        result = parse_psmcluster_65_section_name_sites(bytes(record))
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["section_name"], "Section1")
        self.assertEqual(result["records"][0]["section_name_relative_offset"], 91)
        self.assertEqual(result["records"][0]["sheet_names"], ["Sheet1"])
        validation = validate_psmcluster_65_section_sheet_directory(
            result["records"],
            [{"sheet_stream_id": 6}, {"sheet_stream_id": 221}],
        )
        self.assertTrue(validation["records"][0]["declared_count_matches_nonbase_page_container_count"])
        self.assertEqual(
            validation["records"][0]["ordinal_to_sheet_stream"],
            [{"section_sheet_name": "Sheet1", "sheet_stream_id": 6}],
        )

    def test_4d_text_child_ref_uses_shared_16800_anchor_basis_for_bounded_psm_envelope(self) -> None:
        envelope = struct.pack("<I5H", 10, 100, 200, 180, 260, 0)
        envelope_runs = parse_psmcluster_envelope_runs(
            envelope
            + struct.pack("<I5H", 11, 200, 200, 280, 260, 0)
            + struct.pack("<I5H", 12, 300, 200, 380, 260, 0)
        )
        result = summarize_4d_text_psm_envelope_bindings(
            [{"child_ref": 10, "x": 0.1, "y": 0.2}], envelope_runs
        )
        self.assertEqual(result["direct_child_ref_envelope_match_count"], 1)
        self.assertEqual(result["single_envelope_match_count"], 1)
        self.assertEqual(result["normalized_anchor_page_unit"], 16800)
        self.assertEqual(
            result["normalized_anchor_contract"],
            "page_x = 4D.x * 16800; page_y = 4D.y * 16800",
        )

    def test_mixed_relation_summary_keeps_relation_code_scoped_to_endpoint_namespaces(self) -> None:
        result = summarize_mixed_relation_parent_target_namespaces(
            {
                "records": [
                    {
                        "kind": "relation-container",
                        "parent_ref": 100,
                        "children": [
                            {"relation": 183, "child_ref": 200},
                            {"relation": 181, "child_ref": 559},
                            {"relation": 190, "child_ref": 501},
                        ],
                    }
                ]
            },
            {100: "Sheet6"},
            {},
            {200: "PIPE"},
            set(),
            set(),
            set(),
            {559},
            {501},
            set(),
            set(),
            set(),
            set(),
            set(),
        )
        self.assertEqual(result["edge_count"], 3)
        self.assertEqual(result["patterns"], [
            {
                "relation_code": 181,
                "parent_namespace": "physical-sheet-local-root",
                "target_namespace": "jsite-resource",
                "edge_count": 1,
            },
            {
                "relation_code": 183,
                "parent_namespace": "physical-sheet-local-root",
                "target_namespace": "named-layer-object",
                "edge_count": 1,
            },
            {
                "relation_code": 190,
                "parent_namespace": "physical-sheet-local-root",
                "target_namespace": "dynamic-attribute-ref-0089",
                "edge_count": 1,
            },
        ])

    def test_mixed_relation_low_local_ref_is_not_promoted_to_zero_based_segment_address(self) -> None:
        result = summarize_mixed_relation_parent_target_namespaces(
            {
                "records": [
                    {
                        "kind": "relation-container",
                        "parent_ref": 1,
                        "children": [
                            {"relation": 190, "child_ref": 0x0100},
                            {"relation": 190, "child_ref": 0x8002},
                        ],
                    }
                ]
            },
            {}, {}, {}, set(), set(), set(), set(), set(), set(), set(), set(), {0, 0x8000}, set(),
        )
        self.assertEqual(result["patterns"], [
            {
                "relation_code": 190,
                "parent_namespace": "unclassified-parent",
                "target_namespace": "psm-spacemap-segment-address",
                "edge_count": 1,
            },
            {
                "relation_code": 190,
                "parent_namespace": "unclassified-parent",
                "target_namespace": "unclassified-target",
                "edge_count": 1,
            },
        ])

    def test_mixed_relation_accepts_jsite_placement_primitive_as_sheet_local_ref(self) -> None:
        result = summarize_mixed_relation_parent_target_namespaces(
            {
                "records": [
                    {
                        "kind": "relation-container",
                        "parent_ref": 1025,
                        "children": [{"relation": 201, "child_ref": 1080}],
                    }
                ]
            },
            {}, {}, {}, set(), set(), set(), set(), set(), set(), set(), {1080}, set(), set(),
        )
        self.assertEqual(result["patterns"], [{
            "relation_code": 201,
            "parent_namespace": "unclassified-parent",
            "target_namespace": "decoded-sheet-local-primitive-ref",
            "edge_count": 1,
        }])

    def test_composite_child_backlink_resolves_same_sheet_line_graphic(self) -> None:
        line = bytearray(
            b"\x18\x00\x32\x00\x00\x00"
            + (10).to_bytes(4, "little")
            + (77).to_bytes(4, "little")
            + b"\x00" * 42
        )
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (77).to_bytes(4, "little") + b"\x00" * 42)
        struct.pack_into("<I", composite, 22, 1)
        struct.pack_into("<I5H", composite, 34, 10, 1, 2, 3, 4, 5)
        result = summarize_7b_composite_child_graphic_links(bytes(line + composite))
        self.assertEqual(result["linked_target_family_counts"], {"18_32_line": 1})
        self.assertEqual(result["parent_graphic_ref_exact_match_count"], 1)
        self.assertEqual(result["linked_target_family_counts_by_raw_type"], {"type_5->18_32_line": 1})

    def test_composite_child_backlink_resolves_same_sheet_ellipse_graphic(self) -> None:
        ellipse = bytearray(
            b"\x59\x00\x2b\x00\x00\x00"
            + (10).to_bytes(4, "little")
            + (77).to_bytes(4, "little")
            + b"\x00" * 35
        )
        struct.pack_into("<3d", ellipse, 24, 0.1, 0.2, 0.01)
        composite = bytearray(b"\x7b\x00" + (46).to_bytes(4, "little") + (77).to_bytes(4, "little") + b"\x00" * 42)
        struct.pack_into("<I", composite, 22, 1)
        struct.pack_into("<I5H", composite, 34, 10, 1, 2, 3, 4, 6)
        # 0x7B composite headers are uint16-aligned in real Shape2D Sheets.
        result = summarize_7b_composite_child_graphic_links(bytes(ellipse + b"\x00" + composite))
        self.assertEqual(result["linked_target_family_counts"], {"59_2b_ellipse": 1})
        self.assertEqual(result["parent_graphic_ref_exact_match_count"], 1)
        self.assertEqual(result["linked_target_family_counts_by_raw_type"], {"type_6->59_2b_ellipse": 1})

    def test_composite_range_inventories_direct_non_range_member(self) -> None:
        line = bytearray(
            b"\x18\x00\x32\x00\x00\x00"
            + (10).to_bytes(4, "little")
            + (77).to_bytes(4, "little")
            + b"\x00" * 42
        )
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        composite = bytearray(b"\x7b\x00" + (60).to_bytes(4, "little") + (77).to_bytes(4, "little") + b"\x00" * 56)
        struct.pack_into("<I", composite, 22, 2)
        struct.pack_into("<I5H", composite, 34, 11, 1, 2, 3, 4, 0)
        struct.pack_into("<I5H", composite, 48, 10, 1, 2, 3, 4, 5)
        result = summarize_7b_composite_child_graphic_links(bytes(line + composite))
        self.assertEqual(result["range_direct_non_range_member_count_distribution"], {"1": 1})
        self.assertEqual(result["range_exact_direct_non_range_envelope_count"], 1)

    def test_root_style_librarian_links_to_stylecluster_directory_index(self) -> None:
        roots = {"entries": [{"name": "StyleLibrarian", "root_ref": 1}]}
        clusters = {"entries": [{"directory_index": 1, "name": "StyleCluster", "child_names": ["StyleCluster"]}]}
        link_psm_root_directory_entries(roots, clusters)
        self.assertEqual(
            roots["entries"][0]["cluster_directory_entry_matches"],
            [{"directory_index": 1, "stream_name": "StyleCluster", "child_names": ["StyleCluster"]}],
        )

    def test_top_vfset_short_record_has_bounded_root_object_field(self) -> None:
        record = b"\x67\x00\x14\x00\x00\x00" + struct.pack("<5I", 0x10BB, 0, 0, 3, 0)
        result = parse_psmcluster_top_vfset_records(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["object_ref"], 0x10BB)
        self.assertEqual(result["records"][0]["role_raw"], 3)

    def test_counted_relation_201_binds_dynamic_graphic_to_direct_sheet_line(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (10).to_bytes(4, "little") + (77).to_bytes(4, "little") + b"\x00" * 42)
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = classify_counted_relation_201_dynamic_graphic_bindings(
            {"Sheet6": bytes(line)},
            [{"children": [{"relation": 201, "ref": 77}]}],
            {77},
        )
        self.assertEqual(result["relation_201_dynamic_graphic_edge_count"], 1)
        self.assertEqual(result["direct_sheet_graphic_match_count"], 1)
        self.assertEqual(result["direct_sheet_family_counts"], {"18_32_line": 1})

    def test_counted_relation_201_binds_dynamic_graphic_to_text_secondary_ref(self) -> None:
        text = "E 1".encode("utf-16le")
        record_length = 60 + len(text)
        record = bytearray(b"\x4d\x00" + record_length.to_bytes(4, "little") + (10).to_bytes(4, "little") + (77).to_bytes(4, "little") + b"\x00" * (record_length - 8))
        struct.pack_into("<H", record, 28, 3)
        record[30:36] = text
        struct.pack_into("<4d", record, 36, 0.1, 0.2, 1.0, 0.0)
        result = classify_counted_relation_201_dynamic_graphic_bindings(
            {"Sheet6": bytes(record)},
            [{"children": [{"relation": 201, "ref": 77}]}],
            {77},
        )
        self.assertEqual(result["direct_sheet_graphic_match_count"], 1)
        self.assertEqual(result["direct_sheet_family_counts"], {"4d_text_secondary_ref": 1})

    def test_tag_provenance_returns_every_family_without_sheet_records(self) -> None:
        # Three adjacent bounded envelopes are enough to form one conservative run.
        record = (1).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
        record += (2).to_bytes(2, "little") + (2).to_bytes(2, "little") + (0).to_bytes(2, "little")
        result = psm_envelope_tag_provenance({"PSMcluster0": record * 3}, parse_psmcluster_envelope_runs(record * 3))
        self.assertEqual(set(result["families"]), {
            "18_32_line_graphic_ref",
            "4d_text_child_ref",
            "59_2b_ellipse_graphic_ref",
            "61_arc_graphic_ref",
            "7b_composite_ref",
        })

    def test_lw_names_are_bounded_catalog_entries(self) -> None:
        data = (
            (0).to_bytes(4, "little")
            + (0x33).to_bytes(4, "little")
            + (0x244A).to_bytes(4, "little")
            + (0).to_bytes(4, "little") * 2
            + (1).to_bytes(4, "little")
            + (0).to_bytes(2, "little")
            + (7).to_bytes(4, "little")
            + "LW3.5C5".encode("utf-16le")
        )
        result = parse_stylecluster_named_style_catalog_entries(data)
        self.assertEqual(result["layout"], "bounded-stylecluster-named-line-style-catalog-entry")
        self.assertEqual(result["records"], [{
            "offset": 0,
            "record_length": 44,
            "catalog_code": 0x33,
            "object_ref": 0x244A,
            "catalog_type": 1,
            "catalog_name": "LW3.5C5",
        }])

    def test_polygon_resource_and_two_member_group_are_bounded(self) -> None:
        points = [(0.01, 0.02), (-0.01, 0.02), (-0.01, -0.02), (0.01, -0.02), (0.01, 0.02)]
        polygon = (
            b"\x84\x00\x68\x00\x00\x00"
            + (8231).to_bytes(4, "little")
            + b"\x00" * 14
            + (5).to_bytes(4, "little")
            + (0x0102).to_bytes(2, "little")
            + b"".join(struct.pack("<2d", *point) for point in points)
        )
        triangle_points = [(0.0, 0.02), (-0.01, 0.0), (0.01, 0.0), (0.0, 0.02)]
        triangle = (
            b"\x84\x00\x58\x00\x00\x00"
            + (8301).to_bytes(4, "little")
            + b"\x00" * 14
            + (4).to_bytes(4, "little")
            + (0x0102).to_bytes(2, "little")
            + b"".join(struct.pack("<2d", *point) for point in triangle_points)
        )
        group = (
            b"\x7c\x00\x18\x00\x00\x00"
            + (8230).to_bytes(4, "little")
            + b"\x00" * 8
            + (2).to_bytes(4, "little")
            + (8231).to_bytes(4, "little")
            + (8232).to_bytes(4, "little")
        )
        polygons = parse_stylecluster_84_polygon_resources(polygon + triangle)
        groups = parse_stylecluster_7c_polygon_groups(group)
        self.assertEqual(polygons["record_count"], 2)
        self.assertTrue(polygons["records"][0]["closed"])
        self.assertEqual(polygons["records"][0]["local_style_or_flags_raw"], 5)
        self.assertEqual(len(polygons["records"][1]["points"]), 4)
        self.assertEqual(groups["records"], [{
            "offset": 0,
            "record_length": 24,
            "object_ref": 8230,
            "child_count": 2,
            "child_refs": [8231, 8232],
        }])

    def test_named_internal_style_keeps_raw_slots_and_catalog_backlink(self) -> None:
        object_ref = 8831
        record = bytearray(b"\x1b\x00\xca\x00\x00\x00" + object_ref.to_bytes(4, "little") + b"\x00" * 198)
        record[26:30] = (1).to_bytes(4, "little")
        record[30:34] = (2).to_bytes(4, "little")
        for offset, value in zip((96, 108, 116, 124, 132), (0.00035, 0.1, 0.1, 0.1, 0.1)):
            record[offset:offset + 8] = struct.pack("<d", value)
        catalog = "Reference".encode("utf-16le") + b"\x00\x00" + b"\x00" * 6 + object_ref.to_bytes(4, "little")
        result = parse_stylecluster_1b_named_internal_style_records(catalog + record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["name_backlinks"], ["Reference"])
        self.assertTrue(result["records"][0]["name_backlink_unambiguous"])
        self.assertEqual(result["records"][0]["category_raw"], 1)

    def test_local_arc_resource_uses_local_not_sheet_coordinates(self) -> None:
        arc = (
            b"\x61\x00\x3b\x00\x00\x00"
            + (8212).to_bytes(4, "little")
            + b"\x00" * 14
            + struct.pack("<5d", 0.002, 0.003, 0.001, 0.5, 2.5)
            + b"\x00"
        )
        result = parse_stylecluster_61_local_arc_resources(arc)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["center"], [0.002, 0.003])
        self.assertEqual(result["records"][0]["coordinate_space"], "StyleCluster-local-template")

    def test_local_ellipse_variant_is_not_a_sheet_binding(self) -> None:
        ellipse = (
            b"\x59\x00\x2b\x00\x00\x00"
            + (8300).to_bytes(4, "little")
            + b"\x00" * 22
            + struct.pack("<2d", -0.00000127, 0.0095)
            + b"\x01"
        )
        result = parse_stylecluster_59_local_ellipse_resources(ellipse)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["object_ref"], 8300)
        self.assertEqual(result["records"][0]["center"], [0.0, -0.00000127])
        self.assertEqual(result["records"][0]["radius"], 0.0095)
        self.assertEqual(result["records"][0]["terminal_flag_raw"], 1)

    def test_18_resource_is_a_local_two_point_line(self) -> None:
        line = (
            b"\x18\x00\x32\x00\x00\x00"
            + (8313).to_bytes(4, "little")
            + b"\x00" * 14
            + struct.pack("<4d", 0.005, 0.08, 0.005, -0.08)
        )
        result = parse_stylecluster_18_control_records(line)
        self.assertEqual(result["layout"], "fixed-0x0018-local-two-point-line-resource")
        self.assertEqual(result["records"][0]["start"], [0.005, 0.08])
        self.assertEqual(result["records"][0]["end"], [0.005, -0.08])

    def test_70_resource_has_a_bounded_font_definition(self) -> None:
        font = "Arial".encode("utf-16le")
        record = bytearray(b"\x70\x00\x5e\x00\x00\x00" + (8305).to_bytes(4, "little") + b"\x00" * 90)
        for offset, value in ((18, 0.03), (26, 0.008), (34, 1.0), (42, 0.0), (74, 0.005)):
            record[offset:offset + 8] = struct.pack("<d", value)
        record[86:90] = (5).to_bytes(4, "little")
        record[90:100] = font
        result = parse_stylecluster_70_fixed_records(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["font_name"], "Arial")
        self.assertEqual(result["records"][0]["local_anchor_raw"], [0.03, 0.008])

    def test_line_style_exposes_verified_width_ratio(self) -> None:
        record = bytearray(b"\x2e\x00\x36\x00\x00\x00" + (8193).to_bytes(4, "little") + b"\x00" * 50)
        record[18:20] = (2).to_bytes(2, "little")
        record[20:24] = (1).to_bytes(4, "little")
        record[32:34] = (6).to_bytes(2, "little")
        record[34:38] = (131072).to_bytes(4, "little")
        record[40:48] = struct.pack("<d", 0.00035)
        result = parse_stylecluster_2e_style_records(record)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["records"][0]["style_ref"], 1)
        self.assertEqual(result["records"][0]["category_raw"], 2)
        self.assertEqual(result["records"][0]["flags_raw"], 6)
        self.assertEqual(result["records"][0]["auxiliary_u32_raw"], 131072)
        self.assertEqual(result["records"][0]["line_width_ratio"], 0.00035)

    def test_tag_zero_container_is_reported_without_flattening_payload(self) -> None:
        child = (
            b"\x00\x00\x18\x00\x00\x00"
            + (8704).to_bytes(4, "little")
            + b"\x00" * 8
            + (1).to_bytes(4, "little")
            + b"\x00" * 8
        )
        parent = (
            b"\x00\x00\x36\x00\x00\x00"
            + (8193).to_bytes(4, "little")
            + b"\x00" * 8
            + (7).to_bytes(4, "little")
            + b"\x00" * 8
            + child
        )
        result = parse_stylecluster_zero_object_containers(parent)
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["records"][0]["nested_payload_offset"], 30)
        self.assertEqual(result["records"][1]["parent_object_ref"], 8193)

    def test_template_bitmap_wrapper_exposes_direct_jsite_id(self) -> None:
        wrapper = bytearray(b"\x3d\x00\xea\x00\x00\x00" + (1484).to_bytes(4, "little") + b"\x00" * 230)
        struct.pack_into("<2d", wrapper, 42, 0.7, 0.08)
        struct.pack_into("<d", wrapper, 66, 0.274)
        struct.pack_into("<2d", wrapper, 82, 0.039, 0.015)
        struct.pack_into("<2d", wrapper, 122, 0.7, 0.08)
        struct.pack_into("<d", wrapper, 138, 0.039)
        struct.pack_into("<d", wrapper, 154, 0.015 / 0.039)
        struct.pack_into("<4d", wrapper, 170, 1.0, 0.0, 0.0, 1.0)
        struct.pack_into("<d", wrapper, 218, 0.274)
        wrapper[162:166] = (1402).to_bytes(4, "little")
        result = parse_sheet221_template_special_records(wrapper)
        self.assertEqual(result["bitmap_placement_wrappers_3d"][0]["jsite_resource_id"], 1402)
        self.assertEqual(result["bitmap_placement_wrappers_3d"][0]["placement_bbox"], [0.7, 0.08, 0.739, 0.095])
        self.assertTrue(result["bitmap_placement_wrappers_3d"][0]["placement_geometry_reconciled"])

    def test_template_page_container_exposes_closed_normalized_path(self) -> None:
        wrapper = bytearray(b"\x84\x00\x68\x00\x00\x00" + (4451).to_bytes(4, "little") + b"\x00" * 100)
        wrapper[10:14] = (221).to_bytes(4, "little")
        wrapper[14:18] = (2434).to_bytes(4, "little")
        wrapper[24:28] = (5).to_bytes(4, "little")
        wrapper[28:30] = (0x0102).to_bytes(2, "little")
        points = [(0.0, 0.6), (0.84, 0.6), (0.84, 0.0), (0.0, 0.0), (0.0, 0.6)]
        for index, point in enumerate(points):
            struct.pack_into("<2d", wrapper, 30 + 16 * index, *point)
        result = parse_sheet221_template_special_records(wrapper)
        path = result["page_container_84"][0]
        self.assertEqual(path["sheet_ref"], 221)
        self.assertTrue(path["closed"])
        self.assertEqual(path["coordinate_space"], "Sheet221-template-normalized")
        self.assertEqual(path["points"], [list(point) for point in points])

    def test_dash_usage_reports_only_validated_primitive_families(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (1).to_bytes(4, "little") + b"\x00" * 46)
        line[20:24] = (231).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = summarize_known_dash_pattern_usage(
            {"Sheet221": bytes(line), "not-a-sheet": bytes(line)},
            [{"record_length": 58, "style_ref": 231, "tail_u32_hex": "0x000000E6"}],
            [{"style_ref": 230}],
        )
        pattern = result["records"][0]
        self.assertEqual(pattern["linked_line_style_refs"], [231])
        self.assertEqual(pattern["validated_primitive_use_count"], 1)
        self.assertEqual(pattern["validated_primitive_uses"][0]["sheet"], "Sheet221")

    def test_fixed_style_usage_keeps_zero_use_as_a_bounded_result(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (1).to_bytes(4, "little") + b"\x00" * 46)
        line[20:24] = (99).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = summarize_known_fixed_style_usage(
            {"Sheet222": bytes(line)},
            [{"style_ref": 3}, {"style_ref": 5}],
        )
        self.assertEqual(result["fixed_style_refs"], [3, 5])
        self.assertEqual(result["validated_primitive_use_count"], 0)

    def test_sheet221_template_index_preserves_line_and_closed_path(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (3242).to_bytes(4, "little") + b"\x00" * 46)
        line[14:18] = (2490).to_bytes(4, "little")
        line[20:24] = (69).to_bytes(4, "little")
        struct.pack_into("<4d", line, 24, 0.5, 0.02, 0.8, 0.02)
        path = bytearray(b"\x84\x00\x68\x00\x00\x00" + (4451).to_bytes(4, "little") + b"\x00" * 100)
        points = [(0.0, 0.6), (0.84, 0.6), (0.84, 0.0), (0.0, 0.0), (0.0, 0.6)]
        for index, point in enumerate(points):
            struct.pack_into("<2d", path, 30 + 16 * index, *point)
        result = index_sheet221_template_primitives({"Sheet221": bytes(line + path)})
        self.assertEqual(result[3242][0]["family"], "18_32_template_line")
        self.assertEqual(result[3242][0]["start"], [0.5, 0.02])
        self.assertEqual(result[4451][0]["family"], "page_container_84")
        self.assertTrue(result[4451][0]["closed"])

    def test_zero_relation_anchor_resolves_sheet221_page_path(self) -> None:
        path = bytearray(b"\x84\x00\x68\x00\x00\x00" + (4451).to_bytes(4, "little") + b"\x00" * 100)
        points = [(0.0, 0.6), (0.84, 0.6), (0.84, 0.0), (0.0, 0.0), (0.0, 0.6)]
        for index, point in enumerate(points):
            struct.pack_into("<2d", path, 30 + 16 * index, *point)
        result = classify_counted_zero_relation_anchor_lists(
            {"Sheet221": bytes(path)},
            [{"zero_relation_list_refs": [4451]}],
            {},
        )
        row = result["records"][0]
        self.assertEqual(row["classification"], "sheet221-template-primitive")
        self.assertEqual(row["sheet221_template_primitives"][0]["family"], "page_container_84")

    def test_template_bitmap_wrapper_links_to_embedded_bmp_descriptor(self) -> None:
        wrapper = bytearray(b"\x3d\x00\xea\x00\x00\x00" + (1484).to_bytes(4, "little") + b"\x00" * 230)
        scale = 0.2743767187350539
        struct.pack_into("<d", wrapper, 66, scale)
        struct.pack_into("<2d", wrapper, 82, 537 / 3780 * scale, 212 / 3780 * scale)
        wrapper[162:166] = (1402).to_bytes(4, "little")
        records = parse_sheet221_template_special_records(wrapper)
        link_sheet221_bitmap_resource_descriptors(
            records,
            [{"resource_id": 1402, "contents_kind": "BMP-DIB", "bmp_pixel_width": 537, "bmp_pixel_height": 212, "bmp_bits_per_pixel": 32, "bmp_x_pixels_per_meter": 3780, "bmp_y_pixels_per_meter": 3780}],
        )
        descriptor = records["bitmap_placement_wrappers_3d"][0]["jsite_resource_descriptor"]
        self.assertEqual(descriptor["contents_kind"], "BMP-DIB")
        self.assertEqual(descriptor["bmp_pixel_width"], 537)
        self.assertTrue(records["bitmap_placement_wrappers_3d"][0]["bmp_physical_scale_reconciled"])

    def test_revision_binding_exposes_proven_xml_contract_and_raw_gap(self) -> None:
        expression = (
            '<?xml version="1.0"?><body><intstgxml stream="Revision" '
            'select="/Revision/RevisionRecord[1+1]/RevisedBy" alt=""/></body>'
        ).encode("utf-16le")
        prefix = b"\x12\x34\x56\x78\x00\x00"
        record = bytearray(b"\x4d\x00\x00\x00\x00\x00" + (633).to_bytes(4, "little") + (221).to_bytes(4, "little") + (4347).to_bytes(4, "little") + b"\x00" * 2 + (228).to_bytes(4, "little") + b"\x00" * 6)
        record += prefix + expression
        while len(record) % 4:
            record += b"\x00"
        transform_offset = len(record)
        record += struct.pack("<4d", 0.75, 0.14, 1.0, 0.0)
        struct.pack_into("<I", record, 2, len(record) - 6)
        result = parse_sheet221_template_text_records(bytes(record))
        binding = result["revision_binding_records"][0]
        self.assertEqual(binding["binding_stream"], "Revision")
        self.assertEqual(binding["binding_select"], "/Revision/RevisionRecord[1+1]/RevisedBy")
        self.assertEqual(binding["binding_alt"], "")
        self.assertEqual(binding["binding_xml_prefix_hex"], prefix.hex())
        self.assertEqual(binding["binding_xml_prefix_byte_length"], len(prefix))
        self.assertEqual(binding["binding_xml_prefix_u16le"], [0x3412, 0x7856, 0])
        self.assertEqual(binding["transform_offset"], transform_offset)

    def test_revision_binding_resolves_first_and_last_row_selector_forms(self) -> None:
        def binding(select: str) -> bytes:
            expression = (
                '<?xml version="1.0"?><body><intstgxml stream="Revision" '
                f'select="{select}" alt=""/></body>'
            ).encode("utf-16le")
            record = bytearray(b"\x4d\x00\x00\x00\x00\x00" + b"\x00" * 24)
            # The real binding form aligns the XML terminator and tail
            # transform to the same four-byte scan phase.
            record += b"\x00" * ((-len(record) - len(expression)) % 4)
            record += expression
            record += struct.pack("<4d", 0.75, 0.14, 1.0, 0.0)
            struct.pack_into("<I", record, 2, len(record) - 6)
            return bytes(record)

        template = binding("/Revision/RevisionRecord[1+1]/RevisedBy") + binding(
            "/Revision/RevisionRecord[last()-0]/RevisionDescription"
        )
        revision = (
            b"<Revision><RevisionRecord><RevisedBy>A</RevisedBy></RevisionRecord>"
            b"<RevisionRecord><RevisedBy>B</RevisedBy><RevisionDescription>IFC</RevisionDescription>"
            b"</RevisionRecord></Revision>"
        )
        result = resolve_sheet221_revision_bindings(template, revision)
        self.assertEqual(result["resolved_binding_count"], 2)
        self.assertEqual(result["records"][0]["resolved_value"], "B")
        self.assertEqual(result["records"][0]["revision_row_index_zero_based"], 1)
        self.assertEqual(result["records"][1]["resolved_value"], "IFC")

    def test_revision_binding_uses_explicit_alt_for_unpopulated_history_row(self) -> None:
        expression = (
            '<?xml version="1.0"?><body><intstgxml stream="Revision" '
            'select="/Revision/RevisionRecord[1+1]/RevisedBy" alt=""/></body>'
        ).encode("utf-16le")
        record = bytearray(b"\x4d\x00\x00\x00\x00\x00" + b"\x00" * 24)
        record += b"\x00" * ((-len(record) - len(expression)) % 4)
        record += expression + struct.pack("<4d", 0.75, 0.14, 1.0, 0.0)
        struct.pack_into("<I", record, 2, len(record) - 6)
        revision = b"<Revision><RevisionRecord><RevisedBy>A</RevisedBy></RevisionRecord></Revision>"
        result = resolve_sheet221_revision_bindings(bytes(record), revision)
        binding = result["records"][0]
        self.assertEqual(binding["resolution_status"], "resolved-alt-row-out-of-range")
        self.assertEqual(binding["resolved_value"], "")

    def test_local_style_resource_is_not_inferred_without_a_direct_sheet_slot_match(self) -> None:
        line = bytearray(b"\x18\x00\x32\x00\x00\x00" + (101).to_bytes(4, "little") + b"\x00" * 46)
        struct.pack_into("<4d", line, 24, 0.1, 0.2, 0.3, 0.2)
        result = summarize_stylecluster_local_resource_sheet_references(
            {"Sheet6": bytes(line) + b"\x00" * 1024},
            {"local-line-0x0018": [{"object_ref": 8313}]},
        )
        self.assertEqual(result["physical_sheet_count"], 1)
        self.assertEqual(result["records"][0]["known_sheet_reference_match_count"], 0)

    def test_jsite_oles_property_preserves_short_utf16_identifier_without_naming_it(self) -> None:
        properties = b"OLES\x00\x00" + (3).to_bytes(2, "little") + (4).to_bytes(2, "little") + "221\x00".encode("utf-16le")
        result = jsite_resource_inventory(
            {"JSite559/JProperties": properties}, [559]
        )[0]
        self.assertEqual(result["jproperties_layout"], "bounded-OLES-single-utf16-property")
        self.assertEqual(result["jproperties_property_code"], 3)
        self.assertEqual(result["jproperties_utf16_value"], "221")

    def test_physical_sheet_3d_wrapper_preserves_direct_contentless_jsite_placement(self) -> None:
        wrapper = bytearray(b"\x3d\x00\xea\x00\x00\x00" + (398).to_bytes(4, "little") + (6).to_bytes(4, "little") + (8).to_bytes(4, "little") + b"\x00" * 222)
        struct.pack_into("<2d", wrapper, 42, 0.0, 0.0)
        struct.pack_into("<2d", wrapper, 82, 0.841, 0.594)
        wrapper[162:166] = (559).to_bytes(4, "little")
        records = parse_sheet_3d_placement_wrappers(bytes(wrapper))
        link_sheet_3d_resource_descriptors(
            records,
            [{"resource_id": 559, "contents_kind": "no-embedded-contents", "jproperties_utf16_value": "221"}],
        )
        self.assertEqual(records[0]["sheet_ref"], 6)
        self.assertEqual(records[0]["jsite_resource_id"], 559)
        self.assertEqual(records[0]["placement_size"], [0.841, 0.594])
        self.assertEqual(records[0]["jsite_resource_descriptor"]["contents_kind"], "no-embedded-contents")

    def test_contentless_jsite_oles_value_resolves_to_existing_sheet_template(self) -> None:
        wrappers = [{"jsite_resource_id": 559}]
        link_contentless_jsite_sheet_templates(
            wrappers,
            [{
                "resource_id": 559,
                "contents_kind": "no-embedded-contents",
                "jproperties_utf16_value": "221",
            }],
            {"Sheet221": b"template"},
        )
        self.assertEqual(wrappers[0]["contentless_jsite_template_sheet_stream"], "Sheet221")
        self.assertTrue(wrappers[0]["contentless_jsite_template_reference_validated"])

    def test_13_ac_relation_requires_fixed_length_and_exposes_unaligned_line_bounds(self) -> None:
        record = bytearray(b"\x13\x00\xac\x00\x00\x00" + (600).to_bytes(4, "little") + (591).to_bytes(4, "little") + (1414).to_bytes(4, "little") + b"\x00" * 160)
        struct.pack_into("<I", record, 2, 172)
        for start, values in ((35, (0.1495, 0.4975, 0.1509, 0.4995)), (68, (0.1509, 0.4995, 0.1489, 0.4981)), (101, (0.1489, 0.4981, 0.1495, 0.4975))):
            record[start - 1] = 0x67
            struct.pack_into("<4d", record, start, *values)
        struct.pack_into("<2d", record, 133, 0.1497, 0.49815)
        record[149] = 1
        struct.pack_into("<I", record, 150, 3)
        for index, child_ref in enumerate((602, 603, 604)):
            struct.pack_into("<IHH", record, 154 + index * 8, child_ref, 203, 1 if index == 0 else 0)
        rows = parse_13_ac_layer_relations(bytes(record))
        self.assertEqual(rows[0]["primitive_ref"], 600)
        self.assertEqual(rows[0]["bounding_box"], [0.1489, 0.4975, 0.1509, 0.4995])
        self.assertEqual(rows[0]["start"], [0.1495, 0.4975])
        self.assertEqual(rows[0]["end"], [0.1509, 0.4995])
        self.assertEqual(rows[0]["header_field_20"], 0)
        self.assertEqual(rows[0]["format_flags_32"], 0)
        self.assertEqual(rows[0]["format_marker_34"], 0x67)
        self.assertEqual(rows[0]["member_child_refs"], [602, 603, 604])
        self.assertEqual(rows[0]["member_flags"], [1, 0, 0])
        self.assertEqual(rows[0]["anchor"], [0.1497, 0.49815])

        # Endpoint direction is meaningful; a reverse line remains valid.
        struct.pack_into("<4d", record, 35, 0.1509, 0.4995, 0.1495, 0.4975)
        reversed_rows = parse_13_ac_layer_relations(bytes(record))
        self.assertEqual(reversed_rows[0]["start"], [0.1509, 0.4995])
        self.assertEqual(reversed_rows[0]["bounding_box"], [0.1489, 0.4975, 0.1509, 0.4995])

    def test_13_ac_segments_are_reported_as_reverse_line_aliases(self) -> None:
        relation = bytearray(b"\x13\x00\xac\x00\x00\x00" + (600).to_bytes(4, "little") + (591).to_bytes(4, "little") + (1414).to_bytes(4, "little") + b"\x00" * 160)
        struct.pack_into("<I", relation, 2, 172)
        segments = ((0.1, 0.2, 0.2, 0.2), (0.2, 0.2, 0.2, 0.3), (0.2, 0.3, 0.1, 0.2))
        for index, (start, values) in enumerate(zip((35, 68, 101), segments)):
            relation[start - 1] = 0x67
            struct.pack_into("<4d", relation, start, *values)
            struct.pack_into("<IHH", relation, 154 + index * 8, 602 + index, 203, 1 if index == 0 else 0)
        struct.pack_into("<2d", relation, 133, 0.15, 0.25)
        relation[149] = 1
        struct.pack_into("<I", relation, 150, 3)
        lines = bytearray()
        for child_ref, segment in zip((602, 603, 604), segments):
            line = bytearray(b"\x18\x00\x32\x00\x00\x00" + child_ref.to_bytes(4, "little") + (591).to_bytes(4, "little") + b"\x00" * 42)
            struct.pack_into("<4d", line, 24, segment[2], segment[3], segment[0], segment[1])
            lines.extend(line)
        result = validate_13_ac_reverse_line_aliases(bytes(relation + lines))
        self.assertEqual(result["reverse_18_32_line_match_count"], 3)
        self.assertEqual(result["forward_18_32_line_match_count"], 0)
        self.assertEqual(result["member_flag_counts"], {"0": 2, "1": 1})

    def test_13_63_circle_companion_has_one_relation_209_ellipse_child(self) -> None:
        record = bytearray(b"\x13\x00\x63\x00\x00\x00" + (601).to_bytes(4, "little") + (600).to_bytes(4, "little") + (1414).to_bytes(4, "little") + b"\x00" * 87)
        struct.pack_into("<I", record, 2, 99)
        record[34] = 0x73
        struct.pack_into("<5d", record, 35, 0.25, 0.4, 0.001, 0.0, 1.0)
        record[75] = 1
        struct.pack_into("<2d", record, 76, 0.25, 0.4)
        record[92] = 1
        struct.pack_into("<I", record, 93, 1)
        struct.pack_into("<IHH", record, 97, 599, 209, 5)
        rows = parse_13_63_circle_geometry(bytes(record))
        self.assertEqual(rows[0]["end_angle"], 1.0)
        self.assertEqual(rows[0]["anchor"], [0.25, 0.4])
        self.assertEqual(rows[0]["member_child_ref"], 599)
        self.assertEqual(rows[0]["member_relation_code"], 209)

    def test_59_2b_ellipse_record_has_native_radius(self) -> None:
        record = bytearray(b"\x59\x00\x2b\x00\x00\x00" + (599).to_bytes(4, "little") + (600).to_bytes(4, "little") + (1414).to_bytes(4, "little") + (280).to_bytes(4, "little") + b"\x00" * 27)
        struct.pack_into("<I", record, 2, 43)
        struct.pack_into("<3d", record, 24, 0.25, 0.4, 0.001)
        rows = parse_59_2b_page_layer_bindings(bytes(record))
        self.assertEqual(rows[0]["radius"], 0.001)

    def test_18_32_line_requires_fixed_50_byte_payload(self) -> None:
        record = bytearray(b"\x18\x00\x32\x00\x00\x00" + (602).to_bytes(4, "little") + (600).to_bytes(4, "little") + (1414).to_bytes(4, "little") + (239).to_bytes(4, "little") + b"\x00" * 34)
        struct.pack_into("<I", record, 2, 50)
        struct.pack_into("<4d", record, 24, 0.1, 0.2, 0.3, 0.4)
        self.assertEqual(parse_18_32_layer_bindings(bytes(record))[0]["child_ref"], 602)
        struct.pack_into("<I", record, 2, 49)
        self.assertEqual(parse_18_32_layer_bindings(bytes(record)), [])

    def test_4d_text_exposes_terminal_flag_after_transform(self) -> None:
        record = bytearray(70)
        record[:2] = b"\x4d\x00"
        struct.pack_into("<I", record, 2, 64)
        struct.pack_into("<4I", record, 6, 602, 600, 1414, 244)
        struct.pack_into("<H", record, 28, 2)
        record[30:34] = "Hi".encode("utf-16le")
        struct.pack_into("<4d", record, 34, 0.1, 0.2, 1.0, 0.0)
        struct.pack_into("<I", record, 66, 1)
        rows = parse_4d_text_layer_bindings(bytes(record))
        self.assertEqual(rows[0]["text"], "Hi")
        self.assertEqual(rows[0]["tail_flags_u32"], 1)

    def test_prefixed_tseg_accepts_long_index_and_terminal_zero_padding(self) -> None:
        # One uint16 prefix entry, then one no-child node and the six-byte
        # terminal padding seen in the physical 0x0000 map variant.
        data = b"tseg" + struct.pack("<4H", 1, 10, 99, 1) + struct.pack("<H", 7)
        data += struct.pack("<4H", 12, 3, 0, 0) + b"\0" * 6
        parsed = parse_prefixed_tseg_nodes(data)
        self.assertEqual(parsed["node_count"], 1)
        self.assertEqual(parsed["prefix_uint16_values"], [7])
        self.assertEqual(parsed["trailing_zero_padding_length"], 6)
        self.assertTrue(parsed["fully_consumed"])

    def test_prefixed_zero_relation_semantics_remain_scope_bound(self) -> None:
        nodes = [
            {"id": 1, "children": [{"relation": 190, "ref": 0x6000}, {"relation": 184, "ref": 7}]},
            {"id": 7, "children": [{"relation": 184, "ref": 99}]},
        ]
        evidence = {"relation_target_categories": {
            "190": {"total": 1, "dynamic_attribute_ref_0089": 1},
            "184": {"total": 2, "dynamic_attribute_ref_0089": 0},
        }}
        result = summarize_prefixed_zero_relation_semantics(nodes, evidence)
        self.assertEqual(result["190"]["classification"], "dynamic-attribute-route")
        self.assertEqual(result["184"]["classification"], "shared-internal-terminal-anchor-route")
        self.assertEqual(result["184"]["terminal_ref_counts"], {"99": 2})
        self.assertEqual(result["190"]["scope"], "prefixed-PSMspacemap-0x0000-only")

    def test_prefixed_zero_relation_201_has_no_geometry_without_sheet_companions(self) -> None:
        result = classify_prefixed_zero_relation_201_geometry_companions(
            {}, [{"children": [{"relation": 201, "ref": 42}]}]
        )
        self.assertEqual(result["relation_201_target_count"], 1)
        self.assertEqual(result["range_companion_match_count"], 0)
        self.assertEqual(result["range_companion_all_member_lines_validated_count"], 0)
        self.assertEqual(result["circle_companion_match_count"], 0)
        self.assertEqual(result["circle_companion_ellipse_adjacent_primitive_count"], 0)


if __name__ == "__main__":
    unittest.main()
