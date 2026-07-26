#!/usr/bin/env python3
"""Local-first PCF + SHA ISO analysis workspace.

This first-stage application intentionally keeps imported sources immutable.
It copies browser-uploaded PCF/SHA files into a project workspace, records their
SHA-256 hashes, and only writes derived reports, SVGs, and trace manifests.

Run with:
    python3 app_server.py
Then open http://127.0.0.1:8765 in a browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

from analyze_iso_split import dynamic_graphics, pcf_components, read_sha_streams, report
from analyze_sha_pages import inspect as inspect_sha
from run_sha_iso_render import available_pages
from sha_to_svg_prototype import render


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_DATA_ROOT = ROOT / "app_data"
ALLOWED_EXTENSIONS = {".pcf", ".sha"}
_ANALYSIS_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    """Return a file-system safe display name without trusting a client path."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return cleaned[:80] or "project"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Workspace:
    """Project metadata and derived-artifact manager."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        return self.root / "projects" / project_id

    def metadata_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def create_project(self, name: str) -> dict[str, object]:
        project_id = f"{safe_name(name)}-{uuid.uuid4().hex[:8]}"
        directory = self.project_dir(project_id)
        (directory / "sources").mkdir(parents=True)
        (directory / "derived").mkdir()
        project = {
            "id": project_id,
            "name": name.strip() or "Untitled ISO project",
            "created_at": now(),
            "updated_at": now(),
            "sources": [],
            "analysis": None,
        }
        self.write_project(project)
        return project

    def read_project(self, project_id: str) -> dict[str, object]:
        path = self.metadata_path(project_id)
        if not path.is_file():
            raise FileNotFoundError("Project was not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def write_project(self, project: dict[str, object]) -> None:
        project["updated_at"] = now()
        self.metadata_path(str(project["id"])).write_text(
            json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def list_projects(self) -> list[dict[str, object]]:
        projects_root = self.root / "projects"
        if not projects_root.is_dir():
            return []
        projects = []
        for metadata in projects_root.glob("*/project.json"):
            project = json.loads(metadata.read_text(encoding="utf-8"))
            projects.append(
                {
                    "id": project["id"],
                    "name": project["name"],
                    "updated_at": project["updated_at"],
                    "source_count": len(project.get("sources", [])),
                    "has_analysis": project.get("analysis") is not None,
                }
            )
        return sorted(projects, key=lambda item: str(item["updated_at"]), reverse=True)

    def import_file(self, project_id: str, upload_name: str, body: bytes) -> dict[str, object]:
        filename = Path(upload_name).name
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError("Only .pcf and .sha source files are accepted")
        if not body:
            raise ValueError("The uploaded file is empty")
        project = self.read_project(project_id)
        source_id = uuid.uuid4().hex
        stored_name = f"{source_id}{extension}"
        target = self.project_dir(project_id) / "sources" / stored_name
        target.write_bytes(body)
        record = {
            "id": source_id,
            "display_name": filename,
            "stored_name": stored_name,
            "kind": extension[1:].upper(),
            "bytes": len(body),
            "sha256": sha256(target),
            "imported_at": now(),
        }
        sources = list(project.get("sources", []))
        sources.append(record)
        project["sources"] = sources
        project["analysis"] = None
        self.write_project(project)
        return record

    def source_path(self, project_id: str, source: dict[str, object]) -> Path:
        return self.project_dir(project_id) / "sources" / str(source["stored_name"])

    def analyze(self, project_id: str) -> dict[str, object]:
        """Run only SHA/PCF-derived analysis and store regenerated artifacts."""

        with _ANALYSIS_LOCK:
            project = self.read_project(project_id)
            sources = list(project.get("sources", []))
            pcf_sources = [item for item in sources if item["kind"] == "PCF"]
            sha_sources = [item for item in sources if item["kind"] == "SHA"]
            if not sha_sources:
                raise ValueError("Import at least one SHA file before analysis")

            derived_root = self.project_dir(project_id) / "derived"
            if derived_root.exists():
                shutil.rmtree(derived_root)
            derived_root.mkdir()

            sha_reports: list[dict[str, object]] = []
            for sha_source in sha_sources:
                sha_path = self.source_path(project_id, sha_source)
                streams = read_sha_streams(sha_path)
                dynamic = dynamic_graphics(streams.get("Unclustered Dynamic Attributes", b""))
                pages = available_pages(sha_path)
                sha_id = str(sha_source["id"])
                artifact_dir = derived_root / sha_id
                artifact_dir.mkdir()
                rendered_pages: list[dict[str, object]] = []
                for page in pages:
                    svg_name = f"page-{page}.svg"
                    trace_name = f"page-{page}.trace.json"
                    render(
                        sha_path,
                        artifact_dir / svg_name,
                        page,
                        False,
                        artifact_dir / trace_name,
                        None,
                    )
                    trace = json.loads((artifact_dir / trace_name).read_text(encoding="utf-8"))
                    rendered_pages.append(
                        {
                            "page": page,
                            "sheet_stream": trace["sheet_stream"],
                            "svg": f"derived/{sha_id}/{svg_name}",
                            "trace": f"derived/{sha_id}/{trace_name}",
                            "graphic_regions": len(trace.get("uci_regions", [])),
                            "segments": len(trace.get("segments", [])),
                            "text": len(trace.get("text", [])),
                        }
                    )
                inventory = inspect_sha(sha_path)
                (artifact_dir / "sha-page-report.txt").write_text(inventory, encoding="utf-8")
                sha_reports.append(
                    {
                        "source_id": sha_id,
                        "display_name": sha_source["display_name"],
                        "logical_pages": len(pages),
                        "page_numbers": pages,
                        "stream_count": len(streams),
                        "uci_count": len(dynamic),
                        "dynamic_graphic_count": sum(len(records) for records in dynamic.values()),
                        "has_psm_cluster": "PSMcluster0" in streams,
                        "psm_spacemaps": sorted(name for name in streams if name.startswith("PSMspacemap/")),
                        "pages": rendered_pages,
                        "inventory": f"derived/{sha_id}/sha-page-report.txt",
                    }
                )

            pcf_reports: list[dict[str, object]] = []
            pair_reports: list[dict[str, object]] = []
            for pcf_source in pcf_sources:
                pcf_path = self.source_path(project_id, pcf_source)
                pipeline, components = pcf_components(pcf_path)
                pcf_reports.append(
                    {
                        "source_id": pcf_source["id"],
                        "display_name": pcf_source["display_name"],
                        "pipeline": pipeline,
                        "component_count": len(components),
                        "uci_count": len({str(component["uci"]) for component in components}),
                        "component_kinds": sorted({str(component["kind"]) for component in components}),
                    }
                )
                for sha_source in sha_sources:
                    sha_path = self.source_path(project_id, sha_source)
                    sha_id = str(sha_source["id"])
                    pair_dir = derived_root / sha_id
                    split_name = f"split-{pcf_source['id']}.txt"
                    split_report = report(pcf_path, sha_path)
                    (pair_dir / split_name).write_text(split_report, encoding="utf-8")
                    direct_links = self.direct_links(pcf_path, sha_path)
                    pair_reports.append(
                        {
                            "pcf_source_id": pcf_source["id"],
                            "sha_source_id": sha_source["id"],
                            "pipeline": pipeline,
                            "link_coverage": direct_links,
                            "split_report": f"derived/{sha_id}/{split_name}",
                            "same_line_interfaces": self.split_candidates(split_report),
                        }
                    )

            self.mark_recommended_pairs(pair_reports, sources)

            analysis = {
                "generated_at": now(),
                "source_hashes_verified": all(
                    sha256(self.source_path(project_id, source)) == source["sha256"] for source in sources
                ),
                "pcf": pcf_reports,
                "sha": sha_reports,
                "pairs": pair_reports,
                "confidence_notice": (
                    "Direct links require matching PCF UCI and SHA dynamic-attribute references. "
                    "Spatial Sheet/PSM associations remain candidate evidence."
                ),
                "unresolved": [
                    "PSM hierarchy relation-code semantics are not decoded.",
                    "External JSite resources without CONTENTS are retained as unresolved dependencies.",
                    "PDF files are not imported as reconstruction input in this stage.",
                ],
            }
            project["analysis"] = analysis
            self.write_project(project)
            return project

    @staticmethod
    def split_candidates(split_report: str) -> list[str]:
        lines = split_report.splitlines()
        try:
            start = lines.index("Candidate same-line page interfaces:") + 1
        except ValueError:
            return []
        return [line.strip() for line in lines[start:] if line.startswith("  (")]

    @staticmethod
    def direct_links(pcf_path: Path, sha_path: Path) -> dict[str, int]:
        _, components = pcf_components(pcf_path)
        dynamic = dynamic_graphics(read_sha_streams(sha_path).get("Unclustered Dynamic Attributes", b""))
        linked = [component for component in components if str(component["uci"]) in dynamic]
        return {
            "pcf_components": len(components),
            "direct_uci_links": len(linked),
            "unlinked_pcf_components": len(components) - len(linked),
        }

    @staticmethod
    def mark_recommended_pairs(
        pairs: list[dict[str, object]], sources: list[dict[str, object]]
    ) -> None:
        """Rank each PCF's SHA candidates without treating a guess as fact."""

        names = {str(source["id"]): str(source["display_name"]) for source in sources}
        by_pcf: dict[str, list[dict[str, object]]] = {}
        for pair in pairs:
            pair["pairing_confidence"] = "candidate"
            by_pcf.setdefault(str(pair["pcf_source_id"]), []).append(pair)
        for pcf_id, candidates in by_pcf.items():
            pcf_stem = Workspace.normalized_stem(names[pcf_id])
            for candidate in candidates:
                sha_stem = Workspace.normalized_stem(names[str(candidate["sha_source_id"])])
                shared = int(dict(candidate["link_coverage"])["direct_uci_links"])
                prefix = len(Path(pcf_stem).name)
                while prefix and not sha_stem.startswith(pcf_stem[:prefix]):
                    prefix -= 1
                candidate["pairing_score"] = shared * 1000 + prefix
            best = max(candidates, key=lambda item: (int(item["pairing_score"]), str(item["sha_source_id"])))
            best["pairing_confidence"] = "recommended" if int(best["pairing_score"]) else "unresolved"

    @staticmethod
    def normalized_stem(filename: str) -> str:
        stem = Path(filename).stem.lower()
        return re.sub(r"(?:[-_.](?:pcf|sha|iso|\d+))+$", "", stem)


class AppHandler(SimpleHTTPRequestHandler):
    workspace: Workspace

    server_version = "PipingIsoWorkspace/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self) -> None:  # noqa: N802
        self.route_get(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self.route_get(send_body=False)

    def route_get(self, send_body: bool) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "time": now()}, send_body=send_body)
            return
        if parsed.path == "/api/projects":
            self.send_json({"projects": self.workspace.list_projects()}, send_body=send_body)
            return
        if parsed.path.startswith("/api/projects/"):
            project_id = parsed.path.removeprefix("/api/projects/").strip("/")
            try:
                self.send_json(self.workspace.read_project(project_id), send_body=send_body)
            except FileNotFoundError as error:
                self.send_error_json(HTTPStatus.NOT_FOUND, str(error))
            return
        if parsed.path.startswith("/projects/"):
            self.serve_artifact(parsed.path, send_body=send_body)
            return
        self.serve_web_asset(parsed.path, send_body=send_body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/projects":
                body = self.read_json()
                self.send_json(self.workspace.create_project(str(body.get("name", ""))), HTTPStatus.CREATED)
                return
            match = re.fullmatch(r"/api/projects/([A-Za-z0-9._-]+)/imports", parsed.path)
            if match:
                name = unquote(self.headers.get("X-Filename", "upload"))
                length = int(self.headers.get("Content-Length", "0"))
                self.send_json(self.workspace.import_file(match.group(1), name, self.rfile.read(length)), HTTPStatus.CREATED)
                return
            match = re.fullmatch(r"/api/projects/([A-Za-z0-9._-]+)/analyze", parsed.path)
            if match:
                self.send_json(self.workspace.analyze(match.group(1)))
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "API endpoint was not found")
        except (FileNotFoundError, ValueError) as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # Keep API failures inspectable without corrupting source files.
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"Analysis failed: {error}")

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK, send_body: bool = True) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if send_body:
            self.wfile.write(payload)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def serve_web_asset(self, request_path: str, send_body: bool = True) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (WEB_ROOT / unquote(relative)).resolve()
        if WEB_ROOT not in target.parents and target != WEB_ROOT:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(target, send_body=send_body)

    def serve_artifact(self, request_path: str, send_body: bool = True) -> None:
        relative = unquote(request_path.removeprefix("/projects/")).lstrip("/")
        target = (self.workspace.root / "projects" / relative).resolve()
        projects_root = (self.workspace.root / "projects").resolve()
        if projects_root not in target.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(target, send_body=send_body)

    def serve_file(self, target: Path, send_body: bool = True) -> None:
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_ROOT, help="Local project workspace directory.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only).")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port.")
    args = parser.parse_args()
    AppHandler.workspace = Workspace(args.data_dir.resolve())
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Piping ISO Workspace: http://{args.host}:{args.port}")
    print(f"Project data: {args.data_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
