from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from .core import FieldInput, FieldResult, LayerAnalysis, analyze_fields, connect_catalog
from .docx_writer import generate_rco_docx


class GeradorRCODialog(QDialog):
    def __init__(self, iface, plugin_dir: Path):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.plugin_dir = Path(plugin_dir)
        self.catalog_path = self.plugin_dir / "data" / "catalogo.sqlite"
        self.template_path = self.plugin_dir / "data" / "modelo_rco.docx"
        self.analysis: LayerAnalysis | None = None
        self.layer_ids: list[str] = []

        self.setWindowTitle("Gerador de RCO — ferramenta independente")
        self.resize(1120, 680)
        self.setMinimumSize(850, 520)
        self._build_ui()
        self.refresh_layers()

    def _build_ui(self):
        root = QVBoxLayout(self)

        notice = QLabel(
            "Ferramenta independente. Revise os campos antes de utilizar o RCO em documento oficial."
        )
        notice.setWordWrap(True)
        root.addWidget(notice)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Camada vetorial:"))
        self.layer_combo = QComboBox()
        layer_row.addWidget(self.layer_combo, 1)
        refresh_button = QPushButton("Atualizar lista")
        refresh_button.clicked.connect(self.refresh_layers)
        layer_row.addWidget(refresh_button)
        analyze_button = QPushButton("Analisar camada")
        analyze_button.clicked.connect(self.analyze_selected_layer)
        layer_row.addWidget(analyze_button)
        root.addLayout(layer_row)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.identifier_edit = QLineEdit()
        form.addRow("Título:", self.title_edit)
        form.addRow("Identificação SGBDE:", self.identifier_edit)
        root.addLayout(form)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Campo", "Tipologia", "Descrição", "Confiança", "Fonte"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        self.mark_review = QCheckBox("Marcar campos incertos com [REVISAR]")
        self.mark_review.setChecked(True)
        root.addWidget(self.mark_review)

        self.status_label = QLabel("Selecione uma camada e clique em Analisar camada.")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        buttons_row = QHBoxLayout()
        generate_button = QPushButton("Gerar RCO (.docx)")
        generate_button.clicked.connect(self.generate_rco)
        buttons_row.addWidget(generate_button)
        buttons_row.addStretch(1)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.close)
        buttons_row.addWidget(close_buttons)
        root.addLayout(buttons_row)

    def refresh_layers(self):
        current_id = self.current_layer_id()
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsVectorLayer)
        ]
        layers.sort(key=lambda layer: layer.name().lower())
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        self.layer_ids = []
        selected_index = -1
        active = self.iface.activeLayer()
        for index, layer in enumerate(layers):
            self.layer_combo.addItem(layer.name())
            self.layer_ids.append(layer.id())
            if layer.id() == current_id or (
                current_id is None and active is not None and layer.id() == active.id()
            ):
                selected_index = index
        if selected_index >= 0:
            self.layer_combo.setCurrentIndex(selected_index)
        self.layer_combo.blockSignals(False)

    def current_layer_id(self) -> str | None:
        index = self.layer_combo.currentIndex()
        if 0 <= index < len(self.layer_ids):
            return self.layer_ids[index]
        return None

    def current_layer(self) -> QgsVectorLayer | None:
        layer_id = self.current_layer_id()
        if not layer_id:
            return None
        layer = QgsProject.instance().mapLayer(layer_id)
        return layer if isinstance(layer, QgsVectorLayer) else None

    @staticmethod
    def _geometry_label(layer: QgsVectorLayer) -> str:
        geometry_type = layer.geometryType()
        if geometry_type == QgsWkbTypes.PointGeometry:
            return "Ponto"
        if geometry_type == QgsWkbTypes.LineGeometry:
            return "Linha"
        if geometry_type == QgsWkbTypes.PolygonGeometry:
            return "Polígono"
        return "Sem geometria"

    @staticmethod
    def _infer_typology(field) -> str:
        type_name = (field.typeName() or "").lower()
        if any(token in type_name for token in ("int", "integer", "long")):
            return "Numérico (inteiro)"
        if any(
            token in type_name
            for token in ("double", "real", "float", "numeric", "decimal")
        ):
            return "Numérico (decimal)"
        if "datetime" in type_name or "timestamp" in type_name:
            return "Data e hora"
        if "date" in type_name:
            return "Data"
        if "bool" in type_name:
            return "Lógico"
        return "Texto"

    def analyze_selected_layer(self):
        layer = self.current_layer()
        if layer is None:
            QMessageBox.warning(self, "Gerador de RCO", "Nenhuma camada vetorial foi selecionada.")
            return
        try:
            inputs = [
                FieldInput(
                    name=field.name(),
                    inferred_typology=self._infer_typology(field),
                    type_name=field.typeName() or "",
                    length=field.length(),
                    precision=field.precision(),
                )
                for field in layer.fields()
            ]
            geometry = self._geometry_label(layer)
            conn = connect_catalog(self.catalog_path)
            try:
                self.analysis = analyze_fields(conn, layer.name(), geometry, inputs)
            finally:
                conn.close()
            self.title_edit.setText(layer.name())
            self.identifier_edit.setText(layer.name())
            self._fill_table(self.analysis.fields)
            review_count = sum(field.needs_review for field in self.analysis.fields)
            self.status_label.setText(
                f"{len(self.analysis.fields)} campo(s) analisado(s). "
                f"{review_count} campo(s) precisam de revisão."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao analisar", str(exc))

    def _fill_table(self, fields: list[FieldResult]):
        self.table.setRowCount(len(fields))
        for row, field in enumerate(fields):
            values = [
                field.field_name,
                field.final_typology,
                field.description,
                field.confidence,
                field.source,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if column in (0, 3, 4):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if field.needs_review:
                    item.setBackground(QBrush(QColor(255, 245, 204)))
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()

    def _analysis_from_table(self) -> LayerAnalysis:
        if self.analysis is None:
            raise ValueError("Analise uma camada antes de gerar o documento.")
        fields: list[FieldResult] = []
        for row, original in enumerate(self.analysis.fields):
            typology_item = self.table.item(row, 1)
            description_item = self.table.item(row, 2)
            fields.append(
                FieldResult(
                    field_name=original.field_name,
                    inferred_typology=original.inferred_typology,
                    final_typology=(typology_item.text().strip() if typology_item else "")
                    or original.final_typology,
                    description=(description_item.text().strip() if description_item else "")
                    or original.description,
                    confidence=original.confidence,
                    source=original.source,
                    source_identifier=original.source_identifier,
                    source_page=original.source_page,
                    needs_review=original.needs_review,
                )
            )
        return LayerAnalysis(
            layer_name=self.analysis.layer_name,
            geometry=self.analysis.geometry,
            fields=fields,
        )

    def generate_rco(self):
        try:
            analysis = self._analysis_from_table()
        except ValueError as exc:
            QMessageBox.warning(self, "Gerador de RCO", str(exc))
            return

        review_suffix = "_REVISAR" if any(f.needs_review for f in analysis.fields) else ""
        suggested = f"{analysis.layer_name}_rco{review_suffix}.docx"
        output, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar RCO",
            suggested,
            "Documento do Word (*.docx)",
        )
        if not output:
            return
        if not output.lower().endswith(".docx"):
            output += ".docx"
        try:
            generated = generate_rco_docx(
                analysis=analysis,
                output_docx=Path(output),
                template_path=self.template_path,
                title=self.title_edit.text().strip() or analysis.layer_name,
                identifier=self.identifier_edit.text().strip() or analysis.layer_name,
                mark_pending=self.mark_review.isChecked(),
            )
            QMessageBox.information(
                self,
                "RCO gerado",
                f"Documento salvo em:\n{generated}\n\nUm relatório JSON de auditoria foi salvo junto.",
            )
            self.status_label.setText(f"RCO gerado: {generated}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao gerar RCO", str(exc))
