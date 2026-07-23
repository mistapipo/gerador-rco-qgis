from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from xml.dom import Node, minidom

from .core import LayerAnalysis


def _direct_children(element, tag_name: str):
    return [
        child
        for child in element.childNodes
        if child.nodeType == Node.ELEMENT_NODE and child.tagName == tag_name
    ]


def _cells(row):
    return _direct_children(row, "w:tc")


def _replace_cell_text(cell, text: str) -> None:
    text = str(text or "")
    text_nodes = cell.getElementsByTagName("w:t")
    if text_nodes:
        first = text_nodes[0]
        while first.firstChild:
            first.removeChild(first.firstChild)
        first.appendChild(first.ownerDocument.createTextNode(text))
        if text.startswith(" ") or text.endswith(" "):
            first.setAttribute("xml:space", "preserve")
        for node in text_nodes[1:]:
            while node.firstChild:
                node.removeChild(node.firstChild)
        return

    document = cell.ownerDocument
    paragraphs = _direct_children(cell, "w:p")
    paragraph = paragraphs[0] if paragraphs else document.createElement("w:p")
    if not paragraphs:
        cell.appendChild(paragraph)
    run = document.createElement("w:r")
    node = document.createElement("w:t")
    node.appendChild(document.createTextNode(text))
    run.appendChild(node)
    paragraph.appendChild(run)


def _row_properties(row):
    properties = _direct_children(row, "w:trPr")
    if properties:
        return properties[0]
    properties = row.ownerDocument.createElement("w:trPr")
    if row.firstChild:
        row.insertBefore(properties, row.firstChild)
    else:
        row.appendChild(properties)
    return properties


def _set_cant_split(row) -> None:
    properties = _row_properties(row)
    if not _direct_children(properties, "w:cantSplit"):
        properties.appendChild(row.ownerDocument.createElement("w:cantSplit"))


def _set_repeat_header(row) -> None:
    properties = _row_properties(row)
    headers = _direct_children(properties, "w:tblHeader")
    header = headers[0] if headers else row.ownerDocument.createElement("w:tblHeader")
    if not headers:
        properties.appendChild(header)
    header.setAttribute("w:val", "true")


def generate_rco_docx(
    analysis: LayerAnalysis,
    output_docx: Path,
    template_path: Path,
    title: str | None = None,
    identifier: str | None = None,
    mark_pending: bool = True,
) -> Path:
    final_title = title or analysis.layer_name
    final_identifier = identifier or analysis.layer_name
    output_docx = Path(output_docx)
    template_path = Path(template_path)

    with zipfile.ZipFile(template_path, "r") as source_zip:
        document_xml = source_zip.read("word/document.xml")

    document = minidom.parseString(document_xml)
    tables = document.getElementsByTagName("w:tbl")
    if not tables:
        raise ValueError("O modelo não contém a tabela esperada.")
    table = tables[0]
    rows = _direct_children(table, "w:tr")
    if len(rows) < 7:
        raise ValueError("A tabela do modelo não corresponde ao padrão esperado.")

    def set_value(row_index: int, cell_index: int, value: str) -> None:
        current_rows = _direct_children(table, "w:tr")
        current_cells = _cells(current_rows[row_index])
        if cell_index >= len(current_cells):
            raise ValueError("A estrutura de células do modelo foi alterada.")
        _replace_cell_text(current_cells[cell_index], value)

    set_value(1, 1, final_title)
    set_value(2, 2, final_identifier)
    set_value(3, 2, analysis.geometry)

    rows = _direct_children(table, "w:tr")
    sample_index = 7 if len(rows) > 7 else 6
    sample_row = rows[sample_index].cloneNode(deep=True)

    for row in rows[6:]:
        table.removeChild(row)
        row.unlink()

    rows = _direct_children(table, "w:tr")
    _set_repeat_header(rows[5])
    _set_cant_split(rows[5])

    for field in analysis.fields:
        row = sample_row.cloneNode(deep=True)
        _set_cant_split(row)
        cells = _cells(row)
        if len(cells) < 3:
            raise ValueError("A linha de exemplo do modelo não possui três células.")
        _replace_cell_text(cells[0], field.field_name)
        _replace_cell_text(cells[1], field.final_typology)
        description = field.description
        if mark_pending and field.needs_review and not description.startswith("[REVISAR]"):
            description = "[REVISAR] " + description
        _replace_cell_text(cells[2], description)
        table.appendChild(row)

    updated_xml = document.toxml(encoding="utf-8")
    output_docx.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        temp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(template_path, "r") as source_zip, zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as target_zip:
            for item in source_zip.infolist():
                payload = (
                    updated_xml
                    if item.filename == "word/document.xml"
                    else source_zip.read(item.filename)
                )
                target_zip.writestr(item, payload)
        shutil.move(str(temp_path), str(output_docx))
    finally:
        temp_path.unlink(missing_ok=True)
        document.unlink()

    report = {
        "layer_name": analysis.layer_name,
        "geometry": analysis.geometry,
        "title": final_title,
        "identifier": final_identifier,
        "output_docx": str(output_docx),
        "fields": [asdict(field) for field in analysis.fields],
    }
    output_docx.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_docx
