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
import tempfile
import webbrowser
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - exercised only when Pillow is unavailable at runtime.
    Image = ImageDraw = ImageFont = None


KNOWN_ROUTES = {"/", "/results-analyser", "/results-analyser/"}
MEASUREMENT_SCOPE_BEST = "best"
MEASUREMENT_SCOPE_LOGS = "logs"
MEASUREMENT_SCOPE_ALL = "all"
BEST_FOLDER_NAME = "_best"
ANALYSER_TESTER_NAME = "Alistair Morgan"
SIG_GEN_DEVICE_HENDRIX_TX = "hendrix_tx"
SIG_GEN_DEVICE_WIRELESS_PRO_RX = "wireless-pro-rx"
HENDRIX_GREEN = "#22c55e"
WIRELESS_PRO_GREEN = "#15803d"
CHANNEL_COLORS = [
    "#66d7ff",
    "#ffb266",
    "#86efac",
    "#f9a8d4",
    "#c4b5fd",
    "#fde047",
    "#fb7185",
    "#38bdf8",
]
FIXED_CHANNEL_COLORS = {
    "0": "#ef4444",
    "40": HENDRIX_GREEN,
    "50": HENDRIX_GREEN,
    "80": "#3b82f6",
}
MEASUREMENT_ROLE_DUT = "dut"
MEASUREMENT_ROLE_BASELINE = "baseline"
ANALYSER_SNAPSHOT_FILENAME = "analyser_snapshot.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PREFERRED_DEFAULT_MEASUREMENT_ID = (
    BEST_FOLDER_NAME + "/"
    "Antenna_Pattern_Measurement-2026-04-10_11-22-16-"
    "hendrix-tx_V3-04F_002-bodyworn-Ori_ori1_ori2-Ch_0_40_80-"
    "Pwr_10-Pol_H_V-Step_2deg-RxAnt_Horn_WR340"
)
MEASUREMENT_DEFAULT_STEM = "1_meas_azimuth"
MEASUREMENT_YAML_SUFFIXES = {".yaml", ".yml"}


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


def list_measurement_yaml_paths_from_entries(measurement_dir: Path, entries: list[os.DirEntry[str]]) -> list[Path]:
    yaml_paths: list[Path] = []

    for entry in entries:
        if not entry.is_file():
            continue

        entry_path = measurement_dir / entry.name
        if entry_path.suffix.lower() not in MEASUREMENT_YAML_SUFFIXES:
            continue

        yaml_paths.append(entry_path)

    yaml_paths.sort(
        key=lambda path: (
            0 if path.stem == MEASUREMENT_DEFAULT_STEM else 1,
            path.name.lower(),
        )
    )
    return yaml_paths


def list_measurement_yaml_paths(measurement_dir: Path) -> list[Path]:
    if not path_is_dir(measurement_dir):
        return []

    try:
        entries = iter_directory(measurement_dir)
    except OSError:
        return []

    return list_measurement_yaml_paths_from_entries(measurement_dir, entries)


def resolve_measurement_assets(
    measurement_dir: Path,
    yaml_candidates: list[Path] | None = None,
) -> tuple[Path | None, Path]:
    candidates = yaml_candidates if yaml_candidates is not None else list_measurement_yaml_paths(measurement_dir)
    legacy_results_dir = measurement_dir / MEASUREMENT_DEFAULT_STEM

    if not candidates:
        return None, legacy_results_dir

    candidate_pairs: list[tuple[int, float, int, str, Path, Path]] = []
    legacy_results_exists = path_is_dir(legacy_results_dir)

    for yaml_path in candidates:
        paired_results_dir = measurement_dir / yaml_path.stem
        paired_results_exists = path_is_dir(paired_results_dir)

        if paired_results_exists:
            results_dir = paired_results_dir
            score = 4
        elif legacy_results_exists:
            results_dir = legacy_results_dir
            score = 3
        else:
            results_dir = paired_results_dir
            score = 2 if yaml_path.stem == MEASUREMENT_DEFAULT_STEM else 1

        try:
            yaml_mtime = os.stat(extended_path(yaml_path)).st_mtime
        except OSError:
            yaml_mtime = 0.0

        candidate_pairs.append(
            (
                score,
                yaml_mtime,
                1 if yaml_path.stem == MEASUREMENT_DEFAULT_STEM else 0,
                yaml_path.name.lower(),
                yaml_path,
                results_dir,
            )
        )

    candidate_pairs.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    _, _, _, _, yaml_path, results_dir = candidate_pairs[0]
    return yaml_path, results_dir


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


def normalise_yaml_lookup_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""

    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    return numeric_value if math.isfinite(numeric_value) else None


def normalise_measurement_role(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {MEASUREMENT_ROLE_DUT, "device_under_test"}:
        return MEASUREMENT_ROLE_DUT
    if text in {MEASUREMENT_ROLE_BASELINE, "reference"}:
        return MEASUREMENT_ROLE_BASELINE
    return None


def infer_measurement_role(*values: Any) -> str:
    combined = " ".join(str(value or "") for value in values).strip().lower()

    if "wireless-pro" in combined or "wireless pro" in combined:
        return MEASUREMENT_ROLE_BASELINE

    if "hendrix" in combined or "rxcc" in combined:
        return MEASUREMENT_ROLE_DUT

    return MEASUREMENT_ROLE_DUT


def normalise_sig_gen_device_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None

    compact = text.replace("_", "-").replace(" ", "-")
    hendrix_compact = SIG_GEN_DEVICE_HENDRIX_TX.replace("_", "-")
    wireless_pro_compact = SIG_GEN_DEVICE_WIRELESS_PRO_RX.replace("_", "-")

    if compact == hendrix_compact or ("hendrix" in compact and "tx" in compact):
        return SIG_GEN_DEVICE_HENDRIX_TX

    if compact == wireless_pro_compact or ("wireless-pro" in compact and "rx" in compact):
        return SIG_GEN_DEVICE_WIRELESS_PRO_RX

    return text


def normalise_tx_antenna(value: Any, folder_name: str = "") -> str | None:
    text = str(value or "").strip().lower()

    if text in {"1", "id1"} or "id1" in text or "fpc" in text or "secondary" in text:
        return "secondary"
    if text in {"0", "id0"} or "id0" in text or "pcb" in text or "main" in text or "primary" in text:
        return "main"

    folder_text = folder_name.lower()
    if "ant-1" in folder_text or "ant-id1" in folder_text or "ant-fpc" in folder_text or "ant-secondary" in folder_text:
        return "secondary"
    if "ant-0" in folder_text or "ant-id0" in folder_text or "ant-pcb" in folder_text or "ant-main" in folder_text:
        return "main"

    return None


def format_antenna_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "fpc" in text or "id1" in text:
        return "FPC ant id1"
    if "pcb" in text or "id0" in text:
        return "PCB ant id0"

    antenna = normalise_tx_antenna(value)
    if antenna == "secondary":
        return "Secondary"
    if antenna == "main":
        return "Main"
    return ""


def resolve_series_line_style_key(antenna: Any, device_type: Any, measurement_role: str) -> str:
    antenna_role = normalise_tx_antenna(antenna)
    normalised_device_type = normalise_sig_gen_device_type(device_type)

    if normalised_device_type == SIG_GEN_DEVICE_HENDRIX_TX:
        return "dut-main"

    if normalised_device_type == SIG_GEN_DEVICE_WIRELESS_PRO_RX:
        return "baseline-secondary" if antenna_role == "secondary" else "baseline-main"

    role = normalise_measurement_role(measurement_role) or MEASUREMENT_ROLE_DUT

    if role == MEASUREMENT_ROLE_DUT:
        return "dut-secondary" if antenna_role == "secondary" else "dut-main"

    return "baseline-secondary" if antenna_role == "secondary" else "baseline-main"


def series_line_pattern(antenna: Any, device_type: Any, measurement_role: str) -> tuple[int, ...] | None:
    line_style_key = resolve_series_line_style_key(antenna, device_type, measurement_role)

    if line_style_key == "dut-secondary":
        return (18, 10)

    if line_style_key == "baseline-secondary":
        return (2, 10, 18, 10)

    if line_style_key == "baseline-main":
        return (2, 10)

    return None


def infer_report_tx_power_dbm(device_type: Any, *fallback_values: Any) -> float | None:
    normalised_device_type = normalise_sig_gen_device_type(device_type)

    if normalised_device_type == SIG_GEN_DEVICE_HENDRIX_TX:
        return 18.0

    if normalised_device_type == SIG_GEN_DEVICE_WIRELESS_PRO_RX:
        return 14.0

    combined = " ".join(str(value or "") for value in fallback_values).strip().lower()
    if "hendrix" in combined or "rxcc" in combined:
        return 18.0
    if "wireless-pro" in combined or "wireless pro" in combined:
        return 14.0

    return None


def antenna_summary_sort_rank(value: Any) -> int:
    antenna_role = normalise_tx_antenna(value)
    if antenna_role == "main":
        return 0
    if antenna_role == "secondary":
        return 1
    return 2


def build_plot_summary_rows(dataset: dict[str, Any]) -> list[list[str]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    default_product = report_value(dataset.get("dut_product") or dataset.get("measurement_name"))

    for plot in dataset.get("plots") or []:
        polarisation = report_value(plot.get("polarisation"))
        orientation = report_value(plot.get("orientation"))

        for series in plot.get("series") or []:
            product_name = report_value(series.get("product_name") or default_product)
            antenna_label = report_value(format_antenna_label(series.get("antenna")) or series.get("antenna"))
            key = (product_name, antenna_label, polarisation, orientation)
            tx_power_dbm = infer_report_tx_power_dbm(
                series.get("device_type"),
                series.get("product_name"),
                dataset.get("dut_product"),
                dataset.get("measurement_name"),
            )
            row = grouped.setdefault(
                key,
                {
                    "product_name": product_name,
                    "antenna_label": antenna_label,
                    "polarisation": polarisation,
                    "orientation": orientation,
                    "tx_power_dbm": tx_power_dbm,
                    "best_eirp_dbm": None,
                    "best_dbi": None,
                },
            )

            eirp_dbm = coerce_float(series.get("eirp_dbm"))
            gain_dbi = dbd_to_dbi(series.get("gain_dbd"))

            if eirp_dbm is not None:
                row["best_eirp_dbm"] = eirp_dbm if row["best_eirp_dbm"] is None else max(row["best_eirp_dbm"], eirp_dbm)

            if gain_dbi is not None:
                row["best_dbi"] = gain_dbi if row["best_dbi"] is None else max(row["best_dbi"], gain_dbi)

    rows = [["Product", "Antenna", "Polarisation", "Orientation", "TX Power", "Best EIRP", "Best dBi"]]
    ordered_keys = sorted(
        grouped.keys(),
        key=lambda item: (
            natural_sort_key(item[0]),
            antenna_summary_sort_rank(item[1]),
            natural_sort_key(item[1]),
            polarisation_sort_key(item[2]),
            natural_sort_key(item[3]),
        ),
    )

    for key in ordered_keys:
        row = grouped[key]
        rows.append(
            [
                row["product_name"],
                row["antenna_label"],
                row["polarisation"],
                row["orientation"],
                report_dbm(row["tx_power_dbm"]),
                report_dbm(row["best_eirp_dbm"]),
                report_dbi(row["best_dbi"]),
            ]
        )

    return rows


def format_polarisation_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text == "H":
        return "Hpol"
    if text == "V":
        return "Vpol"
    return str(value or "").strip() or "-"


def format_summary_channel_label(value: Any) -> str:
    text = str(value or "").strip()
    return f"ch{text}" if text else "-"


def compact_measurement_identity_label(measurement: dict[str, Any]) -> str:
    measurement_name = str(measurement.get("measurement_name") or "").strip()
    measurement_name = re.sub(r"^Antenna_Pattern_Measurement-", "", measurement_name)
    folder_match = re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-(.+?)-Ori(?:_|-|$)", measurement_name, re.IGNORECASE)

    if folder_match is not None:
        folder_segment = folder_match.group(1).strip()
        tokens = [token for token in folder_segment.split("_") if token]
        if len(tokens) >= 3 and re.match(r"^sn", tokens[2], re.IGNORECASE):
            return "_".join(tokens[:3])
        if folder_segment:
            return folder_segment

    hardware_config = str(measurement.get("dut_hardware_config") or "").strip()
    serial_number = str(measurement.get("dut_serial_number") or "").strip()
    if hardware_config and serial_number:
        return f"{hardware_config}_{serial_number}"

    if hardware_config or serial_number:
        return hardware_config or serial_number

    measurement_label = str(measurement.get("measurement_label") or "").strip()
    if measurement_label:
        return measurement_label

    yaml_relative_path = str(measurement.get("yaml_relative_path") or "").strip()
    if yaml_relative_path:
        return re.sub(r"\.[^.]+$", "", Path(yaml_relative_path).name)

    return "-"


def normalise_polar_angle(value: Any) -> float | None:
    numeric_value = coerce_float(value)
    if numeric_value is None:
        return None

    wrapped = ((numeric_value % 360.0) + 360.0) % 360.0
    return 0.0 if abs(wrapped - 360.0) <= 1e-9 else wrapped


def calculate_total_above_threshold_span_deg(points: list[dict[str, Any]], threshold_dbm: Any) -> float | None:
    numeric_threshold = coerce_float(threshold_dbm)
    if numeric_threshold is None or len(points) < 2:
        return None

    sample_map: dict[str, tuple[float, float]] = {}
    for point in points:
        angle = normalise_polar_angle(point.get("angle_deg"))
        value = coerce_float(point.get("rx_peak_dbm"))
        if angle is None or value is None:
            continue

        key = f"{angle:.6f}"
        existing = sample_map.get(key)
        if existing is None or value > existing[1]:
            sample_map[key] = (angle, value)

    samples = sorted(sample_map.values(), key=lambda item: item[0])
    if len(samples) < 2:
        return None

    total_span = 0.0
    for index, current in enumerate(samples):
        if index == len(samples) - 1:
            next_sample = (samples[0][0] + 360.0, samples[0][1])
        else:
            next_sample = samples[index + 1]

        delta_angle = next_sample[0] - current[0]
        if delta_angle <= 0:
            continue

        current_above = current[1] >= numeric_threshold
        next_above = next_sample[1] >= numeric_threshold

        if current_above and next_above:
            total_span += delta_angle
            continue

        if abs(current[1] - next_sample[1]) <= 1e-12:
            continue

        crossing_ratio = (numeric_threshold - current[1]) / (next_sample[1] - current[1])
        if crossing_ratio < 0 or crossing_ratio > 1:
            continue

        crossing_angle = current[0] + crossing_ratio * delta_angle
        if current_above and not next_above:
            total_span += crossing_angle - current[0]
        elif (not current_above) and next_above:
            total_span += next_sample[0] - crossing_angle

    return total_span


def build_combined_plot_matrix_tables(dataset: dict[str, Any], max_plot_columns_per_table: int = 4) -> list[tuple[list[list[str]], list[int]]]:
    source_measurements = dataset.get("source_measurements") or []
    visible_measurement_ids: set[str] = set()
    cell_lookup: dict[tuple[str, tuple[str, str, str, str]], dict[str, float | None]] = {}
    column_lookup: dict[tuple[str, str, str, str], dict[str, str]] = {}

    for plot in dataset.get("plots") or []:
        polarisation = str(plot.get("polarisation") or "").strip()
        orientation = str(plot.get("orientation") or "").strip()

        for series in plot.get("series") or []:
            antenna_label = format_antenna_label(series.get("antenna")) or str(series.get("antenna") or "").strip()
            column_key = (
                orientation,
                polarisation,
                str(series.get("channel") or "").strip(),
                antenna_label,
            )
            column_lookup.setdefault(
                column_key,
                {
                    "orientation": orientation,
                    "polarisation": polarisation,
                    "channel": str(series.get("channel") or "").strip(),
                    "antenna": antenna_label,
                },
            )

            measurement_id = str(series.get("source_measurement_id") or "").strip()
            if not measurement_id:
                continue

            visible_measurement_ids.add(measurement_id)
            eirp_dbm = coerce_float(series.get("eirp_dbm"))
            peak_dbm = coerce_float(series.get("peak_dbm"))
            span_deg = calculate_total_above_threshold_span_deg(series.get("points") or [], None if peak_dbm is None else peak_dbm - 3.0)
            existing = cell_lookup.get((measurement_id, column_key))

            if eirp_dbm is None:
                continue

            if existing is None or existing.get("eirp_dbm") is None or eirp_dbm > existing.get("eirp_dbm", float("-inf")):
                cell_lookup[(measurement_id, column_key)] = {
                    "eirp_dbm": eirp_dbm,
                    "span_deg": span_deg,
                }

    if not column_lookup or not visible_measurement_ids:
        return []

    ordered_measurements: list[dict[str, Any]] = []
    remaining_measurement_ids = set(visible_measurement_ids)
    for measurement in source_measurements:
        measurement_id = str(measurement.get("measurement_id") or "").strip()
        if measurement_id and measurement_id in remaining_measurement_ids:
            ordered_measurements.append(measurement)
            remaining_measurement_ids.discard(measurement_id)

    for measurement_id in sorted(remaining_measurement_ids, key=natural_sort_key):
        ordered_measurements.append({"measurement_id": measurement_id, "measurement_name": measurement_id})

    ordered_columns = sorted(
        column_lookup.keys(),
        key=lambda item: (
            natural_sort_key(item[0]),
            polarisation_sort_key(item[1]),
            natural_sort_key(item[2]),
            antenna_summary_sort_rank(item[3]),
            natural_sort_key(item[3]),
        ),
    )

    tables: list[tuple[list[list[str]], list[int]]] = []
    for start_index in range(0, len(ordered_columns), max_plot_columns_per_table):
        column_chunk = ordered_columns[start_index:start_index + max_plot_columns_per_table]
        header_row = ["Config_Serial"]

        for column_key in column_chunk:
            column = column_lookup[column_key]
            label_parts = [
                column.get("orientation") or "-",
                format_polarisation_label(column.get("polarisation")),
                format_summary_channel_label(column.get("channel")),
            ]
            if column.get("antenna"):
                label_parts.append(str(column["antenna"]))
            label = " ".join(part for part in label_parts if part and part != "-")
            header_row.extend([f"{label} EIRP", f"{label} 3dB Span"])

        rows = [header_row]
        for measurement in ordered_measurements:
            measurement_id = str(measurement.get("measurement_id") or "").strip()
            row = [compact_measurement_identity_label(measurement)]

            for column_key in column_chunk:
                cell = cell_lookup.get((measurement_id, column_key))
                eirp_text = "-" if cell is None or cell.get("eirp_dbm") is None else f"{round(float(cell['eirp_dbm'])):.0f}"
                span_value = None if cell is None else cell.get("span_deg")
                span_text = "-" if span_value is None else f"{round(float(span_value)):.0f}\N{DEGREE SIGN}"
                row.extend([eirp_text, span_text])

            rows.append(row)

        widths = [2200] + [950, 1050] * len(column_chunk)
        tables.append((rows, widths))

    return tables


def should_close_polar_wrap(points: list[dict[str, Any]], max_wrap_gap_degrees: float = 10.0) -> bool:
    if len(points) < 2:
        return False

    first_angle = coerce_float(points[0].get("angle_deg"))
    last_angle = coerce_float(points[-1].get("angle_deg"))

    if first_angle is None or last_angle is None:
        return False

    if abs(first_angle) > 1e-6:
        return False

    wrap_gap = (360.0 - last_angle) + first_angle
    return 0.0 < wrap_gap <= max_wrap_gap_degrees


def interpolate_point(start: tuple[float, float], end: tuple[float, float], ratio: float) -> tuple[float, float]:
    return (
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    )


def draw_patterned_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: tuple[int, int, int],
    width: int,
    pattern: tuple[int, ...] | None,
) -> None:
    if len(points) < 2:
        return

    if not pattern:
        draw.line(points, fill=fill, width=width, joint="curve")
        return

    pattern_values = [max(1.0, float(value)) for value in pattern]
    pattern_index = 0
    remaining = pattern_values[pattern_index]
    drawing = True

    for start, end in zip(points, points[1:]):
        segment_length = math.hypot(end[0] - start[0], end[1] - start[1])
        if segment_length <= 0:
            continue

        segment_offset = 0.0
        while segment_offset < segment_length:
            step = min(remaining, segment_length - segment_offset)
            start_ratio = segment_offset / segment_length
            end_ratio = (segment_offset + step) / segment_length
            segment_start = interpolate_point(start, end, start_ratio)
            segment_end = interpolate_point(start, end, end_ratio)

            if drawing:
                draw.line((segment_start, segment_end), fill=fill, width=width)

            segment_offset += step
            remaining -= step

            if remaining <= 1e-6:
                pattern_index = (pattern_index + 1) % len(pattern_values)
                remaining = pattern_values[pattern_index]
                drawing = pattern_index % 2 == 0


def read_yaml_summary_fields(path: Path) -> dict[str, Any]:
    field_values: dict[str, Any] = {
        "dut_product": None,
        "dut_hardware_config": None,
        "dut_serial_number": None,
        "sig_gen_1_device_type": None,
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
        normalise_yaml_lookup_key("DUT_product"): "dut_product",
        normalise_yaml_lookup_key("DUT_hardware_config"): "dut_hardware_config",
        normalise_yaml_lookup_key("DUT_serial_number"): "dut_serial_number",
        normalise_yaml_lookup_key("tx_mode"): "tx_mode",
        normalise_yaml_lookup_key("Tx_mode"): "tx_mode",
        normalise_yaml_lookup_key("foldername_comment"): "foldername_comment",
        normalise_yaml_lookup_key("orientation_photo_location"): "orientation_photo_location",
    }
    section_fields = {
        normalise_yaml_lookup_key("sig_gen_1"): {
            normalise_yaml_lookup_key("device_type"): "sig_gen_1_device_type",
            normalise_yaml_lookup_key("tx_mode"): "tx_mode",
            normalise_yaml_lookup_key("tx_cable_loss"): "tx_cable_loss_db",
            normalise_yaml_lookup_key("tx_cable_loss_db"): "tx_cable_loss_db",
            normalise_yaml_lookup_key("tx_power"): "tx_power_dbm",
            normalise_yaml_lookup_key("level_dbm"): "tx_power_dbm",
        },
        normalise_yaml_lookup_key("rx_path"): {
            normalise_yaml_lookup_key("antenna"): "rx_antenna_name",
            normalise_yaml_lookup_key("antenna_comment"): "rx_antenna_comment",
            normalise_yaml_lookup_key("rx_antena_gain"): "rx_antenna_gain_dbi",
            normalise_yaml_lookup_key("rx_antenna_gain"): "rx_antenna_gain_dbi",
            normalise_yaml_lookup_key("rx_cable_loss"): "rx_cable_loss_db",
            normalise_yaml_lookup_key("rx_cable_loss_2.45Ghz"): "rx_cable_loss_db",
            normalise_yaml_lookup_key("rx_dist_m"): "rx_dist_m",
        },
    }
    active_section: str | None = None
    current_dut_definition: str | None = None
    dut_definitions: dict[str, dict[str, Any]] = {}
    selected_duts: list[str] = []
    dut_definition_fields = {
        normalise_yaml_lookup_key("product"): "dut_product",
        normalise_yaml_lookup_key("hardware_config"): "dut_hardware_config",
        normalise_yaml_lookup_key("serial_number"): "dut_serial_number",
        normalise_yaml_lookup_key("foldername_comment"): "foldername_comment",
    }

    with open(extended_path(path), "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            content = strip_yaml_inline_comment(line)
            stripped = content.strip()

            if not stripped:
                continue

            indent = len(content) - len(content.lstrip(" \t"))

            if indent == 0:
                active_section = None
                current_dut_definition = None

                if stripped.endswith(":"):
                    section_name = normalise_yaml_lookup_key(stripped[:-1])
                    if section_name == normalise_yaml_lookup_key("dut_definitions"):
                        active_section = section_name
                    else:
                        active_section = section_name if section_name in section_fields else None
                    continue

                if ":" not in stripped:
                    continue

                key, raw_value = stripped.split(":", 1)
                normalised_key = normalise_yaml_lookup_key(key)
                if normalised_key == normalise_yaml_lookup_key("DUTS"):
                    parsed_value = parse_yaml_scalar(raw_value)
                    if isinstance(parsed_value, list):
                        selected_duts = [str(item).strip() for item in parsed_value if str(item).strip()]
                    elif parsed_value not in {None, ""}:
                        selected_duts = [str(parsed_value).strip()]
                    continue

                mapped_key = top_level_fields.get(normalised_key)
                if mapped_key is None:
                    continue

                field_values[mapped_key] = parse_yaml_scalar(raw_value)
                continue

            if active_section == normalise_yaml_lookup_key("dut_definitions"):
                if stripped.endswith(":") and ":" not in stripped[:-1]:
                    current_dut_definition = stripped[:-1].strip()
                    dut_definitions.setdefault(current_dut_definition, {})
                    continue

                if current_dut_definition is None or ":" not in stripped:
                    continue

                key, raw_value = stripped.split(":", 1)
                mapped_key = dut_definition_fields.get(normalise_yaml_lookup_key(key))
                if mapped_key is None:
                    continue

                dut_definitions.setdefault(current_dut_definition, {})[mapped_key] = parse_yaml_scalar(raw_value)
                continue

            if active_section is None or ":" not in stripped:
                continue

            key, raw_value = stripped.split(":", 1)
            mapped_key = section_fields.get(active_section, {}).get(normalise_yaml_lookup_key(key))
            if mapped_key is None:
                continue

            field_values[mapped_key] = parse_yaml_scalar(raw_value)

    selected_dut_definition: dict[str, Any] | None = None
    for dut_name in selected_duts:
        if dut_name in dut_definitions:
            selected_dut_definition = dut_definitions[dut_name]
            break

    if selected_dut_definition is None and dut_definitions:
        first_key = next(iter(dut_definitions))
        selected_dut_definition = dut_definitions[first_key]

    if selected_dut_definition:
        for key in ("dut_product", "dut_hardware_config", "dut_serial_number", "foldername_comment"):
            if selected_dut_definition.get(key) not in {None, ""}:
                field_values[key] = selected_dut_definition.get(key)

    field_values["tx_cable_loss_db"] = coerce_float(field_values["tx_cable_loss_db"])
    field_values["tx_power_dbm"] = coerce_float(field_values["tx_power_dbm"])
    field_values["sig_gen_1_device_type"] = normalise_sig_gen_device_type(field_values["sig_gen_1_device_type"])
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


def build_measurement_completion(results_dir: Path, yaml_path: Path) -> dict[str, Any]:
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

        yaml_candidates = list_measurement_yaml_paths_from_entries(current_dir, entries)
        yaml_path, _ = resolve_measurement_assets(current_dir, yaml_candidates)
        if yaml_path is not None:
            discovered.append(current_dir)

        result_dir_names = {MEASUREMENT_DEFAULT_STEM, *(yaml_candidate.stem for yaml_candidate in yaml_candidates)}

        for entry in entries:
            if not entry.is_dir():
                continue

            if entry.name in result_dir_names:
                continue

            pending.append(current_dir / entry.name)

    return discovered


def measurement_manifest(logs_root: Path, measurement_dir: Path) -> dict[str, Any] | None:
    yaml_path, results_dir = resolve_measurement_assets(measurement_dir)

    if not path_is_dir(measurement_dir) or yaml_path is None or not path_is_file(yaml_path):
        return None

    measurement_id = display_path(measurement_dir, logs_root)
    measurement_name = measurement_dir.name

    updated_at = max(
        os.stat(extended_path(measurement_dir)).st_mtime,
        os.stat(extended_path(yaml_path)).st_mtime,
    )
    measurement_timestamp = measurement_name_timestamp(measurement_id)
    completion = build_measurement_completion(results_dir, yaml_path)

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

    yaml_path, results_dir = resolve_measurement_assets(measurement_dir)
    if yaml_path is None:
        yaml_path = measurement_dir / f"{MEASUREMENT_DEFAULT_STEM}.yaml"
    return measurement_dir, yaml_path, results_dir


def dedupe_measurement_ids(measurement_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    for measurement_id in measurement_ids:
        text = str(measurement_id or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)

    return ordered


def parse_measurement_ids(query: dict[str, list[str]]) -> list[str]:
    measurement_ids = dedupe_measurement_ids(query.get("measurement_id", []))

    if measurement_ids:
        return measurement_ids

    joined_values = query.get("measurement_ids", [])
    expanded: list[str] = []

    for value in joined_values:
        expanded.extend(part.strip() for part in str(value or "").split(","))

    return dedupe_measurement_ids(expanded)


def dataset_sort_timestamp(dataset: dict[str, Any]) -> float:
    measurement_id = str(dataset.get("measurement_id") or "")
    measurement_timestamp = measurement_name_timestamp(measurement_id)
    if measurement_timestamp is not None:
        return measurement_timestamp

    updated_at = str(dataset.get("updated_at") or "")
    try:
        return datetime.fromisoformat(updated_at).timestamp()
    except ValueError:
        return 0.0


def short_measurement_label(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^Antenna_Pattern_Measurement-", "", text)
    return text if len(text) <= 52 else text[:49] + "..."


def common_text_value(values: list[Any]) -> str | None:
    texts = [str(value).strip() for value in values if value not in {None, ""}]
    if not texts:
        return None

    unique = {text for text in texts}
    if len(unique) == 1:
        return texts[0]

    return "Multiple"


def common_numeric_value(values: list[Any]) -> float | None:
    numerics = [coerce_float(value) for value in values]
    valid = [value for value in numerics if value is not None]
    if not valid:
        return None

    rounded = {round(value, 6) for value in valid}
    if len(rounded) == 1:
        return valid[0]

    return None


def load_combined_measurement_dataset(logs_root: Path, measurement_ids: list[str]) -> dict[str, Any]:
    ordered_measurement_ids = dedupe_measurement_ids(measurement_ids)

    if not ordered_measurement_ids:
        raise ValueError("At least one measurement_id is required")

    datasets = [load_measurement_dataset(logs_root, measurement_id) for measurement_id in ordered_measurement_ids]
    datasets.sort(key=dataset_sort_timestamp)
    anchor = datasets[-1]
    source_measurements = []

    for dataset in datasets:
        source_measurements.append(
            {
                "measurement_id": dataset.get("measurement_id"),
                "measurement_name": dataset.get("measurement_name"),
                "measurement_label": short_measurement_label(dataset.get("measurement_name")),
                "dut_hardware_config": dataset.get("dut_hardware_config"),
                "dut_serial_number": dataset.get("dut_serial_number"),
                "yaml_relative_path": dataset.get("yaml_relative_path"),
                "updated_at": dataset.get("updated_at"),
                "folder_count": len(dataset.get("folders") or []),
            }
        )

    all_series: list[dict[str, Any]] = []
    rows: set[str] = set()
    columns: set[str] = set()
    orientation_images: dict[str, str] = {}
    angle_min: float | None = None
    angle_max: float | None = None
    global_peak_dbm: float | None = None
    updated_at = max(dataset_sort_timestamp(dataset) for dataset in datasets)

    for dataset in datasets:
        orientation_images.update(dataset.get("orientation_images") or {})
        rows.update(str(row) for row in dataset.get("rows") or [])
        columns.update(str(column) for column in dataset.get("columns") or [])

        for plot in dataset.get("plots") or []:
            for series in plot.get("series") or []:
                points = []
                for point in series.get("points") or []:
                    rx_peak_dbm = coerce_float(point.get("rx_peak_dbm"))
                    angle_deg = coerce_float(point.get("angle_deg"))
                    if rx_peak_dbm is None or angle_deg is None:
                        continue

                    global_peak_dbm = rx_peak_dbm if global_peak_dbm is None else max(global_peak_dbm, rx_peak_dbm)
                    angle_min = angle_deg if angle_min is None else min(angle_min, angle_deg)
                    angle_max = angle_deg if angle_max is None else max(angle_max, angle_deg)
                    points.append(
                        {
                            "angle_deg": angle_deg,
                            "rx_peak_dbm": rx_peak_dbm,
                        }
                    )

                if not points:
                    continue

                all_series.append(
                    {
                        "source_measurement_id": dataset.get("measurement_id"),
                        "source_measurement_name": dataset.get("measurement_name"),
                        "dut_serial_number": dataset.get("dut_serial_number"),
                        "product_name": dataset.get("dut_product") or dataset.get("measurement_name"),
                        "measurement_label": short_measurement_label(dataset.get("measurement_name")),
                        "folder_name": series.get("folder_name"),
                        "polarisation": plot.get("polarisation"),
                        "orientation": plot.get("orientation"),
                        "channel": series.get("channel"),
                        "power_level": series.get("power_level"),
                        "antenna": series.get("antenna"),
                        "device_type": series.get("device_type"),
                        "frequency_hz": series.get("frequency_hz"),
                        "peak_dbm": coerce_float(series.get("peak_dbm")),
                        "eirp_dbm": coerce_float(series.get("eirp_dbm")),
                        "gain_dbd": coerce_float(series.get("gain_dbd")),
                        "points": points,
                    }
                )

    if global_peak_dbm is None or angle_min is None or angle_max is None:
        return {
            "measurement_id": anchor.get("measurement_id"),
            "measurement_ids": ordered_measurement_ids,
            "measurement_name": f"Combined selection ({len(datasets)} YAMLs)",
            "yaml_relative_path": f"{len(datasets)} YAMLs selected",
            "yaml_relative_paths": [dataset.get("yaml_relative_path") for dataset in datasets],
            "yaml_created_at": anchor.get("yaml_created_at"),
            "updated_at": anchor.get("updated_at"),
            "combined": True,
            "selection_count": len(datasets),
            "source_measurements": source_measurements,
            "suggested_measurement_role": anchor.get("suggested_measurement_role"),
            "sig_gen_1_device_type": common_text_value([dataset.get("sig_gen_1_device_type") for dataset in datasets]) or anchor.get("sig_gen_1_device_type"),
            "dut_product": common_text_value([dataset.get("dut_product") for dataset in datasets]) or anchor.get("dut_product"),
            "dut_hardware_config": common_text_value([dataset.get("dut_hardware_config") for dataset in datasets]),
            "dut_serial_number": common_text_value([dataset.get("dut_serial_number") for dataset in datasets]),
            "tx_mode": common_text_value([dataset.get("tx_mode") for dataset in datasets]),
            "rx_antenna_name": common_text_value([dataset.get("rx_antenna_name") for dataset in datasets]),
            "rx_antenna_comment": common_text_value([dataset.get("rx_antenna_comment") for dataset in datasets]),
            "rx_antenna_gain_dbi": common_numeric_value([dataset.get("rx_antenna_gain_dbi") for dataset in datasets]),
            "rx_cable_loss_db": common_numeric_value([dataset.get("rx_cable_loss_db") for dataset in datasets]),
            "rx_dist_m": common_numeric_value([dataset.get("rx_dist_m") for dataset in datasets]),
            "global_peak_dbm": None,
            "rows": [],
            "columns": [],
            "orientation_images": orientation_images,
            "folders": [],
            "plots": [],
            "x_range": {"min": 0, "max": 0},
            "y_range": {"min": -1, "max": 0},
        }

    grouped_plots: dict[tuple[str, str], dict[str, Any]] = {}
    folder_records = []
    normalised_min = 0.0

    for series in all_series:
        peak_dbm = coerce_float(series.get("peak_dbm"))
        peak_offset_db = None if peak_dbm is None else peak_dbm - global_peak_dbm
        plotted_points = []

        for point in series["points"]:
            normalised_db = point["rx_peak_dbm"] - global_peak_dbm
            normalised_min = min(normalised_min, normalised_db)
            plotted_points.append(
                {
                    "angle_deg": round(point["angle_deg"], 6),
                    "rx_peak_dbm": round(point["rx_peak_dbm"], 6),
                    "normalised_db": round(normalised_db, 6),
                }
            )

        folder_records.append(
            {
                "source_measurement_id": series.get("source_measurement_id"),
                "source_measurement_name": series.get("source_measurement_name"),
                "dut_serial_number": series.get("dut_serial_number"),
                "product_name": series.get("product_name"),
                "measurement_label": series.get("measurement_label"),
                "folder_name": series.get("folder_name"),
                "orientation": series.get("orientation"),
                "polarisation": series.get("polarisation"),
                "channel": series.get("channel"),
                "power_level": series.get("power_level"),
                "antenna": series.get("antenna"),
                "device_type": series.get("device_type"),
                "frequency_hz": series.get("frequency_hz"),
                "peak_dbm": round(peak_dbm, 6) if peak_dbm is not None else None,
                "eirp_dbm": round(series["eirp_dbm"], 6) if series.get("eirp_dbm") is not None else None,
                "gain_dbd": round(series["gain_dbd"], 6) if series.get("gain_dbd") is not None else None,
            }
        )

        group_key = (str(series.get("polarisation")), str(series.get("orientation")))
        group = grouped_plots.setdefault(
            group_key,
            {
                "polarisation": group_key[0],
                "orientation": group_key[1],
                "series": [],
            },
        )
        group["series"].append(
            {
                "source_measurement_id": series.get("source_measurement_id"),
                "source_measurement_name": series.get("source_measurement_name"),
                "dut_serial_number": series.get("dut_serial_number"),
                "product_name": series.get("product_name"),
                "measurement_label": series.get("measurement_label"),
                "folder_name": series.get("folder_name"),
                "channel": series.get("channel"),
                "power_level": series.get("power_level"),
                "antenna": series.get("antenna"),
                "device_type": series.get("device_type"),
                "frequency_hz": series.get("frequency_hz"),
                "peak_dbm": round(peak_dbm, 6) if peak_dbm is not None else None,
                "eirp_dbm": round(series["eirp_dbm"], 6) if series.get("eirp_dbm") is not None else None,
                "gain_dbd": round(series["gain_dbd"], 6) if series.get("gain_dbd") is not None else None,
                "peak_offset_db": round(peak_offset_db, 6) if peak_offset_db is not None else None,
                "points": plotted_points,
            }
        )

    y_floor = min(-1.0, math.floor(normalised_min / 5.0) * 5.0)
    plot_records = [
        grouped_plots[key]
        for key in sorted(grouped_plots.keys(), key=lambda value: (polarisation_sort_key(value[0]), natural_sort_key(value[1])))
    ]

    return {
        "measurement_id": anchor.get("measurement_id"),
        "measurement_ids": ordered_measurement_ids,
        "measurement_name": f"Combined selection ({len(datasets)} YAMLs)",
        "yaml_relative_path": f"{len(datasets)} YAMLs selected",
        "yaml_relative_paths": [dataset.get("yaml_relative_path") for dataset in datasets],
        "yaml_created_at": anchor.get("yaml_created_at"),
        "updated_at": anchor.get("updated_at"),
        "combined": True,
        "selection_count": len(datasets),
        "source_measurements": source_measurements,
        "suggested_measurement_role": anchor.get("suggested_measurement_role"),
        "sig_gen_1_device_type": common_text_value([dataset.get("sig_gen_1_device_type") for dataset in datasets]) or anchor.get("sig_gen_1_device_type"),
        "dut_product": common_text_value([dataset.get("dut_product") for dataset in datasets]) or "Combined Selection",
        "dut_hardware_config": common_text_value([dataset.get("dut_hardware_config") for dataset in datasets]),
        "dut_serial_number": common_text_value([dataset.get("dut_serial_number") for dataset in datasets]),
        "tx_mode": common_text_value([dataset.get("tx_mode") for dataset in datasets]),
        "rx_antenna_name": common_text_value([dataset.get("rx_antenna_name") for dataset in datasets]),
        "rx_antenna_comment": common_text_value([dataset.get("rx_antenna_comment") for dataset in datasets]),
        "rx_antenna_gain_dbi": common_numeric_value([dataset.get("rx_antenna_gain_dbi") for dataset in datasets]),
        "rx_cable_loss_db": common_numeric_value([dataset.get("rx_cable_loss_db") for dataset in datasets]),
        "rx_dist_m": common_numeric_value([dataset.get("rx_dist_m") for dataset in datasets]),
        "global_peak_dbm": round(global_peak_dbm, 6),
        "rows": sorted(rows, key=polarisation_sort_key),
        "columns": sorted(columns, key=natural_sort_key),
        "orientation_images": orientation_images,
        "folders": sorted(
            folder_records,
            key=lambda item: (
                natural_sort_key(item.get("source_measurement_name") or ""),
                natural_sort_key(item.get("folder_name") or ""),
            ),
        ),
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
        normalise_yaml_lookup_key("orientations"): "orientations",
        normalise_yaml_lookup_key("polarisation"): "polarisation",
        normalise_yaml_lookup_key("step_deg"): "step_deg",
    }
    section_fields = {
        normalise_yaml_lookup_key("sig_gen_1"): {
            normalise_yaml_lookup_key("channels"): "channels",
            normalise_yaml_lookup_key("power_levels"): "power_levels",
            normalise_yaml_lookup_key("CTX"): "CTX",
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

            indent = len(content) - len(content.lstrip(" \t"))

            if indent == 0:
                active_section = None

                if stripped.endswith(":"):
                    section_name = normalise_yaml_lookup_key(stripped[:-1])
                    active_section = section_name if section_name in section_fields else None
                    continue

                if ":" not in stripped:
                    continue

                key, raw_value = stripped.split(":", 1)
                mapped_key = top_level_fields.get(normalise_yaml_lookup_key(key))
                if mapped_key is None:
                    continue

                field_values[mapped_key] = parse_yaml_scalar(raw_value)
                continue

            if active_section is None or ":" not in stripped:
                continue

            key, raw_value = stripped.split(":", 1)
            mapped_key = section_fields.get(active_section, {}).get(normalise_yaml_lookup_key(key))
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
    values = {"ori": "", "pol": "", "ch": "", "pwr": "", "ctx": "", "ant": ""}
    patterns = {
        "ori": r"(?:^|_)ori-([^_]+)",
        "pol": r"(?:^|_)pol-([^_]+)",
        "ch": r"(?:^|_)ch-([^_]+)",
        "pwr": r"(?:^|_)pwr-([^_]+)",
        "ctx": r"(?:^|_)ctx-([^_]+)",
        "ant": r"(?:^|_)ant-([^_]+)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, subfolder_name, flags=re.IGNORECASE)
        if match is not None:
            values[key] = match.group(1)

    return values


def collect_observed_dimension_values(subfolder_names: list[str]) -> dict[str, list[str]]:
    collected = {"ori": [], "pol": [], "ch": [], "pwr": [], "ctx": [], "ant": []}

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
            measurement_dir, yaml_path, results_dir = resolve_measurement(logs_root, str(measurement["measurement_id"]))
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


def report_dbi(value: Any) -> str:
    numeric_value = coerce_float(value)
    return "-" if numeric_value is None else f"{numeric_value:.1f} dBi"


def dbd_to_dbi(value: Any) -> float | None:
    numeric_value = coerce_float(value)
    return None if numeric_value is None else numeric_value + 2.15


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


def safe_report_filename(value: Any) -> str:
    text = str(value or "plot").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._") or "plot"


def load_report_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]

    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue

    return ImageFont.load_default()


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.lstrip("#")
    return tuple(int(cleaned[index:index + 2], 16) for index in (0, 2, 4))


def db_to_amplitude_ratio(value: Any) -> float:
    numeric_value = coerce_float(value)
    if numeric_value is None:
        return 0.0
    return 10 ** (numeric_value / 20.0)


def format_channel_label(channel: Any) -> str:
    return "Ch ?" if channel is None or channel == "" else f"Ch {channel}"


def format_channel_or_frequency_label(channel: Any, frequency_hz: Any) -> str:
    channel_text = "" if channel is None else str(channel).strip()
    if channel_text:
        return format_channel_label(channel_text)

    frequency_label = report_hz(frequency_hz)
    return "Ch ?" if frequency_label == "-" else frequency_label


def format_power_level_label(value: Any) -> str:
    if value is None or value == "":
        return ""

    numeric_value = coerce_float(value)
    if numeric_value is not None:
        return f"Pwr {numeric_value:g}"

    return str(value)


def format_series_label(entry: dict[str, Any]) -> str:
    antenna_label = format_antenna_label(entry.get("antenna"))
    power_level = format_power_level_label(entry.get("power_level"))
    parts = [
        antenna_label,
        format_channel_or_frequency_label(entry.get("channel"), entry.get("frequency_hz")),
        power_level,
    ]
    return " | ".join(part for part in parts if part)


def get_channel_color(channel: Any, index: int, device_type: Any = None) -> str:
    key = "" if channel is None else str(channel).strip()
    normalised_device_type = normalise_sig_gen_device_type(device_type)

    if key in {"40", "50"} and normalised_device_type == SIG_GEN_DEVICE_WIRELESS_PRO_RX:
        return WIRELESS_PRO_GREEN

    return FIXED_CHANNEL_COLORS.get(key, CHANNEL_COLORS[index % len(CHANNEL_COLORS)])


def polar_to_cartesian(center_x: int, center_y: int, radius: float, angle_deg: Any, value: float) -> tuple[float, float]:
    angle = math.radians(float(angle_deg))
    distance = radius * max(0.0, min(1.0, value))
    return center_x + distance * math.sin(angle), center_y - distance * math.cos(angle)


def draw_text_centered(draw: ImageDraw.ImageDraw, position: tuple[float, float], text: str, font: ImageFont.ImageFont, fill: str) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text((position[0] - width / 2, position[1] - height / 2), text, font=font, fill=fill)


def render_analyser_summary_plot(plot: dict[str, Any], output_path: Path, measurement_role: str) -> None:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError(
            "Pillow is required to render analyser summary plots. "
            f"The current server interpreter is: {sys.executable}"
        )

    width = 1000
    height = 820
    center_x = 500
    center_y = 305
    radius = 235
    tile_bg = "#091018"
    paper_bg = "#111923"
    grid = "#3b4654"
    axis = "#768394"
    label = "#f4f7fb"
    muted = "#c6d7e7"
    accent = "#ffb266"
    font_title = load_report_font(30, True)
    font_body = load_report_font(22)
    font_small = load_report_font(18, True)
    font_legend = load_report_font(20, True)

    image = Image.new("RGB", (width, height), tile_bg)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((14, 14, width - 14, height - 14), radius=32, fill=tile_bg, outline="#2c3948", width=2)
    draw.rounded_rectangle((45, 64, width - 45, 610), radius=24, fill=paper_bg, outline="#3a4655", width=2)

    title = f"{plot.get('polarisation', '-')} / {plot.get('orientation', '-')}"
    draw.text((60, 24), title, fill=label, font=font_title)

    series = sorted(plot.get("series") or [], key=lambda item: natural_sort_key(item.get("channel", "")))
    all_values = [
        coerce_float(point.get("rx_peak_dbm"))
        for entry in series
        for point in entry.get("points", [])
    ]
    values = [value for value in all_values if value is not None]
    plot_peak = max(values) if values else None
    plot_min = min(values) if values else None
    draw.text((width - 365, 31), f"Max {report_dbm(plot_peak)} | Min {report_dbm(plot_min)}", fill=accent, font=font_body)

    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        end = polar_to_cartesian(center_x, center_y, radius, angle, 1.0)
        draw.line((center_x, center_y, end[0], end[1]), fill=grid, width=2)

    for guide_db in [-20, -10, -6, -3]:
        guide_radius = radius * db_to_amplitude_ratio(guide_db)
        draw.ellipse(
            (center_x - guide_radius, center_y - guide_radius, center_x + guide_radius, center_y + guide_radius),
            outline="#8b98a8" if guide_db == -3 else grid,
            width=2,
        )
        draw.text((center_x + 10, center_y - guide_radius - 22), f"{guide_db} dB", fill=muted, font=font_small)

    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), outline=axis, width=3)

    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        label_pos = polar_to_cartesian(center_x, center_y, radius + 34, angle, 1.0)
        draw_text_centered(draw, label_pos, f"{angle}°", font_small, label)

    if plot_peak is not None:
        for index, entry in enumerate(series):
            color = get_channel_color(entry.get("channel"), index, entry.get("device_type"))
            pattern = series_line_pattern(entry.get("antenna"), entry.get("device_type"), measurement_role)
            points = []
            sorted_points = sorted(entry.get("points", []), key=lambda item: coerce_float(item.get("angle_deg")) or 0)
            for point in sorted_points:
                rx_peak = coerce_float(point.get("rx_peak_dbm"))
                angle = coerce_float(point.get("angle_deg"))
                if rx_peak is None or angle is None:
                    continue
                ratio = db_to_amplitude_ratio(rx_peak - plot_peak)
                points.append(polar_to_cartesian(center_x, center_y, radius, angle, ratio))

            if should_close_polar_wrap(sorted_points) and points:
                points.append(points[0])

            if len(points) >= 2:
                draw_patterned_polyline(draw, points, hex_to_rgb(color), 5, pattern)

    draw_text_centered(draw, (center_x, 625), "E/Emax (per plot, dB guides)", font_body, muted)

    legend_y = 672
    for index, entry in enumerate(series[:8]):
        color = get_channel_color(entry.get("channel"), index, entry.get("device_type"))
        pattern = series_line_pattern(entry.get("antenna"), entry.get("device_type"), measurement_role)
        row_y = legend_y + index * 24
        draw_patterned_polyline(
            draw,
            [(64, row_y + 15), (88, row_y + 15)],
            hex_to_rgb(color),
            5,
            pattern,
        )
        entry_values = [
            coerce_float(point.get("rx_peak_dbm"))
            for point in entry.get("points", [])
            if coerce_float(point.get("rx_peak_dbm")) is not None
        ]
        entry_min = min(entry_values) if entry_values else None
        gain_dbi = dbd_to_dbi(entry.get("gain_dbd"))
        text = (
            f"{format_series_label(entry)} | "
            f"Peak {report_dbm(entry.get('peak_dbm'))} | "
            f"Min {report_dbm(entry_min)} | "
            f"EIRP {report_dbm(entry.get('eirp_dbm'))} | "
            f"{report_dbi(gain_dbi)}"
        )
        draw.text((96, row_y), text, fill=muted, font=font_legend)

    image.save(extended_path(output_path), format="PNG")


def render_analyser_summary_plots(
    output_dir: Path,
    dataset: dict[str, Any],
    measurement_role: str,
) -> list[tuple[str, Path]]:
    output_dir.mkdir(exist_ok=True)
    rendered: list[tuple[str, Path]] = []

    for plot in dataset.get("plots") or []:
        name = f"summary_{safe_report_filename(plot.get('polarisation'))}_{safe_report_filename(plot.get('orientation'))}.png"
        output_path = output_dir / name
        render_analyser_summary_plot(plot, output_path, measurement_role)
        rendered.append((f"{plot.get('polarisation', '-')} / {plot.get('orientation', '-')}", output_path))

    return rendered


def render_analyser_summary_plot_collage(
    output_path: Path,
    rendered_plots: list[tuple[str, Path]],
) -> Path | None:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError(
            "Pillow is required to render analyser summary plot collages. "
            f"The current server interpreter is: {sys.executable}"
        )

    if not rendered_plots:
        return None

    loaded_images: list[tuple[str, Image.Image]] = []

    for caption, image_path in rendered_plots:
        with Image.open(extended_path(image_path)) as source_image:
            loaded_images.append((caption, source_image.convert("RGB").copy()))

    columns = 1 if len(loaded_images) == 1 else 2
    rows = math.ceil(len(loaded_images) / columns)
    image_width = max(image.width for _, image in loaded_images)
    image_height = max(image.height for _, image in loaded_images)
    label_height = 34
    margin = 24
    gap = 22
    page_bg = "#081018"
    tile_bg = "#111923"
    tile_outline = "#2c3948"
    label_fill = "#f4f7fb"
    label_font = load_report_font(22, True)
    tile_width = image_width
    tile_height = image_height + label_height
    collage_width = margin * 2 + columns * tile_width + max(0, columns - 1) * gap
    collage_height = margin * 2 + rows * tile_height + max(0, rows - 1) * gap
    collage = Image.new("RGB", (collage_width, collage_height), page_bg)
    draw = ImageDraw.Draw(collage)

    for index, (caption, image) in enumerate(loaded_images):
        column_index = index % columns
        row_index = index // columns
        tile_left = margin + column_index * (tile_width + gap)
        tile_top = margin + row_index * (tile_height + gap)
        tile_right = tile_left + tile_width
        tile_bottom = tile_top + tile_height

        draw.rounded_rectangle(
            (tile_left - 10, tile_top - 10, tile_right + 10, tile_bottom + 10),
            radius=22,
            fill=tile_bg,
            outline=tile_outline,
            width=2,
        )
        collage.paste(image, (tile_left, tile_top))
        draw.text((tile_left + 8, tile_top + image.height + 6), caption, fill=label_fill, font=label_font)

    collage.save(extended_path(output_path), format="PNG")
    return output_path


def draw_text_right_aligned(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    draw.text((position[0] - width, position[1]), text, font=font, fill=fill)


def snapshot_line_pattern(line_style_key: Any) -> tuple[int, ...] | None:
    key = str(line_style_key or "").strip().lower()

    if key == "dut-secondary":
        return (14, 8)

    if key == "baseline-main":
        return (2, 10)

    if key == "baseline-secondary":
        return (2, 10, 14, 8)

    return None


def format_snapshot_reference_text(section: dict[str, Any]) -> str:
    plot_peak = report_dbm(section.get("plot_peak_dbm"))
    plot_min = report_dbm(section.get("plot_min_dbm"))
    global_peak = report_dbm(section.get("global_peak_dbm"))
    y_label = str(section.get("y_label") or "").strip().lower()

    if y_label.startswith("e/emax"):
        return f"Max {plot_peak} | Min {plot_min}"

    if global_peak != "-":
        return f"Ref {global_peak} | Min {plot_min}"

    return f"Max {plot_peak} | Min {plot_min}"


def format_snapshot_legend_text(entry: dict[str, Any]) -> str:
    label = str(entry.get("label") or "").strip()
    metrics = str(entry.get("metrics") or "").strip()

    if label and metrics:
        return f"{label} | {metrics}"

    return label or metrics or "-"


def format_snapshot_polarisation_label(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()

    if upper == "H":
        return "Hpol"

    if upper == "V":
        return "Vpol"

    return text or "-"


def render_snapshot_section_image(section: dict[str, Any]) -> Image.Image:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError(
            "Pillow is required to render analyser plot snapshots. "
            f"The current server interpreter is: {sys.executable}"
        )

    section_title = str(section.get("title") or "").strip()
    series_entries = [entry for entry in section.get("series") or [] if (entry.get("points") or [])]
    difference_entries = [entry for entry in section.get("difference_series") or [] if (entry.get("points") or [])]
    legend_entries = [(entry, False) for entry in series_entries] + [(entry, True) for entry in difference_entries]

    width = 540
    title_height = 34 if section_title else 0
    plot_panel_top = 62 + title_height
    center_x = width // 2
    center_y = plot_panel_top + 136
    radius = 132
    plot_panel_bottom = center_y + radius + 48
    legend_top = plot_panel_bottom + 18
    legend_row_height = 24
    height = max(plot_panel_bottom + 64, legend_top + max(1, len(legend_entries)) * legend_row_height + 28)
    tile_bg = "#091018"
    paper_bg = "#111923"
    grid = "#3b4654"
    axis = "#768394"
    label = "#f4f7fb"
    muted = "#c6d7e7"
    accent = "#ffb266"
    title_font = load_report_font(22, True)
    body_font = load_report_font(16)
    small_font = load_report_font(13, True)
    legend_font = load_report_font(14)

    image = Image.new("RGB", (width, height), tile_bg)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 10, width - 10, height - 10), radius=24, fill=tile_bg, outline="#2c3948", width=2)
    draw.rounded_rectangle((26, plot_panel_top - 16, width - 26, plot_panel_bottom), radius=20, fill=paper_bg, outline="#3a4655", width=2)

    if section_title:
        draw.text((26, 20), section_title, fill=label, font=title_font)

    draw_text_right_aligned(
        draw,
        (width - 26, 23 if section_title else 20),
        format_snapshot_reference_text(section),
        body_font,
        accent,
    )

    y_min = coerce_float(section.get("y_min"))
    y_max = coerce_float(section.get("y_max"))
    if y_min is None:
        y_min = 0.0
    if y_max is None:
        y_max = 1.0
    scale_span = y_max - y_min
    if abs(scale_span) < 1e-9:
        scale_span = 1.0

    def radial_scale(value: Any) -> float:
        numeric_value = coerce_float(value)
        if numeric_value is None:
            return 0.0
        ratio = (numeric_value - y_min) / scale_span
        return radius * max(0.0, min(1.0, ratio))

    def snapshot_point(angle_deg: Any, value: Any) -> tuple[float, float]:
        angle = coerce_float(angle_deg)
        if angle is None:
            angle = 0.0
        return polar_to_cartesian(center_x, center_y, radius, angle, radial_scale(value) / radius if radius else 0.0)

    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        end = polar_to_cartesian(center_x, center_y, radius, angle, 1.0)
        draw.line((center_x, center_y, end[0], end[1]), fill=grid, width=1)

    for tick in section.get("ring_ticks") or []:
        tick_value = coerce_float(tick.get("value"))
        if tick_value is None:
            continue
        tick_radius = radial_scale(tick_value)
        class_name = str(tick.get("class_name") or "")
        outline = axis if class_name == "polar-outer-ring" else "#8b98a8" if class_name == "polar-reference" else grid
        draw.ellipse(
            (center_x - tick_radius, center_y - tick_radius, center_x + tick_radius, center_y + tick_radius),
            outline=outline,
            width=2 if class_name == "polar-outer-ring" else 1,
        )

        tick_label = str(tick.get("label") or "").strip()
        if tick_label:
            draw.text((center_x + 10, center_y - tick_radius - 16), tick_label, fill=muted, font=small_font)

    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        label_pos = polar_to_cartesian(center_x, center_y, radius + 18, angle, 1.0)
        draw_text_centered(draw, label_pos, f"{angle}\u00b0", small_font, label)

    for entry in series_entries:
        color = str(entry.get("color") or "#66d7ff")
        pattern = snapshot_line_pattern(entry.get("line_style_key"))
        raw_points = [
            {
                "angle_deg": coerce_float(point.get("angle_deg")),
                "display_value": coerce_float(point.get("display_value")),
            }
            for point in entry.get("points") or []
        ]
        sorted_points = [
            point
            for point in sorted(raw_points, key=lambda item: item.get("angle_deg") or 0.0)
            if point.get("angle_deg") is not None and point.get("display_value") is not None
        ]
        cartesian_points = [snapshot_point(point["angle_deg"], point["display_value"]) for point in sorted_points]

        if should_close_polar_wrap(sorted_points) and cartesian_points:
            cartesian_points.append(cartesian_points[0])

        draw_patterned_polyline(draw, cartesian_points, hex_to_rgb(color), 4, pattern)

    for entry in difference_entries:
        color = str(entry.get("color") or "#ffb266")
        pattern = snapshot_line_pattern(entry.get("line_style_key"))
        raw_points = [
            {
                "angle_deg": coerce_float(point.get("angle_deg")),
                "display_value": coerce_float(point.get("display_value")),
            }
            for point in entry.get("points") or []
        ]
        sorted_points = [
            point
            for point in sorted(raw_points, key=lambda item: item.get("angle_deg") or 0.0)
            if point.get("angle_deg") is not None and point.get("display_value") is not None
        ]
        cartesian_points = [snapshot_point(point["angle_deg"], point["display_value"]) for point in sorted_points]

        if should_close_polar_wrap(sorted_points) and cartesian_points:
            cartesian_points.append(cartesian_points[0])

        draw_patterned_polyline(draw, cartesian_points, hex_to_rgb(color), 3, pattern)

    axis_label = str(section.get("y_label") or "").strip()
    if axis_label:
        draw_text_centered(draw, (center_x, plot_panel_bottom + 18), axis_label, body_font, muted)

    legend_y = legend_top
    for entry, _is_difference in legend_entries:
        color = str(entry.get("color") or "#66d7ff")
        pattern = snapshot_line_pattern(entry.get("line_style_key"))
        baseline_y = legend_y + 10
        draw_patterned_polyline(draw, [(38, baseline_y), (64, baseline_y)], hex_to_rgb(color), 4, pattern)
        draw.text((74, legend_y), format_snapshot_legend_text(entry), fill=muted, font=legend_font)
        legend_y += legend_row_height

    return image


def render_snapshot_placeholder(width: int, height: int, text: str) -> Image.Image:
    image = Image.new("RGB", (width, height), "#091018")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 10, width - 10, height - 10), radius=24, fill="#111923", outline="#2c3948", width=2)
    draw_text_centered(draw, (width / 2, height / 2), text, load_report_font(18, True), "#f4f7fb")
    return image


def render_snapshot_cell_image(plot: dict[str, Any] | None) -> Image.Image:
    if not plot:
        return render_snapshot_placeholder(540, 430, "No data for this plot.")

    sections = plot.get("sections") or []
    if not sections:
        return render_snapshot_placeholder(540, 430, "No data for this plot.")

    section_images = [render_snapshot_section_image(section) for section in sections]
    width = max(section.width for section in section_images)
    gap = 14
    height = sum(section.height for section in section_images) + gap * max(0, len(section_images) - 1)
    cell = Image.new("RGB", (width, height), "#081018")
    y_offset = 0

    for section_image in section_images:
        cell.paste(section_image, (0, y_offset))
        y_offset += section_image.height + gap

    return cell


def render_analyser_snapshot_image(snapshot_payload: dict[str, Any]) -> Image.Image:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError(
            "Pillow is required to render analyser plot snapshots. "
            f"The current server interpreter is: {sys.executable}"
        )

    if not isinstance(snapshot_payload, dict):
        raise ValueError("Snapshot payload must be a JSON object")

    rows = [str(value or "").strip() for value in snapshot_payload.get("rows") or [] if str(value or "").strip()]
    columns = [str(value or "").strip() for value in snapshot_payload.get("columns") or [] if str(value or "").strip()]
    plots = snapshot_payload.get("plots") or []
    empty_message = str(snapshot_payload.get("empty_message") or "No plots available.")

    title = str(snapshot_payload.get("title") or "Results Analyser Snapshot").strip()
    subtitle = str(snapshot_payload.get("subtitle") or "").strip()
    mode = str(snapshot_payload.get("mode") or "").strip()
    overlay_channels = bool(snapshot_payload.get("overlay_channels"))
    mode_label = "dB" if mode == "db" else "E/Emax"
    overlay_label = "Overlay Channels Off" if not overlay_channels else "Overlay Channels On"
    meta_text = f"Mode {mode_label} | {overlay_label}"

    if not rows or not columns:
        width = 1100
        height = 260
        image = Image.new("RGB", (width, height), "#081018")
        draw = ImageDraw.Draw(image)
        title_font = load_report_font(28, True)
        subtitle_font = load_report_font(18)
        draw.text((28, 24), title, fill="#f4f7fb", font=title_font)
        if subtitle:
            draw.text((28, 64), subtitle, fill="#c6d7e7", font=subtitle_font)
        draw.text((28, 92), meta_text, fill="#ffb266", font=subtitle_font)
        draw.rounded_rectangle((24, 126, width - 24, height - 24), radius=24, fill="#111923", outline="#2c3948", width=2)
        draw_text_centered(draw, (width / 2, 184), empty_message, load_report_font(18, True), "#f4f7fb")
        return image

    plot_lookup = {
        (str(plot.get("polarisation") or "").strip(), str(plot.get("orientation") or "").strip()): plot
        for plot in plots
    }
    cell_lookup: dict[tuple[str, str], Image.Image] = {}
    cell_width = 540

    for column in columns:
        for row in rows:
            cell_image = render_snapshot_cell_image(plot_lookup.get((row, column)))
            cell_lookup[(row, column)] = cell_image
            cell_width = max(cell_width, cell_image.width)

    row_heights: list[int] = []
    for column in columns:
        row_heights.append(max(cell_lookup[(row, column)].height for row in rows))

    margin = 28
    row_label_width = 94
    column_gap = 18
    row_gap = 18
    header_height = 34
    title_height = 88 if subtitle else 64
    page_width = margin * 2 + row_label_width + len(rows) * cell_width + max(0, len(rows) - 1) * column_gap
    page_height = (
        margin * 2
        + title_height
        + header_height
        + sum(row_heights)
        + max(0, len(columns) - 1) * row_gap
    )
    image = Image.new("RGB", (page_width, page_height), "#081018")
    draw = ImageDraw.Draw(image)
    title_font = load_report_font(28, True)
    subtitle_font = load_report_font(18)
    header_font = load_report_font(18, True)

    draw.text((margin, margin - 4), title, fill="#f4f7fb", font=title_font)
    if subtitle:
        draw.text((margin, margin + 34), subtitle, fill="#c6d7e7", font=subtitle_font)
    draw.text((margin, margin + title_height - 18), meta_text, fill="#ffb266", font=subtitle_font)

    grid_left = margin + row_label_width
    header_y = margin + title_height

    for row_index, row in enumerate(rows):
        header_x = grid_left + row_index * (cell_width + column_gap) + cell_width / 2
        draw_text_centered(draw, (header_x, header_y + 14), format_snapshot_polarisation_label(row), header_font, "#f4f7fb")

    current_y = header_y + header_height

    for column_index, column in enumerate(columns):
        row_height = row_heights[column_index]
        draw_text_centered(draw, (margin + row_label_width / 2, current_y + row_height / 2), column, header_font, "#f4f7fb")

        for row_index, row in enumerate(rows):
            cell_image = cell_lookup[(row, column)]
            x = grid_left + row_index * (cell_width + column_gap)
            image.paste(cell_image, (x, current_y))

        current_y += row_height + row_gap

    return image


def write_rendered_snapshot(output_path: Path, snapshot_payload: dict[str, Any]) -> Path:
    image = render_analyser_snapshot_image(snapshot_payload)
    image.save(extended_path(output_path), format="PNG")
    return output_path


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


def write_analyser_docx_summary(logs_root: Path, measurement_id: str, measurement_role: str | None = None) -> Path:
    measurement_dir, yaml_path, results_dir = resolve_measurement(logs_root, measurement_id)
    dataset = load_measurement_dataset(logs_root, measurement_id)
    yaml_dimensions = read_yaml_named_dimensions(yaml_path)
    output_path = measurement_dir / "analyser_summary.docx"
    builder = DocxReportBuilder("DAMSpy Results Analyser Summary")
    resolved_measurement_role = normalise_measurement_role(measurement_role) or infer_measurement_role(
        dataset.get("dut_product"),
        dataset.get("measurement_name"),
    )
    report_tx_power_dbm = infer_report_tx_power_dbm(
        dataset.get("sig_gen_1_device_type"),
        dataset.get("dut_product"),
        dataset.get("measurement_name"),
    )

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
            ["TX power", report_dbm(report_tx_power_dbm)],
            ["Device role", resolved_measurement_role.title()],
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
    plot_rows = build_plot_summary_rows(dataset)
    builder.add_table(plot_rows, [2200, 1700, 1200, 1200, 1200, 1400, 1400])

    builder.add_heading("Analyser Summary Plots", 1)
    with tempfile.TemporaryDirectory(prefix="damspy_docx_") as temp_dir_name:
        temp_output_dir = Path(temp_dir_name)
        rendered_plots = render_analyser_summary_plots(temp_output_dir, dataset, resolved_measurement_role)
        collage_path = render_analyser_summary_plot_collage(
            temp_output_dir / "summary_overview.png",
            rendered_plots,
        )
        if collage_path is not None:
            builder.add_image(collage_path, "Analyser plot overview", max_width_in=6.2)

        for caption, image_path in rendered_plots:
            builder.add_image(image_path, f"Analyser plot: {caption}", max_width_in=6.1)

        builder.write(output_path)
    return output_path


def latest_measurement_id(logs_root: Path, measurement_ids: list[str]) -> str:
    ordered_measurement_ids = dedupe_measurement_ids(measurement_ids)
    if not ordered_measurement_ids:
        raise ValueError("At least one measurement_id is required")

    manifests = []
    for measurement_id in ordered_measurement_ids:
        measurement_dir, _, _ = resolve_measurement(logs_root, measurement_id)
        manifest = measurement_manifest(logs_root, measurement_dir)
        if manifest is not None:
            manifests.append(manifest)

    if not manifests:
        return ordered_measurement_ids[-1]

    manifests.sort(key=lambda item: item.get("_sort_at", 0.0))
    return str(manifests[-1]["measurement_id"])


def combined_summary_filename() -> str:
    return "combined_summary_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".docx"


def combined_snapshot_filename() -> str:
    return "combined_snapshot_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".png"


def write_combined_analyser_docx_summary(
    logs_root: Path,
    measurement_ids: list[str],
    measurement_role: str | None = None,
) -> Path:
    ordered_measurement_ids = dedupe_measurement_ids(measurement_ids)
    combined_dataset = load_combined_measurement_dataset(logs_root, ordered_measurement_ids)
    output_measurement_id = latest_measurement_id(logs_root, ordered_measurement_ids)
    output_measurement_dir, _, _ = resolve_measurement(logs_root, output_measurement_id)
    output_path = output_measurement_dir / combined_summary_filename()
    builder = DocxReportBuilder("DAMSpy Combined Results Summary")
    resolved_measurement_role = normalise_measurement_role(measurement_role) or infer_measurement_role(
        combined_dataset.get("dut_product"),
        combined_dataset.get("measurement_name"),
    )
    report_tx_power_dbm = infer_report_tx_power_dbm(
        combined_dataset.get("sig_gen_1_device_type"),
        combined_dataset.get("dut_product"),
        combined_dataset.get("measurement_name"),
    )

    builder.add_paragraph("DAMSpy Combined Results Summary", "Title")
    builder.add_paragraph(
        f"{len(ordered_measurement_ids)} YAMLs selected | Output in {output_measurement_dir.name}",
        "Subtitle",
    )
    builder.add_table(
        [
            ["Field", "Value"],
            ["Created at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["Selection count", str(len(ordered_measurement_ids))],
            ["Output folder", output_measurement_dir.name],
            ["Combined global dB ref", report_dbm(combined_dataset.get("global_peak_dbm"))],
            ["Device role", resolved_measurement_role.title()],
        ],
        [2500, 6860],
    )

    builder.add_heading("Selected YAMLs", 1)
    selection_rows = [["Measurement", "YAML", "Updated", "Folders"]]
    for measurement in combined_dataset.get("source_measurements") or []:
        selection_rows.append(
            [
                report_value(measurement.get("measurement_name")),
                report_value(measurement.get("yaml_relative_path")),
                report_value(measurement.get("updated_at")),
                report_value(measurement.get("folder_count")),
            ]
        )
    builder.add_table(selection_rows, [3100, 3700, 1600, 960])

    builder.add_heading("Combined Details", 1)
    builder.add_table(
        [
            ["Field", "Value"],
            ["DUT product", report_value(combined_dataset.get("dut_product"))],
            ["DUT hardware config", report_value(combined_dataset.get("dut_hardware_config"))],
            ["DUT serial number", report_value(combined_dataset.get("dut_serial_number"))],
            ["TX mode", report_value(combined_dataset.get("tx_mode"))],
            ["TX power", report_dbm(report_tx_power_dbm)],
            ["RX antenna", report_value(combined_dataset.get("rx_antenna_name"))],
            ["RX antenna comment", report_value(combined_dataset.get("rx_antenna_comment"))],
            ["RX antenna gain", report_value(combined_dataset.get("rx_antenna_gain_dbi"), " dBi")],
            ["RX cable loss", report_db(combined_dataset.get("rx_cable_loss_db"))],
            ["RX distance", report_value(combined_dataset.get("rx_dist_m"), " m")],
            ["Folders with data", report_value(len(combined_dataset.get("folders") or []))],
        ],
        [2800, 6560],
    )

    builder.add_heading("Combined Selected Plot Table", 1)
    matrix_tables = build_combined_plot_matrix_tables(combined_dataset)
    if not matrix_tables:
        builder.add_paragraph("No combined plot table data was available.")
    else:
        for table_index, (table_rows, table_widths) in enumerate(matrix_tables, start=1):
            if len(matrix_tables) > 1:
                builder.add_paragraph(f"Plot table part {table_index} of {len(matrix_tables)}", "Caption", True)
            builder.add_table(table_rows, table_widths)

    builder.add_heading("Combined Plot Summary", 1)
    plot_rows = build_plot_summary_rows(combined_dataset)
    builder.add_table(plot_rows, [2200, 1700, 1200, 1200, 1200, 1400, 1400])

    builder.add_heading("Combined Summary Plots", 1)
    with tempfile.TemporaryDirectory(prefix="damspy_combined_docx_") as temp_dir_name:
        temp_output_dir = Path(temp_dir_name)
        rendered_plots = render_analyser_summary_plots(temp_output_dir, combined_dataset, resolved_measurement_role)
        collage_path = render_analyser_summary_plot_collage(
            temp_output_dir / "combined_summary_overview.png",
            rendered_plots,
        )
        if collage_path is not None:
            builder.add_image(collage_path, "Combined plot overview", max_width_in=6.2)

        for caption, image_path in rendered_plots:
            builder.add_image(image_path, f"Combined plot: {caption}", max_width_in=6.1)

        builder.add_heading("Individual Measurement Plots", 1)
        for measurement in combined_dataset.get("source_measurements") or []:
            measurement_id = str(measurement.get("measurement_id") or "").strip()
            if not measurement_id:
                continue

            measurement_dataset = load_measurement_dataset(logs_root, measurement_id)
            measurement_label = compact_measurement_identity_label(measurement)
            builder.add_heading(f"{measurement_label}", 2)

            measurement_rendered_plots = render_analyser_summary_plots(
                temp_output_dir / safe_report_filename(measurement_id),
                measurement_dataset,
                resolved_measurement_role,
            )
            measurement_collage_path = render_analyser_summary_plot_collage(
                temp_output_dir / f"{safe_report_filename(measurement_id)}_overview.png",
                measurement_rendered_plots,
            )
            if measurement_collage_path is not None:
                builder.add_image(measurement_collage_path, f"{measurement_label} overview", max_width_in=6.2)

            for caption, image_path in measurement_rendered_plots:
                builder.add_image(image_path, f"{measurement_label}: {caption}", max_width_in=6.1)

        builder.write(output_path)

    return output_path


def write_analyser_plot_snapshot(logs_root: Path, measurement_id: str, image_bytes: bytes) -> Path:
    measurement_dir, _, _ = resolve_measurement(logs_root, measurement_id)

    if not image_bytes:
        raise ValueError("Snapshot image data is required")

    output_path = measurement_dir / ANALYSER_SNAPSHOT_FILENAME
    output_path.write_bytes(image_bytes)
    return output_path


def write_rendered_analyser_plot_snapshot(
    logs_root: Path,
    measurement_id: str,
    snapshot_payload: dict[str, Any],
) -> Path:
    measurement_dir, _, _ = resolve_measurement(logs_root, measurement_id)
    output_path = measurement_dir / ANALYSER_SNAPSHOT_FILENAME
    return write_rendered_snapshot(output_path, snapshot_payload)


def write_combined_analyser_plot_snapshot(logs_root: Path, measurement_ids: list[str], image_bytes: bytes) -> Path:
    ordered_measurement_ids = dedupe_measurement_ids(measurement_ids)

    if not ordered_measurement_ids:
        raise ValueError("At least one measurement_id is required")

    if not image_bytes:
        raise ValueError("Snapshot image data is required")

    output_measurement_id = latest_measurement_id(logs_root, ordered_measurement_ids)
    output_measurement_dir, _, _ = resolve_measurement(logs_root, output_measurement_id)
    output_path = output_measurement_dir / combined_snapshot_filename()
    output_path.write_bytes(image_bytes)
    return output_path


def write_rendered_combined_analyser_plot_snapshot(
    logs_root: Path,
    measurement_ids: list[str],
    snapshot_payload: dict[str, Any],
) -> Path:
    ordered_measurement_ids = dedupe_measurement_ids(measurement_ids)

    if not ordered_measurement_ids:
        raise ValueError("At least one measurement_id is required")

    output_measurement_id = latest_measurement_id(logs_root, ordered_measurement_ids)
    output_measurement_dir, _, _ = resolve_measurement(logs_root, output_measurement_id)
    output_path = output_measurement_dir / combined_snapshot_filename()
    return write_rendered_snapshot(output_path, snapshot_payload)


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
            tx_power_dbm = coerce_float(series_info.get("level_dbm"))
        if tx_power_dbm is None:
            tx_power_dbm = coerce_float(yaml_summary.get("tx_power_dbm"))
        tx_cable_loss_db = coerce_float(series_info.get("tx_cable_loss"))
        if tx_cable_loss_db is None:
            tx_cable_loss_db = coerce_float(series_info.get("tx_cable_loss_db"))
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
        series_device_type = normalise_sig_gen_device_type(
            series_info.get("device_type") or yaml_summary.get("sig_gen_1_device_type")
        )
        tx_antenna = normalise_tx_antenna(series_info.get("antenna"), entry.name)
        product_text = " ".join(
            str(value or "")
            for value in [
                measurement_dir.name,
                yaml_summary.get("dut_product"),
                yaml_summary.get("dut_hardware_config"),
                yaml_summary.get("tx_mode"),
                yaml_summary.get("foldername_comment"),
                entry.name,
            ]
        ).lower()
        if series_device_type == SIG_GEN_DEVICE_HENDRIX_TX:
            tx_antenna = "main"
        elif series_device_type == SIG_GEN_DEVICE_WIRELESS_PRO_RX:
            tx_antenna = "FPC ant id1" if tx_antenna == "secondary" else "PCB ant id0"
        elif "hendrix" in product_text or "nedrix" in product_text:
            tx_antenna = "main"
        elif "wireless pro" in product_text or "wireless-pro" in product_text or "wireless_pro" in product_text:
            tx_antenna = "FPC ant id1" if tx_antenna == "secondary" else "PCB ant id0"
        folder_record = {
            "source_measurement_name": measurement_dir.name,
            "dut_serial_number": yaml_summary.get("dut_serial_number"),
            "folder_name": entry.name,
            "product_name": yaml_summary.get("dut_product") or measurement_dir.name,
            "orientation": metadata.get("orientation") or "unknown",
            "polarisation": metadata.get("polarisation") or "unknown",
            "channel": series_info.get("channel"),
            "power_level": series_info.get("power_level"),
            "antenna": tx_antenna,
            "device_type": series_device_type,
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
            "suggested_measurement_role": infer_measurement_role(
                yaml_summary.get("dut_product"),
                measurement_dir.name,
            ),
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
                "source_measurement_name": folder.get("source_measurement_name"),
                "dut_serial_number": folder.get("dut_serial_number"),
                "product_name": folder.get("product_name"),
                "orientation": folder["orientation"],
                "polarisation": folder["polarisation"],
                "channel": folder["channel"],
                "power_level": folder["power_level"],
                "antenna": folder["antenna"],
                "device_type": folder.get("device_type"),
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
                    "source_measurement_name": folder.get("source_measurement_name"),
                    "dut_serial_number": folder.get("dut_serial_number"),
                    "product_name": folder.get("product_name"),
                    "channel": folder["channel"],
                    "power_level": folder["power_level"],
                    "antenna": folder["antenna"],
                    "device_type": folder.get("device_type"),
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
        "suggested_measurement_role": infer_measurement_role(
            yaml_summary.get("dut_product"),
            measurement_dir.name,
        ),
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

        if clean_path == "/api/results-analyser/combined-data":
            self.handle_combined_measurement_data()
            return

        if clean_path == "/api/results-analyser/write-summary-csv":
            self.handle_write_summary_csv()
            return

        if clean_path == "/api/results-analyser/write-docx-summary":
            self.handle_write_docx_summary()
            return

        if clean_path == "/api/results-analyser/write-combined-docx-summary":
            self.handle_write_combined_docx_summary()
            return

        super().do_GET()

    def do_POST(self) -> None:
        clean_path = urlsplit(self.path).path

        if clean_path == "/api/results-analyser/write-plot-snapshot":
            self.handle_write_plot_snapshot()
            return

        if clean_path == "/api/results-analyser/write-combined-plot-snapshot":
            self.handle_write_combined_plot_snapshot()
            return

        self.send_json({"error": f"Unsupported POST endpoint: {clean_path}"}, status=HTTPStatus.NOT_FOUND)

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

    def read_request_body(self) -> bytes:
        content_length_header = self.headers.get("Content-Length", "").strip()

        if not content_length_header:
            return b""

        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise ValueError("Content-Length header must be an integer") from exc

        if content_length < 0:
            raise ValueError("Content-Length must not be negative")

        return self.rfile.read(content_length)

    def read_png_request_body(self) -> bytes:
        image_bytes = self.read_request_body()

        if not image_bytes:
            raise ValueError("Snapshot image data is required")

        if not image_bytes.startswith(PNG_SIGNATURE):
            raise ValueError("Snapshot payload must be a PNG image")

        return image_bytes

    def read_json_request_body(self) -> dict[str, Any]:
        body = self.read_request_body()

        if not body:
            raise ValueError("Snapshot payload is required")

        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("Snapshot payload must be UTF-8 JSON") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Snapshot payload must be valid JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("Snapshot payload must be a JSON object")

        return payload

    def handle_yaml_list(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        scope = query.get("scope", [MEASUREMENT_SCOPE_BEST])[0]
        measurements = list_measurements(self.logs_root, scope)
        measurement_ids = {measurement["measurement_id"] for measurement in measurements}
        if scope == MEASUREMENT_SCOPE_BEST and PREFERRED_DEFAULT_MEASUREMENT_ID in measurement_ids:
            default_measurement_id = PREFERRED_DEFAULT_MEASUREMENT_ID
        elif scope == MEASUREMENT_SCOPE_LOGS:
            default_measurement_id = next(
                (
                    measurement["measurement_id"]
                    for measurement in measurements
                    if not measurement_is_in_best_folder(str(measurement["measurement_id"]))
                ),
                measurements[0]["measurement_id"] if measurements else None,
            )
        else:
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

    def handle_combined_measurement_data(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_ids = parse_measurement_ids(query)

        if not measurement_ids:
            self.send_json(
                {"error": "At least one measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            dataset = load_combined_measurement_dataset(self.logs_root, measurement_ids)
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
        measurement_role = query.get("measurement_role", [""])[0]

        if not measurement_id:
            self.send_json(
                {"error": "measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            output_path = write_analyser_docx_summary(self.logs_root, measurement_id, measurement_role)
        except PermissionError as exc:
            self.send_json(
                {
                    "error": (
                        "Permission denied while writing the DOCX summary. "
                        "Close the existing analyser_summary.docx if it is open or locked by another application. "
                        f"Path: {exc.filename or measurement_id}"
                    ),
                },
                status=HTTPStatus.CONFLICT,
            )
            return
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

    def handle_write_plot_snapshot(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_id = query.get("measurement_id", [""])[0]

        if not measurement_id:
            self.send_json(
                {"error": "measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                snapshot_payload = self.read_json_request_body()
                output_path = write_rendered_analyser_plot_snapshot(self.logs_root, measurement_id, snapshot_payload)
            else:
                image_bytes = self.read_png_request_body()
                output_path = write_analyser_plot_snapshot(self.logs_root, measurement_id, image_bytes)
        except PermissionError as exc:
            self.send_json(
                {
                    "error": (
                        "Permission denied while writing the plot snapshot. "
                        "Close the existing analyser_snapshot.png if it is open or locked by another application. "
                        f"Path: {exc.filename or measurement_id}"
                    ),
                },
                status=HTTPStatus.CONFLICT,
            )
            return
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

    def handle_write_combined_docx_summary(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_ids = parse_measurement_ids(query)
        measurement_role = query.get("measurement_role", [""])[0]

        if not measurement_ids:
            self.send_json(
                {"error": "At least one measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            output_path = write_combined_analyser_docx_summary(self.logs_root, measurement_ids, measurement_role)
        except PermissionError as exc:
            self.send_json(
                {
                    "error": (
                        "Permission denied while writing the combined DOCX summary. "
                        "Close any existing combined_summary DOCX that may be open or locked by another application. "
                        f"Path: {exc.filename or ', '.join(measurement_ids)}"
                    ),
                },
                status=HTTPStatus.CONFLICT,
            )
            return
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

    def handle_write_combined_plot_snapshot(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        measurement_ids = parse_measurement_ids(query)

        if not measurement_ids:
            self.send_json(
                {"error": "At least one measurement_id query parameter is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                snapshot_payload = self.read_json_request_body()
                output_path = write_rendered_combined_analyser_plot_snapshot(
                    self.logs_root,
                    measurement_ids,
                    snapshot_payload,
                )
            else:
                image_bytes = self.read_png_request_body()
                output_path = write_combined_analyser_plot_snapshot(self.logs_root, measurement_ids, image_bytes)
        except PermissionError as exc:
            self.send_json(
                {
                    "error": (
                        "Permission denied while writing the combined plot snapshot. "
                        "Close any existing combined_snapshot PNG that may be open or locked by another application. "
                        f"Path: {exc.filename or ', '.join(measurement_ids)}"
                    ),
                },
                status=HTTPStatus.CONFLICT,
            )
            return
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
