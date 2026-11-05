"""Shared test setup: one QApplication for every Qt test (offscreen, no display needed)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    APP = QApplication.instance() or QApplication([])
except ImportError:            # server-only environments
    APP = None
