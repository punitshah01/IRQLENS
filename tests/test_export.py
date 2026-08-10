from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from backend.app.services.exporter import ExportEngine


def test_export_formats(tmp_path: Path):
    engine = ExportEngine()
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]

    json_path = tmp_path / "out.json"
    csv_path = tmp_path / "out.csv"
    xml_path = tmp_path / "out.xml"
    txt_path = tmp_path / "out.txt"

    engine.write_json(json_path, rows)
    engine.write_csv(csv_path, rows)
    engine.write_xml(xml_path, "root", "rows", rows)
    engine.write_txt(txt_path, "title", ["line1", "line2"])

    assert json_path.exists() and json_path.read_text(encoding="utf-8").startswith("[")
    assert csv_path.exists() and "a,b" in csv_path.read_text(encoding="utf-8")
    assert txt_path.exists() and "line1" in txt_path.read_text(encoding="utf-8")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    assert root.tag == "root"
    assert root.find("rows") is not None
