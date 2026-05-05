"""PyQt5 GUI that loads STEP files and shows 2D HLR projections.

Heavy work (file I/O, meshing, HLR) runs on background QThreads so the UI never
freezes. For 100 MB+ files the meshing step is the slowest; deflection is
auto-derived from the model's bounding box.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qt5agg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

from .exporter import export_dxf, export_svg
from .loader import load_step, shape_diagonal
from .projector import VIEWS, edges_to_polylines, project

Polyline = List


class _LoaderThread(QThread):
    done = pyqtSignal(object, float)  # shape, diagonal
    failed = pyqtSignal(str)
    info = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            shape = load_step(self.path, progress=self.info.emit)
            diag = shape_diagonal(shape)
            self.done.emit(shape, diag)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class _ProjectThread(QThread):
    done = pyqtSignal(list, list)  # visible, hidden polylines
    failed = pyqtSignal(str)
    info = pyqtSignal(str)

    def __init__(self, shape, view: str, deflection: float):
        super().__init__()
        self.shape = shape
        self.view = view
        self.deflection = deflection

    def run(self):
        try:
            visible, hidden = project(
                self.shape, self.view, self.deflection, progress=self.info.emit
            )
            self.info.emit("Discretizing edges...")
            v_lines = edges_to_polylines(visible, self.deflection)
            h_lines = edges_to_polylines(hidden, self.deflection)
            self.done.emit(v_lines, h_lines)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class StepViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STEP 2D Viewer")
        self.resize(1280, 820)

        self.shape = None
        self.diagonal: float = 0.0
        self.visible_pl: List = []
        self.hidden_pl: List = []
        self._loader: Optional[_LoaderThread] = None
        self._projector: Optional[_ProjectThread] = None

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(10, 7), facecolor="white")
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self._reset_axes()

        nav = NavigationToolbar(self.canvas, self)
        layout.addWidget(nav)
        layout.addWidget(self.canvas)
        self.setCentralWidget(central)

        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        open_act = QAction("Open STEP...", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self.open_file)
        tb.addAction(open_act)

        tb.addSeparator()
        tb.addWidget(QLabel(" View: "))
        self.view_combo = QComboBox()
        self.view_combo.addItems(list(VIEWS.keys()))
        self.view_combo.setCurrentText("front")
        self.view_combo.currentTextChanged.connect(self._reproject)
        tb.addWidget(self.view_combo)

        tb.addSeparator()
        tb.addWidget(QLabel(" Deflection: "))
        self.defl_spin = QDoubleSpinBox()
        self.defl_spin.setDecimals(4)
        self.defl_spin.setRange(0.0001, 1e6)
        self.defl_spin.setValue(1.0)
        self.defl_spin.setSingleStep(0.5)
        self.defl_spin.setToolTip(
            "Mesh deflection. Smaller = higher fidelity but slower & more memory.\n"
            "Auto-set from model size when a file is loaded."
        )
        tb.addWidget(self.defl_spin)
        recompute_act = QAction("Recompute", self)
        recompute_act.triggered.connect(self._reproject)
        tb.addAction(recompute_act)

        tb.addSeparator()
        self.hidden_act = QAction("Hidden Lines", self, checkable=True)
        self.hidden_act.setChecked(True)
        self.hidden_act.triggered.connect(self._redraw)
        tb.addAction(self.hidden_act)

        tb.addSeparator()
        for fmt, label in (("svg", "Export SVG"), ("dxf", "Export DXF"), ("png", "Export PNG")):
            act = QAction(label, self)
            act.triggered.connect(lambda _checked=False, f=fmt: self._export(f))
            tb.addAction(act)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open a STEP file to begin.")

    def _reset_axes(self):
        self.ax.clear()
        self.ax.set_aspect("equal", adjustable="datalim")
        self.ax.axis("off")

    # ---- file loading ----

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open STEP file", "", "STEP files (*.step *.stp);;All files (*)"
        )
        if not path:
            return

        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.statusBar().showMessage(f"Loading {os.path.basename(path)} ({size_mb:.1f} MB)...")

        self._loader = _LoaderThread(path)
        self._loader.info.connect(lambda m: self.statusBar().showMessage(m))
        self._loader.failed.connect(self._on_error)
        self._loader.done.connect(self._on_loaded)
        self._loader.start()

    def _on_loaded(self, shape, diagonal: float):
        self.shape = shape
        self.diagonal = diagonal
        # Auto-deflection: 0.1% of bbox diagonal — good default for visualization.
        defl = max(diagonal * 0.001, 1e-4)
        self.defl_spin.blockSignals(True)
        self.defl_spin.setValue(defl)
        self.defl_spin.blockSignals(False)
        self.statusBar().showMessage(
            f"Loaded. Bounding-box diagonal = {diagonal:.2f}, deflection = {defl:.4f}"
        )
        self._reproject()

    # ---- projection ----

    def _reproject(self):
        if self.shape is None:
            return
        if self._projector and self._projector.isRunning():
            self.statusBar().showMessage("Busy — wait for current projection to finish.")
            return

        view = self.view_combo.currentText()
        defl = self.defl_spin.value()
        self.statusBar().showMessage(f"Projecting ({view}, deflection={defl:.4f})...")

        self._projector = _ProjectThread(self.shape, view, defl)
        self._projector.info.connect(lambda m: self.statusBar().showMessage(m))
        self._projector.failed.connect(self._on_error)
        self._projector.done.connect(self._on_projected)
        self._projector.start()

    def _on_projected(self, visible, hidden):
        self.visible_pl = visible
        self.hidden_pl = hidden
        self.statusBar().showMessage(
            f"Done. {len(visible)} visible / {len(hidden)} hidden edge segments."
        )
        self._redraw(autoscale=True)

    # ---- rendering ----

    def _redraw(self, autoscale: bool = False):
        prev_xlim = self.ax.get_xlim()
        prev_ylim = self.ax.get_ylim()

        self._reset_axes()

        if self.visible_pl:
            self.ax.add_collection(
                LineCollection(self.visible_pl, colors="black", linewidths=0.8)
            )
        if self.hidden_pl and self.hidden_act.isChecked():
            self.ax.add_collection(
                LineCollection(
                    self.hidden_pl,
                    colors="#888888",
                    linewidths=0.5,
                    linestyles=(0, (4, 2)),
                )
            )

        if autoscale or prev_xlim == (0.0, 1.0):
            self.ax.autoscale_view()
        else:
            self.ax.set_xlim(prev_xlim)
            self.ax.set_ylim(prev_ylim)

        self.canvas.draw_idle()

    # ---- export ----

    def _export(self, fmt: str):
        if not self.visible_pl and not self.hidden_pl:
            QMessageBox.warning(self, "Nothing to export", "Load a STEP file first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {fmt.upper()}", "", f"{fmt.upper()} (*.{fmt})"
        )
        if not path:
            return
        try:
            hidden = self.hidden_pl if self.hidden_act.isChecked() else []
            if fmt == "svg":
                export_svg(path, self.visible_pl, hidden)
            elif fmt == "dxf":
                export_dxf(path, self.visible_pl, hidden)
            elif fmt == "png":
                self.fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
            self.statusBar().showMessage(f"Exported -> {path}")
        except Exception as e:
            self._on_error(f"Export failed: {e}\n\n{traceback.format_exc()}")

    # ---- error handling ----

    def _on_error(self, msg: str):
        self.statusBar().showMessage("Error.")
        QMessageBox.critical(self, "Error", msg)


def main():
    app = QApplication(sys.argv)
    win = StepViewer()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
