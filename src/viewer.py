"""PyQt5 GUI that loads STEP files and shows 2D HLR projections.

Heavy work (file I/O, meshing, HLR) runs on background QThreads so the UI never
freezes. For 100 MB+ files the meshing step is the slowest; deflection is
auto-derived from the model's bounding box.
"""

from __future__ import annotations

import math
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
from .loader import load_step, load_step_labels, shape_diagonal
from .projector import VIEWS, edges_to_polylines, project, project_point

Polyline = List


class _LoaderThread(QThread):
    done = pyqtSignal(object, float, list)  # shape, diagonal, components
    failed = pyqtSignal(str)
    info = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            shape = load_step(self.path, progress=self.info.emit)
            diag = shape_diagonal(shape)
            self.info.emit("Reading component names...")
            components = load_step_labels(self.path)
            self.done.emit(shape, diag, components)
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
        self._components: list = []   # [(name, (cx,cy,cz)), ...]
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

        self.annot_act = QAction("Dimensions", self, checkable=True)
        self.annot_act.setChecked(False)
        self.annot_act.setToolTip("Show overall W×H dimension lines and a scale bar")
        self.annot_act.triggered.connect(self._redraw)
        tb.addAction(self.annot_act)

        self.labels_act = QAction("Labels", self, checkable=True)
        self.labels_act.setChecked(False)
        self.labels_act.setToolTip("Show component names from the STEP assembly tree")
        self.labels_act.triggered.connect(self._redraw)
        tb.addAction(self.labels_act)

        self.flipy_act = QAction("Flip Y", self, checkable=True)
        self.flipy_act.setChecked(True)
        self.flipy_act.setToolTip("Invert the vertical axis (fixes upside-down floor plans)")
        self.flipy_act.triggered.connect(self._redraw)
        tb.addAction(self.flipy_act)

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

    def _on_loaded(self, shape, diagonal: float, components: list):
        self.shape = shape
        self.diagonal = diagonal
        self._components = components
        defl = max(diagonal * 0.001, 1e-4)
        self.defl_spin.blockSignals(True)
        self.defl_spin.setValue(defl)
        self.defl_spin.blockSignals(False)
        n_labels = len(components)
        label_info = f", {n_labels} named component(s)" if n_labels else ""
        self.statusBar().showMessage(
            f"Loaded. Bounding-box diagonal = {diagonal:.2f}{label_info}, deflection = {defl:.4f}"
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
            if self.flipy_act.isChecked():
                lo, hi = self.ax.get_ylim()
                self.ax.set_ylim(hi, lo)
        else:
            self.ax.set_xlim(prev_xlim)
            self.ax.set_ylim(prev_ylim)

        if self.annot_act.isChecked():
            self._draw_dimensions()

        if self.labels_act.isChecked():
            self._draw_labels()

        self.canvas.draw_idle()

    def _draw_dimensions(self):
        """Overlay overall W×H dimension arrows and a scale bar in model units (mm)."""
        if not self.visible_pl:
            return

        all_pts = [p for poly in self.visible_pl for p in poly]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        W = xmax - xmin
        H = ymax - ymin
        if W == 0 or H == 0:
            return

        pad = max(W, H) * 0.10
        blue = "#0055aa"

        # Width dimension (below the drawing)
        dy = ymin - pad * 0.9
        self.ax.annotate(
            "", xy=(xmax, dy), xytext=(xmin, dy),
            arrowprops=dict(arrowstyle="<->", color=blue, lw=1.4, mutation_scale=14),
        )
        for tx in (xmin, xmax):
            self.ax.plot([tx, tx], [dy - H * 0.012, dy + H * 0.012], color=blue, lw=1.2)
        w_label = f"W = {W:.0f} mm" if W < 10_000 else f"W = {W / 1000:.2f} m"
        self.ax.text(
            (xmin + xmax) / 2, dy - H * 0.025, w_label,
            ha="center", va="top", fontsize=9, color=blue, fontweight="bold",
        )

        # Height dimension (right of the drawing)
        dx = xmax + pad * 0.9
        self.ax.annotate(
            "", xy=(dx, ymax), xytext=(dx, ymin),
            arrowprops=dict(arrowstyle="<->", color=blue, lw=1.4, mutation_scale=14),
        )
        for ty in (ymin, ymax):
            self.ax.plot([dx - W * 0.012, dx + W * 0.012], [ty, ty], color=blue, lw=1.2)
        h_label = f"H = {H:.0f} mm" if H < 10_000 else f"H = {H / 1000:.2f} m"
        self.ax.text(
            dx + W * 0.025, (ymin + ymax) / 2, h_label,
            ha="left", va="center", fontsize=9, color=blue, fontweight="bold",
            rotation=90,
        )

        # Scale bar (bottom-left corner)
        raw = W * 0.15
        mag = 10 ** math.floor(math.log10(raw))
        scale_len = round(raw / mag) * mag
        scale_label = f"{scale_len:.0f} mm" if scale_len < 1000 else f"{scale_len / 1000:.1f} m"
        bar_y = ymin - pad * 2.0
        bx0, bx1 = xmin, xmin + scale_len
        tick_h = H * 0.012
        self.ax.plot([bx0, bx1], [bar_y, bar_y], color="black", lw=3)
        for bx in (bx0, bx1):
            self.ax.plot([bx, bx], [bar_y - tick_h, bar_y + tick_h], color="black", lw=2)
        self.ax.text(
            (bx0 + bx1) / 2, bar_y - tick_h * 1.5, scale_label,
            ha="center", va="top", fontsize=8,
        )

        # Expand axes so annotations are not clipped
        xl = list(self.ax.get_xlim())
        yl = list(self.ax.get_ylim())
        xl[1] = max(xl[1], dx + W * 0.15)
        yl[0] = min(yl[0], bar_y - H * 0.06)
        self.ax.set_xlim(xl)
        self.ax.set_ylim(yl)

    def _draw_labels(self):
        """Overlay component names from the STEP assembly tree at their projected centres."""
        if not self._components:
            self.statusBar().showMessage(
                "No named components found in this STEP file — Labels has nothing to show."
            )
            return

        view = self.view_combo.currentText()
        seen: set = set()
        for name, (cx, cy, cz) in self._components:
            if name in seen:
                continue
            seen.add(name)
            try:
                x2d, y2d = project_point(cx, cy, cz, view)
                self.ax.text(
                    x2d, y2d, name,
                    ha="center", va="center", fontsize=7, color="#990000",
                    bbox=dict(
                        boxstyle="round,pad=0.25", facecolor="white",
                        alpha=0.75, edgecolor="#cccccc", linewidth=0.6,
                    ),
                    clip_on=True,
                )
            except Exception:
                pass

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
