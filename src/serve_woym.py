#!/usr/bin/env python3
"""Serve the DAMSpy visualiser pages and analyser APIs over HTTP."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import posixpath
import re
import sys
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


KNOWN_ROUTES = {"/", "/results-analyser", "/results-analyser/"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve DAMSpy VC from the shared parent folder so sibling DAMspy-core assets can be fetched."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on. Default: 8000")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not try to open the browser automatically.",
    )
    return parser.parse_args()


def get_paths() -> tuple[Path, Path, Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    shared_root = repo_root.parent
    index_path = repo_root / "src" / "index.html"
    logs_root = shared_root / "DAMspy-core" / "src" / "DAMspy_logs"
    return repo_root, shared_root, index_path, logs_root


def extended_path(path: Path) -> str:
    resolved = str(path.resolve(strict=False))

    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved

    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]

    return "\\\\?\\" + resolved


def path_exists(path: Path) -> bool:
    return os.path.exists(extended_path(path))


def path_is_dir(path: Path) -> bool:
    return os.path.isdir(extended_path(path))


def path_is_file(path: Path) -> bool:
    return os.path.isfile(extended_path(path))


def iter_directory(path: Path) -> list[os.DirEntry[str]]:
    with os.scandir(extended_path(path)) as entries:
        return list(entries)


def read_json_file(path: Path) -> dict[str, Any]:
    with open(extended_path(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def display_path(path: Path, root: Path) -> str:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        return str(resolved_path)


def read_csv_points(path: Path, axis_key: str) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []

    with open(extended_path(path), "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)

        for row in reader:
            try:
                angle = float(row[axis_key])
                rx_peak_dbm = float(row["rx_peak_dbm"])
            except (KeyError, TypeError, ValueError):
                continue

            point: dict[str, float] = {
                "angle_deg": angle,
                "rx_peak_dbm": rx_peak_dbm,
            }

            try:
                point["peak_freq_hz"] = float(row["peak_freq_hz"])
            except (KeyError, TypeError, ValueError):
                pass

            points.append(point)

    points.sort(key=lambda item: item["angle_deg"])
    return points


def format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def measurement_name_timestamp(value: str) -> float | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", value)

    if match is None:
        return None

    try:
        parsed = datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H-%M-%S")
    except ValueError:
        return None

    return parsed.replace(tzinfo=timezone.utc).timestamp()


def natural_sort_key(value: Any) -> list[Any]:
    text = str(value)
    parts = re.split(r"(\d+)", text)
    key: list[Any] = []

    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())

    return key


def polarisation_sort_key(value: Any) -> tuple[int, str]:
    text = str(value)
    order = {"H": 0, "V": 1}.get(text, 99)
    return order, text


def measurement_manifest(logs_root: Path, measurement_name: str) -> dict[str, Any] | None:
    measurement_dir = logs_root / measurement_name
    yaml_path = measurement_dir / "1_meas_azimuth.yaml"

    if not path_is_dir(measurement_dir) or not path_is_file(yaml_path):
        return None

    updated_at = max(
        os.stat(extended_path(measurement_dir)).st_mtime,
        os.stat(extended_path(yaml_path)).st_mtime,
    )
    measurement_timestamp = measurement_name_timestamp(measurement_name)

    return {
        "measurement_id": measurement_name,
        "measurement_name": measurement_name,
        "yaml_relative_path": f"{measurement_name}/1_meas_azimuth.yaml",
        "updated_at": format_timestamp(updated_at),
        "_updated_at": updated_at,
        "_sort_at": measurement_timestamp if measurement_timestamp is not None else updated_at,
    }


def list_measurements(logs_root: Path) -> list[dict[str, Any]]:
    if not path_is_dir(logs_root):
        return []

    manifests: list[dict[str, Any]] = []

    for entry in iter_directory(logs_root):
        if not entry.is_dir():
            continue

        manifest = measurement_manifest(logs_root, entry.name)
        if manifest is not None:
            manifests.append(manifest)

    manifests.sort(key=lambda item: item["_sort_at"], reverse=True)

    for manifest in manifests:
        manifest.pop("_updated_at", None)
        manifest.pop("_sort_at", None)

    return manifests


def resolve_measurement(logs_root: Path, measurement_id: str) -> tuple[Path, Path, Path]:
    measurement_dir = (logs_root / measurement_id).resolve(strict=False)
    logs_root_resolved = logs_root.resolve(strict=False)

    try:
        measurement_dir.relative_to(logs_root_resolved)
    except ValueError as exc:
        raise ValueError("measurement_id is outside the DAMspy logs root") from exc

    yaml_path = measurement_dir / "1_meas_azimuth.yaml"
    results_dir = measurement_dir / "1_meas_azimuth"
    return measurement_dir, yaml_path, results_dir


def find_first_csv(path: Path) -> Path | None:
    for entry in iter_directory(path):
        if entry.is_file() and entry.name.lower().endswith(".csv"):
            return path / entry.name

    return None


def load_measurement_dataset(logs_root: Path, measurement_id: str) -> dict[str, Any]:
    measurement_dir, yaml_path, results_dir = resolve_measurement(logs_root, measurement_id)
    if not path_is_file(yaml_path):
        raise FileNotFoundError(f"Could not find {yaml_path.name} for {measurement_id}")

    if not path_is_dir(results_dir):
        raise FileNotFoundError(f"Could not find results directory for {measurement_id}")

    folders: list[dict[str, Any]] = []
    plot_groups: dict[tuple[str, str], dict[str, Any]] = {}
    global_peak_dbm: float | None = None
    angle_min: float | None = None
    angle_max: float | None = None
    updated_at = max(
        os.stat(extended_path(measurement_dir)).st_mtime,
        os.stat(extended_path(yaml_path)).st_mtime,
        os.stat(extended_path(results_dir)).st_mtime,
    )

    for entry in iter_directory(results_dir):
        if not entry.is_dir():
            continue

        folder_path = results_dir / entry.name
        metadata_path = folder_path / "metadata.json"

        if not path_is_file(metadata_path):
            continue

        csv_path = find_first_csv(folder_path)
        if csv_path is None:
            continue

        metadata = read_json_file(metadata_path)
        axis_name = str(metadata.get("axis") or "azimuth")
        axis_key = f"{axis_name}_deg"
        points = read_csv_points(csv_path, axis_key)

        if not points:
            continue

        peak_dbm = max(point["rx_peak_dbm"] for point in points)
        global_peak_dbm = peak_dbm if global_peak_dbm is None else max(global_peak_dbm, peak_dbm)

        series_info = metadata.get("sig_gen_1") or {}
        folder_record = {
            "folder_name": entry.name,
            "orientation": metadata.get("orientation") or "unknown",
            "polarisation": metadata.get("polarisation") or "unknown",
            "channel": series_info.get("channel"),
            "power_level": series_info.get("power_level"),
            "frequency_hz": series_info.get("frequency_hz"),
            "peak_dbm": peak_dbm,
            "points": points,
        }
        folders.append(folder_record)

        group_key = (str(folder_record["polarisation"]), str(folder_record["orientation"]))
        plot_group = plot_groups.setdefault(
            group_key,
            {
                "polarisation": group_key[0],
                "orientation": group_key[1],
                "series": [],
            },
        )
        plot_group["series"].append(folder_record)

        angle_values = [point["angle_deg"] for point in points]
        current_min = min(angle_values)
        current_max = max(angle_values)
        angle_min = current_min if angle_min is None else min(angle_min, current_min)
        angle_max = current_max if angle_max is None else max(angle_max, current_max)

        updated_at = max(updated_at, os.stat(extended_path(folder_path)).st_mtime, os.stat(extended_path(csv_path)).st_mtime)

    if global_peak_dbm is None or angle_min is None or angle_max is None:
        return {
            "measurement_id": measurement_id,
            "measurement_name": measurement_dir.name,
            "yaml_relative_path": display_path(yaml_path, logs_root),
            "updated_at": format_timestamp(updated_at),
            "global_peak_dbm": None,
            "rows": [],
            "columns": [],
            "folders": [],
            "plots": [],
            "x_range": {"min": 0, "max": 0},
            "y_range": {"min": -1, "max": 0},
        }

    normalised_min = 0.0

    for folder in folders:
        for point in folder["points"]:
            point["normalised_db"] = point["rx_peak_dbm"] - global_peak_dbm
            normalised_min = min(normalised_min, point["normalised_db"])

    rows = sorted({str(folder["polarisation"]) for folder in folders}, key=polarisation_sort_key)
    columns = sorted({str(folder["orientation"]) for folder in folders}, key=natural_sort_key)
    folder_records = []
    plot_records = []

    for folder in sorted(folders, key=lambda item: natural_sort_key(item["folder_name"])):
        folder_records.append(
            {
                "folder_name": folder["folder_name"],
                "orientation": folder["orientation"],
                "polarisation": folder["polarisation"],
                "channel": folder["channel"],
                "power_level": folder["power_level"],
                "frequency_hz": folder["frequency_hz"],
                "peak_dbm": round(folder["peak_dbm"], 6),
            }
        )

    for key in sorted(plot_groups.keys(), key=lambda value: (polarisation_sort_key(value[0]), natural_sort_key(value[1]))):
        group = plot_groups[key]
        series_records = []

        for folder in sorted(group["series"], key=lambda item: natural_sort_key(item["channel"])):
            series_records.append(
                {
                    "folder_name": folder["folder_name"],
                    "channel": folder["channel"],
                    "power_level": folder["power_level"],
                    "frequency_hz": folder["frequency_hz"],
                    "peak_dbm": round(folder["peak_dbm"], 6),
                    "peak_offset_db": round(folder["peak_dbm"] - global_peak_dbm, 6),
                    "points": [
                        {
                            "angle_deg": round(point["angle_deg"], 6),
                            "rx_peak_dbm": round(point["rx_peak_dbm"], 6),
                            "normalised_db": round(point["normalised_db"], 6),
                        }
                        for point in folder["points"]
                    ],
                }
            )

        plot_records.append(
            {
                "polarisation": group["polarisation"],
                "orientation": group["orientation"],
                "series": series_records,
            }
        )

    y_floor = min(-1.0, math.floor(normalised_min / 5.0) * 5.0)

    return {
        "measurement_id": measurement_id,
        "measurement_name": measurement_dir.name,
        "yaml_relative_path": display_path(yaml_path, logs_root),
        "updated_at": format_timestamp(updated_at),
        "global_peak_dbm": round(global_peak_dbm, 6),
        "rows": rows,
        "columns": columns,
        "folders": folder_records,
        "plots": plot_records,
        "x_range": {
            "min": round(angle_min, 6),
            "max": round(angle_max, 6),
        },
        "y_range": {
            "min": round(y_floor, 6),
            "max": 0.0,
        },
    }


class WOYMRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, repo_root: Path, shared_root: Path, logs_root: Path, **kwargs):
        self.repo_root = repo_root
        self.shared_root = shared_root
        self.logs_root = logs_root
        self.repo_name = repo_root.name
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        clean_path = urlsplit(self.path).path

        if clean_path == "/api/results-analyser/yamls":
            self.handle_yaml_list()
            return

        if clean_path == "/api/results-analyser/data":
            self.handle_measurement_data()
            return

        super().do_GET()

    def translate_path(self, path: str) -> str:
        clean_path = urlsplit(path).path
        clean_path = unquote(clean_path)

        if clean_path in KNOWN_ROUTES:
            return str(self.repo_root / "src" / "index.html")

        relative_path = self._normalise_relative_path(clean_path)

        if relative_path.parts and relative_path.parts[0] == "DAMspy-core":
            return str(self.shared_root / relative_path)

        if relative_path.parts and relative_path.parts[0] == self.repo_name:
            relative_path = Path(*relative_path.parts[1:])

        return str(self.repo_root / relative_path)

    @staticmethod
    def _normalise_relative_path(path: str) -> Path:
        normalised = posixpath.normpath(path)
        parts = [part for part in normalised.split("/") if part not in {"", ".", ".."}]
        return Path(*parts) if parts else Path()

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_yaml_list(self) -> None:
        measurements = list_measurements(self.logs_root)
        default_measurement_id = measurements[0]["measurement_id"] if measurements else None
        self.send_json(
            {
                "measurements": measurements,
                "default_measurement_id": default_measurement_id,
            }
        )

    def handle_measurement_data(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_id = query.get("measurement_id", [""])[0]

        if not measurement_id:
            self.send_json(
                {"error": "measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            dataset = load_measurement_dataset(self.logs_root, measurement_id)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_json(dataset)


def format_url(host: str, port: int, page_path: str) -> str:
    browser_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}{page_path}"


def log_environment(repo_root: Path, shared_root: Path, logs_root: Path, page_url: str) -> None:
    json_path = logs_root / "latest_woym.json"

    print(f"Serving directory: {shared_root}")
    print(f"VC repository:      {repo_root}")
    print(f"Monitor page:       {page_url}")
    print(f"Analyser page:      {page_url.rstrip('/')}/results-analyser")
    print(f"Expected JSON URL:  /DAMspy-core/src/DAMspy_logs/latest_woym.json")
    print(f"JSON file on disk:  {json_path}")

    if not path_exists(json_path):
        print("Warning: latest_woym.json does not exist at startup. The page will show DATA UNAVAILABLE until it appears.")

    print("Press Ctrl+C to stop.")


def build_port_candidates(requested_port: int) -> list[int]:
    fallback_ports = [8001, 8080, 8765, 8888, 9000]
    candidates = [requested_port]

    for port in fallback_ports:
        if port not in candidates:
            candidates.append(port)

    return candidates


def main() -> int:
    args = parse_args()
    repo_root, shared_root, index_path, logs_root = get_paths()

    if not index_path.exists():
        print(f"Error: monitor page not found at {index_path}", file=sys.stderr)
        return 1

    handler = lambda *handler_args, **handler_kwargs: WOYMRequestHandler(
        *handler_args,
        repo_root=repo_root,
        shared_root=shared_root,
        logs_root=logs_root,
        **handler_kwargs,
    )

    last_error: OSError | None = None

    for port in build_port_candidates(args.port):
        page_url = format_url(args.host, port, "/")

        try:
            with ThreadingHTTPServer((args.host, port), handler) as server:
                print()

                if port != args.port:
                    print(f"Port {args.port} was unavailable. Using port {port} instead.")

                log_environment(repo_root, shared_root, logs_root, page_url)
                print()

                if not args.no_browser:
                    webbrowser.open(page_url)

                server.serve_forever()
                return 0
        except OSError as exc:
            last_error = exc
            continue
        except KeyboardInterrupt:
            print("\nServer stopped.")
            return 0

    print(f"Error: could not start server on host {args.host}. Last error: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
