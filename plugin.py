from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtGui import QAction, QIcon

from .dialog import GeradorRCODialog


class GeradorRCOPlugin:
    """Integra o Gerador de RCO ao menu e à barra do QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.plugin_dir = Path(__file__).resolve().parent

    def initGui(self):
        icon = QIcon(str(self.plugin_dir / "icon.png"))
        self.action = QAction(icon, "Gerador de RCO", self.iface.mainWindow())
        self.action.setObjectName("gerador_rco_action")
        self.action.setWhatsThis("Gera um documento RCO a partir de uma camada vetorial do QGIS.")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&Gerador de RCO", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&Gerador de RCO", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def run(self):
        if self.dialog is None:
            self.dialog = GeradorRCODialog(self.iface, self.plugin_dir)
        self.dialog.refresh_layers()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
