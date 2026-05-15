#!/usr/bin/env python3
"""Serve the DAMSpy visualiser pages and analyser APIs over HTTP."""

from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import math
import os
import posixpath
import re
import sys
import webbrowser
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


KNOWN_ROUTES = {"/", "/results-analyser", "/results-analyser/"}
MEASUREMENT_SCOPE_BEST = "best"
MEASUREMENT_SCOPE_LOGS = "logs"
MEASUREMENT_SCOPE_ALL = "all"
BEST_FOLDER_NAME = "_best"
ANALYSER_TESTER_NAME = "Alistair Morgan"
PREFERRED_DEFAULT_MEASUREMENT_ID = (
    BEST_FOLDER_NAME + "/"
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
    parser.add_argument(
        "--write-summary-csv",
        action="store_true",
        help="Write a measurement summary CSV into the DAMspy logs root and exit.",
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


def find_first_file_by_suffix(path: Path, suffixes: set[str]) -> Path | None:
    for entry in iter_directory(path):
        if entry.is_file() and Path(entry.name).suffix.lower() in suffixes:
            return path / entry.name

    return None


def find_first_png(path: Path) -> Path | None:
    return find_first_file_by_suffix(path, {".png"})


def expected_measurement_count(yaml_path: Path) -> int:
    expected_count = 1

    with open(extended_path(yaml_path), "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            content = strip_yaml_inline_comment(line)
            stripped = content.strip()

            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue

            _, raw_value = stripped.split(":", 1)
            parsed_value = parse_yaml_scalar(raw_value)

            if isinstance(parsed_value, list):
                expected_count *= len(parsed_value)

    return expected_count


def build_measurement_completion(measurement_dir: Path, yaml_path: Path) -> dict[str, Any]:
    results_dir = measurement_dir / "1_meas_azimuth"
    expected_count = expected_measurement_count(yaml_path)

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
    missing_csv = False
    missing_png = False

    for folder_path in subfolders:
        has_csv = find_first_csv(folder_path) is not None
        has_png = find_first_png(folder_path) is not None

        if has_csv:
            csv_count += 1
        else:
            missing_csv = True

        if has_png:
            png_count += 1
        else:
            missing_png = True

    if actual_count <= 0:
        quantity_status = "red"
    elif expected_count > 0 and actual_count == expected_count:
        quantity_status = "green"
    else:
        quantity_status = "orange"

    if actual_count <= 0 or csv_count <= 0:
        completeness_status = "red"
    elif csv_count == actual_count and png_count == actual_count:
        completeness_status = "green"
    else:
        completeness_status = "orange"

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


def measurement_is_in_best_folder(measurement_id: str) -> bool:
    first_part = Path(measurement_id).parts[0] if Path(measurement_id).parts else ""
    return first_part == BEST_FOLDER_NAME


def list_measurements(logs_root: Path, scope: str = MEASUREMENT_SCOPE_ALL) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    requested_scope = scope if scope in {MEASUREMENT_SCOPE_BEST, MEASUREMENT_SCOPE_LOGS, MEASUREMENT_SCOPE_ALL} else MEASUREMENT_SCOPE_ALL

    for measurement_dir in iter_measurement_directories(logs_root):
        manifest = measurement_manifest(logs_root, measurement_dir)
        if manifest is not None:
            is_best_measurement = measurement_is_in_best_folder(str(manifest["measurement_id"]))

            if requested_scope == MEASUREMENT_SCOPE_BEST and not is_best_measurement:
                continue

            if requested_scope == MEASUREMENT_SCOPE_LOGS and is_best_measurement:
                continue

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


def list_measurement_subfolders(results_dir: Path) -> list[str]:
    if not path_is_dir(results_dir):
        return []

    names = [entry.name for entry in iter_directory(results_dir) if entry.is_dir()]
    names.sort(key=natural_sort_key)
    return names


SUMMARY_FLAG_COLUMNS = [
    "ori1",
    "ori2",
    "ori3",
    "ori4",
    "Pol_V",
    "Pol_H",
    "Ch_0",
    "Ch_20",
    "Ch_40",
    "Ch_60",
    "Ch_80",
    "Pwr_0",
    "Pwr_10",
    "ctx0",
    "ctx1",
    "step_1deg",
    "step_2deg",
]


def build_summary_flag_row(measurement_name: str, subfolder_name: str, marker: str = "1") -> list[str]:
    subfolder_key = subfolder_name.lower()
    measurement_key = measurement_name.lower()
    flags = {
        "ori1": marker if "ori-ori1" in subfolder_key else "",
        "ori2": marker if "ori-ori2" in subfolder_key else "",
        "ori3": marker if "ori-ori3" in subfolder_key else "",
        "ori4": marker if "ori-ori4" in subfolder_key else "",
        "Pol_V": marker if "pol-v" in subfolder_key else "",
        "Pol_H": marker if "pol-h" in subfolder_key else "",
        "Ch_0": marker if "ch-0" in subfolder_key else "",
        "Ch_20": marker if "ch-20" in subfolder_key else "",
        "Ch_40": marker if "ch-40" in subfolder_key else "",
        "Ch_60": marker if "ch-60" in subfolder_key else "",
        "Ch_80": marker if "ch-80" in subfolder_key else "",
        "Pwr_0": marker if "pwr-0" in subfolder_key else "",
        "Pwr_10": marker if "pwr-10" in subfolder_key else "",
        "ctx0": marker if re.search(r"ctx[-_]?0(?:[^0-9]|$)", subfolder_key) else "",
        "ctx1": marker if re.search(r"ctx[-_]?1(?:[^0-9]|$)", subfolder_key) else "",
        "step_1deg": marker if "step_1deg" in measurement_key else "",
        "step_2deg": marker if "step_2deg" in measurement_key else "",
    }

    return [flags[column] for column in SUMMARY_FLAG_COLUMNS]


def read_yaml_named_dimensions(path: Path) -> dict[str, Any]:
    field_values: dict[str, Any] = {
        "orientations": [],
        "polarisation": [],
        "channels": [],
        "power_levels": [],
        "CTX": [],
        "step_deg": None,
    }
    top_level_fields = {
        "orientations": "orientations",
        "polarisation": "polarisation",
        "step_deg": "step_deg",
    }
    section_fields = {
        "sig_gen_1": {
            "channels": "channels",
            "power_levels": "power_levels",
            "CTX": "CTX",
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

    return field_values


def unique_values_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)

    return ordered


def normalise_dimension_values(value: Any) -> list[str]:
    if value is None:
        return []

    items = value if isinstance(value, list) else [value]
    normalised = [str(item).strip() for item in items if item is not None and str(item).strip()]
    return unique_values_in_order(normalised)


def parse_subfolder_dimensions(subfolder_name: str) -> dict[str, str]:
    values = {"ori": "", "pol": "", "ch": "", "pwr": "", "ctx": ""}
    patterns = {
        "ori": r"(?:^|_)ori-([^_]+)",
        "pol": r"(?:^|_)pol-([^_]+)",
        "ch": r"(?:^|_)ch-([^_]+)",
        "pwr": r"(?:^|_)pwr-([^_]+)",
        "ctx": r"(?:^|_)ctx-([^_]+)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, subfolder_name, flags=re.IGNORECASE)
        if match is not None:
            values[key] = match.group(1)

    return values


def collect_observed_dimension_values(subfolder_names: list[str]) -> dict[str, list[str]]:
    collected = {"ori": [], "pol": [], "ch": [], "pwr": [], "ctx": []}

    for subfolder_name in subfolder_names:
        parsed = parse_subfolder_dimensions(subfolder_name)
        for key, value in parsed.items():
            if value:
                collected[key].append(value)

    return {key: unique_values_in_order(values) for key, values in collected.items()}


def infer_subfolder_template_segments(subfolder_names: list[str]) -> list[str]:
    if not subfolder_names:
        return ["ori-", "pol-", "ant-na", "pwr-", "ch-"]

    return subfolder_names[0].split("_")


def build_expected_dimension_combinations(yaml_path: Path, subfolder_names: list[str]) -> list[dict[str, str]]:
    yaml_dimensions = read_yaml_named_dimensions(yaml_path)
    observed_dimensions = collect_observed_dimension_values(subfolder_names)
    orientations = normalise_dimension_values(yaml_dimensions.get("orientations")) or observed_dimensions["ori"]
    polarisations = normalise_dimension_values(yaml_dimensions.get("polarisation")) or observed_dimensions["pol"]
    channels = normalise_dimension_values(yaml_dimensions.get("channels")) or observed_dimensions["ch"]
    power_levels = normalise_dimension_values(yaml_dimensions.get("power_levels")) or observed_dimensions["pwr"] or [""]
    ctx_values = normalise_dimension_values(yaml_dimensions.get("CTX")) or observed_dimensions["ctx"] or [""]

    if not orientations or not polarisations or not channels:
        return []

    combinations = []

    for ori, pol, ch, pwr, ctx in itertools.product(orientations, polarisations, channels, power_levels, ctx_values):
        combinations.append({"ori": ori, "pol": pol, "ch": ch, "pwr": pwr, "ctx": ctx})

    return combinations


def build_guessed_subfolder_name(template_segments: list[str], dimensions: dict[str, str]) -> str:
    replacements = {
        "ori-": "ori-" + dimensions["ori"],
        "pol-": "pol-" + dimensions["pol"],
        "pwr-": "pwr-" + dimensions["pwr"],
        "ch-": "ch-" + dimensions["ch"],
        "ctx-": "ctx-" + dimensions["ctx"],
    }
    rendered_segments: list[str] = []
    used_prefixes: set[str] = set()

    for segment in template_segments:
        replaced = segment

        for prefix, replacement in replacements.items():
            if segment.lower().startswith(prefix):
                replaced = replacement if replacement != prefix else ""
                used_prefixes.add(prefix)
                break

        if replaced:
            rendered_segments.append(replaced)

    for prefix in ["ori-", "pol-", "pwr-", "ch-", "ctx-"]:
        replacement = replacements[prefix]
        if prefix not in used_prefixes and replacement != prefix:
            rendered_segments.append(replacement)

    return "_".join(rendered_segments)


def build_expected_subfolder_rows(measurement_name: str, yaml_path: Path, subfolder_names: list[str]) -> list[tuple[str, str]]:
    template_segments = infer_subfolder_template_segments(subfolder_names)
    present_by_key: dict[tuple[str, str, str, str, str], str] = {}

    for subfolder_name in subfolder_names:
        parsed = parse_subfolder_dimensions(subfolder_name)
        present_by_key[(parsed["ori"], parsed["pol"], parsed["ch"], parsed["pwr"], parsed["ctx"])] = subfolder_name

    expected_combinations = build_expected_dimension_combinations(yaml_path, subfolder_names)
    if not expected_combinations:
        return [(subfolder_name, "present") for subfolder_name in subfolder_names]

    rows: list[tuple[str, str]] = []

    for combination in expected_combinations:
        key = (combination["ori"], combination["pol"], combination["ch"], combination["pwr"], combination["ctx"])
        present_name = present_by_key.get(key)
        if present_name is not None:
            rows.append((present_name, "present"))
        else:
            rows.append((build_guessed_subfolder_name(template_segments, combination), "missing"))

    return rows


def write_measurement_summary_csv(logs_root: Path) -> Path:
    output_path = logs_root / "measurement_summary.csv"
    measurements = list_measurements(logs_root)

    with open(extended_path(output_path), "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["folder_number", "total_required_subfolders", "folder_status", "test_folder_name", "subfolder_name", *SUMMARY_FLAG_COLUMNS])

        for measurement in measurements:
            row_number = 1
            measurement_dir = logs_root / measurement["measurement_id"]
            yaml_path = measurement_dir / "1_meas_azimuth.yaml"
            results_dir = measurement_dir / "1_meas_azimuth"
            subfolder_names = list_measurement_subfolders(results_dir)
            subfolder_rows = build_expected_subfolder_rows(measurement.get("measurement_name", ""), yaml_path, subfolder_names)

            if not subfolder_rows:
                writer.writerow(
                    [
                        row_number,
                        measurement.get("expected_subfolders", 0),
                        "missing",
                        measurement.get("measurement_name", ""),
                        "",
                        *build_summary_flag_row(measurement.get("measurement_name", ""), "", "X"),
                    ]
                )
                row_number += 1
                continue

            for subfolder_name, folder_status in subfolder_rows:
                writer.writerow(
                    [
                        row_number,
                        measurement.get("expected_subfolders", 0),
                        folder_status,
                        measurement.get("measurement_name", ""),
                        subfolder_name,
                        *build_summary_flag_row(
                            measurement.get("measurement_name", ""),
                            subfolder_name,
                            "1" if folder_status == "present" else "X",
                        ),
                    ]
                )
                row_number += 1

    return output_path


def xml_text(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def report_value(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"

    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        text = str(value)

    return text + suffix


def report_dbm(value: Any) -> str:
    numeric_value = coerce_float(value)
    return "-" if numeric_value is None else f"{numeric_value:.1f} dBm"


def report_db(value: Any) -> str:
    numeric_value = coerce_float(value)
    return "-" if numeric_value is None else f"{numeric_value:.1f} dB"


def report_hz(value: Any) -> str:
    numeric_value = coerce_float(value)
    if numeric_value is None:
        return "-"
    if abs(numeric_value) >= 1e9:
        return f"{numeric_value / 1e9:.3f}".rstrip("0").rstrip(".") + " GHz"
    if abs(numeric_value) >= 1e6:
        return f"{numeric_value / 1e6:.3f}".rstrip("0").rstrip(".") + " MHz"
    if abs(numeric_value) >= 1e3:
        return f"{numeric_value / 1e3:.3f}".rstrip("0").rstrip(".") + " kHz"
    return f"{numeric_value:.0f} Hz"


def docx_para(text: Any = "", style: str | None = None, bold: bool = False) -> str:
    properties = []
    if style:
        properties.append(f'<w:pStyle w:val="{style}"/>')
    run_properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    paragraph_properties = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
    return f"<w:p>{paragraph_properties}<w:r>{run_properties}<w:t xml:space=\"preserve\">{xml_text(text)}</w:t></w:r></w:p>"


def docx_cell(text: Any, width: int, fill: str | None = None, bold: bool = False) -> str:
    shading = f'<w:shd w:fill="{fill}"/>' if fill else ""
    run_properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (
        "<w:tc>"
        f"<w:tcPr><w:tcW w:w=\"{width}\" w:type=\"dxa\"/>{shading}</w:tcPr>"
        f"<w:p><w:r>{run_properties}<w:t xml:space=\"preserve\">{xml_text(text)}</w:t></w:r></w:p>"
        "</w:tc>"
    )


def docx_table(rows: list[list[Any]], widths: list[int], header: bool = True) -> str:
    table_width = sum(widths)
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    rendered_rows = []

    for row_index, row in enumerate(rows):
        fill = "EAF3FA" if header and row_index == 0 else None
        bold = header and row_index == 0
        cells = [docx_cell(row[column_index] if column_index < len(row) else "", widths[column_index], fill, bold) for column_index in range(len(widths))]
        rendered_rows.append(f"<w:tr>{''.join(cells)}</w:tr>")

    return (
        "<w:tbl>"
        "<w:tblPr>"
        f"<w:tblW w:w=\"{table_width}\" w:type=\"dxa\"/>"
        '<w:tblBorders><w:top w:val="single" w:sz="4" w:color="D7E2EA"/>'
        '<w:left w:val="single" w:sz="4" w:color="D7E2EA"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="D7E2EA"/>'
        '<w:right w:val="single" w:sz="4" w:color="D7E2EA"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="D7E2EA"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="D7E2EA"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="90" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="90" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tblCellMar>'
        "</w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>"
        f"{''.join(rendered_rows)}"
        "</w:tbl>"
    )


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with open(extended_path(path), "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None

    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None

    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return (width, height) if width > 0 and height > 0 else None


class DocxReportBuilder:
    def __init__(self, title: str):
        self.title = title
        self.body: list[str] = []
        self.rels: list[tuple[str, str, str]] = []
        self.media: list[tuple[str, Path]] = []
        self.next_rid = 1

    def add_paragraph(self, text: Any = "", style: str | None = None, bold: bool = False) -> None:
        self.body.append(docx_para(text, style, bold))

    def add_heading(self, text: Any, level: int = 1) -> None:
        self.add_paragraph(text, f"Heading{min(max(level, 1), 3)}")

    def add_table(self, rows: list[list[Any]], widths: list[int], header: bool = True) -> None:
        if rows:
            self.body.append(docx_table(rows, widths, header))
            self.add_paragraph("")

    def add_image(self, image_path: Path, caption: str, max_width_in: float = 5.9) -> None:
        dimensions = png_dimensions(image_path)
        if dimensions is None:
            return

        width_px, height_px = dimensions
        max_width_emu = int(max_width_in * 914400)
        height_emu = int(max_width_emu * (height_px / width_px))
        image_index = len(self.media) + 1
        media_name = f"image{image_index}.png"
        rid = f"rId{self.next_rid}"
        self.next_rid += 1
        self.media.append((media_name, image_path))
        self.rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", f"media/{media_name}"))
        self.add_paragraph(caption, "Caption", True)
        self.body.append(
            "<w:p><w:r><w:drawing><wp:inline xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
            f"<wp:extent cx=\"{max_width_emu}\" cy=\"{height_emu}\"/>"
            f"<wp:docPr id=\"{image_index}\" name=\"Plot {image_index}\"/>"
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            "<a:graphic xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
            "<pic:pic xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
            f"<pic:nvPicPr><pic:cNvPr id=\"{image_index}\" name=\"Plot {image_index}\"/><pic:cNvPicPr/></pic:nvPicPr>"
            "<pic:blipFill>"
            f"<a:blip r:embed=\"{rid}\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"/>"
            "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
            f"<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"{max_width_emu}\" cy=\"{height_emu}\"/></a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>"
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    def document_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<w:body>{''.join(self.body)}"
            '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
            "</w:body></w:document>"
        )

    def styles_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="22"/><w:color w:val="112033"/></w:rPr><w:pPr><w:spacing w:after="120" w:line="260" w:lineRule="auto"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="44"/><w:color w:val="006FA6"/></w:rPr><w:pPr><w:spacing w:after="180"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="24"/><w:color w:val="5B6D80"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="006FA6"/></w:rPr><w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="24364A"/></w:rPr><w:pPr><w:spacing w:before="180" w:after="100"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="23"/><w:color w:val="24364A"/></w:rPr><w:pPr><w:spacing w:before="140" w:after="80"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="Caption"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="20"/><w:color w:val="5B6D80"/></w:rPr><w:pPr><w:spacing w:before="100" w:after="80"/></w:pPr></w:style>'
            "</w:styles>"
        )

    def relationships_xml(self) -> str:
        style_relationship = '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        relationships = "".join(
            f'<Relationship Id="{rid}" Type="{rel_type}" Target="{target}"/>'
            for rid, rel_type, target in self.rels
        )
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{style_relationship}{relationships}</Relationships>'

    def content_types_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>"
        )

    def write(self, output_path: Path) -> None:
        with zipfile.ZipFile(extended_path(output_path), "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", self.content_types_xml())
            archive.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>',
            )
            archive.writestr("word/document.xml", self.document_xml())
            archive.writestr("word/styles.xml", self.styles_xml())
            archive.writestr("word/_rels/document.xml.rels", self.relationships_xml())
            archive.writestr(
                "docProps/core.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{xml_text(self.title)}</dc:title><dc:creator>DAMSpy VC</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}</dcterms:created></cp:coreProperties>',
            )
            archive.writestr(
                "docProps/app.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>DAMSpy VC</Application></Properties>',
            )
            for media_name, image_path in self.media:
                archive.write(extended_path(image_path), f"word/media/{media_name}")


def write_analyser_docx_summary(logs_root: Path, measurement_id: str) -> Path:
    measurement_dir, yaml_path, results_dir = resolve_measurement(logs_root, measurement_id)
    dataset = load_measurement_dataset(logs_root, measurement_id)
    yaml_dimensions = read_yaml_named_dimensions(yaml_path)
    output_path = measurement_dir / "analyser_summary.docx"
    builder = DocxReportBuilder("DAMSpy Results Analyser Summary")

    builder.add_paragraph("DAMSpy Results Analyser Summary", "Title")
    builder.add_paragraph(dataset.get("measurement_name", measurement_id), "Subtitle")
    builder.add_table(
        [
            ["Field", "Value"],
            ["Test date", report_value(dataset.get("test_date") or dataset.get("yaml_created_at"))],
            ["Tester", ANALYSER_TESTER_NAME],
            ["Measurement folder", report_value(dataset.get("measurement_name"))],
            ["Selected YAML", report_value(dataset.get("yaml_relative_path"))],
            ["Global dB ref", report_dbm(dataset.get("global_peak_dbm"))],
        ],
        [2500, 6860],
    )

    builder.add_heading("Results Analyser Details", 1)
    builder.add_table(
        [
            ["Field", "Value"],
            ["DUT hardware config", report_value(dataset.get("dut_hardware_config"))],
            ["DUT serial number", report_value(dataset.get("dut_serial_number"))],
            ["TX mode", report_value(dataset.get("tx_mode"))],
            ["RX antenna", report_value(dataset.get("rx_antenna_name"))],
            ["RX antenna comment", report_value(dataset.get("rx_antenna_comment"))],
            ["RX antenna gain", report_value(dataset.get("rx_antenna_gain_dbi"), " dBi")],
            ["RX cable loss", report_db(dataset.get("rx_cable_loss_db"))],
            ["RX distance", report_value(dataset.get("rx_dist_m"), " m")],
            ["Folders with data", report_value(len(dataset.get("folders") or []))],
        ],
        [2800, 6560],
    )

    builder.add_heading("YAML Summary", 1)
    yaml_rows = [["Field", "Value"]]
    for key, value in {**read_yaml_summary_fields(yaml_path), **yaml_dimensions}.items():
        if isinstance(value, list):
            display_value = ", ".join(str(item) for item in value)
        else:
            display_value = value
        yaml_rows.append([key.replace("_", " "), report_value(display_value)])
    builder.add_table(yaml_rows, [2800, 6560])

    builder.add_heading("Plot Summary", 1)
    plot_rows = [["Polarisation", "Orientation", "Series", "Peak", "Minimum"]]
    for plot in dataset.get("plots") or []:
        values = [
            point["rx_peak_dbm"]
            for series in plot.get("series", [])
            for point in series.get("points", [])
            if coerce_float(point.get("rx_peak_dbm")) is not None
        ]
        plot_rows.append(
            [
                plot.get("polarisation"),
                plot.get("orientation"),
                len(plot.get("series") or []),
                report_dbm(max(values) if values else None),
                report_dbm(min(values) if values else None),
            ]
        )
    builder.add_table(plot_rows, [1700, 2100, 1400, 2080, 2080])

    builder.add_heading("Plots", 1)
    pngs_by_folder: dict[str, Path] = {}
    for folder in dataset.get("folders") or []:
        folder_path = results_dir / str(folder.get("folder_name", ""))
        png_path = find_first_png(folder_path)
        if png_path is not None:
            pngs_by_folder[str(folder.get("folder_name"))] = png_path

    for plot in dataset.get("plots") or []:
        builder.add_heading(f"{plot.get('polarisation')} / {plot.get('orientation')}", 2)
        for series in plot.get("series") or []:
            folder_name = str(series.get("folder_name", ""))
            png_path = pngs_by_folder.get(folder_name)
            if png_path is None:
                continue
            caption = (
                f"{folder_name} | Channel {report_value(series.get('channel'))} | "
                f"Power {report_value(series.get('power_level'))} | "
                f"{report_hz(series.get('frequency_hz'))} | Peak {report_dbm(series.get('peak_dbm'))}"
            )
            builder.add_image(png_path, caption)

    builder.write(output_path)
    return output_path


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

        if clean_path == "/api/results-analyser/write-summary-csv":
            self.handle_write_summary_csv()
            return

        if clean_path == "/api/results-analyser/write-docx-summary":
            self.handle_write_docx_summary()
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
        query = parse_qs(urlsplit(self.path).query)
        scope = query.get("scope", [MEASUREMENT_SCOPE_BEST])[0]
        measurements = list_measurements(self.logs_root, scope)
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

    def handle_write_summary_csv(self) -> None:
        try:
            output_path = write_measurement_summary_csv(self.logs_root)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_json(
            {
                "output_path": str(output_path),
                "relative_path": display_path(output_path, self.logs_root),
            }
        )

    def handle_write_docx_summary(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_id = query.get("measurement_id", [""])[0]

        if not measurement_id:
            self.send_json(
                {"error": "measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            output_path = write_analyser_docx_summary(self.logs_root, measurement_id)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_json(
            {
                "output_path": str(output_path),
                "relative_path": display_path(output_path, self.logs_root),
            }
        )


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

    if args.write_summary_csv:
        output_path = write_measurement_summary_csv(logs_root)
        print(f"Wrote summary CSV: {output_path}")
        return 0

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
