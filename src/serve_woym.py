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
PREFERRED_DEFAULT_MEASUREMENT_ID = (
    "_best/"
    "Antenna_Pattern_Measurement-2026-04-10_11-22-16-"
    "hendrix-tx_V3-04F_002-bodyworn-Ori_ori1_ori2-Ch_0_40_80-"
    "Pwr_10-Pol_H_V-Step_2deg-RxAnt_Horn_WR340"
)


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


def strip_yaml_inline_comment(value: str) -> str:
    in_single_quote = False
    in_double_quote = False

    for index, char in enumerate(value):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue

        if char == "#" and not in_single_quote and not in_double_quote:
            return value[:index].rstrip()

    return value.rstrip()


def split_yaml_flow_items(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False

    for char in value:
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
            continue

        if char == "," and not in_single_quote and not in_double_quote:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue

        current.append(char)

    item = "".join(current).strip()
    if item:
        items.append(item)

    return items


def parse_yaml_scalar(raw_value: str) -> Any:
    value = strip_yaml_inline_comment(raw_value).strip()

    if not value:
        return None

    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_yaml_scalar(item) for item in split_yaml_flow_items(inner)]

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if re.fullmatch(r"[-+]?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value

    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", value):
        try:
            return float(value)
        except ValueError:
            return value

    return value


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    return numeric_value if math.isfinite(numeric_value) else None


def canonical_measurement_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    numeric_value = coerce_float(value)
    if numeric_value is not None:
        if numeric_value.is_integer():
            return str(int(numeric_value))
        return format(numeric_value, ".15g")

    return str(value).strip().lower()


def coerce_measurement_sequence(value: Any) -> list[str]:
    if value is None:
        return []

    items = value if isinstance(value, list) else [value]
    values: list[str] = []

    for item in items:
        canonical_value = canonical_measurement_value(item)
        if canonical_value:
            values.append(canonical_value)

    return values


def read_yaml_summary_fields(path: Path) -> dict[str, Any]:
    field_values: dict[str, Any] = {
        "dut_product": None,
        "dut_hardware_config": None,
        "dut_serial_number": None,
        "tx_mode": None,
        "foldername_comment": None,
        "orientation_photo_location": None,
        "rx_antenna_name": None,
        "rx_antenna_comment": None,
        "tx_cable_loss_db": None,
        "tx_power_dbm": None,
        "rx_antenna_gain_dbi": None,
        "rx_cable_loss_db": None,
        "rx_dist_m": None,
    }
    top_level_fields = {
        "DUT_product": "dut_product",
        "DUT_hardware_config": "dut_hardware_config",
        "DUT_serial_number": "dut_serial_number",
        "tx_mode": "tx_mode",
        "Tx_mode": "tx_mode",
        "foldername_comment": "foldername_comment",
        "orientation_photo_location": "orientation_photo_location",
    }
    section_fields = {
        "sig_gen_1": {
            "tx_mode": "tx_mode",
            "tx_cable_loss": "tx_cable_loss_db",
            "tx_power": "tx_power_dbm",
        },
        "rx_path": {
            "antenna": "rx_antenna_name",
            "antenna_comment": "rx_antenna_comment",
            "rx_antena_gain": "rx_antenna_gain_dbi",
            "rx_antenna_gain": "rx_antenna_gain_dbi",
            "rx_cable_loss": "rx_cable_loss_db",
            "rx_cable_loss_2.45Ghz": "rx_cable_loss_db",
            "rx_dist_m": "rx_dist_m",
        },
    }
    active_section: str | None = None

    with open(extended_path(path), "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            content = strip_yaml_inline_comment(line)
            stripped = content.strip()

            if not stripped:
                continue

            indent = len(content) - len(content.lstrip(" "))

            if indent == 0:
                active_section = None

                if stripped.endswith(":"):
                    section_name = stripped[:-1].strip()
                    active_section = section_name if section_name in section_fields else None
                    continue

                if ":" not in stripped:
                    continue

                key, raw_value = stripped.split(":", 1)
                mapped_key = top_level_fields.get(key.strip())
                if mapped_key is None:
                    continue

                field_values[mapped_key] = parse_yaml_scalar(raw_value)
                continue

            if active_section is None or ":" not in stripped:
                continue

            key, raw_value = stripped.split(":", 1)
            mapped_key = section_fields.get(active_section, {}).get(key.strip())
            if mapped_key is None:
                continue

            field_values[mapped_key] = parse_yaml_scalar(raw_value)

    field_values["tx_cable_loss_db"] = coerce_float(field_values["tx_cable_loss_db"])
    field_values["tx_power_dbm"] = coerce_float(field_values["tx_power_dbm"])
    field_values["rx_antenna_gain_dbi"] = coerce_float(field_values["rx_antenna_gain_dbi"])
    field_values["rx_cable_loss_db"] = coerce_float(field_values["rx_cable_loss_db"])
    field_values["rx_dist_m"] = coerce_float(field_values["rx_dist_m"])

    return field_values


def read_yaml_completion_dimensions(path: Path) -> dict[str, list[str]]:
    field_values: dict[str, Any] = {
        "orientations": [],
        "polarisations": [],
        "channels": [],
        "power_levels": [],
    }
    top_level_fields = {
        "orientations": "orientations",
        "polarisation": "polarisations",
    }
    section_fields = {
        "sig_gen_1": {
            "channels": "channels",
            "power_levels": "power_levels",
        },
    }
    active_section: str | None = None

    with open(extended_path(path), "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            content = strip_yaml_inline_comment(line)
            stripped = content.strip()

            if not stripped:
                continue

            indent = len(content) - len(content.lstrip(" "))

            if indent == 0:
                active_section = None

                if stripped.endswith(":"):
                    section_name = stripped[:-1].strip()
                    active_section = section_name if section_name in section_fields else None
                    continue

                if ":" not in stripped:
                    continue

                key, raw_value = stripped.split(":", 1)
                mapped_key = top_level_fields.get(key.strip())
                if mapped_key is None:
                    continue

                field_values[mapped_key] = parse_yaml_scalar(raw_value)
                continue

            if active_section is None or ":" not in stripped:
                continue

            key, raw_value = stripped.split(":", 1)
            mapped_key = section_fields.get(active_section, {}).get(key.strip())
            if mapped_key is None:
                continue

            field_values[mapped_key] = parse_yaml_scalar(raw_value)

    return {
        "orientations": coerce_measurement_sequence(field_values.get("orientations")),
        "polarisations": coerce_measurement_sequence(field_values.get("polarisations")),
        "channels": coerce_measurement_sequence(field_values.get("channels")),
        "power_levels": coerce_measurement_sequence(field_values.get("power_levels")),
    }


def read_name_completion_dimensions(measurement_name: str) -> dict[str, list[str]]:
    patterns = {
        "orientations": r"-Ori_(.*?)-Ch_",
        "channels": r"-Ch_(.*?)-Pwr_",
        "power_levels": r"-Pwr_(.*?)-Pol_",
        "polarisations": r"-Pol_(.*?)-Step_",
    }
    dimensions: dict[str, list[str]] = {}

    for key, pattern in patterns.items():
        match = re.search(pattern, measurement_name)
        if match is None:
            dimensions[key] = []
            continue

        dimensions[key] = coerce_measurement_sequence(match.group(1).split("_"))

    return dimensions


def expected_measurement_keys(yaml_path: Path, measurement_name: str) -> set[tuple[str, str, str, str]]:
    yaml_dimensions = read_yaml_completion_dimensions(yaml_path)
    name_dimensions = read_name_completion_dimensions(measurement_name)
    orientations = yaml_dimensions["orientations"] or name_dimensions["orientations"]
    polarisations = yaml_dimensions["polarisations"] or name_dimensions["polarisations"]
    channels = yaml_dimensions["channels"] or name_dimensions["channels"]
    power_levels = yaml_dimensions["power_levels"] or name_dimensions["power_levels"]

    if not orientations or not polarisations or not channels:
        return set()

    if not power_levels:
        power_levels = [""]

    return {
        (orientation, polarisation, channel, power_level)
        for orientation in orientations
        for polarisation in polarisations
        for channel in channels
        for power_level in power_levels
    }


def find_first_file_by_suffix(path: Path, suffixes: set[str]) -> Path | None:
    for entry in iter_directory(path):
        if entry.is_file() and Path(entry.name).suffix.lower() in suffixes:
            return path / entry.name

    return None


def find_first_png(path: Path) -> Path | None:
    return find_first_file_by_suffix(path, {".png"})


def expected_measurement_count(yaml_path: Path, measurement_name: str) -> int:
    return len(expected_measurement_keys(yaml_path, measurement_name))


def build_measurement_completion(measurement_dir: Path, yaml_path: Path) -> dict[str, Any]:
    results_dir = measurement_dir / "1_meas_azimuth"
    expected_count = expected_measurement_count(yaml_path, measurement_dir.name)

    if not path_is_dir(results_dir):
        return {
            "quantity_status": "red",
            "completeness_status": "red",
            "expected_count": expected_count,
            "actual_count": 0,
            "csv_count": 0,
            "png_count": 0,
        }

    subfolders = [results_dir / entry.name for entry in iter_directory(results_dir) if entry.is_dir()]
    actual_count = len(subfolders)
    csv_count = 0
    png_count = 0

    for folder_path in subfolders:
        if find_first_csv(folder_path) is not None:
            csv_count += 1

        if find_first_png(folder_path) is not None:
            png_count += 1

    if expected_count > 0 and actual_count == expected_count:
        quantity_status = "green"
    else:
        quantity_status = "red"

    if actual_count > 0 and csv_count == actual_count:
        completeness_status = "green" if png_count == actual_count else "orange"
    else:
        completeness_status = "red"

    return {
        "quantity_status": quantity_status,
        "completeness_status": completeness_status,
        "expected_count": expected_count,
        "actual_count": actual_count,
        "csv_count": csv_count,
        "png_count": png_count,
    }


def free_space_path_loss_db(frequency_hz: Any, distance_m: Any) -> float | None:
    frequency = coerce_float(frequency_hz)
    distance = coerce_float(distance_m)

    if frequency is None or distance is None or frequency <= 0 or distance <= 0:
        return None

    return 20.0 * math.log10((4.0 * math.pi * distance * frequency) / 299_792_458.0)


def calculate_eirp_dbm(
    peak_dbm: Any,
    frequency_hz: Any,
    rx_cable_loss_db: Any,
    rx_antenna_gain_dbi: Any,
    rx_dist_m: Any,
) -> float | None:
    peak = coerce_float(peak_dbm)
    rx_cable_loss = coerce_float(rx_cable_loss_db)
    rx_antenna_gain = coerce_float(rx_antenna_gain_dbi)
    path_loss = free_space_path_loss_db(frequency_hz, rx_dist_m)

    if peak is None or rx_cable_loss is None or rx_antenna_gain is None or path_loss is None:
        return None

    return peak + rx_cable_loss - rx_antenna_gain + path_loss


def calculate_gain_dbd(eirp_dbm: Any, tx_power_dbm: Any, tx_cable_loss_db: Any) -> float | None:
    eirp = coerce_float(eirp_dbm)
    tx_power = coerce_float(tx_power_dbm)
    tx_cable_loss = coerce_float(tx_cable_loss_db)

    if eirp is None or tx_power is None or tx_cable_loss is None:
        return None

    return eirp - (tx_power - tx_cable_loss) - 2.15


def display_path(path: Path, root: Path) -> str:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)

    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        return str(resolved_path)


def lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def resolve_orientation_photo_dir(logs_root: Path, measurement_dir: Path, raw_location: Any) -> Path | None:
    if raw_location is None:
        return None

    raw_text = str(raw_location).strip()
    if not raw_text:
        return None

    configured_path = Path(raw_text)
    candidate_paths = [configured_path] if configured_path.is_absolute() else [measurement_dir / configured_path, logs_root / configured_path]

    for candidate in candidate_paths:
        if path_is_dir(candidate):
            return candidate

    return None


def build_orientation_image_map(
    logs_root: Path,
    measurement_dir: Path,
    raw_location: Any,
    orientations: list[str],
) -> dict[str, str]:
    photo_dir = resolve_orientation_photo_dir(logs_root, measurement_dir, raw_location)
    if photo_dir is None:
        return {}

    files_by_key: dict[str, Path] = {}

    try:
        entries = iter_directory(photo_dir)
    except OSError:
        return {}

    for entry in entries:
        if not entry.is_file():
            continue

        image_path = photo_dir / entry.name
        suffix = image_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue

        files_by_key[lookup_key(image_path.stem)] = image_path

    image_map: dict[str, str] = {}

    for orientation in orientations:
        image_path = files_by_key.get(lookup_key(orientation))
        if image_path is None:
            continue

        image_map[str(orientation)] = "/DAMspy-core/src/DAMspy_logs/" + display_path(image_path, logs_root)

    return image_map


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


def get_file_created_timestamp(path: Path) -> float:
    stat_result = os.stat(extended_path(path))
    created_at = getattr(stat_result, "st_birthtime", None)
    return created_at if created_at is not None else stat_result.st_ctime


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


def iter_measurement_directories(logs_root: Path) -> list[Path]:
    if not path_is_dir(logs_root):
        return []

    discovered: list[Path] = []
    pending = [logs_root]

    while pending:
        current_dir = pending.pop()

        try:
            entries = iter_directory(current_dir)
        except OSError:
            continue

        yaml_path = current_dir / "1_meas_azimuth.yaml"
        if path_is_file(yaml_path):
            discovered.append(current_dir)

        for entry in entries:
            if not entry.is_dir():
                continue

            if entry.name == "1_meas_azimuth":
                continue

            pending.append(current_dir / entry.name)

    return discovered


def measurement_manifest(logs_root: Path, measurement_dir: Path) -> dict[str, Any] | None:
    yaml_path = measurement_dir / "1_meas_azimuth.yaml"

    if not path_is_dir(measurement_dir) or not path_is_file(yaml_path):
        return None

    measurement_id = display_path(measurement_dir, logs_root)
    measurement_name = measurement_dir.name

    updated_at = max(
        os.stat(extended_path(measurement_dir)).st_mtime,
        os.stat(extended_path(yaml_path)).st_mtime,
    )
    measurement_timestamp = measurement_name_timestamp(measurement_id)
    completion = build_measurement_completion(measurement_dir, yaml_path)

    return {
        "measurement_id": measurement_id,
        "measurement_name": measurement_name,
        "yaml_relative_path": display_path(yaml_path, logs_root),
        "updated_at": format_timestamp(updated_at),
        "quantity_status": completion["quantity_status"],
        "completeness_status": completion["completeness_status"],
        "expected_subfolders": completion["expected_count"],
        "actual_subfolders": completion["actual_count"],
        "subfolders_with_csv": completion["csv_count"],
        "subfolders_with_png": completion["png_count"],
        "_updated_at": updated_at,
        "_sort_at": measurement_timestamp if measurement_timestamp is not None else updated_at,
    }


def list_measurements(logs_root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []

    for measurement_dir in iter_measurement_directories(logs_root):
        manifest = measurement_manifest(logs_root, measurement_dir)
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

    yaml_summary = read_yaml_summary_fields(yaml_path)
    yaml_created_at = format_timestamp(get_file_created_timestamp(yaml_path))
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

        peak_point = max(points, key=lambda point: point["rx_peak_dbm"])
        peak_dbm = peak_point["rx_peak_dbm"]
        global_peak_dbm = peak_dbm if global_peak_dbm is None else max(global_peak_dbm, peak_dbm)

        series_info = metadata.get("sig_gen_1") or {}
        rx_path_info = metadata.get("rx_path") or {}
        frequency_hz = peak_point.get("peak_freq_hz") or series_info.get("frequency_hz") or (metadata.get("spec_an_1") or {}).get("center_frequency_hz")
        tx_power_dbm = coerce_float(series_info.get("tx_power"))
        if tx_power_dbm is None:
            tx_power_dbm = coerce_float(yaml_summary.get("tx_power_dbm"))
        tx_cable_loss_db = coerce_float(series_info.get("tx_cable_loss"))
        if tx_cable_loss_db is None:
            tx_cable_loss_db = coerce_float(yaml_summary.get("tx_cable_loss_db"))
        rx_antenna_gain_dbi = coerce_float(rx_path_info.get("rx_antenna_gain"))
        if rx_antenna_gain_dbi is None:
            rx_antenna_gain_dbi = coerce_float(rx_path_info.get("rx_antena_gain"))
        if rx_antenna_gain_dbi is None:
            rx_antenna_gain_dbi = coerce_float(yaml_summary.get("rx_antenna_gain_dbi"))
        rx_cable_loss_db = coerce_float(rx_path_info.get("rx_cable_loss"))
        if rx_cable_loss_db is None:
            rx_cable_loss_db = coerce_float(rx_path_info.get("rx_cable_loss_2.45Ghz"))
        if rx_cable_loss_db is None:
            rx_cable_loss_db = coerce_float(yaml_summary.get("rx_cable_loss_db"))
        rx_dist_m = coerce_float(rx_path_info.get("rx_dist_m"))
        if rx_dist_m is None:
            rx_dist_m = coerce_float(yaml_summary.get("rx_dist_m"))
        eirp_dbm = calculate_eirp_dbm(
            peak_dbm,
            frequency_hz,
            rx_cable_loss_db,
            rx_antenna_gain_dbi,
            rx_dist_m,
        )
        gain_dbd = calculate_gain_dbd(
            eirp_dbm,
            tx_power_dbm,
            tx_cable_loss_db,
        )
        folder_record = {
            "folder_name": entry.name,
            "orientation": metadata.get("orientation") or "unknown",
            "polarisation": metadata.get("polarisation") or "unknown",
            "channel": series_info.get("channel"),
            "power_level": series_info.get("power_level"),
            "frequency_hz": frequency_hz,
            "peak_dbm": peak_dbm,
            "eirp_dbm": eirp_dbm,
            "gain_dbd": gain_dbd,
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
            "yaml_created_at": yaml_created_at,
            "updated_at": format_timestamp(updated_at),
            **yaml_summary,
            "global_peak_dbm": None,
            "rows": [],
            "columns": [],
            "orientation_images": {},
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
    orientation_images = build_orientation_image_map(
        logs_root,
        measurement_dir,
        yaml_summary.get("orientation_photo_location"),
        columns,
    )
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
                "eirp_dbm": round(folder["eirp_dbm"], 6) if folder["eirp_dbm"] is not None else None,
                "gain_dbd": round(folder["gain_dbd"], 6) if folder["gain_dbd"] is not None else None,
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
                    "eirp_dbm": round(folder["eirp_dbm"], 6) if folder["eirp_dbm"] is not None else None,
                    "gain_dbd": round(folder["gain_dbd"], 6) if folder["gain_dbd"] is not None else None,
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
        "yaml_created_at": yaml_created_at,
        "updated_at": format_timestamp(updated_at),
        **yaml_summary,
        "global_peak_dbm": round(global_peak_dbm, 6),
        "rows": rows,
        "columns": columns,
        "orientation_images": orientation_images,
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
        measurement_ids = {measurement["measurement_id"] for measurement in measurements}
        default_measurement_id = (
            PREFERRED_DEFAULT_MEASUREMENT_ID
            if PREFERRED_DEFAULT_MEASUREMENT_ID in measurement_ids
            else measurements[0]["measurement_id"] if measurements else None
        )
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
