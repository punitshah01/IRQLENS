from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

SCHEMA_VERSION = "1.0"
MAX_COLUMN_WIDTH = 60


def sanitize(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    return value


def utc_iso(timestamp: Optional[float] = None) -> str:
    moment = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(float(timestamp), timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unwrap_envelope(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("schema_version") == SCHEMA_VERSION and "data" in payload:
        return payload["data"]
    return payload


def _safe_text(text: str) -> str:
    # Spreadsheets execute cells that start with these characters as formulas.
    if text[:1] not in ("=", "+", "-", "@", "\t", "\r"):
        return text
    try:
        float(text)
        return text
    except ValueError:
        return "'" + text


def flatten_row(row: Dict[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, value in sanitize(row).items():
        if isinstance(value, dict):
            for inner_key, inner in flatten_row(value).items():
                flat[f"{key}.{inner_key}"] = inner
        elif isinstance(value, list):
            flat[key] = _safe_text("; ".join(
                json.dumps(item, separators=(",", ":")) if isinstance(item, (dict, list)) else str(item) for item in value
            ))
        else:
            flat[key] = _safe_text(value) if isinstance(value, str) else value
    return flat


def _cell(value: Any) -> Any:
    value = sanitize(value)
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        value = str(value)
    return _safe_text(ILLEGAL_CHARACTERS_RE.sub("", value)) if isinstance(value, str) else value


def _replace_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        # Still present only when the write or the rename failed.
        tmp.unlink(missing_ok=True)


class ExportEngine:
    def write_bytes(self, path: Path, data: bytes) -> None:
        _replace_atomically(path, data)

    def write_json(self, path: Path, data: Any, meta: Optional[Dict[str, Any]] = None, compact: bool = False) -> None:
        meta = meta or {}
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "sut_id": str(meta.get("sut_id") or ""),
            "session_id": str(meta.get("session_id") or ""),
            "captured_at": utc_iso(),
            "sampling_interval_s": sanitize(meta.get("sampling_interval_s")),
            "data": sanitize(data),
        }
        text = json.dumps(envelope, indent=None if compact else 2, allow_nan=False, default=str)
        _replace_atomically(path, text.encode("utf-8"))

    def write_txt(self, path: Path, title: str, lines: Iterable[str]) -> None:
        content = [title, ""]
        content.extend(lines)
        _replace_atomically(path, "\n".join(content).encode("utf-8"))

    def write_csv(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        flat_rows = [flatten_row(row) for row in rows]
        columns: List[str] = []
        for row in flat_rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        buffer = io.StringIO(newline="")
        if columns:
            writer = csv.DictWriter(buffer, fieldnames=columns)
            writer.writeheader()
            writer.writerows(flat_rows)
        # utf-8-sig adds the BOM Excel needs to detect UTF-8.
        _replace_atomically(path, buffer.getvalue().encode("utf-8-sig"))

    def write_xlsx(self, path: Path, sheets: Sequence[Dict[str, Any]]) -> None:
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet in sheets:
            headers = [str(header) for header in sheet["headers"]]
            worksheet = workbook.create_sheet(title=str(sheet["name"])[:31])
            worksheet.append(headers)
            widths = [len(header) for header in headers]
            for row in sheet["rows"]:
                cells = [_cell(value) for value in row]
                worksheet.append(cells)
                for index, value in enumerate(cells[: len(widths)]):
                    if value is not None:
                        widths[index] = max(widths[index], len(f"{value:.2f}" if isinstance(value, float) else str(value)))
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
            worksheet.freeze_panes = "A2"
            for row in worksheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, bool):
                        continue
                    if isinstance(cell.value, float):
                        cell.number_format = "0.00"
                    elif isinstance(cell.value, int):
                        cell.number_format = "#,##0"
            for index, width in enumerate(widths, start=1):
                worksheet.column_dimensions[get_column_letter(index)].width = min(MAX_COLUMN_WIDTH, width + 2)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            workbook.save(tmp)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
