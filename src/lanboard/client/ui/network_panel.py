"""Network tab: HUD (S4), Inspector (S1) and Chaos Lab (S2) stacked in one scrollable column.

Each section has a collapsible header so a presenter can, for example, fold the
HUD away and bring the inspector and the chaos sliders on screen together.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from .chaos_panel import ChaosPanel
from .hud import HudPanel
from .inspector import InspectorPanel
from .widgets import Section


class NetworkPanel(QWidget):
    def __init__(self, net, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePage")
        self.hud = HudPanel(net, controller)
        self.inspector = InspectorPanel(net)
        self.inspector.setMinimumHeight(400)        # table + detail pane always usable
        self.chaos = ChaosPanel(net)

        column = QWidget()
        column.setObjectName("SidePage")
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.sections = [Section("Live HUD", self.hud), Section("Inspector", self.inspector),
                         Section("Chaos Lab", self.chaos)]
        for sec in self.sections:
            cl.addWidget(sec)
        cl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(column)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
