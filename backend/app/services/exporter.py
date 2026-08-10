from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List
from xml.etree import ElementTree as ET


class ExportEngine:
    def write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def write_txt(self, path: Path, title: str, lines: Iterable[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = [title, ""]
        content.extend(lines)
        path.write_text("\n".join(content), encoding="utf-8")

    def write_csv(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        columns: List[str] = sorted({k for row in rows for k in row.keys()})
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def write_xml(self, path: Path, root_name: str, rows_name: str, rows: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element(root_name)
        container = ET.SubElement(root, rows_name)
        for row in rows:
            item = ET.SubElement(container, "item")
            for key, value in row.items():
                node = ET.SubElement(item, str(key))
                node.text = "" if value is None else str(value)
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
