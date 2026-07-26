import tempfile
import unittest
from pathlib import Path

from app_server import Workspace, safe_name


class WorkspaceTests(unittest.TestCase):
    def test_safe_name_is_stable_and_path_safe(self) -> None:
        self.assertEqual(safe_name("  N400 / Test Project  "), "N400-Test-Project")

    def test_import_keeps_a_hashed_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            project = workspace.create_project("demo")
            source = workspace.import_file(str(project["id"]), "line.pcf", b"PIPELINE-REFERENCE L-1\n")
            path = workspace.source_path(str(project["id"]), source)
            self.assertEqual(path.read_bytes(), b"PIPELINE-REFERENCE L-1\n")
            self.assertEqual(len(str(source["sha256"])), 64)
            self.assertEqual(workspace.read_project(str(project["id"]))["sources"][0]["id"], source["id"])

    def test_rejects_non_engineering_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            project = workspace.create_project("demo")
            with self.assertRaises(ValueError):
                workspace.import_file(str(project["id"]), "drawing.pdf", b"not allowed")

    def test_pairing_prefers_shared_uci_evidence(self) -> None:
        pairs = [
            {"pcf_source_id": "pcf", "sha_source_id": "wrong", "link_coverage": {"direct_uci_links": 0}},
            {"pcf_source_id": "pcf", "sha_source_id": "right", "link_coverage": {"direct_uci_links": 12}},
        ]
        Workspace.mark_recommended_pairs(pairs, [
            {"id": "pcf", "display_name": "N400P3A-AMSS2-N444201-01-pcf.pcf"},
            {"id": "wrong", "display_name": "different-line.sha"},
            {"id": "right", "display_name": "N400P3A-AMSS2-N444201-01-0.sha"},
        ])
        self.assertEqual(pairs[1]["pairing_confidence"], "recommended")
        self.assertEqual(pairs[0]["pairing_confidence"], "candidate")


if __name__ == "__main__":
    unittest.main()
